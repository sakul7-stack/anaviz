-- Generic fixed-schema cache for the PROJECT container.
--
-- TimescaleDB is used for the two timestamp-dense tables below. The cache is
-- disposable and hydrates again over HTTP, so these declarations also make a
-- fresh cache volume start with the intended hypertable layout.
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Any number of configured datasets share these tables; rows are isolated by
-- `dataset_key` = "<dataset_id>:<config_fingerprint>". A changed config
-- fingerprint therefore creates a fresh namespace and can never leak stale
-- rows into the new schema. No per-source tables, no continuous aggregates,
-- no refresh policies — the app hydrates on demand, stores raw samples in
-- cache_series, and (when a config declares rollup_levels) aggregates them
-- into cache_rollup buckets. Tiers are strictly opt-in: explicit bucket sizes
-- in the config, never derived from the source's sampling step.

CREATE TABLE IF NOT EXISTS cache_series (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    ts          TIMESTAMPTZ NOT NULL,
    value       DOUBLE PRECISION NOT NULL,
    quality     SMALLINT NOT NULL DEFAULT 0,
    PRIMARY KEY (dataset_key, entity_id, measure_id, ts)
);

CREATE INDEX IF NOT EXISTS ix_cache_series_range
    ON cache_series (dataset_key, entity_id, measure_id, ts);

-- Partition raw samples by time while preserving the existing primary key and
-- conflict-safe inserts used by GenericCache.store(). migrate_data handles an
-- old ordinary cache table during an in-place upgrade.
SELECT create_hypertable(
    'cache_series', 'ts',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- Coverage describes ranges that have been queried, not ranges in which every
-- timestamp has a value. 'empty' ranges are known to contain no samples and
-- are never re-probed; 'queried' ranges keep internal source gaps visible.
CREATE TABLE IF NOT EXISTS cache_coverage (
    dataset_key TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    measure_id  TEXT NOT NULL,
    t_min       TIMESTAMPTZ NOT NULL,
    t_max       TIMESTAMPTZ NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('queried', 'empty')),
    UNIQUE (dataset_key, entity_id, measure_id, t_min, t_max, status)
);

CREATE INDEX IF NOT EXISTS ix_cache_coverage_range
    ON cache_coverage (dataset_key, entity_id, measure_id, t_min, t_max);

-- On-demand rollup tiers: generic first/min/max/last/avg buckets built from
-- cache_series rows at hydration time. Bucket grid anchored at the Unix epoch
-- so independent hydration ranges merge additively (upsert in the app).
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

CREATE INDEX IF NOT EXISTS ix_cache_rollup_range
    ON cache_rollup (dataset_key, entity_id, measure_id, bucket_s, bucket_start);

SELECT create_hypertable(
    'cache_rollup', 'bucket_start',
    if_not_exists => TRUE,
    migrate_data => TRUE
);
