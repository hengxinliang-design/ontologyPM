// Employee load forecast (ontology function employeeLoadForecast, requirements baseline D46–D49).
// The system trains it every month and switches each output on only when its backtest passes the gate, so the
// page shows the model's state in one line and adds the forecast column only when an output is enabled.

export type ForecastOutput = "utilization" | "overload" | "idle";
export const FORECAST_OUTPUT_LABEL: Record<ForecastOutput, string> = { utilization: "利用率估计", overload: "过载概率", idle: "闲置概率" };
const ORDER: ForecastOutput[] = ["utilization", "overload", "idle"];

export interface ForecastOutputState { enabled: boolean; candidate: string | null; reason: string }
export interface LoadForecastRow {
  employee_id: string | null;
  employee_name: string;
  current_utilization_percent: number;
  predicted_utilization_percent?: number;
  interval_low_percent?: number;
  interval_high_percent?: number;
  overload_probability_percent?: number;
  idle_probability_percent?: number;
  reasons: string[];
}
export interface LoadForecastResult {
  status: "active" | "not_enabled" | "insufficient_data" | "not_trained";
  message?: string;
  model: { version: number; trained_at: string; data_cutoff_month: string; target_month: string; history_months: number;
    person_months: number; enabled_outputs: ForecastOutput[]; notes: string | null } | null;
  outputs: Partial<Record<ForecastOutput, ForecastOutputState>>;
  statistics_month?: string;
  target_month?: string;
  data: LoadForecastRow[];
}

export function forecastEnabled(forecast: LoadForecastResult | null): boolean {
  return Boolean(forecast?.model?.enabled_outputs.length);
}

// The folded summary: what is on, for which month, or why nothing is.
export function forecastStatusLine(forecast: LoadForecastResult | null): string {
  if (!forecast) return "负荷预测：暂不可用";
  if (!forecast.model) return "负荷预测：尚未训练（每月初自动训练）";
  const cutoff = `数据截止 ${forecast.model.data_cutoff_month}`;
  if (forecast.status === "insufficient_data") return `负荷预测：历史数据不足 · ${cutoff}`;
  const enabled = forecast.model.enabled_outputs;
  if (!enabled.length) return `负荷预测：未启用（回测未达门槛） · ${cutoff}`;
  return `负荷预测：已启用${enabled.map(o => FORECAST_OUTPUT_LABEL[o]).join("、")} · 预计 ${forecast.target_month} · ${cutoff}`;
}

// One line per output for the expanded panel, in a fixed order.
export function forecastOutputLines(forecast: LoadForecastResult | null): { label: string; enabled: boolean; reason: string }[] {
  if (!forecast) return [];
  return ORDER.filter(o => forecast.outputs[o]).map(o => ({ label: FORECAST_OUTPUT_LABEL[o], enabled: forecast.outputs[o]!.enabled, reason: forecast.outputs[o]!.reason }));
}

// The cell: only what is enabled comes back from the ontology, so absent values are simply not shown.
export function forecastCell(row: LoadForecastRow | undefined): { main: string | null; detail: string | null; tone: "critical" | "warning" | "healthy" | "baseline" } {
  if (!row) return { main: null, detail: null, tone: "baseline" };
  const main = row.predicted_utilization_percent != null
    ? `预计 ${row.predicted_utilization_percent}%（${row.interval_low_percent}–${row.interval_high_percent}%）` : null;
  const parts = [
    row.overload_probability_percent != null ? `过载 ${row.overload_probability_percent}%` : "",
    row.idle_probability_percent != null ? `闲置 ${row.idle_probability_percent}%` : "",
  ].filter(Boolean);
  const over = row.overload_probability_percent ?? 0;
  const idle = row.idle_probability_percent ?? 0;
  const tone = over >= 50 ? "critical" : over >= 30 ? "warning" : idle >= 50 ? "healthy" : "baseline";
  return { main, detail: parts.length ? parts.join(" · ") : null, tone };
}
