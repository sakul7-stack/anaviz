"""
SPOT — Streaming Peak Over Threshold anomaly detector.

Adapted from DeepHYDRA (extreme value theory approach).
Simplified for per-request use: fit on a training window, score a test window.

Reference:
  Siffer et al. (2017) "Anomaly Detection in Streams with Extreme Value Theory"
"""
from __future__ import annotations

import numpy as np


class SPOT:
    """
    Fit a Generalized Pareto Distribution to the tail of a training series,
    then assign anomaly probability scores to test points.

    Usage::

        spot = SPOT(risk=1e-4, init_percentile=0.98)
        threshold = spot.fit(train_array)
        scores = spot.score(test_array)   # values in [0, 1]
    """

    def __init__(self, risk: float = 1e-4, init_percentile: float = 0.98) -> None:
        self.risk = risk
        self.init_percentile = init_percentile
        self.t: float = 0.0
        self.c: float = 0.0
        self.sigma: float = 1.0
        self.threshold: float = float("inf")

    @staticmethod
    def _fit_gpd(peaks: np.ndarray) -> tuple[float, float]:
        """Method of Moments estimator for GPD (Hosking & Wallis, 1987)."""
        if len(peaks) < 5:
            return 0.0, float(np.std(peaks)) if len(peaks) > 1 else 1.0
        mu = float(np.mean(peaks))
        s2 = float(np.var(peaks, ddof=1))
        if s2 < 1e-12 or mu <= 0:
            return 0.0, max(mu, 1e-8)
        ratio = mu * mu / s2
        sigma = 0.5 * mu * (1.0 + ratio)
        c = float(np.clip(0.5 * (1.0 - ratio), -0.5, 0.5))
        return c, max(sigma, 1e-8)

    def fit(self, train: np.ndarray) -> float:
        """Fit SPOT on training data. Returns calibrated detection threshold."""
        train = np.asarray(train, dtype=float)
        train = train[np.isfinite(train)]
        if len(train) == 0:
            self.threshold = float("inf")
            return self.threshold

        self.t = float(np.quantile(train, self.init_percentile))
        peaks = train[train > self.t] - self.t
        if len(peaks) < 10:
            self.t = float(np.quantile(train, 0.95))
            peaks = train[train > self.t] - self.t

        self.c, self.sigma = self._fit_gpd(peaks)
        n = len(train)
        Nt = len(peaks)

        if Nt == 0 or self.sigma <= 0:
            self.threshold = self.t * 2.0
            return self.threshold

        r = n * self.risk / Nt
        try:
            if abs(self.c) < 1e-6:
                self.threshold = self.t - self.sigma * float(np.log(max(r, 1e-300)))
            else:
                self.threshold = self.t + (self.sigma / self.c) * (
                    pow(max(r, 1e-300), -self.c) - 1.0
                )
        except (ValueError, OverflowError):
            self.threshold = self.t + self.sigma * 10.0

        return float(self.threshold)

    def score(self, test: np.ndarray) -> np.ndarray:
        """Return anomaly score in [0, 1] for each test point (1 = most anomalous)."""
        test = np.asarray(test, dtype=float)
        scores = np.zeros(len(test), dtype=float)
        above = np.isfinite(test) & (test > self.t)
        if not above.any() or self.sigma <= 0:
            return scores
        exc = test[above] - self.t
        if abs(self.c) < 1e-6:
            cdf_vals = 1.0 - np.exp(-exc / self.sigma)
        else:
            inner = np.clip(1.0 + self.c * exc / self.sigma, 1e-12, None)
            cdf_vals = 1.0 - np.power(inner, -1.0 / self.c)
        scores[above] = np.clip(cdf_vals, 0.0, 1.0)
        return scores
