CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Core time-series store
CREATE TABLE eventhistory (
    element_id   INTEGER          NOT NULL,
    ts           TIMESTAMPTZ      NOT NULL,
    value        DOUBLE PRECISION NOT NULL,
    quality_flag SMALLINT         NOT NULL DEFAULT 0
);

SELECT create_hypertable('eventhistory', 'ts',
                         chunk_time_interval => INTERVAL '7 days');

CREATE INDEX ix_eventhistory_elem_ts ON eventhistory (element_id, ts DESC);
-- A channel/timestamp identifies one source observation.  The ingestion and
-- cache writers use this index for idempotent conflict-safe inserts.
CREATE UNIQUE INDEX ux_eventhistory_element_ts ON eventhistory (element_id, ts);

-- FSM state transitions
CREATE TABLE transitions (
    transition_id  BIGINT      PRIMARY KEY,
    system_id      INTEGER     NOT NULL,
    old_state_int  SMALLINT    NOT NULL,
    new_state_int  SMALLINT    NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    operator_id    INTEGER
);
CREATE INDEX ix_transitions_sys_ts ON transitions (system_id, ts);

-- Channel metadata
CREATE TABLE hardware_mapping (
    element_id INTEGER PRIMARY KEY,
    full_name  TEXT    NOT NULL UNIQUE
);

-- Subsystem registry
CREATE TABLE subsystems (
    system_id INTEGER PRIMARY KEY,
    subsystem TEXT    NOT NULL
);
