export interface BranchCatalog {
  id: string;
  rows: number;
  data_start: string;
  data_end: string;
}

export interface ProductionCatalog {
  id: string;
  line: string;
  start_day: string;
  end_day: string;
  branches: BranchCatalog[];
  bottle_model_id?: string | null;
  segment_id?: string | null;
  dar_profile_id?: string | null;
}

export interface FilterSelection {
  defect_codes: number[];
  cavities: string[];
  colors: string[];
  sections: string[];
  machine_codes: number[];
}

export interface Catalog {
  source?: string;
  publication_id?: string | null;
  lines: string[];
  productions: ProductionCatalog[];
  filters: FilterSelection;
  max_productions_per_query: number;
}

export interface TrendQuery {
  production_ids: string[];
  branches: string[];
  metric: string;
  window: string;
  filters: FilterSelection;
}

export type ScoreSeverity = "Critico" | "Grave" | "Mayor" | "Menor" | "Leve";
export type CustomerQualityLabel = "good" | "observed" | "bad" | "critical";

export interface ScoreConfigOverride {
  aql?: { category_limits_percent?: Partial<Record<ScoreSeverity, number>> };
  weights?: {
    mia_defect?: number;
    categories?: Partial<Record<ScoreSeverity, number>>;
    dar?: number;
    zc?: number;
  };
  dar?: {
    progressive_thresholds_percent?: { score_5?: number; score_10?: number };
  };
  zc?: { target_percent?: { default?: number } };
  score?: {
    interpretation?: {
      good_max?: number;
      observed_max?: number;
      bad_max?: number;
    };
  };
}

export interface ProductionQualityScoreQuery {
  selection: TrendQuery;
  score_config_override?: ScoreConfigOverride;
}

export interface CustomerQualityQuery {
  selection: TrendQuery;
}

export interface CustomerQualityResponse {
  rows: Array<{
    production_id: string;
    label: CustomerQualityLabel | "unknown";
  }>;
}

export interface CustomerScoreCause {
  component: string;
  label: string;
  contribution: number;
  share_of_total_penalty: number;
}

export interface CustomerScoreComponent {
  component: string;
  label: string;
  score: number;
  weight: number;
  contribution: number;
  details: string;
}

export interface MiaScoreDetail {
  defect_code: number;
  defect_label: string;
  rejected_count: number;
  inspected_count: number;
  current_percent: number;
  historical_mean_percent: number | null;
  historical_sigma_percent: number | null;
  z_score: number | null;
  score: number;
  weight: number;
  contribution: number;
  samples: number;
}

export interface AqlCategoryScore {
  category: string;
  affected_cavity_minutes: number;
  total_cavity_minutes: number;
  exposure_percent: number;
  aql_percent: number | null;
  ratio: number | null;
  score: number;
  weight: number;
  contribution: number;
  top_defect_code: number | null;
  top_defect_label: string | null;
}

export interface DarScore {
  event_count: number;
  affected_minutes: number;
  total_cavity_minutes: number;
  exposure_percent: number;
  aql_percent: number | null;
  score: number;
  weight: number;
  contribution: number;
  details: string;
  breakdown: Array<{
    group: string;
    codes: number[];
    event_count: number;
    affected_minutes: number;
    share_percent: number;
    exposure_percent: number;
    score: number;
    threshold_label: string;
  }>;
}

export interface ZcScore {
  target_percent: number;
  actual_percent: number | null;
  loss_points: number | null;
  score: number;
  weight: number;
  contribution: number;
  windows: number;
}

export interface ShiftCustomerScore {
  shift: string;
  operational_day: string;
  shift_name: string;
  start: string;
  end: string;
  total_penalty: number;
  normalized_score: number;
  evaluated_weight: number;
  status: CustomerQualityLabel;
  main_causes: CustomerScoreCause[];
  components: CustomerScoreComponent[];
  mia_details: MiaScoreDetail[];
  aql_categories: AqlCategoryScore[];
  dar: DarScore | null;
  zc: ZcScore | null;
  warnings: string[];
}

export interface ProductionCustomerScore {
  production_id: string;
  line: string;
  total_penalty: number;
  normalized_score: number;
  evaluated_weight: number;
  status: CustomerQualityLabel;
  main_causes: CustomerScoreCause[];
  components: CustomerScoreComponent[];
  mia_details: MiaScoreDetail[];
  aql_categories: AqlCategoryScore[];
  dar: DarScore | null;
  zc: ZcScore | null;
  shift_scores: ShiftCustomerScore[];
  warnings: string[];
}

export interface ProductionQualityScoreResponse {
  pending_productions?: Array<{ production_id: string; reasons: string[] }>;
  status: string;
  productions: ProductionCustomerScore[];
  warnings: string[];
  meta: {
    source?: string;
    publication_id?: string | null;
    cutoff?: string | null;
    query_time_ms: number;
    config_version: number;
    files_read: number;
    rows_scanned: number;
    effective_config: ScoreConfigOverride;
  };
}

/** How far Gold has calculated one production and which Silver families block it. */
export interface GoldCoverage {
  id_produccion: string;
  origin: string | null;
  estado: "READY" | "WAITING_DEPENDENCY" | "INCOMPLETE" | string;
  calculated_until: string | null;
  missing: string[];
}

export interface GoldStatus {
  publication_id: string;
  created_at: string;
  states: Record<string, number>;
  pending_reasons: Record<string, number>;
  coverage: GoldCoverage[];
}

export interface ApiErrorResponse {
  error?: { code?: string; message?: string; request_id?: string };
}
