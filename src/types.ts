export type Market = "AUTO" | "US" | "HK" | "A";

export interface CompanySnapshot {
  snapshot_date?: string;
  price?: number;
  market_cap?: number;
  pe_ratio?: number;
  pb_ratio?: number;
  gross_margin?: number;
  net_margin?: number;
  roe?: number;
  revenue?: number;
  net_income?: number;
  operating_cash_flow?: number;
  free_cash_flow?: number;
  total_assets?: number;
  total_liabilities?: number;
  debt_to_asset_ratio?: number;
  raw_data?: {
    quote_source?: string;
    quote_symbol?: string;
    quote_currency?: string;
    quote_fetched_at?: string;
    market_cap_unit?: string;
    previous_close?: number;
    price_change_pct?: number;
    [key: string]: unknown;
  };
}

export interface Company {
  id: string;
  name: string;
  name_en: string;
  ticker: string;
  market: string;
  exchange: string;
  industry: string;
  sector: string;
  description: string;
  tags: string[];
  aliases: string[];
  snapshot?: CompanySnapshot;
}

export interface CompanyUniverseResponse {
  companies: Company[];
  total: number;
  limit: number;
  offset: number;
  summary: CompanyUniverseSummary;
}

export interface CompanyUniverseSummary {
  total: number;
  by_market: Record<string, number>;
  sync?: {
    started_at?: string;
    completed_at?: string;
    markets?: Record<string, { fetched?: number; upserted?: number; source?: string }>;
    [key: string]: unknown;
  };
}

export interface V2Rating {
  list_rank: number;
  company_id: string;
  market: "US" | "A" | "HK" | string;
  ticker: string;
  original_code?: string;
  name: string;
  name_en?: string;
  theme: string;
  industry?: string;
  exchange?: string;
  moat_score: number;
  quality_score: number;
  valuation_score: number;
  action_score: number;
  data_quality_score?: number;
  final_rating: string;
  final_action: string;
  rating_version: string;
  rated_at?: string;
  rating_basis?: {
    mode?: string;
    snapshot_used?: boolean;
    aics_scorecard_used?: boolean;
    pe_ratio?: number | null;
    roe?: number | null;
  };
}

export interface V2RatingsResponse {
  version: string;
  as_of: string;
  total: number;
  expected_total: number;
  summary: {
    total: number;
    by_market: Record<string, number>;
    by_action: Record<string, number>;
    top10: Array<Pick<V2Rating, "list_rank" | "ticker" | "name" | "action_score" | "final_action"> & { rank?: number }>;
  };
  ratings: V2Rating[];
}

export interface ExpertProfile {
  investment_philosophy: string;
  core_framework: string;
  decision_process: string;
  question_template: string;
  speaking_style: string;
  strengths: string;
  weaknesses: string;
  preferred_industries: string[];
  avoided_industries: string[];
  market_tags: string[];
  style_tags: string[];
  risk_preference: string;
  time_horizon: string;
  source_summary: string;
}

export interface Expert {
  id: string;
  name: string;
  name_en: string;
  category: string;
  nationality: string;
  role_title: string;
  bio: string;
  avatar_url?: string;
  is_active: boolean;
  profile: ExpertProfile;
  chair_reason?: string;
  chair_score?: number;
}

export interface RecommendedExpert {
  expert: Expert;
  fit_score: number;
  reason: string;
}

export interface DataPackAgent {
  agent: string;
  status: string;
  evidence_ids?: string[];
  [key: string]: unknown;
}

export interface EvidenceItem {
  evidence_id: string;
  category: string;
  title: string;
  summary: string;
  source_provider: string;
  source_url?: string;
  source_document_id?: string;
  raw_value?: string;
  normalized_value?: string;
  extracted_quote?: string;
  date?: string;
  confidence: number;
  freshness_score: number;
}

export interface MetricScore {
  name: string;
  raw_value?: unknown;
  score?: number | null;
  weight: number;
  reason: string;
  evidence_ids?: string[];
  status?: "scored" | "missing_evidence" | "not_applicable" | string;
  formula?: string;
  calculation?: string;
  evidence_required?: string[];
  data_source?: string;
  confidence?: number | null;
  missing_reason?: string;
}

export interface DataQualityRequirement {
  key: string;
  label: string;
  passed: boolean;
  required?: boolean;
  requirement?: string;
  evidence_ids?: string[];
  source?: unknown;
  details?: Record<string, unknown>;
}

export interface DataQualityGate {
  passed: boolean;
  status?: "passed" | "failed" | string;
  version?: string;
  requirements?: DataQualityRequirement[];
  blocking_items?: string[];
  summary?: string;
}

export interface ScoreBucket {
  key?: string;
  name: string;
  score?: number | null;
  weight: number;
  metrics?: MetricScore[];
  summary?: string | {
    text?: string;
    band?: string;
    coverage_ratio?: number;
    [key: string]: unknown;
  };
  status?: "scored" | "partial" | "missing_evidence" | string;
  scored_weight?: number;
  total_weight?: number;
  coverage_ratio?: number;
}

export interface CompanyScorecard {
  scoring_version?: string;
  data_quality_score?: number;
  company_quality_score?: number;
  valuation_attractiveness_score?: number;
  investment_action_score?: number;
  catalyst_score?: number;
  timing_score?: number;
  grade?: string;
  final_action?: string;
  confidence?: number;
  buckets?: ScoreBucket[];
  valuation_buckets?: ScoreBucket[];
  data_quality_buckets?: ScoreBucket[];
  bucket_scores?: Record<string, number>;
  valuation_bucket_scores?: Record<string, number>;
  data_quality_bucket_scores?: Record<string, number>;
  data_quality_gate?: DataQualityGate;
  red_flags?: Array<{
    title?: string;
    severity?: string;
    reason?: string;
    evidence_ids?: string[];
  }>;
  missing_metrics?: string[];
  action_rules?: string[];
  matrix?: {
    quality_axis?: string;
    valuation_axis?: string;
    quadrant?: string;
    interpretation?: string;
    implication?: string;
  };
  summary?: {
    text?: string;
    data_quality_grade?: string;
    company_quality_grade?: string;
    valuation_grade?: string;
  };
  score_coverage?: Record<string, unknown>;
}

export interface DataPlanStep {
  step: string;
  status: string;
  source: string;
  confidence: number;
  evidence_ids?: string[];
}

export interface DataPack {
  schema_version?: string;
  run_id?: string;
  fundamental?: DataPackAgent;
  sentiment?: DataPackAgent;
  macro?: DataPackAgent;
  technical?: DataPackAgent;
  data_quality?: {
    overall_score?: number;
    usable_for_decision?: boolean;
    dqs_mode?: string;
    dqs_version?: string;
    dqs_passed?: boolean;
    dqs_status?: string;
    dqs_requirements?: DataQualityRequirement[];
    dqs_blocking_items?: string[];
    dqs_summary?: string;
    legacy_overall_score?: number;
    missing_data?: string[];
    data_conflicts?: string[];
    freshness_score?: number;
    evidence_count?: number;
    source_mix?: string[];
    document_store?: string;
    collection_gaps?: string[];
    notes?: string[];
  };
  collection_summary?: {
    documents_dir?: string;
    news_count?: number;
    social_count?: number;
    filing_count?: number;
    financial_statement_count?: number;
    financial_series_count?: number;
    research_report_count?: number;
    peer_count?: number;
    technical_history_count?: number;
    valuation_history_count?: number;
    gaps?: string[];
    source_attempts?: Array<Record<string, unknown>>;
  };
  data_plan?: DataPlanStep[];
  evidence_store?: EvidenceItem[];
  evidence_index?: EvidenceItem[];
  financial_summary?: {
    gross_margin?: number;
    net_margin?: number;
    roe?: number;
    pb_ratio?: number;
    pe_ratio?: number;
    revenue?: number;
    net_income?: number;
    operating_cash_flow?: number;
    free_cash_flow?: number;
    total_assets?: number;
    total_liabilities?: number;
    debt_to_asset_ratio?: number;
    source_evidence_ids?: string[];
  };
  financial_series?: Array<Record<string, unknown>>;
  valuation_summary?: {
    method?: string;
    fair_value_range?: {
      bear?: number;
      base?: number;
      bull?: number;
      currency?: string;
    };
    current_price?: number;
    upside_downside_base?: number;
    historical_percentile?: Record<string, unknown>;
    key_sensitivity?: string[];
    assumptions?: string[];
  };
  valuation_history?: Record<string, unknown>;
  generated_at?: string;
  source_records?: string[];
  scorecard?: CompanyScorecard;
}

export interface DecisionVisualization {
  company_quality_score?: number | null;
  valuation_attractiveness_score?: number | null;
  investment_action_score?: number | null;
  data_quality_score: number;
  data_quality_passed?: boolean;
  data_quality_status?: string;
  data_quality_gate?: DataQualityGate;
  quadrant_code: "quality_value" | "quality_wait" | "cheap_trap" | "avoid" | "data_insufficient";
  quadrant_title: string;
  quadrant_description: string;
  spectrum_label: string;
  spectrum_position?: number | null;
  x_axis_label: string;
  y_axis_label: string;
  primary_action: string;
  secondary_action?: string;
  risk_level?: string;
  position_hint?: string;
  thresholds: {
    quality_high: number;
    valuation_attractive: number;
  };
  buy_conditions?: string[];
}

export interface CommitteeRound {
  id?: string;
  round_number: number;
  round_name: string;
  round_status?: string;
  round_output: any;
  completed_at?: string;
}

export interface ReportState {
  report_id: string;
  report_title: string;
  report_date: string;
  company: Company;
  company_tags: Record<string, string[]>;
  recommended_experts: RecommendedExpert[];
  selected_experts: Expert[];
  chairman: Expert | Record<string, never>;
  data_pack: DataPack;
  scorecard?: CompanyScorecard;
  data_quality_gate?: DataQualityGate;
  decision_visualization?: DecisionVisualization;
  status: string;
  current_round: number;
  rounds: CommitteeRound[];
  final_action?: string;
  overall_score?: number;
  confidence?: number;
  final_report_markdown?: string;
  pdf_url?: string;
  created_at?: string;
}

export interface ReportSummary {
  report_id: string;
  company: Company;
  report_date: string;
  selected_experts: Expert[];
  chairman: Expert;
  overall_score?: number;
  final_action?: string;
  status: string;
  current_round?: number;
  pdf_url?: string;
  created_at: string;
}

export interface Health {
  ok: boolean;
  date: string;
  llm: {
    enabled: boolean;
    base_url: string;
    model: string;
    has_key: boolean;
    allow_fallback: boolean;
  };
}
