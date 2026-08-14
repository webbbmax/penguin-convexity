PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS candidate_tracking_records (
  candidate_id INTEGER PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  qualification_batch_id TEXT,
  input_updated_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','running','completed','partial')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  source_states_json TEXT NOT NULL DEFAULT '{}',
  evaluated_at TEXT,
  last_attempt_at TEXT,
  completed_at TEXT,
  error_detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_tracking_state
  ON candidate_tracking_records(state,input_updated_at,updated_at);

INSERT INTO schema_meta(key,value,updated_at)
VALUES('candidate_tracking_schema_version','c2.2-candidate-tracking-v1',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;
