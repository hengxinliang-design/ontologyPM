import { describe, expect, it } from "vitest";
import { forecastCell, forecastEnabled, forecastOutputLines, forecastStatusLine, type ForecastOutput, type LoadForecastResult } from "./loadForecast";

const model = { version: 3, trained_at: "2026-11-01T00:00:00Z", data_cutoff_month: "2026-10", target_month: "2026-11",
  history_months: 22, person_months: 730, enabled_outputs: [] as ForecastOutput[], notes: null };

function result(overrides: Partial<LoadForecastResult>): LoadForecastResult {
  return { status: "not_enabled", model, outputs: {}, data: [], target_month: "2026-11", ...overrides };
}

describe("load forecast on the resource page", () => {
  it("says why nothing is shown when no output passed its gate", () => {
    const off = result({ outputs: { utilization: { enabled: false, candidate: null, reason: "梯度提升树平均误差 16.73 个百分点，未达到门槛" } } });
    expect(forecastEnabled(off)).toBe(false);
    expect(forecastStatusLine(off)).toBe("负荷预测：未启用（回测未达门槛） · 数据截止 2026-10");
    expect(forecastOutputLines(off)).toEqual([{ label: "利用率估计", enabled: false, reason: "梯度提升树平均误差 16.73 个百分点，未达到门槛" }]);
  });

  it("names the enabled outputs and the month predicted", () => {
    const on = result({ status: "active", model: { ...model, enabled_outputs: ["utilization", "overload"] } });
    expect(forecastEnabled(on)).toBe(true);
    expect(forecastStatusLine(on)).toBe("负荷预测：已启用利用率估计、过载概率 · 预计 2026-11 · 数据截止 2026-10");
  });

  it("covers the states before a model exists", () => {
    expect(forecastStatusLine(null)).toBe("负荷预测：暂不可用");
    expect(forecastStatusLine(result({ status: "not_trained", model: null }))).toBe("负荷预测：尚未训练（每月初自动训练）");
    expect(forecastStatusLine(result({ status: "insufficient_data" }))).toBe("负荷预测：历史数据不足 · 数据截止 2026-10");
  });

  it("shows only the values the ontology returned, with a tone from the probabilities", () => {
    const row = { employee_id: "E1", employee_name: "刘洋", current_utilization_percent: 88, reasons: [] };
    expect(forecastCell({ ...row, predicted_utilization_percent: 112.5, interval_low_percent: 95, interval_high_percent: 130, overload_probability_percent: 72 }))
      .toEqual({ main: "预计 112.5%（95–130%）", detail: "过载 72%", tone: "critical" });
    expect(forecastCell({ ...row, idle_probability_percent: 64 })).toEqual({ main: null, detail: "闲置 64%", tone: "healthy" });
    expect(forecastCell(undefined).main).toBeNull();
  });
});
