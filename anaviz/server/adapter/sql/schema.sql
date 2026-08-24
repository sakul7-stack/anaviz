-- TimescaleDB is already supplied by the project image. Keep this startup
-- schema idempotent so a fresh database and an existing cache converge on the
-- same layout.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- cache_series: raw time-series samples
CREATE TABLE IF NOT EXISTS cache_series (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    quality     SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_cs_r
    ON cache_series (dataset_key, entity_id, measure_id, ts);
SELECT create_hypertable(
    'cache_series', 'ts',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- cache_coverage: which ranges have been fetched
CREATE TABLE IF NOT EXISTS cache_coverage (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    t_min       TIMESTAMPTZ NOT NULL,
    t_max       TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queried', 'empty')),
    UNIQUE (dataset_key, entity_id, measure_id, t_min, t_max, status)
);
CREATE INDEX IF NOT EXISTS ix_cc_r
    ON cache_coverage (dataset_key, entity_id, measure_id, t_min, t_max);

-- cache_rollup: aggregated buckets
CREATE TABLE IF NOT EXISTS cache_rollup (
    dataset_key  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    measure_id   TEXT NOT NULL,
    bucket_s     DOUBLE PRECISION NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    n            INTEGER NOT NULL,
    v_first      DOUBLE PRECISION NOT NULL,
    first_ts     TIMESTAMPTZ NOT NULL,
    v_min        DOUBLE PRECISION NOT NULL,
    min_ts       TIMESTAMPTZ NOT NULL,
    v_max        DOUBLE PRECISION NOT NULL,
    max_ts       TIMESTAMPTZ NOT NULL,
    v_last       DOUBLE PRECISION NOT NULL,
    last_ts      TIMESTAMPTZ NOT NULL,
    v_avg        DOUBLE PRECISION NOT NULL,
    q_worst      SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, bucket_s, bucket_start)
);
CREATE INDEX IF NOT EXISTS ix_cr_r
    ON cache_rollup (dataset_key, entity_id, measure_id, bucket_s, bucket_start);
SELECT create_hypertable(
    'cache_rollup', 'bucket_start',
    if_not_exists => TRUE,
    migrate_data => TRUE
);
