PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS candidate_first_gate_queue (
  candidate_id INTEGER PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  qualification_batch_id TEXT,
  source_queue TEXT NOT NULL CHECK(source_queue IN ('daily_incremental','historical_backlog','unassigned')),
  state TEXT NOT NULL CHECK(state IN ('pending','running','retrying','completed','failed','excluded')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  enqueued_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  next_retry_at TEXT,
  error_code TEXT NOT NULL DEFAULT '',
  error_detail TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_first_gate_queue_state
  ON candidate_first_gate_queue(state,next_retry_at,enqueued_at,candidate_id);
CREATE INDEX IF NOT EXISTS idx_candidate_first_gate_queue_source
  ON candidate_first_gate_queue(source_queue,state,candidate_id);
CREATE INDEX IF NOT EXISTS idx_c22_evaluations_candidate_current
  ON evaluations(candidate_id,is_current,evaluated_at DESC);

INSERT INTO schema_meta(key,value,updated_at)
VALUES('candidate_first_gate_handoff_schema_version','c2.2-first-gate-handoff-v1',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;
