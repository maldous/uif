-- Deliberately reviewed keys only. No private text or automatic extraction.
CREATE TABLE curated_topics (
  source_id TEXT NOT NULL,
  channel TEXT NOT NULL CHECK (channel IN ('voice', 'chat')),
  topic_key TEXT NOT NULL,
  observed_at INTEGER NOT NULL,
  received_at INTEGER NOT NULL,
  sequence INTEGER NOT NULL,
  position INTEGER NOT NULL,
  config_hash TEXT NOT NULL,
  PRIMARY KEY (source_id, channel, topic_key)
);

CREATE TRIGGER observation_topics AFTER INSERT ON observations
WHEN NEW.health = 'ok' AND json_type(NEW.payload, '$.conversationTopics') = 'object'
BEGIN
  INSERT INTO curated_topics (source_id, channel, topic_key, observed_at, received_at, sequence, position, config_hash)
    SELECT NEW.source_id, 'voice', value, NEW.observed_at, NEW.received_at, NEW.sequence, CAST(key AS INTEGER), NEW.config_hash
    FROM json_each(NEW.payload, '$.conversationTopics.voice') WHERE true
    ON CONFLICT(source_id, channel, topic_key) DO UPDATE SET
      observed_at = NEW.observed_at, received_at = NEW.received_at,
      sequence = NEW.sequence, position = excluded.position, config_hash = NEW.config_hash
    WHERE NEW.observed_at >= curated_topics.observed_at;
  INSERT INTO curated_topics (source_id, channel, topic_key, observed_at, received_at, sequence, position, config_hash)
    SELECT NEW.source_id, 'chat', value, NEW.observed_at, NEW.received_at, NEW.sequence, CAST(key AS INTEGER), NEW.config_hash
    FROM json_each(NEW.payload, '$.conversationTopics.chat') WHERE true
    ON CONFLICT(source_id, channel, topic_key) DO UPDATE SET
      observed_at = NEW.observed_at, received_at = NEW.received_at,
      sequence = NEW.sequence, position = excluded.position, config_hash = NEW.config_hash
    WHERE NEW.observed_at >= curated_topics.observed_at;
  DELETE FROM curated_topics WHERE source_id = NEW.source_id AND channel = 'voice' AND topic_key NOT IN (
    SELECT topic_key FROM curated_topics WHERE source_id = NEW.source_id AND channel = 'voice'
      ORDER BY observed_at DESC, sequence DESC, position ASC LIMIT 10
  );
  DELETE FROM curated_topics WHERE source_id = NEW.source_id AND channel = 'chat' AND topic_key NOT IN (
    SELECT topic_key FROM curated_topics WHERE source_id = NEW.source_id AND channel = 'chat'
      ORDER BY observed_at DESC, sequence DESC, position ASC LIMIT 10
  );
END;
