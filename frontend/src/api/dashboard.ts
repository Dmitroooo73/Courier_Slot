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
