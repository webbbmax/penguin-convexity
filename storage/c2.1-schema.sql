PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id TEXT PRIMARY KEY,
  trigger_kind TEXT NOT NULL,
  state TEXT NOT NULL,
  stage TEXT NOT NULL,
  started_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  current_item TEXT NOT NULL DEFAULT '',
  completed_units INTEGER NOT NULL DEFAULT 0,
  total_units INTEGER NOT NULL DEFAULT 0,
  message TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL DEFAULT '',
  error_detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS source_cursors (
  source_id TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  stage TEXT NOT NULL,
  cursor_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  last_success_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, scope_key, stage)
);

CREATE TABLE IF NOT EXISTS source_health (
  source_id TEXT NOT NULL,
  scope_key TEXT NOT NULL,
  status TEXT NOT NULL,
  reason_code TEXT NOT NULL DEFAULT '',
  plain_reason TEXT NOT NULL DEFAULT '',
  http_status INTEGER,
  quota_remaining REAL,
  quota_reset_at TEXT,
  affected_object_count INTEGER NOT NULL DEFAULT 0,
  last_success_at TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_id, scope_key)
);

CREATE TABLE IF NOT EXISTS candidates (
  candidate_id INTEGER PRIMARY KEY,
  network_id TEXT NOT NULL,
  token_address TEXT NOT NULL,
  token_address_normalized TEXT NOT NULL,
  gate0_t0 TEXT NOT NULL,
  effective_t0 TEXT NOT NULL,
  t0_status TEXT NOT NULL DEFAULT 'not_verified',
  t0_evidence_type TEXT NOT NULL,
  t0_scope_json TEXT NOT NULL DEFAULT '{}',
  gate0_pool_id TEXT NOT NULL DEFAULT '',
  dex_ids_json TEXT NOT NULL DEFAULT '[]',
  source_run_id TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  continuity_status TEXT NOT NULL DEFAULT 'unknown',
  continuity_reason TEXT NOT NULL DEFAULT '',
  relationship_class TEXT NOT NULL DEFAULT 'D',
  relationship_reason TEXT NOT NULL DEFAULT '',
  mapped_project_id TEXT NOT NULL DEFAULT '',
  mapped_asset_id TEXT NOT NULL DEFAULT '',
  canonical_name TEXT NOT NULL DEFAULT '',
  symbol TEXT NOT NULL DEFAULT '',
  website_domain TEXT NOT NULL DEFAULT '',
  official_repo TEXT NOT NULL DEFAULT '',
  identity_status TEXT NOT NULL DEFAULT 'not_verified',
  local_stage TEXT NOT NULL DEFAULT 'discovered',
  local_reason TEXT NOT NULL DEFAULT '',
  last_evaluated_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (network_id, token_address_normalized)
);

CREATE INDEX IF NOT EXISTS idx_c21_candidates_stage
  ON candidates(local_stage, relationship_class, t0_status);
CREATE INDEX IF NOT EXISTS idx_c21_candidates_project
  ON candidates(mapped_project_id);
CREATE INDEX IF NOT EXISTS idx_c21_candidates_evaluation
  ON candidates(continuity_status, relationship_class, mapped_project_id);
CREATE INDEX IF NOT EXISTS idx_c21_candidates_t0
  ON candidates(effective_t0, t0_status);

CREATE TABLE IF NOT EXISTS candidate_pools (
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  pool_id TEXT NOT NULL,
  dex_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  source_id TEXT NOT NULL,
  indexed_status TEXT NOT NULL DEFAULT 'unknown',
  PRIMARY KEY (candidate_id, pool_id)
);

CREATE TABLE IF NOT EXISTS product_evidence (
  evidence_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  evidence_type TEXT NOT NULL,
  status TEXT NOT NULL,
  identity_status TEXT NOT NULL DEFAULT 'not_verified',
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  boundary_note TEXT NOT NULL DEFAULT '',
  UNIQUE (candidate_id, evidence_type, source_name, source_url)
);

CREATE INDEX IF NOT EXISTS idx_c21_product_candidate
  ON product_evidence(candidate_id, status);

CREATE TABLE IF NOT EXISTS market_observations (
  observation_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  window_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_status TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  pair_address TEXT NOT NULL DEFAULT '',
  pair_created_at TEXT,
  token_side TEXT NOT NULL DEFAULT '',
  liquidity_usd REAL,
  fdv_usd REAL,
  market_cap_usd REAL,
  volume_usd REAL,
  transaction_count REAL,
  observed_buys REAL,
  observed_sells REAL,
  volume_liquidity_ratio REAL,
  price_usd REAL,
  standard_sell_notional_usd REAL,
  standard_sell_quote_state TEXT NOT NULL DEFAULT 'no_data',
  standard_sell_quote_loss_pct REAL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (candidate_id, window_id, source_name)
);

CREATE INDEX IF NOT EXISTS idx_c21_market_candidate
  ON market_observations(candidate_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS risk_observations (
  observation_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  source_name TEXT NOT NULL,
  source_status TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  hard_trade_block INTEGER NOT NULL DEFAULT 0,
  severe_anomaly INTEGER NOT NULL DEFAULT 0,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS supply_observations (
  observation_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  window_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_status TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  supply_raw TEXT,
  decimals INTEGER,
  top10_share_pct REAL,
  holder_hhi REAL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (candidate_id, window_id, source_name)
);

CREATE TABLE IF NOT EXISTS pool_window_observations (
  observation_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  window_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_status TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  indexed_pool_count INTEGER,
  ohlcv_success_count INTEGER,
  unindexed_discovered_pool_count INTEGER,
  previous_average_volume_usd REAL,
  current_average_volume_usd REAL,
  previous_weighted_median_price_usd REAL,
  current_weighted_median_price_usd REAL,
  activity_log_change REAL,
  valuation_log_change REAL,
  relative_expansion REAL,
  risk_adjusted_surplus REAL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE (candidate_id, window_id, source_name)
);

CREATE TABLE IF NOT EXISTS evaluations (
  evaluation_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  evaluation_window_id TEXT NOT NULL,
  evaluated_at TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  rule_config_hash TEXT NOT NULL,
  cohort_snapshot_id TEXT NOT NULL,
  cohort_scope TEXT NOT NULL,
  cohort_sample_size INTEGER NOT NULL DEFAULT 0,
  age_days INTEGER NOT NULL,
  age_band TEXT NOT NULL,
  hard_gate_status TEXT NOT NULL,
  hard_gate_json TEXT NOT NULL,
  display_state TEXT NOT NULL,
  display_reason TEXT NOT NULL,
  paths_json TEXT NOT NULL,
  factor_directions_json TEXT NOT NULL,
  confidence_json TEXT NOT NULL,
  threshold_context_json TEXT NOT NULL,
  market_snapshot_json TEXT NOT NULL,
  source_impact_json TEXT NOT NULL,
  sort_score REAL NOT NULL DEFAULT 0,
  sort_reason TEXT NOT NULL,
  consecutive_completed_misses INTEGER NOT NULL DEFAULT 0,
  formed_at TEXT,
  invalidated_at TEXT,
  is_current INTEGER NOT NULL DEFAULT 1,
  UNIQUE (candidate_id, evaluation_window_id, rule_version)
);

CREATE INDEX IF NOT EXISTS idx_c21_evaluations_current
  ON evaluations(is_current, hard_gate_status, display_state, sort_score DESC);

CREATE TABLE IF NOT EXISTS material_changes (
  change_id TEXT PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
  changed_at TEXT NOT NULL,
  change_type TEXT NOT NULL,
  previous_value TEXT NOT NULL DEFAULT '',
  current_value TEXT NOT NULL DEFAULT '',
  why_it_matters TEXT NOT NULL,
  source_cutoff_at TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_c21_changes_time
  ON material_changes(changed_at DESC);

CREATE TABLE IF NOT EXISTS snapshot_builds (
  build_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  source_cutoff_at TEXT NOT NULL,
  front_path TEXT NOT NULL,
  backend_path TEXT NOT NULL,
  front_sha256 TEXT NOT NULL DEFAULT '',
  backend_sha256 TEXT NOT NULL DEFAULT '',
  front_visible_count INTEGER NOT NULL DEFAULT 0,
  hard_gate_passed_count INTEGER NOT NULL DEFAULT 0,
  error_detail TEXT NOT NULL DEFAULT ''
);
