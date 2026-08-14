CREATE TABLE IF NOT EXISTS c2_4_first_gate_history (
  candidate_id INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL UNIQUE,
  passed_at TEXT NOT NULL,
  age_days_at_pass INTEGER NOT NULL,
  checks_json TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS c2_4_public_history (
  candidate_id INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL UNIQUE,
  first_public_at TEXT NOT NULL,
  last_public_at TEXT NOT NULL,
  last_public_age_days INTEGER NOT NULL,
  last_public_state TEXT NOT NULL,
  last_evaluation_window_id TEXT NOT NULL,
  public_active INTEGER NOT NULL DEFAULT 1,
  last_public_exit_reason TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS c2_4_lifecycle_state (
  candidate_id INTEGER PRIMARY KEY,
  asset_id TEXT NOT NULL UNIQUE,
  lifecycle_pool TEXT NOT NULL CHECK(lifecycle_pool IN ('new_0_90','continued_91_plus')),
  continued_tracking_since TEXT,
  consecutive_completed_misses INTEGER NOT NULL DEFAULT 0,
  last_exit_window_id TEXT NOT NULL DEFAULT '',
  stopped_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(candidate_id) REFERENCES candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_c2_4_public_history_age
  ON c2_4_public_history(last_public_age_days,last_public_at);
CREATE INDEX IF NOT EXISTS idx_c2_4_lifecycle_pool
  ON c2_4_lifecycle_state(lifecycle_pool,updated_at);

INSERT INTO schema_meta(key,value,updated_at)
VALUES('c2_4_tracking_schema_version','2',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;
