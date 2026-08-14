PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS candidate_production_records (
  candidate_id INTEGER PRIMARY KEY REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL,
  project_id TEXT,
  local_state TEXT NOT NULL,
  local_reason_code TEXT NOT NULL,
  local_plain_reason TEXT NOT NULL,
  local_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  local_checked_at TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  market_state TEXT,
  market_reason_code TEXT NOT NULL DEFAULT '',
  market_plain_reason TEXT NOT NULL DEFAULT '',
  market_source TEXT NOT NULL DEFAULT '',
  market_source_state TEXT,
  market_attempt_count INTEGER NOT NULL DEFAULT 0,
  market_observed_at TEXT,
  pair_address TEXT NOT NULL DEFAULT '',
  token_side TEXT NOT NULL DEFAULT '',
  observed_buys REAL,
  observed_sells REAL,
  next_retry_at TEXT,
  tracking_eligible INTEGER NOT NULL DEFAULT 0 CHECK(tracking_eligible IN (0,1)),
  tracking_reason_code TEXT NOT NULL DEFAULT '',
  t0_status TEXT NOT NULL,
  effective_t0 TEXT NOT NULL,
  age_days INTEGER,
  hard_block_state TEXT NOT NULL DEFAULT 'pending',
  identity_state TEXT NOT NULL,
  product_evidence_state TEXT NOT NULL,
  risk_data_state TEXT NOT NULL,
  relationship_class TEXT NOT NULL,
  identity_consistent INTEGER NOT NULL DEFAULT 0 CHECK(identity_consistent IN (0,1)),
  qualifying_product_evidence INTEGER NOT NULL DEFAULT 0 CHECK(qualifying_product_evidence IN (0,1)),
  confirmed_hard_block INTEGER NOT NULL DEFAULT 0 CHECK(confirmed_hard_block IN (0,1)),
  front_contract_ready INTEGER NOT NULL DEFAULT 0 CHECK(front_contract_ready IN (0,1)),
  front_eligible INTEGER NOT NULL DEFAULT 0 CHECK(front_eligible IN (0,1)),
  front_reason_code TEXT NOT NULL DEFAULT '',
  qualification_batch_id TEXT,
  qualified_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_production_local
  ON candidate_production_records(local_state, market_state, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_candidate_production_tracking
  ON candidate_production_records(tracking_eligible, relationship_class, front_eligible);
CREATE INDEX IF NOT EXISTS idx_candidate_production_asset
  ON candidate_production_records(asset_id);

CREATE TABLE IF NOT EXISTS candidate_scan_partitions (
  partition_id TEXT PRIMARY KEY,
  queue_name TEXT NOT NULL CHECK(queue_name IN ('daily_incremental','historical_backlog')),
  network_id TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','running','retrying','paused','completed','failed')),
  stage TEXT NOT NULL DEFAULT 'local_scan',
  total_count INTEGER NOT NULL,
  processed_count INTEGER NOT NULL DEFAULT 0,
  local_scanned_count INTEGER NOT NULL DEFAULT 0,
  market_requested_count INTEGER NOT NULL DEFAULT 0,
  market_confirmed_count INTEGER NOT NULL DEFAULT 0,
  tracking_eligible_count INTEGER NOT NULL DEFAULT 0,
  last_committed_cursor INTEGER NOT NULL DEFAULT 0,
  last_checkpoint_at TEXT,
  last_heartbeat_at TEXT,
  next_retry_at TEXT,
  source_state TEXT,
  error_detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  started_at TEXT,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_candidate_scan_partitions_queue
  ON candidate_scan_partitions(queue_name, state, next_retry_at, network_id, created_at);

CREATE TABLE IF NOT EXISTS candidate_scan_partition_members (
  partition_id TEXT NOT NULL REFERENCES candidate_scan_partitions(partition_id) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','local_done','completed')),
  PRIMARY KEY(partition_id, sequence_no),
  UNIQUE(partition_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_scan_members_state
  ON candidate_scan_partition_members(partition_id, state, sequence_no);
CREATE INDEX IF NOT EXISTS idx_candidate_scan_members_candidate
  ON candidate_scan_partition_members(candidate_id, partition_id);

CREATE TABLE IF NOT EXISTS candidate_qualification_batches (
  qualification_batch_id TEXT PRIMARY KEY,
  partition_id TEXT NOT NULL REFERENCES candidate_scan_partitions(partition_id),
  state TEXT NOT NULL CHECK(state IN ('building','completed','failed')),
  candidate_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  input_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_qualification_members (
  qualification_batch_id TEXT NOT NULL REFERENCES candidate_qualification_batches(qualification_batch_id) ON DELETE CASCADE,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL,
  relationship_class TEXT NOT NULL,
  front_eligible INTEGER NOT NULL CHECK(front_eligible IN (0,1)),
  PRIMARY KEY(qualification_batch_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_qualification_asset
  ON candidate_qualification_members(asset_id, qualification_batch_id);

CREATE TABLE IF NOT EXISTS candidate_production_runs (
  run_id TEXT PRIMARY KEY,
  trigger_kind TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  selected_queue TEXT,
  current_partition_id TEXT,
  completed_partitions INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL DEFAULT '',
  error_detail TEXT NOT NULL DEFAULT ''
);

INSERT INTO schema_meta(key,value,updated_at)
VALUES('candidate_production_schema_version','c2.2-candidate-production-v1',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at;
