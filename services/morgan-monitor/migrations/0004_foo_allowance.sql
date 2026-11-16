-- A separately qualified allowance source. Heartbeats do not imply allowance.
ALTER TABLE observations ADD COLUMN allowance_observed_at INTEGER;

CREATE TABLE foo_allowances (
  source_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('valid', 'exhausted', 'unavailable')),
  can_run_agents INTEGER,
  observed_at INTEGER NOT NULL,
  received_at INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  config_hash TEXT NOT NULL,
  CHECK ((state = 'valid' AND can_run_agents IS 1) OR
         (state = 'exhausted' AND can_run_agents IS 0) OR
         (state = 'unavailable' AND can_run_agents IS NULL))
);

CREATE TRIGGER allowance_guard BEFORE INSERT ON observations
WHEN json_type(NEW.payload, '$.fooAllowance') = 'object'
BEGIN
  SELECT CASE WHEN NEW.allowance_observed_at IS NULL
    THEN RAISE(ABORT, 'allowance_timestamp_required') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM foo_allowances WHERE source_id = NEW.source_id
      AND observed_at > NEW.allowance_observed_at
  ) THEN RAISE(ABORT, 'allowance_clock_regression') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM foo_allowances WHERE source_id = NEW.source_id
      AND observed_at = NEW.allowance_observed_at
      AND (state != json_extract(NEW.payload, '$.fooAllowance.state') OR
           can_run_agents IS NOT json_extract(NEW.payload, '$.fooAllowance.canRunAgents'))
  ) THEN RAISE(ABORT, 'allowance_observation_conflict') END;
END;

CREATE TRIGGER observation_allowance AFTER INSERT ON observations
WHEN NEW.health = 'ok' AND json_type(NEW.payload, '$.fooAllowance') = 'object'
BEGIN
  INSERT INTO foo_allowances (source_id, state, can_run_agents, observed_at, received_at, sequence, config_hash)
  VALUES (NEW.source_id, json_extract(NEW.payload, '$.fooAllowance.state'),
    json_extract(NEW.payload, '$.fooAllowance.canRunAgents'), NEW.allowance_observed_at,
    NEW.received_at, NEW.sequence, NEW.config_hash)
  ON CONFLICT(source_id) DO UPDATE SET
    state = excluded.state,
    can_run_agents = excluded.can_run_agents,
    observed_at = excluded.observed_at,
    received_at = CASE WHEN excluded.observed_at > foo_allowances.observed_at
      THEN excluded.received_at ELSE foo_allowances.received_at END,
    sequence = excluded.sequence,
    config_hash = excluded.config_hash;
END;
