CREATE TABLE public_snapshots (
  revision TEXT PRIMARY KEY CHECK (length(revision) = 64),
  created_at INTEGER NOT NULL,
  payload TEXT NOT NULL CHECK (json_valid(payload))
);
CREATE TRIGGER public_snapshot_retention AFTER INSERT ON public_snapshots BEGIN
  DELETE FROM public_snapshots WHERE revision NOT IN (
    SELECT revision FROM public_snapshots ORDER BY created_at DESC, rowid DESC LIMIT 128
  );
END;
