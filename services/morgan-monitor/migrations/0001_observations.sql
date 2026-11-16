-- Receiver-owned timestamps and sequence guards live outside Foo. No raw source
-- bodies, private session identifiers, titles, paths, URLs or secrets are stored.
CREATE TABLE source_heads (
  source_id TEXT PRIMARY KEY,
  sequence INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  observed_at INTEGER NOT NULL,
  received_at INTEGER NOT NULL,
  health TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  last_good_observed_at INTEGER,
  last_good_received_at INTEGER,
  last_good_payload TEXT,
  last_good_config_hash TEXT
);

CREATE TABLE observations (
  source_id TEXT NOT NULL,
  sequence INTEGER NOT NULL CHECK (sequence > 0),
  event_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  observed_at INTEGER NOT NULL,
  received_at INTEGER NOT NULL,
  health TEXT NOT NULL CHECK (health IN ('ok', 'error', 'unconfigured')),
  config_hash TEXT NOT NULL,
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  PRIMARY KEY (source_id, sequence),
  UNIQUE (source_id, event_id)
);

-- These guards are transaction-local, not a racy JavaScript read-then-write.
CREATE TRIGGER observation_guard BEFORE INSERT ON observations BEGIN
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM source_heads WHERE source_id = NEW.source_id
      AND sequence >= NEW.sequence
  ) THEN RAISE(ABORT, 'sequence_replay') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM source_heads WHERE source_id = NEW.source_id
      AND observed_at > NEW.observed_at
  ) THEN RAISE(ABORT, 'clock_regression') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM source_heads WHERE source_id = NEW.source_id
      AND received_at > NEW.received_at - 1000
  ) THEN RAISE(ABORT, 'rate_limited') END;
END;

CREATE TRIGGER observation_advance AFTER INSERT ON observations BEGIN
  INSERT INTO source_heads (
    source_id, sequence, event_id, payload_sha256, observed_at, received_at,
    health, config_hash, last_good_observed_at, last_good_received_at,
    last_good_payload, last_good_config_hash
  ) VALUES (
    NEW.source_id, NEW.sequence, NEW.event_id, NEW.payload_sha256,
    NEW.observed_at, NEW.received_at, NEW.health, NEW.config_hash,
    CASE WHEN NEW.health = 'ok' THEN NEW.observed_at END,
    CASE WHEN NEW.health = 'ok' THEN NEW.received_at END,
    CASE WHEN NEW.health = 'ok' THEN NEW.payload END,
    CASE WHEN NEW.health = 'ok' THEN NEW.config_hash END
  ) ON CONFLICT(source_id) DO UPDATE SET
    sequence = NEW.sequence,
    event_id = NEW.event_id,
    payload_sha256 = NEW.payload_sha256,
    observed_at = NEW.observed_at,
    received_at = NEW.received_at,
    health = NEW.health,
    config_hash = NEW.config_hash,
    last_good_observed_at = CASE WHEN NEW.health = 'ok' THEN NEW.observed_at ELSE last_good_observed_at END,
    last_good_received_at = CASE WHEN NEW.health = 'ok' THEN NEW.received_at ELSE last_good_received_at END,
    last_good_payload = CASE WHEN NEW.health = 'ok' THEN NEW.payload ELSE last_good_payload END,
    last_good_config_hash = CASE WHEN NEW.health = 'ok' THEN NEW.config_hash ELSE last_good_config_hash END;
  -- Bound durable receipt history to the latest 100 events per registered source.
  -- The head permanently retains the monotonic sequence and last good payload.
  DELETE FROM observations WHERE source_id = NEW.source_id AND sequence NOT IN (
    SELECT sequence FROM observations WHERE source_id = NEW.source_id
      ORDER BY sequence DESC LIMIT 100
  );
END;
