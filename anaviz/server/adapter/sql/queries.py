"""Named SQL constants. All cache queries live here, not in Python code."""

# ── Coverage ──────────────────────────────────────────────────────────────

COVERAGE = """
SELECT t_min, t_max FROM cache_coverage
WHERE dataset_key = %s AND entity_id = %s AND measure_id = %s
  AND t_max > %s AND t_min < %s
"""

STORE_COVERAGE = """
INSERT INTO cache_coverage
    (dataset_key, entity_id, measure_id, t_min, t_max, status)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""

# ── Series store ──────────────────────────────────────────────────────────

STORE_SERIES = """
INSERT INTO cache_series
    (dataset_key, entity_id, measure_id, ts, value, quality)
SELECT %s, %s, %s, ts, value, quality FROM _cs
ON CONFLICT DO NOTHING
"""

# ── Series read ───────────────────────────────────────────────────────────

COUNT = """
SELECT count(*) FROM cache_series
WHERE dataset_key = %s AND entity_id = %s AND measure_id = %s
  AND ts >= %s AND ts < %s
"""

READ = """
SELECT extract(epoch FROM ts)::float8, value, quality, total
FROM (
    SELECT ts, value, quality, COUNT(*) OVER () AS total
    FROM cache_series
    WHERE dataset_key = %s AND entity_id = %s AND measure_id = %s
      AND ts >= %s AND ts < %s
) q
ORDER BY ts LIMIT %s
"""

READ_BUCKETED = """
SELECT
    idx * %s AS bucket_start,
    (array_agg(v ORDER BY t))[1]              AS v_first,
    (array_agg(t ORDER BY t))[1]              AS first_ts,
    min(v)                                     AS v_min,
    (array_agg(t ORDER BY v, t))[1]           AS min_ts,
    max(v)                                     AS v_max,
    (array_agg(t ORDER BY v DESC, t DESC))[1] AS max_ts,
    (array_agg(v ORDER BY t DESC))[1]         AS v_last,
    (array_agg(t ORDER BY t DESC))[1]         AS last_ts,
    avg(v)                                     AS v_avg,
    count(*)                                   AS n,
    max(q)                                     AS q_worst
FROM (
    SELECT
        floor(extract(epoch FROM ts) / %s)::bigint AS idx,
        extract(epoch FROM ts)::float8 AS t,
        value AS v,
        quality::smallint AS q
    FROM cache_series
    WHERE dataset_key = %s AND entity_id = %s AND measure_id = %s
      AND ts >= %s AND ts < %s
) sub
GROUP BY idx ORDER BY idx LIMIT %s
"""

# ── Rollup ────────────────────────────────────────────────────────────────

UPSERT_ROLLUP = """
INSERT INTO cache_rollup
    (dataset_key, entity_id, measure_id, bucket_s, bucket_start, n,
     v_first, first_ts, v_min, min_ts, v_max, max_ts,
     v_last, last_ts, v_avg, q_worst)
SELECT %s, entity_id, measure_id, bucket_s, bucket_start, n,
       v_first, first_ts, v_min, min_ts, v_max, max_ts,
       v_last, last_ts, v_avg, q_worst FROM _rs
ON CONFLICT (dataset_key, entity_id, measure_id, bucket_s, bucket_start)
DO UPDATE SET
    n        = cache_rollup.n + EXCLUDED.n,
    v_first  = CASE WHEN EXCLUDED.first_ts < cache_rollup.first_ts
                    THEN EXCLUDED.v_first ELSE cache_rollup.v_first END,
    first_ts = LEAST(cache_rollup.first_ts, EXCLUDED.first_ts),
    v_min    = LEAST(cache_rollup.v_min, EXCLUDED.v_min),
    min_ts   = CASE WHEN EXCLUDED.v_min < cache_rollup.v_min OR
                    (EXCLUDED.v_min = cache_rollup.v_min AND
                     EXCLUDED.min_ts < cache_rollup.min_ts)
                    THEN EXCLUDED.min_ts ELSE cache_rollup.min_ts END,
    v_max    = GREATEST(cache_rollup.v_max, EXCLUDED.v_max),
    max_ts   = CASE WHEN EXCLUDED.v_max > cache_rollup.v_max OR
                    (EXCLUDED.v_max = cache_rollup.v_max AND
                     EXCLUDED.max_ts > cache_rollup.max_ts)
                    THEN EXCLUDED.max_ts ELSE cache_rollup.max_ts END,
    v_last   = CASE WHEN EXCLUDED.last_ts > cache_rollup.last_ts
                    THEN EXCLUDED.v_last ELSE cache_rollup.v_last END,
    last_ts  = GREATEST(cache_rollup.last_ts, EXCLUDED.last_ts),
    v_avg    = (cache_rollup.v_avg * cache_rollup.n +
                EXCLUDED.v_avg * EXCLUDED.n) /
               (cache_rollup.n + EXCLUDED.n),
    q_worst  = GREATEST(cache_rollup.q_worst, EXCLUDED.q_worst)
"""

READ_ROLLUP = """
SELECT
    extract(epoch FROM bucket_start), v_first,
    extract(epoch FROM first_ts),     v_min,
    extract(epoch FROM min_ts),       v_max,
    extract(epoch FROM max_ts),       v_last,
    extract(epoch FROM last_ts),      v_avg,
    n, q_worst
FROM cache_rollup
WHERE dataset_key = %s AND entity_id = %s AND measure_id = %s
  AND bucket_s = %s AND bucket_start >= %s AND bucket_start < %s
ORDER BY bucket_start LIMIT %s
"""

# ── Cleanup ───────────────────────────────────────────────────────────────

DROP_SERIES   = "DELETE FROM cache_series   WHERE dataset_key = %s"
DROP_COVERAGE = "DELETE FROM cache_coverage WHERE dataset_key = %s"
DROP_ROLLUP   = "DELETE FROM cache_rollup   WHERE dataset_key = %s"

CLEAR_ALL = """
TRUNCATE cache_series, cache_coverage, cache_rollup CASCADE
"""
