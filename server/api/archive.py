"""Archive clients: abstract interface + HTTP + HLT (direct DB) implementations.

- HttpArchiveClient talks to the datasource container's /archive/* API (:9000).
- HLTArchiveClient reads real data directly from a local TimescaleDB (single-
  container / host-ingest workflow).
- "simulator" maps to HttpArchiveClient (legacy simulator workflow).
"""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
import numpy as np

from ..config import Settings


# ── Data contracts ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChannelInfo:
    element_id: int
    name: str


@dataclass(frozen=True)
class TimeExtent:
    t_min: float
    t_max: float


@dataclass(frozen=True)
class TransitionRecord:
    transition_id: int
    system_id: int
    old: int
    new: int
    ts: datetime
    operator_id: int


# ── Abstract interface ────────────────────────────────────────────────────────

class ArchiveClient(ABC):
    @abstractmethod
    async def channels(self) -> list[ChannelInfo]: ...
    @abstractmethod
    async def extent(self) -> TimeExtent: ...
    @abstractmethod
    async def eventhistory_csv(
        self, element_id: int, t0: float, t1: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None: ...
    @abstractmethod
    async def transitions(
        self, t0: float, t1: float, system_id: int | None = None
    ) -> list[TransitionRecord]: ...
    async def aclose(self) -> None: ...


# ── HLT: reads directly from the local TimescaleDB (no HTTP) ─────────────────

class HLTArchiveClient(ArchiveClient):
    """
    Reads pre-ingested HLT data directly from TimescaleDB.
    Data must already be loaded via `python -m server ingest`.
    No network call is made — the archive IS the local DB.
    """

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url
        self._pool = None   # set lazily on first use

    async def _get_pool(self):
        if self._pool is None:
            from psycopg_pool import AsyncConnectionPool
            self._pool = AsyncConnectionPool(
                self._db_url, min_size=1, max_size=4, open=False)
            await self._pool.open()
        return self._pool

    async def channels(self) -> list[ChannelInfo]:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT element_id, full_name FROM hardware_mapping ORDER BY element_id")
            rows = await cur.fetchall()
        return [ChannelInfo(r[0], r[1]) for r in rows]

    async def extent(self) -> TimeExtent:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                "SELECT extract(epoch FROM min(ts))::float8, "
                "       extract(epoch FROM max(ts))::float8 "
                "FROM eventhistory")
            lo, hi = await cur.fetchone()
        return TimeExtent(lo or 0.0, hi or 0.0)

    async def eventhistory_csv(
        self, element_id: int, t0: float, t1: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """
        For HLT data that is already in the DB, this is a no-op:
        the cache layer will find full coverage and skip calling this.
        Return None to signal "nothing to fetch" (data already local).
        """
        return None

    async def transitions(
        self, t0: float, t1: float, system_id: int | None = None
    ) -> list[TransitionRecord]:
        pool = await self._get_pool()
        t0_dt = datetime.fromtimestamp(t0, tz=timezone.utc)
        t1_dt = datetime.fromtimestamp(t1, tz=timezone.utc)
        async with pool.connection() as conn:
            if system_id is not None:
                cur = await conn.execute(
                    "SELECT transition_id, system_id, old_state_int, new_state_int, "
                    "ts, operator_id FROM transitions "
                    "WHERE system_id=%s AND ts>=%s AND ts<%s ORDER BY ts",
                    (system_id, t0_dt, t1_dt))
            else:
                cur = await conn.execute(
                    "SELECT transition_id, system_id, old_state_int, new_state_int, "
                    "ts, operator_id FROM transitions "
                    "WHERE ts>=%s AND ts<%s ORDER BY ts",
                    (t0_dt, t1_dt))
            rows = await cur.fetchall()
        return [TransitionRecord(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]

    async def aclose(self) -> None:
        if self._pool:
            await self._pool.close()


# ── HTTP (datasource container /archive/* API) ───────────────────────────────

class HttpArchiveClient(ArchiveClient):
    """Talks to an archive that exposes the contract as REST/JSON."""

    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self.base   = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base, timeout=timeout)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self.client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def channels(self) -> list[ChannelInfo]:
        rows = await self._get("/archive/channels")
        return [ChannelInfo(r["element_id"], r["name"]) for r in rows]

    async def extent(self) -> TimeExtent:
        data = await self._get("/archive/extent")
        return TimeExtent(data["t_min"], data["t_max"])

    async def eventhistory_csv(
        self, element_id: int, t0: float, t1: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        resp = await self.client.get(
            "/archive/eventhistory.csv",
            params=dict(element_id=element_id, t0=t0, t1=t1))
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        parsed = np.genfromtxt(
            io.StringIO(resp.text), delimiter=",", skip_header=1, dtype=np.float64)
        if parsed.ndim != 2 or parsed.shape[0] == 0:
            return None
        return parsed[:, 0], parsed[:, 1], np.round(parsed[:, 2]).astype(np.int16)

    async def transitions(
        self, t0: float, t1: float, system_id: int | None = None
    ) -> list[TransitionRecord]:
        params: dict[str, Any] = {"t0": t0, "t1": t1}
        if system_id is not None:
            params["system_id"] = system_id
        rows = await self._get("/archive/transitions", params=params)
        return [TransitionRecord(
            r["transition_id"], r["system_id"], r["old"], r["new"],
            _parse_dt(r["ts"]), r["operator_id"],
        ) for r in rows]

    async def aclose(self) -> None:
        await self.client.aclose()


# ── Factory ───────────────────────────────────────────────────────────────────

def archive_from_settings(settings: Settings) -> ArchiveClient:
    """Return the right client based on DATA_SOURCE.

    - "http"     → HttpArchiveClient: hydrate from the datasource container's
                   /archive/* API on :9000 (the 2-container architecture).
    - "hlt"      → HLTArchiveClient: read real data directly from the local DB
                   (single-container / host-ingest workflow).
    - "simulator" → HttpArchiveClient (legacy simulator workflow).
    """
    if settings.data_source == "hlt":
        return HLTArchiveClient(settings.db_url)
    return HttpArchiveClient(settings.archive_url)


def _parse_dt(val) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    return datetime.fromisoformat(str(val))
