export interface ForecastExplanation {
  method: "ridge" | "catboost_shap" | "seasonal_naive_7d";
  base_orders: number;
  raw_orders: number;
  reference_date?: string;
  contributions: { feature: string; value: string | number | null; impact_orders: number }[];
}

export interface ZoneForecast {
  zone_id: string;
  zone_name: string;
  forecast_orders: number;
  capacity_orders: number;
  orders_over_capacity: number;
  explanation?: ForecastExplanation;
}

interface Weather {
  mean_temperature_c: number | null;
  precipitation_mm: number | null;
  lead_hours?: number;
}

export interface Forecast {
  target_date: string;
  forecast_cutoff: string;
  orders_source: string;
  model: string;
  uses_weather?: boolean;
  model_version_id?: string | null;
  weather: Weather;
  forecasts: ZoneForecast[];
}

interface ModelChoice {
  model: string;
  uses_weather: boolean;
}

interface Score {
  mae_orders: number;
  wape_percent: number | null;
  observations: number;
}

export interface ModelVersion extends ModelChoice {
  version_id: string;
  created_at_utc: string;
  target_date: string;
  status: "registered" | "published";
  training: { start: string; end: string; rows: number };
  dataset_sha256: string;
  orders_source: string;
  features: string[];
  feature_schema_version: string;
  parameters: Record<string, unknown>;
  seed: number;
  versions: Record<string, string>;
  validation: Score;
  holdout: { start: string; end: string; train_through: string; selected: Score; baseline: Score };
  artifact: { format: string; sha256: string; bytes: number };
}

export async function getModelVersions(before: string | null, signal: AbortSignal): Promise<{
  versions: ModelVersion[]; next_before: string | null;
}> {
  const params = new URLSearchParams();
  if (before) params.set("before", before);
  const response = await fetch("/api/models?" + params, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Не удалось загрузить реестр моделей.");
  return response.json();
}

export interface ForecastPoint {
  date: string;
  actual: number;
  forecast: number;
}

export interface Prediction extends ForecastPoint {
  zone_id: string;
  baseline: number;
}

export interface ImportanceWindow {
  start: string;
  end: string;
  features: { feature: string; mean_abs_orders: number; permutation_mae_delta: number }[];
}

export interface Metrics {
  validation: (ModelChoice & Score & { by_zone?: Record<string, Score> })[];
  selected: ModelChoice;
  feature_importance?: ImportanceWindow[];
  scenarios?: Record<string, ScenarioCurve[]>;
  data_quality?: {
    checked_days: number;
    checked_orders: number;
    anomaly_count: number;
    missing_weather_days: number;
  };
  holdout: {
    start: string;
    end: string;
    selected: Score;
    baseline: Score;
    predictions: Prediction[];
  };
}

export interface ScenarioCurve {
  feature: "mean_temperature_c" | "orders_lag_7";
  used: boolean;
  base_value: number | null;
  base_orders: number;
  points: { value: number; orders: number }[];
}

export interface Observation extends ModelChoice {
  target_date: string;
  published_at_utc?: string;
  observed_at_utc?: string | null;
  forecasts: Record<string, number>;
  validation_mae: Record<string, number>;
  actuals: Record<string, number> | null;
}

export interface MonitoringDecision {
  zone_id: string;
  start_date: string;
  end_date: string;
  rule_version: string;
  saved_at_utc: string;
  status: "pending" | "normal" | "warning" | "alert";
  action: "collect" | "observe" | "check_next_week" | "retrain_recommended";
  points: (ForecastPoint & { reference: number })[];
  weeks: { start: string; end: string; days: number; error: number; limit: number; complete: boolean; aboveLimit: boolean }[];
  rule: { week_days: number; window_days: number; limit_factor: number };
  source_sha256: string;
  orders_sources: string[];
}

export interface DecisionPage {
  decisions: MonitoringDecision[];
  next_before: string | null;
}

export interface DriftSeries {
  feature: string;
  status: "insufficient" | "steady" | "shift";
  distance: number | null;
  reference_count: number;
  current_count: number;
  reference_median: number | null;
  current_median: number | null;
}

interface DriftWindow {
  start_date: string;
  end_date: string;
  days: number;
  sha256: string;
}

export interface InputDrift {
  rule_version: string;
  end_date: string;
  saved_at_utc: string;
  reference: DriftWindow;
  reference_fixed: boolean;
  current: DriftWindow;
  rule: { distance_threshold: number; reference_min: number; current_min: number };
  orders: Record<string, DriftSeries[]>;
  weather: DriftSeries[];
}

export async function getMonitoringDecisions(zone: string, before: string | null, signal: AbortSignal): Promise<DecisionPage> {
  const query = new URLSearchParams({ zone });
  if (before) query.set("before", before);
  const response = await fetch("/api/monitoring/decisions?" + query, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Не удалось загрузить историю проверок. Попробуйте ещё раз.");
  return response.json() as Promise<DecisionPage>;
}

export interface Run {
  run_id: string;
  target_date: string;
  finished_at_utc: string;
  started_at_utc?: string;
  status: "success" | "failed";
  step: string;
  error?: string;
  orders?: { generated_days: number; reused_days: number };
  weather?: { origin: string; downloaded_utc_days?: number };
}

export interface DashboardData {
  dates: string[];
  forecast: Forecast | null;
  metrics: Metrics | null;
  runs: Run[];
  observations?: Observation[];
  monitoring_decisions?: MonitoringDecision[];
  input_drift?: InputDrift | null;
  forecast_model?: ModelVersion | null;
}

export async function getDashboard(date: string, signal: AbortSignal): Promise<DashboardData> {
  const query = date ? "?" + new URLSearchParams({ date }) : "";
  const response = await fetch("/api/dashboard" + query, { signal, cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const message = body && typeof body.detail === "string" ? body.detail : "Пульт не получил данные от сервера.";
    throw new Error(message);
  }
  return response.json() as Promise<DashboardData>;
}
