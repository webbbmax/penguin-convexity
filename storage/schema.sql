PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  access_method TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'convexity',
  confidence TEXT NOT NULL DEFAULT '待验证'
    CHECK (confidence IN ('高', '中', '低', '待验证')),
  conflict_risk TEXT NOT NULL DEFAULT '待评估'
    CHECK (conflict_risk IN ('高', '中', '低', '待评估')),
  status TEXT NOT NULL DEFAULT 'planned'
    CHECK (status IN ('planned', 'active', 'paused', 'error')),
  schedule_text TEXT NOT NULL DEFAULT '',
  last_checked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_accounts (
  account_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  platform TEXT NOT NULL,
  handle TEXT NOT NULL,
  actor_role TEXT NOT NULL DEFAULT '',
  independence_group TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'paused', 'rejected')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (source_id, platform, handle)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  job_name TEXT NOT NULL,
  mode TEXT NOT NULL
    CHECK (mode IN ('initialization', 'scheduled', 'manual', 'retry', 'replay')),
  status TEXT NOT NULL
    CHECK (status IN ('running', 'success', 'partial_success', 'failed', 'skipped')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  duration_ms INTEGER,
  collected_count INTEGER NOT NULL DEFAULT 0 CHECK (collected_count >= 0),
  duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
  normalized_count INTEGER NOT NULL DEFAULT 0 CHECK (normalized_count >= 0),
  matched_count INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
  filtered_count INTEGER NOT NULL DEFAULT 0 CHECK (filtered_count >= 0),
  shadow_added_count INTEGER NOT NULL DEFAULT 0 CHECK (shadow_added_count >= 0),
  active_added_count INTEGER NOT NULL DEFAULT 0 CHECK (active_added_count >= 0),
  upgraded_count INTEGER NOT NULL DEFAULT 0 CHECK (upgraded_count >= 0),
  downgraded_count INTEGER NOT NULL DEFAULT 0 CHECK (downgraded_count >= 0),
  invalidated_count INTEGER NOT NULL DEFAULT 0 CHECK (invalidated_count >= 0),
  error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
  zero_result_class TEXT NOT NULL DEFAULT 'none'
    CHECK (zero_result_class IN (
      'none',
      'initialization',
      'no_qualifying_candidates',
      'source_returned_no_data',
      'task_not_run',
      'rules_too_strict',
      'source_failure'
    )),
  zero_result_explanation TEXT NOT NULL DEFAULT '',
  triggered_by TEXT NOT NULL DEFAULT 'system',
  error_summary TEXT NOT NULL DEFAULT '',
  schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS run_source_stats (
  run_source_stat_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  source_id TEXT REFERENCES sources(source_id),
  collector_id TEXT NOT NULL,
  status TEXT NOT NULL
    CHECK (status IN ('success', 'partial_success', 'failed', 'skipped', 'no_data')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  collected_count INTEGER NOT NULL DEFAULT 0 CHECK (collected_count >= 0),
  duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
  matched_count INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
  filtered_count INTEGER NOT NULL DEFAULT 0 CHECK (filtered_count >= 0),
  shadow_added_count INTEGER NOT NULL DEFAULT 0 CHECK (shadow_added_count >= 0),
  active_added_count INTEGER NOT NULL DEFAULT 0 CHECK (active_added_count >= 0),
  failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
  filter_reason_summary_json TEXT NOT NULL DEFAULT '{}',
  error_message TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_errors (
  error_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  source_id TEXT REFERENCES sources(source_id),
  task_name TEXT NOT NULL,
  error_type TEXT NOT NULL,
  message TEXT NOT NULL,
  retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
  retry_status TEXT NOT NULL DEFAULT 'not_requested'
    CHECK (retry_status IN ('not_requested', 'pending', 'running', 'succeeded', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_events (
  raw_event_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  ingestion_run_id TEXT REFERENCES runs(run_id),
  external_id TEXT NOT NULL,
  published_at TEXT,
  collected_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  excerpt TEXT NOT NULL DEFAULT '',
  project_hint TEXT NOT NULL DEFAULT '',
  asset_hint TEXT NOT NULL DEFAULT '',
  chain_hint TEXT NOT NULL DEFAULT '',
  event_type TEXT NOT NULL DEFAULT '',
  raw_payload_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new', 'normalized', 'rejected', 'archived')),
  UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS filter_decisions (
  decision_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  raw_event_id TEXT REFERENCES raw_events(raw_event_id),
  source_id TEXT REFERENCES sources(source_id),
  candidate_key TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL
    CHECK (result IN ('accepted_shadow', 'accepted_active', 'rejected', 'deferred')),
  reason_code TEXT NOT NULL,
  reason_detail TEXT NOT NULL DEFAULT '',
  rule_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  project_id TEXT PRIMARY KEY,
  canonical_name TEXT NOT NULL,
  website_domain TEXT NOT NULL DEFAULT '',
  official_repo TEXT NOT NULL DEFAULT '',
  team_summary TEXT NOT NULL DEFAULT '',
  identity_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (identity_status IN ('pending', 'verified', 'conflict', 'rejected')),
  first_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_identity_aliases (
  alias_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  alias_type TEXT NOT NULL
    CHECK (alias_type IN (
      'project_id', 'name', 'domain', 'repository', 'coingecko_id',
      'source_external_id', 'source_qualified_id', 'source_slug',
      'contract', 'chain_contract'
    )),
  alias_value TEXT NOT NULL,
  normalized_value TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_record_id TEXT NOT NULL DEFAULT '',
  confidence TEXT NOT NULL DEFAULT 'medium'
    CHECK (confidence IN ('strong', 'medium', 'weak')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'historical', 'conflict')),
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  symbol TEXT NOT NULL,
  chain TEXT NOT NULL,
  contract_address TEXT NOT NULL DEFAULT '',
  asset_type TEXT NOT NULL DEFAULT 'token',
  capture_grade TEXT NOT NULL DEFAULT 'unknown'
    CHECK (capture_grade IN ('A', 'B', 'C', 'unknown')),
  identity_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (identity_status IN ('pending', 'verified', 'conflict', 'rejected')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS networks (
  network_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  chain_type TEXT NOT NULL
    CHECK (chain_type IN ('EVM', 'Solana', 'native', 'other')),
  chain_id TEXT NOT NULL,
  environment TEXT NOT NULL DEFAULT 'mainnet'
    CHECK (environment IN ('mainnet', 'testnet', 'devnet')),
  rpc_url TEXT NOT NULL DEFAULT '',
  explorer_url TEXT NOT NULL DEFAULT '',
  discovery_priority TEXT NOT NULL DEFAULT 'normal'
    CHECK (discovery_priority IN ('common', 'normal', 'paused')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'paused')),
  source_url TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (chain_id, environment)
);

CREATE TABLE IF NOT EXISTS asset_contracts (
  asset_contract_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  network_id TEXT NOT NULL REFERENCES networks(network_id),
  contract_address TEXT NOT NULL,
  contract_standard TEXT NOT NULL DEFAULT 'unknown',
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  identity_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (identity_status IN ('pending', 'market_matched', 'verified', 'conflict', 'rejected')),
  identity_source TEXT NOT NULL DEFAULT '',
  source_id TEXT REFERENCES sources(source_id),
  source_url TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL,
  verified_at TEXT,
  verification_method TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (asset_id, network_id, contract_address)
);

CREATE TABLE IF NOT EXISTS venues (
  venue_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  venue_name TEXT NOT NULL,
  venue_type TEXT NOT NULL CHECK (venue_type IN ('DEX', 'CEX')),
  pair_symbol TEXT NOT NULL DEFAULT '',
  pool_address TEXT NOT NULL DEFAULT '',
  buy_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (buy_status IN ('verified', 'blocked', 'unknown')),
  sell_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (sell_status IN ('verified', 'blocked', 'unknown')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'paused', 'closed', 'unknown')),
  checked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  venue_id TEXT REFERENCES venues(venue_id),
  observed_at TEXT NOT NULL,
  price_usd REAL,
  liquidity_usd REAL,
  volume_24h_usd REAL,
  market_cap_usd REAL,
  fdv_usd REAL,
  circulating_supply REAL,
  exit_notional_usd REAL,
  estimated_exit_slippage_pct REAL,
  data_source_id TEXT REFERENCES sources(source_id),
  definition_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contract_risks (
  contract_risk_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  assessed_at TEXT NOT NULL,
  mint_risk TEXT NOT NULL DEFAULT 'unknown',
  freeze_risk TEXT NOT NULL DEFAULT 'unknown',
  transfer_tax_risk TEXT NOT NULL DEFAULT 'unknown',
  pause_risk TEXT NOT NULL DEFAULT 'unknown',
  upgrade_risk TEXT NOT NULL DEFAULT 'unknown',
  owner_risk TEXT NOT NULL DEFAULT 'unknown',
  lp_control_risk TEXT NOT NULL DEFAULT 'unknown',
  concentration_risk TEXT NOT NULL DEFAULT 'unknown',
  overall_risk TEXT NOT NULL DEFAULT 'unknown'
    CHECK (overall_risk IN ('low', 'medium', 'high', 'blocked', 'unknown')),
  evidence_json TEXT NOT NULL DEFAULT '[]',
  rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tradeability_checks (
  check_id TEXT PRIMARY KEY,
  asset_contract_id TEXT NOT NULL REFERENCES asset_contracts(asset_contract_id),
  venue_id TEXT REFERENCES venues(venue_id),
  checked_at TEXT NOT NULL,
  contract_exists_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (contract_exists_status IN ('verified', 'missing', 'unknown')),
  source_code_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (source_code_status IN ('verified', 'unverified', 'not_applicable', 'unknown')),
  metadata_match_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (metadata_match_status IN ('match', 'mismatch', 'unknown')),
  pair_match_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (pair_match_status IN ('match', 'mismatch', 'unknown')),
  recent_buys_24h INTEGER,
  recent_sells_24h INTEGER,
  sell_path_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (sell_path_status IN ('read_only_verified', 'blocked', 'unknown')),
  exit_notional_usd REAL,
  estimated_exit_slippage_pct REAL,
  overall_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (overall_status IN ('pass', 'pending', 'fail')),
  verification_scope TEXT NOT NULL DEFAULT '',
  risk_flags_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  source_id TEXT REFERENCES sources(source_id),
  rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS network_discoveries (
  discovery_id TEXT PRIMARY KEY,
  network_id TEXT NOT NULL REFERENCES networks(network_id),
  contract_address TEXT NOT NULL,
  token_name TEXT NOT NULL DEFAULT '',
  symbol TEXT NOT NULL DEFAULT '',
  contract_standard TEXT NOT NULL DEFAULT 'unknown',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_run_id TEXT REFERENCES runs(run_id),
  discovery_kinds_json TEXT NOT NULL DEFAULT '[]',
  source_ids_json TEXT NOT NULL DEFAULT '[]',
  source_urls_json TEXT NOT NULL DEFAULT '[]',
  source_conflict_risk TEXT NOT NULL DEFAULT 'high'
    CHECK (source_conflict_risk IN ('low', 'medium', 'high')),
  holders_count INTEGER,
  price_usd REAL,
  liquidity_usd REAL,
  volume_24h_usd REAL,
  market_cap_usd REAL,
  recent_buys_24h INTEGER,
  recent_sells_24h INTEGER,
  exit_notional_usd REAL,
  estimated_exit_slippage_pct REAL,
  contract_exists_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (contract_exists_status IN ('verified', 'missing', 'unknown')),
  metadata_match_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (metadata_match_status IN ('match', 'mismatch', 'unknown')),
  pair_match_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (pair_match_status IN ('match', 'mismatch', 'unknown')),
  sell_path_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (sell_path_status IN ('read_only_verified', 'blocked', 'unknown')),
  contract_risk TEXT NOT NULL DEFAULT 'unknown'
    CHECK (contract_risk IN ('low', 'medium', 'high', 'blocked', 'unknown')),
  preflight_status TEXT NOT NULL DEFAULT 'not_checked'
    CHECK (preflight_status IN ('pass', 'pending', 'fail', 'not_checked')),
  discovery_score INTEGER NOT NULL DEFAULT 0
    CHECK (discovery_score BETWEEN 0 AND 100),
  queue_status TEXT NOT NULL DEFAULT 'identity_pending'
    CHECK (queue_status IN ('preflight_pass', 'identity_pending', 'existing_asset', 'rejected', 'promoted')),
  status_reason TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (network_id, contract_address)
);

CREATE TABLE IF NOT EXISTS source_discoveries (
  source_discovery_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  external_id TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL DEFAULT '',
  slug TEXT NOT NULL DEFAULT '',
  website_url TEXT NOT NULL DEFAULT '',
  website_domain TEXT NOT NULL DEFAULT '',
  repository_url TEXT NOT NULL DEFAULT '',
  social_url TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  raw_project_type TEXT NOT NULL DEFAULT '',
  cluster_key TEXT NOT NULL DEFAULT '',
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  last_run_id TEXT REFERENCES runs(run_id),
  matched_project_id TEXT REFERENCES projects(project_id),
  project_identity_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (project_identity_status IN ('pending', 'corroborated', 'verified', 'conflict', 'rejected')),
  asset_identity_status TEXT NOT NULL DEFAULT 'not_identified'
    CHECK (asset_identity_status IN ('not_identified', 'pending', 'verified', 'conflict')),
  value_capture_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (value_capture_status IN ('unknown', 'claimed', 'verified', 'not_applicable')),
  attribution_confidence TEXT NOT NULL DEFAULT 'low'
    CHECK (attribution_confidence IN ('high', 'medium', 'low')),
  attribution_reason TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'archived')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS discovery_identity_reviews (
  identity_review_id TEXT PRIMARY KEY,
  discovery_id TEXT NOT NULL REFERENCES network_discoveries(discovery_id),
  run_id TEXT REFERENCES runs(run_id),
  reviewed_at TEXT NOT NULL,
  provider TEXT NOT NULL,
  resolution_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (resolution_status IN ('verified', 'corroborated', 'pending', 'conflict', 'rejected')),
  confidence TEXT NOT NULL DEFAULT 'low'
    CHECK (confidence IN ('high', 'medium', 'low')),
  canonical_name TEXT NOT NULL DEFAULT '',
  coingecko_id TEXT NOT NULL DEFAULT '',
  website_url TEXT NOT NULL DEFAULT '',
  website_domain TEXT NOT NULL DEFAULT '',
  website_status TEXT NOT NULL DEFAULT 'missing'
    CHECK (website_status IN ('accessible', 'restricted', 'failed', 'missing')),
  official_contract_status TEXT NOT NULL DEFAULT 'not_found'
    CHECK (official_contract_status IN ('confirmed', 'registry_matched', 'not_found', 'conflict')),
  name_match_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (name_match_status IN ('match', 'partial', 'mismatch', 'unknown')),
  social_urls_json TEXT NOT NULL DEFAULT '[]',
  repo_urls_json TEXT NOT NULL DEFAULT '[]',
  value_capture_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (value_capture_status IN ('claimed', 'unknown', 'not_applicable')),
  promotion_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (promotion_status IN ('pending', 'shadow_promoted', 'existing_project', 'rejected')),
  matched_project_id TEXT REFERENCES projects(project_id),
  promoted_project_id TEXT REFERENCES projects(project_id),
  promoted_asset_id TEXT REFERENCES assets(asset_id),
  promoted_case_id TEXT REFERENCES candidate_cases(case_id),
  reason TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_asset_identity_reviews (
  project_asset_review_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  run_id TEXT REFERENCES runs(run_id),
  reviewed_at TEXT NOT NULL,
  provider TEXT NOT NULL,
  resolution_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (resolution_status IN ('verified', 'corroborated', 'pending', 'conflict')),
  confidence TEXT NOT NULL DEFAULT 'low'
    CHECK (confidence IN ('high', 'medium', 'low')),
  coingecko_id TEXT NOT NULL DEFAULT '',
  asset_name TEXT NOT NULL DEFAULT '',
  symbol TEXT NOT NULL DEFAULT '',
  match_method TEXT NOT NULL DEFAULT '',
  asset_id TEXT REFERENCES assets(asset_id),
  platforms_json TEXT NOT NULL DEFAULT '{}',
  official_links_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT NOT NULL DEFAULT '',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_items (
  evidence_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(project_id),
  asset_id TEXT REFERENCES assets(asset_id),
  raw_event_id TEXT REFERENCES raw_events(raw_event_id),
  evidence_type TEXT NOT NULL,
  stance TEXT NOT NULL CHECK (stance IN ('support', 'counter', 'neutral', 'unverified')),
  fact_boundary TEXT NOT NULL
    CHECK (fact_boundary IN ('confirmed_fact', 'high_confidence_inference', 'project_claim', 'unverified_signal')),
  confidence TEXT NOT NULL CHECK (confidence IN ('高', '中', '低', '待验证')),
  observed_at TEXT NOT NULL,
  expires_at TEXT,
  source_id TEXT REFERENCES sources(source_id),
  source_url TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_lineage (
  lineage_id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL
    CHECK (target_type IN (
      'raw_event',
      'evidence_item',
      'machine_research_score',
      'machine_conclusion',
      'state_transition',
      'tracking_decision_review'
  )),
  target_id TEXT NOT NULL,
  raw_event_id TEXT REFERENCES raw_events(raw_event_id),
  evidence_id TEXT,
  project_id TEXT,
  case_id TEXT,
  source_id TEXT,
  run_id TEXT,
  relation_type TEXT NOT NULL
    CHECK (relation_type IN (
      'raw_capture',
      'direct_normalization',
      'referenced_input',
      'legacy_missing_raw'
    )),
  lineage_status TEXT NOT NULL
    CHECK (lineage_status IN (
      'verified',
      'raw_only',
      'missing_raw',
      'missing_reference'
    )),
  parser_version TEXT NOT NULL,
  source_url TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  captured_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_adapter_records (
  adapter_record_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  run_id TEXT,
  source_record_type TEXT NOT NULL,
  source_record_id TEXT NOT NULL,
  raw_event_id TEXT,
  evidence_id TEXT,
  project_id TEXT,
  adapter_stage TEXT NOT NULL
    CHECK (adapter_stage IN (
      'raw_capture',
      'evidence_link',
      'recovery',
      'validation'
    )),
  adapter_status TEXT NOT NULL
    CHECK (adapter_status IN (
      'complete',
      'raw_only',
      'recovered',
      'missing_raw',
      'conflict'
    )),
  content_hash TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '',
  adapter_version TEXT NOT NULL,
  processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_cases (
  case_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(project_id),
  asset_id TEXT REFERENCES assets(asset_id),
  title TEXT NOT NULL,
  maturity_level TEXT NOT NULL DEFAULT 'L0'
    CHECK (maturity_level IN ('L0', 'L1', 'L2', 'L3', 'L4', 'L5')),
  workflow_state TEXT NOT NULL DEFAULT 'shadow_signal'
    CHECK (workflow_state IN (
      'shadow_signal',
      'identity_pending',
      'tradeability_pending',
      'active_embryo',
      'priority_watch',
      'extreme_test',
      'trial_ready',
      'igniting',
      'odds_decay',
      'invalidated',
      'transferred_l5',
      'archived'
    )),
  risk_level TEXT NOT NULL DEFAULT 'unknown'
    CHECK (risk_level IN ('low', 'medium', 'high', 'blocked', 'unknown')),
  remaining_convexity TEXT NOT NULL DEFAULT 'unknown'
    CHECK (remaining_convexity IN ('high', 'medium', 'low', 'none', 'unknown')),
  ignition_proximity TEXT NOT NULL DEFAULT 'unknown'
    CHECK (ignition_proximity IN ('immediate', 'near', 'forming', 'distant', 'unknown')),
  tradeability_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK (tradeability_status IN ('verified', 'limited', 'blocked', 'unknown')),
  liquidity_grade TEXT NOT NULL DEFAULT 'unknown'
    CHECK (liquidity_grade IN ('standard', 'extreme', 'untradeable', 'unknown')),
  convexity_source TEXT NOT NULL DEFAULT '',
  action_stage TEXT NOT NULL DEFAULT '只观察'
    CHECK (action_stage IN ('只观察', '极限试仓', '普通建仓', '反身性管理', '已失去凸性')),
  value_capture_grade TEXT NOT NULL DEFAULT 'unknown'
    CHECK (value_capture_grade IN ('A', 'B', 'C', 'unknown')),
  current_thesis TEXT NOT NULL DEFAULT '',
  invalidation TEXT NOT NULL DEFAULT '',
  next_review_at TEXT,
  rule_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_monitoring_targets (
  monitoring_target_id TEXT PRIMARY KEY,
  target_identity_key TEXT NOT NULL,
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  case_id TEXT REFERENCES candidate_cases(case_id),
  target_type TEXT NOT NULL
    CHECK (target_type IN (
      'official_website',
      'official_social',
      'github_organization',
      'github_repository',
      'defillama_protocol',
      'snapshot_space',
      'cactus_governance',
      'asset',
      'contract'
    )),
  target_value TEXT NOT NULL,
  target_url TEXT NOT NULL DEFAULT '',
  source_id TEXT NOT NULL DEFAULT '',
  source_record_type TEXT NOT NULL DEFAULT '',
  source_record_id TEXT NOT NULL DEFAULT '',
  raw_event_id TEXT,
  evidence_id TEXT,
  relation_status TEXT NOT NULL
    CHECK (relation_status IN (
      'verified',
      'corroborated',
      'blocked',
      'conflict'
    )),
  collection_status TEXT NOT NULL
    CHECK (collection_status IN (
      'ready',
      'registered',
      'blocked',
      'conflict'
    )),
  verification_method TEXT NOT NULL DEFAULT '',
  gap_reason TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  observed_at TEXT,
  generated_at TEXT NOT NULL,
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded')),
  rule_version TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_project_monitoring_targets_current
  ON project_monitoring_targets(target_identity_key)
  WHERE publication_status = 'published';

CREATE INDEX IF NOT EXISTS idx_project_monitoring_targets_project
  ON project_monitoring_targets(project_id, publication_status, target_type);

CREATE TABLE IF NOT EXISTS weak_signal_inbox (
  weak_signal_id TEXT PRIMARY KEY,
  signal_identity_key TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  source_record_type TEXT NOT NULL
    CHECK (source_record_type IN ('source_discovery', 'network_discovery')),
  source_record_id TEXT NOT NULL,
  project_id TEXT REFERENCES projects(project_id),
  case_id TEXT REFERENCES candidate_cases(case_id),
  raw_event_id TEXT REFERENCES raw_events(raw_event_id),
  signal_type TEXT NOT NULL
    CHECK (signal_type IN (
      'protocol_listing',
      'code_activity',
      'governance_activity',
      'token_profile',
      'paid_boost',
      'contract_deployment'
    )),
  source_tier TEXT NOT NULL
    CHECK (source_tier IN (
      'public_code',
      'independent_registry',
      'community_governance',
      'chain_trace',
      'promotional',
      'paid_promotion'
    )),
  promotion_bias TEXT NOT NULL
    CHECK (promotion_bias IN ('low', 'medium', 'high')),
  project_relation_status TEXT NOT NULL
    CHECK (project_relation_status IN (
      'verified',
      'corroborated',
      'pending',
      'conflict',
      'unattributed'
    )),
  triage_status TEXT NOT NULL
    CHECK (triage_status IN (
      'ready_for_corroboration',
      'discovery_only',
      'identity_blocked',
      'conflict'
    )),
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  upgrade_requirement TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  generated_at TEXT NOT NULL,
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded')),
  rule_version TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_weak_signal_inbox_current
  ON weak_signal_inbox(signal_identity_key)
  WHERE publication_status = 'published';

CREATE INDEX IF NOT EXISTS idx_weak_signal_inbox_project
  ON weak_signal_inbox(project_id, publication_status, observed_at);

CREATE INDEX IF NOT EXISTS idx_weak_signal_inbox_triage
  ON weak_signal_inbox(triage_status, source_id, observed_at);

CREATE TABLE IF NOT EXISTS mismatch_scores (
  mismatch_score_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  scored_at TEXT NOT NULL,
  fact_certainty INTEGER NOT NULL CHECK (fact_certainty BETWEEN 0 AND 20),
  economic_increment INTEGER NOT NULL CHECK (economic_increment BETWEEN 0 AND 20),
  value_capture INTEGER NOT NULL CHECK (value_capture BETWEEN 0 AND 25),
  event_proximity INTEGER NOT NULL CHECK (event_proximity BETWEEN 0 AND 20),
  price_unreacted INTEGER NOT NULL CHECK (price_unreacted BETWEEN 0 AND 15),
  risk_deduction INTEGER NOT NULL CHECK (risk_deduction BETWEEN 0 AND 30),
  total_score INTEGER NOT NULL CHECK (total_score BETWEEN 0 AND 100),
  deduction_detail_json TEXT NOT NULL DEFAULT '[]',
  rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS convexity_reviews (
  review_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  reviewed_at TEXT NOT NULL,
  primary_convexity_source TEXT NOT NULL,
  maximum_controllable_loss TEXT NOT NULL,
  nonlinear_upside_path TEXT NOT NULL,
  ignition_conditions TEXT NOT NULL,
  odds_decay_conditions TEXT NOT NULL,
  remaining_convexity TEXT NOT NULL,
  invalidation_window TEXT NOT NULL,
  supporting_evidence_json TEXT NOT NULL DEFAULT '[]',
  counter_evidence_json TEXT NOT NULL DEFAULT '[]',
  open_questions_json TEXT NOT NULL DEFAULT '[]',
  reviewer_type TEXT NOT NULL CHECK (reviewer_type IN ('rule_engine', 'model', 'human')),
  conclusion_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS machine_research_scores (
  machine_score_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  run_id TEXT REFERENCES runs(run_id),
  mismatch_score_id TEXT NOT NULL REFERENCES mismatch_scores(mismatch_score_id),
  convexity_review_id TEXT NOT NULL REFERENCES convexity_reviews(review_id),
  scored_at TEXT NOT NULL,
  lifecycle_bucket TEXT NOT NULL
    CHECK (lifecycle_bucket IN ('early', 'og', 'other')),
  lifecycle_label TEXT NOT NULL,
  evidence_quality_score INTEGER NOT NULL
    CHECK (evidence_quality_score BETWEEN 0 AND 100),
  mismatch_score INTEGER NOT NULL
    CHECK (mismatch_score BETWEEN 0 AND 100),
  convexity_readiness_score INTEGER NOT NULL
    CHECK (convexity_readiness_score BETWEEN 0 AND 100),
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high', 'medium', 'low', 'insufficient')),
  dimension_scores_json TEXT NOT NULL DEFAULT '{}',
  blockers_json TEXT NOT NULL DEFAULT '[]',
  source_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  source_url TEXT NOT NULL DEFAULT '',
  scoring_boundary TEXT NOT NULL,
  rule_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_machine_research_scores_case_time
  ON machine_research_scores(case_id, scored_at DESC);

CREATE TABLE IF NOT EXISTS machine_conclusions (
  machine_conclusion_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  run_id TEXT REFERENCES runs(run_id),
  machine_score_id TEXT NOT NULL REFERENCES machine_research_scores(machine_score_id),
  generated_at TEXT NOT NULL,
  conclusion_state TEXT NOT NULL
    CHECK (conclusion_state IN (
      'identity_pending',
      'asset_pending',
      'market_exit_pending',
      'evidence_building',
      'convexity_structure_pending',
      'priority_watch',
      'actionable',
      'reflexive',
      'invalidated'
    )),
  conclusion_state_label TEXT NOT NULL,
  opportunity_stage TEXT NOT NULL
    CHECK (opportunity_stage IN ('actionable', 'observe', 'reflexive', 'invalidated')),
  action_category TEXT NOT NULL
    CHECK (action_category IN ('ordinary', 'extreme', 'observe', 'reflexive', 'invalidated')),
  action_label TEXT NOT NULL,
  headline TEXT NOT NULL,
  why_not_actionable TEXT NOT NULL DEFAULT '',
  next_step TEXT NOT NULL,
  next_task_id TEXT NOT NULL DEFAULT '',
  upgrade_conditions_json TEXT NOT NULL DEFAULT '[]',
  invalidation_conditions_json TEXT NOT NULL DEFAULT '[]',
  source_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  source_url TEXT NOT NULL DEFAULT '',
  confidence TEXT NOT NULL
    CHECK (confidence IN ('high', 'medium', 'low', 'insufficient')),
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded')),
  rule_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_machine_conclusions_case_time
  ON machine_conclusions(case_id, generated_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_trade_paths (
  catalyst_trade_path_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  project_id TEXT NOT NULL REFERENCES projects(project_id),
  asset_id TEXT REFERENCES assets(asset_id),
  run_id TEXT REFERENCES runs(run_id),
  generated_at TEXT NOT NULL,
  path_stage TEXT NOT NULL
    CHECK (path_stage IN (
      'catalyst_pending',
      'asset_pending',
      'transmission_pending',
      'market_pending',
      'exit_pending',
      'research_ready',
      'action_ready',
      'invalidated'
    )),
  path_stage_label TEXT NOT NULL,
  catalyst_type TEXT NOT NULL
    CHECK (catalyst_type IN (
      'governance',
      'code_release',
      'security_change',
      'product_release',
      'regulatory',
      'unknown'
    )),
  catalyst_status TEXT NOT NULL
    CHECK (catalyst_status IN ('active', 'stale', 'expired', 'missing')),
  catalyst_evidence_id TEXT,
  catalyst_summary TEXT NOT NULL DEFAULT '',
  catalyst_source_url TEXT NOT NULL DEFAULT '',
  catalyst_observed_at TEXT,
  confirmation_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  transmission_steps_json TEXT NOT NULL DEFAULT '[]',
  transmission_status TEXT NOT NULL
    CHECK (transmission_status IN ('verified', 'partial', 'missing')),
  expression_asset_text TEXT NOT NULL DEFAULT '',
  contract_address TEXT NOT NULL DEFAULT '',
  network_name TEXT NOT NULL DEFAULT '',
  venue_text TEXT NOT NULL DEFAULT '',
  sell_path_status TEXT NOT NULL DEFAULT 'unknown',
  observed_exit_notional_usd REAL,
  observed_exit_slippage_pct REAL,
  modeled_exit_notional_usd REAL NOT NULL DEFAULT 20000,
  modeled_exit_slippage_pct REAL,
  modeled_exit_method TEXT NOT NULL DEFAULT '',
  execution_status TEXT NOT NULL
    CHECK (execution_status IN ('verified', 'limited', 'blocked', 'unknown')),
  invalidation_conditions_json TEXT NOT NULL DEFAULT '[]',
  blockers_json TEXT NOT NULL DEFAULT '[]',
  next_task_id TEXT NOT NULL DEFAULT '',
  next_step TEXT NOT NULL DEFAULT '',
  source_evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  source_url TEXT NOT NULL DEFAULT '',
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded')),
  rule_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalyst_trade_paths_case_time
  ON catalyst_trade_paths(case_id, generated_at DESC);

CREATE TABLE IF NOT EXISTS state_transitions (
  transition_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  rule_version TEXT NOT NULL,
  actor TEXT NOT NULL,
  transitioned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS opportunity_stage_history (
  history_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  run_id TEXT REFERENCES runs(run_id),
  observed_at TEXT NOT NULL,
  change_direction TEXT NOT NULL
    CHECK (change_direction IN ('baseline', 'upgrade', 'downgrade', 'changed')),
  from_stage TEXT NOT NULL DEFAULT '',
  to_stage TEXT NOT NULL,
  from_stage_order INTEGER,
  to_stage_order INTEGER NOT NULL,
  explanation TEXT NOT NULL,
  changed_fields_json TEXT NOT NULL DEFAULT '[]',
  trigger_categories_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  state_json TEXT NOT NULL DEFAULT '{}',
  rule_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracking_task_runs (
  tracking_result_id TEXT PRIMARY KEY,
  tracking_task_id TEXT NOT NULL,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  project_id TEXT REFERENCES projects(project_id),
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  project_category TEXT NOT NULL,
  task_type TEXT NOT NULL,
  priority TEXT NOT NULL CHECK (priority IN ('P0', 'P1', 'P2', 'P3')),
  execution_status TEXT NOT NULL
    CHECK (execution_status IN ('success', 'partial_success', 'no_change', 'failed')),
  decision TEXT NOT NULL
    CHECK (decision IN ('upgrade', 'continue', 'stop', 'monitor', 'undetermined')),
  conclusion_before TEXT NOT NULL DEFAULT '',
  conclusion_after TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL,
  sources_checked_json TEXT NOT NULL DEFAULT '[]',
  source_results_json TEXT NOT NULL DEFAULT '[]',
  findings_json TEXT NOT NULL DEFAULT '[]',
  findings_count INTEGER NOT NULL DEFAULT 0 CHECK (findings_count >= 0),
  new_findings_count INTEGER NOT NULL DEFAULT 0 CHECK (new_findings_count >= 0),
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  next_review_at TEXT,
  retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
  retry_status TEXT NOT NULL DEFAULT 'not_requested'
    CHECK (retry_status IN ('not_requested', 'pending', 'running', 'succeeded', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
  error_message TEXT NOT NULL DEFAULT '',
  task_version TEXT NOT NULL,
  UNIQUE (tracking_task_id, run_id)
);

CREATE TABLE IF NOT EXISTS tracking_decision_reviews (
  tracking_review_id TEXT PRIMARY KEY,
  tracking_result_id TEXT NOT NULL REFERENCES tracking_task_runs(tracking_result_id),
  tracking_task_id TEXT NOT NULL,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  decision TEXT NOT NULL CHECK (decision IN ('upgrade', 'stop')),
  review_action TEXT NOT NULL CHECK (review_action IN ('confirmed', 'rejected')),
  review_note TEXT NOT NULL DEFAULT '',
  evidence_ids_json TEXT NOT NULL DEFAULT '[]',
  actor TEXT NOT NULL DEFAULT 'user',
  reviewed_at TEXT NOT NULL,
  rule_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
  alert_id TEXT PRIMARY KEY,
  case_id TEXT REFERENCES candidate_cases(case_id),
  run_id TEXT REFERENCES runs(run_id),
  severity TEXT NOT NULL CHECK (severity IN ('P0', 'P1', 'P2')),
  rule_id TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'acknowledged', 'resolved', 'expired')),
  dedupe_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS decision_reports (
  report_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  generated_at TEXT NOT NULL,
  action TEXT NOT NULL
    CHECK (action IN ('只观察', '极限试仓', '普通建仓', '减仓', '退出', '反身性管理')),
  position_stage TEXT NOT NULL DEFAULT '',
  conditions TEXT NOT NULL DEFAULT '',
  invalidation TEXT NOT NULL,
  review_at TEXT,
  confidence TEXT NOT NULL CHECK (confidence IN ('高', '中', '低', '待验证')),
  conclusion_version TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'internal'
    CHECK (visibility IN ('internal', 'shareable'))
);

CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES candidate_cases(case_id),
  observed_from TEXT NOT NULL,
  observed_to TEXT,
  price_path_json TEXT NOT NULL DEFAULT '[]',
  facts_realized_json TEXT NOT NULL DEFAULT '[]',
  max_drawdown_pct REAL,
  max_gain_pct REAL,
  exit_reason TEXT NOT NULL DEFAULT '',
  outcome_status TEXT NOT NULL DEFAULT 'open'
    CHECK (outcome_status IN ('open', 'won', 'lost', 'mixed', 'invalidated', 'transferred')),
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_metrics (
  source_metric_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  first_hit_count INTEGER NOT NULL DEFAULT 0,
  converted_candidate_count INTEGER NOT NULL DEFAULT 0,
  confirmed_count INTEGER NOT NULL DEFAULT 0,
  false_positive_count INTEGER NOT NULL DEFAULT 0,
  median_lead_minutes REAL,
  precision_rate REAL,
  conversion_rate REAL,
  created_at TEXT NOT NULL,
  UNIQUE (source_id, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS scan_results (
  scan_result_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  network_id TEXT NOT NULL REFERENCES networks(network_id),
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  discovery_id TEXT REFERENCES network_discoveries(discovery_id),
  external_key TEXT NOT NULL DEFAULT '',
  result_status TEXT NOT NULL
    CHECK (result_status IN ('eligible', 'pending', 'existing', 'rejected', 'error')),
  reason TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  raw_payload_json TEXT NOT NULL DEFAULT '{}',
  observed_at TEXT NOT NULL,
  UNIQUE (run_id, network_id, source_id, external_key)
);

CREATE TABLE IF NOT EXISTS scan_run_scopes (
  scan_scope_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
  requested_network_ids_json TEXT NOT NULL DEFAULT '[]',
  requested_source_ids_json TEXT NOT NULL DEFAULT '[]',
  triggered_by TEXT NOT NULL DEFAULT 'user',
  no_limit INTEGER NOT NULL DEFAULT 1 CHECK (no_limit IN (0, 1)),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_annotations (
  annotation_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(project_id),
  discovery_id TEXT REFERENCES network_discoveries(discovery_id),
  case_id TEXT REFERENCES candidate_cases(case_id),
  field_name TEXT NOT NULL,
  annotation_value_json TEXT NOT NULL DEFAULT '{}',
  note TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'superseded', 'withdrawn')),
  actor TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publication_records (
  publication_id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(project_id),
  case_id TEXT REFERENCES candidate_cases(case_id),
  publication_status TEXT NOT NULL DEFAULT 'draft'
    CHECK (publication_status IN ('draft', 'preview', 'published', 'withdrawn')),
  visibility TEXT NOT NULL DEFAULT 'internal'
    CHECK (visibility IN ('internal', 'public')),
  title TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  withdrawn_at TEXT,
  source_snapshot_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS normalized_events_v2 (
  event_id TEXT PRIMARY KEY,
  raw_event_id TEXT NOT NULL UNIQUE REFERENCES raw_events(raw_event_id),
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  source_record_type TEXT NOT NULL DEFAULT 'raw_event',
  source_record_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  entity_type TEXT NOT NULL DEFAULT 'unknown'
    CHECK (entity_type IN (
      'project', 'asset', 'contract', 'repository', 'governance',
      'network', 'protocol', 'package', 'release', 'unknown'
    )),
  entity_id TEXT NOT NULL DEFAULT '',
  project_id TEXT REFERENCES projects(project_id),
  event_type TEXT NOT NULL DEFAULT '',
  mainline_type TEXT NOT NULL DEFAULT 'general'
    CHECK (mainline_type IN (
      'general', 'git', 'release', 'package', 'evm', 'solana'
    )),
  event_time TEXT NOT NULL,
  collected_at TEXT NOT NULL,
  raw_locator TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  evidence_grade TEXT NOT NULL DEFAULT 'raw'
    CHECK (evidence_grade IN ('raw', 'weak', 'corroborated', 'confirmed')),
  attribution_status TEXT NOT NULL DEFAULT 'unattributed'
    CHECK (attribution_status IN (
      'verified', 'corroborated', 'pending', 'conflict', 'unattributed'
    )),
  processing_status TEXT NOT NULL DEFAULT 'new'
    CHECK (processing_status IN (
      'new', 'normalized', 'attributed', 'evidence_ready', 'ignored', 'error'
    )),
  payload_json TEXT NOT NULL DEFAULT '{}',
  schema_version INTEGER NOT NULL DEFAULT 2 CHECK (schema_version = 2),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_cursors_v2 (
  source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
  cursor_kind TEXT NOT NULL DEFAULT 'event_high_water'
    CHECK (cursor_kind IN ('event_high_water', 'provider_cursor', 'hybrid')),
  cursor_value TEXT NOT NULL DEFAULT '',
  replay_from_cursor TEXT NOT NULL DEFAULT '',
  high_water_event_time TEXT,
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_event_at TEXT,
  last_result_count INTEGER NOT NULL DEFAULT 0 CHECK (last_result_count >= 0),
  backlog_count INTEGER NOT NULL DEFAULT 0 CHECK (backlog_count >= 0),
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  gap_status TEXT NOT NULL DEFAULT 'none'
    CHECK (gap_status IN ('none', 'open', 'replaying', 'resolved')),
  gap_from TEXT,
  gap_to TEXT,
  gap_reason TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event_replay_runs (
  replay_run_id TEXT PRIMARY KEY,
  source_id TEXT REFERENCES sources(source_id),
  mode TEXT NOT NULL CHECK (mode IN ('incremental', 'replay', 'gap_recovery')),
  cursor_from TEXT NOT NULL DEFAULT '',
  cursor_to TEXT NOT NULL DEFAULT '',
  range_from TEXT,
  range_to TEXT,
  input_count INTEGER NOT NULL DEFAULT 0 CHECK (input_count >= 0),
  inserted_count INTEGER NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
  updated_count INTEGER NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
  duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
  orphan_count INTEGER NOT NULL DEFAULT 0 CHECK (orphan_count >= 0),
  gap_detected_count INTEGER NOT NULL DEFAULT 0 CHECK (gap_detected_count >= 0),
  gap_recovered_count INTEGER NOT NULL DEFAULT 0 CHECK (gap_recovered_count >= 0),
  status TEXT NOT NULL CHECK (status IN ('running', 'success', 'partial_success', 'failed')),
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS source_health_v2 (
  source_id TEXT PRIMARY KEY REFERENCES sources(source_id),
  health_state TEXT NOT NULL DEFAULT 'unknown'
    CHECK (health_state IN (
      'healthy', 'true_zero', 'silent', 'failed', 'quota_exhausted',
      'rule_gap', 'stale', 'unknown'
    )),
  last_run_id TEXT REFERENCES runs(run_id),
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_event_at TEXT,
  last_status TEXT NOT NULL DEFAULT '',
  last_result_count INTEGER NOT NULL DEFAULT 0 CHECK (last_result_count >= 0),
  quota_remaining TEXT NOT NULL DEFAULT '',
  silence_reason TEXT NOT NULL DEFAULT '',
  diagnosis TEXT NOT NULL DEFAULT '',
  checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health_history (
  health_record_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES sources(source_id),
  replay_run_id TEXT REFERENCES event_replay_runs(replay_run_id),
  health_state TEXT NOT NULL
    CHECK (health_state IN (
      'healthy', 'true_zero', 'silent', 'failed', 'quota_exhausted',
      'rule_gap', 'stale', 'unknown'
    )),
  last_run_id TEXT REFERENCES runs(run_id),
  result_count INTEGER NOT NULL DEFAULT 0 CHECK (result_count >= 0),
  diagnosis TEXT NOT NULL DEFAULT '',
  observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orphan_events_v2 (
  orphan_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE REFERENCES normalized_events_v2(event_id),
  attribution_status TEXT NOT NULL
    CHECK (attribution_status IN ('pending', 'conflict', 'resolved', 'ignored')),
  project_hint TEXT NOT NULL DEFAULT '',
  asset_hint TEXT NOT NULL DEFAULT '',
  chain_hint TEXT NOT NULL DEFAULT '',
  candidate_entity_type TEXT NOT NULL DEFAULT 'unknown',
  candidate_entity_id TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  resolved_project_id TEXT REFERENCES projects(project_id),
  resolved_entity_id TEXT NOT NULL DEFAULT '',
  resolution_method TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS event_attribution_history (
  attribution_id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES normalized_events_v2(event_id),
  from_project_id TEXT REFERENCES projects(project_id),
  to_project_id TEXT REFERENCES projects(project_id),
  from_entity_id TEXT NOT NULL DEFAULT '',
  to_entity_id TEXT NOT NULL DEFAULT '',
  from_status TEXT NOT NULL DEFAULT 'unattributed',
  to_status TEXT NOT NULL,
  attribution_method TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  attributed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_nodes (
  node_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL
    CHECK (entity_type IN (
      'project', 'asset', 'contract', 'repository', 'governance',
      'network', 'protocol', 'package', 'release'
    )),
  canonical_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  project_id TEXT REFERENCES projects(project_id),
  identity_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (identity_status IN ('verified', 'corroborated', 'pending', 'conflict', 'blocked')),
  source_record_type TEXT NOT NULL DEFAULT '',
  source_record_id TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  generated_at TEXT NOT NULL,
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded'))
);

CREATE TABLE IF NOT EXISTS entity_edges (
  edge_id TEXT PRIMARY KEY,
  from_node_id TEXT NOT NULL REFERENCES entity_nodes(node_id),
  to_node_id TEXT NOT NULL REFERENCES entity_nodes(node_id),
  relation_type TEXT NOT NULL
    CHECK (relation_type IN (
      'project_has_asset', 'asset_deployed_on', 'project_owns_repository',
      'project_uses_governance', 'project_monitors', 'project_publishes_package',
      'repository_publishes_release'
    )),
  relation_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (relation_status IN ('verified', 'corroborated', 'pending', 'conflict', 'blocked')),
  source_id TEXT NOT NULL DEFAULT '',
  source_record_id TEXT NOT NULL DEFAULT '',
  raw_event_id TEXT REFERENCES raw_events(raw_event_id),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  generated_at TEXT NOT NULL,
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded'))
);

CREATE TABLE IF NOT EXISTS watcher_definitions (
  watcher_id TEXT PRIMARY KEY,
  watcher_identity_key TEXT NOT NULL,
  project_id TEXT REFERENCES projects(project_id),
  entity_node_id TEXT REFERENCES entity_nodes(node_id),
  watcher_type TEXT NOT NULL
    CHECK (watcher_type IN (
      'git_activity', 'software_release', 'package_registry',
      'evm_contract', 'solana_program', 'governance', 'protocol', 'website'
    )),
  target_value TEXT NOT NULL,
  target_url TEXT NOT NULL DEFAULT '',
  network_id TEXT REFERENCES networks(network_id),
  source_id TEXT NOT NULL DEFAULT '',
  cursor_source_id TEXT REFERENCES source_cursors_v2(source_id),
  collection_mode TEXT NOT NULL
    CHECK (collection_mode IN ('api', 'poll', 'chain_rpc', 'registry')),
  watcher_status TEXT NOT NULL
    CHECK (watcher_status IN ('ready', 'registered', 'blocked', 'conflict')),
  gap_reason TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  generated_at TEXT NOT NULL,
  publication_status TEXT NOT NULL DEFAULT 'published'
    CHECK (publication_status IN ('published', 'superseded')),
  rule_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_events_collected_at ON raw_events(collected_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_chain_contract
  ON assets(chain, contract_address)
  WHERE contract_address <> '';
CREATE INDEX IF NOT EXISTS idx_asset_contracts_asset
  ON asset_contracts(asset_id, is_primary, identity_status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_contracts_network_address
  ON asset_contracts(network_id, contract_address);
CREATE INDEX IF NOT EXISTS idx_tradeability_checks_contract
  ON tradeability_checks(asset_contract_id, checked_at);
CREATE INDEX IF NOT EXISTS idx_network_discoveries_queue
  ON network_discoveries(queue_status, discovery_score, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_source_discoveries_cluster
  ON source_discoveries(cluster_key, project_identity_status, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_source_discoveries_source
  ON source_discoveries(source_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_source_discoveries_project
  ON source_discoveries(matched_project_id, project_identity_status);
CREATE INDEX IF NOT EXISTS idx_project_identity_aliases_lookup
  ON project_identity_aliases(normalized_value, status, project_id);
CREATE INDEX IF NOT EXISTS idx_project_identity_aliases_project
  ON project_identity_aliases(project_id, alias_type, status);
CREATE INDEX IF NOT EXISTS idx_discovery_identity_reviews_latest
  ON discovery_identity_reviews(discovery_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_filter_decisions_run_id ON filter_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_candidate_cases_state ON candidate_cases(workflow_state, maturity_level);
CREATE INDEX IF NOT EXISTS idx_candidate_cases_review ON candidate_cases(next_review_at);
CREATE INDEX IF NOT EXISTS idx_evidence_lineage_raw
  ON evidence_lineage(raw_event_id, lineage_status);
CREATE INDEX IF NOT EXISTS idx_evidence_lineage_evidence
  ON evidence_lineage(evidence_id, target_type);
CREATE INDEX IF NOT EXISTS idx_evidence_lineage_target
  ON evidence_lineage(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_evidence_lineage_project
  ON evidence_lineage(project_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_source_adapter_source_status
  ON source_adapter_records(source_id, adapter_status, processed_at);
CREATE INDEX IF NOT EXISTS idx_source_adapter_evidence
  ON source_adapter_records(evidence_id, processed_at);
CREATE INDEX IF NOT EXISTS idx_source_adapter_run
  ON source_adapter_records(run_id, processed_at);
CREATE INDEX IF NOT EXISTS idx_state_transitions_case ON state_transitions(case_id, transitioned_at);
CREATE INDEX IF NOT EXISTS idx_opportunity_stage_history_case
  ON opportunity_stage_history(case_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_opportunity_stage_history_direction
  ON opportunity_stage_history(change_direction, observed_at);
CREATE INDEX IF NOT EXISTS idx_tracking_task_runs_task
  ON tracking_task_runs(tracking_task_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_tracking_task_runs_case
  ON tracking_task_runs(case_id, finished_at);
CREATE INDEX IF NOT EXISTS idx_tracking_task_runs_retry
  ON tracking_task_runs(retryable, retry_status, finished_at);
CREATE INDEX IF NOT EXISTS idx_tracking_decision_reviews_result
  ON tracking_decision_reviews(tracking_result_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_tracking_decision_reviews_case
  ON tracking_decision_reviews(case_id, reviewed_at);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_run_source_stats_run ON run_source_stats(run_id);
CREATE INDEX IF NOT EXISTS idx_run_errors_run ON run_errors(run_id, retry_status);
CREATE INDEX IF NOT EXISTS idx_scan_results_run_source
  ON scan_results(run_id, network_id, source_id, result_status);
CREATE INDEX IF NOT EXISTS idx_scan_run_scopes_created
  ON scan_run_scopes(created_at, run_id);
CREATE INDEX IF NOT EXISTS idx_manual_annotations_target
  ON manual_annotations(project_id, discovery_id, case_id, status);
CREATE INDEX IF NOT EXISTS idx_publication_records_status
  ON publication_records(publication_status, visibility, updated_at);
CREATE INDEX IF NOT EXISTS idx_normalized_events_v2_source_time
  ON normalized_events_v2(source_id, event_time);
CREATE INDEX IF NOT EXISTS idx_normalized_events_v2_project
  ON normalized_events_v2(project_id, attribution_status, event_time);
CREATE INDEX IF NOT EXISTS idx_normalized_events_v2_mainline
  ON normalized_events_v2(mainline_type, processing_status, event_time);
CREATE INDEX IF NOT EXISTS idx_event_replay_runs_source
  ON event_replay_runs(source_id, started_at);
CREATE INDEX IF NOT EXISTS idx_source_health_history_source
  ON source_health_history(source_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_orphan_events_v2_status
  ON orphan_events_v2(attribution_status, updated_at);
CREATE INDEX IF NOT EXISTS idx_event_attribution_history_event
  ON event_attribution_history(event_id, attributed_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_nodes_current
  ON entity_nodes(entity_type, canonical_key)
  WHERE publication_status = 'published';
CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_edges_current
  ON entity_edges(from_node_id, to_node_id, relation_type)
  WHERE publication_status = 'published';
CREATE UNIQUE INDEX IF NOT EXISTS idx_watcher_definitions_current
  ON watcher_definitions(watcher_identity_key)
  WHERE publication_status = 'published';
CREATE INDEX IF NOT EXISTS idx_watcher_definitions_project
  ON watcher_definitions(project_id, watcher_type, watcher_status);

CREATE TRIGGER IF NOT EXISTS prevent_raw_events_update
BEFORE UPDATE ON raw_events
BEGIN
  SELECT RAISE(ABORT, 'raw_events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_raw_events_delete
BEFORE DELETE ON raw_events
BEGIN
  SELECT RAISE(ABORT, 'raw_events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_evidence_lineage_update
BEFORE UPDATE ON evidence_lineage
BEGIN
  SELECT RAISE(ABORT, 'evidence_lineage is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_evidence_lineage_delete
BEFORE DELETE ON evidence_lineage
BEGIN
  SELECT RAISE(ABORT, 'evidence_lineage is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_source_adapter_records_update
BEFORE UPDATE ON source_adapter_records
BEGIN
  SELECT RAISE(ABORT, 'source_adapter_records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_source_adapter_records_delete
BEFORE DELETE ON source_adapter_records
BEGIN
  SELECT RAISE(ABORT, 'source_adapter_records are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_event_replay_runs_update
BEFORE UPDATE ON event_replay_runs
BEGIN
  SELECT RAISE(ABORT, 'event_replay_runs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_event_replay_runs_delete
BEFORE DELETE ON event_replay_runs
BEGIN
  SELECT RAISE(ABORT, 'event_replay_runs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_source_health_history_update
BEFORE UPDATE ON source_health_history
BEGIN
  SELECT RAISE(ABORT, 'source_health_history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_source_health_history_delete
BEFORE DELETE ON source_health_history
BEGIN
  SELECT RAISE(ABORT, 'source_health_history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_event_attribution_history_update
BEFORE UPDATE ON event_attribution_history
BEGIN
  SELECT RAISE(ABORT, 'event_attribution_history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_event_attribution_history_delete
BEFORE DELETE ON event_attribution_history
BEGIN
  SELECT RAISE(ABORT, 'event_attribution_history is immutable');
END;

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (1, 'convexity_foundation_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (10, 'tracking_decision_review_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (11, 'project_identity_alias_ledger_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (12, 'immutable_evidence_lineage_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (13, 'source_adapter_mainline_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (14, 'catalyst_trade_path_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (15, 'project_monitoring_infrastructure_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (16, 'weak_signal_inbox_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (17, 'maximum_funnel_data_backbone_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (2, 'multi_chain_contract_tradeability_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (3, 'common_network_discovery_queue_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (4, 'discovery_identity_promotion_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (5, 'convexity_master_pool_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (6, 'manual_scan_scope_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (7, 'project_source_discovery_attribution_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (8, 'opportunity_stage_history_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO schema_migrations (version, name, applied_at)
VALUES (9, 'tracking_task_execution_v1', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

INSERT OR IGNORE INTO networks (
  network_id, name, chain_type, chain_id, environment, rpc_url, explorer_url,
  discovery_priority, status, source_url, created_at, updated_at
)
VALUES
  ('ethereum-mainnet', 'Ethereum', 'EVM', '1', 'mainnet', '',
   'https://etherscan.io', 'common', 'active',
   'https://ethereum.org/en/developers/docs/networks/',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('solana-mainnet', 'Solana', 'Solana', 'mainnet-beta', 'mainnet',
   'https://api.mainnet-beta.solana.com', 'https://solscan.io',
   'common', 'active', 'https://solana.com/docs/core/clusters',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('base-mainnet', 'Base', 'EVM', '8453', 'mainnet', '',
   'https://base.blockscout.com', 'common', 'active',
   'https://docs.base.org/base-chain/network-information',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('arbitrum-mainnet', 'Arbitrum One', 'EVM', '42161', 'mainnet', '',
   'https://arbiscan.io', 'common', 'active',
   'https://docs.arbitrum.io/run-arbitrum-node/reference/rpc-endpoints',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('bnb-mainnet', 'BNB Smart Chain', 'EVM', '56', 'mainnet', '',
   'https://bscscan.com', 'common', 'active',
   'https://docs.bnbchain.org/bnb-smart-chain/developers/json_rpc/json-rpc-endpoint/',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('robinhood-mainnet', 'Robinhood Chain', 'EVM', '4663', 'mainnet',
   'https://rpc.mainnet.chain.robinhood.com', 'https://robinhoodchain.blockscout.com',
   'common', 'active', 'https://docs.robinhood.com/chain/connecting/',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  ('robinhood-testnet', 'Robinhood Chain Testnet', 'EVM', '46630', 'testnet',
   'https://rpc.testnet.chain.robinhood.com', 'https://explorer.testnet.chain.robinhood.com',
   'paused', 'paused', 'https://docs.robinhood.com/chain/connecting/',
   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
