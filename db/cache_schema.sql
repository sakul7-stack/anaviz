-- Generic fixed-schema cache for the PROJECT container.
--
-- Any number of configured datasets share these two tables; rows are isolated
-- by `dataset_key` = "<dataset_id>:<config_fingerprint>". A changed config
-- fingerprint therefore creates a fresh namespace and can never leak stale
-- rows into the new schema. No per-source tables, no continuous aggregates,
-- no refresh policies — the app hydrates on demand and downsamples in memory.

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
