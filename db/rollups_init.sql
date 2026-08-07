-- Continuous aggregate rollup views for the PROJECT (cache) database.
-- Created WITH NO DATA and refreshed ON DEMAND by the app (server/api/cache.py
-- calls refresh_continuous_aggregate after hydrating a range). No refresh
-- policies, no manual full refresh — nothing is precomputed.

CREATE MATERIALIZED VIEW IF NOT EXISTS eh_1m
WITH (timescaledb.continuous) AS
SELECT element_id,
       time_bucket('1 minute', ts) AS bucket,
       first(value, ts)             AS v_first,
       min(ts)                      AS first_ts,
       min(value)                   AS v_min,
       first(ts, value)             AS min_ts,
       max(value)                   AS v_max,
       last(ts, value)              AS max_ts,
       last(value, ts)              AS v_last,
       max(ts)                      AS last_ts,
       avg(value)                   AS v_avg,
       count(*)                     AS n,
       max(quality_flag)            AS q_worst
FROM eventhistory
GROUP BY element_id, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS eh_5m
WITH (timescaledb.continuous) AS
SELECT element_id,
       time_bucket('5 minutes', ts) AS bucket,
       first(value, ts)             AS v_first,
       min(ts)                      AS first_ts,
       min(value)                   AS v_min,
       first(ts, value)             AS min_ts,
       max(value)                   AS v_max,
       last(ts, value)              AS max_ts,
       last(value, ts)              AS v_last,
       max(ts)                      AS last_ts,
       avg(value)                   AS v_avg,
       count(*)                     AS n,
       max(quality_flag)            AS q_worst
FROM eventhistory
GROUP BY element_id, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS eh_1h
WITH (timescaledb.continuous) AS
SELECT element_id,
       time_bucket('1 hour', ts) AS bucket,
       first(value, ts)          AS v_first,
       min(ts)                   AS first_ts,
       min(value)                AS v_min,
       first(ts, value)          AS min_ts,
       max(value)                AS v_max,
       last(ts, value)           AS max_ts,
       last(value, ts)           AS v_last,
       max(ts)                   AS last_ts,
       avg(value)                AS v_avg,
       count(*)                  AS n,
       max(quality_flag)         AS q_worst
FROM eventhistory
GROUP BY element_id, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS eh_1d
WITH (timescaledb.continuous) AS
SELECT element_id,
       time_bucket('1 day', ts) AS bucket,
       first(value, ts)         AS v_first,
       min(ts)                  AS first_ts,
       min(value)               AS v_min,
       first(ts, value)         AS min_ts,
       max(value)               AS v_max,
       last(ts, value)          AS max_ts,
       last(value, ts)          AS v_last,
       max(ts)                  AS last_ts,
       avg(value)               AS v_avg,
       count(*)                 AS n,
       max(quality_flag)        AS q_worst
FROM eventhistory
GROUP BY element_id, bucket
WITH NO DATA;

-- Indexes for WHERE element_id=? AND bucket range pattern
CREATE INDEX IF NOT EXISTS ix_eh_1m_elem_bucket ON eh_1m (element_id, bucket);
CREATE INDEX IF NOT EXISTS ix_eh_5m_elem_bucket ON eh_5m (element_id, bucket);
CREATE INDEX IF NOT EXISTS ix_eh_1h_elem_bucket ON eh_1h (element_id, bucket);
CREATE INDEX IF NOT EXISTS ix_eh_1d_elem_bucket ON eh_1d (element_id, bucket);
