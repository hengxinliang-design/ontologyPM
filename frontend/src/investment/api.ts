import { request } from "../api";
import type { InvestmentAnalysis, InvestmentSettings } from "./types";

export async function fetchInvestmentAnalysis(period: { startMonth?: string; endMonth?: string }): Promise<InvestmentAnalysis> {
  const params = new URLSearchParams();
  if (period.startMonth) params.set("start_month", period.startMonth);
  if (period.endMonth) params.set("end_month", period.endMonth);
  const query = params.toString();
  return (await request<{ data: InvestmentAnalysis }>(`/api/v1/investment/analysis${query ? `?${query}` : ""}`)).data;
}

export async function updateInvestmentSettings(payload: Partial<InvestmentSettings>): Promise<InvestmentSettings> {
  return (await request<{ data: InvestmentSettings }>("/api/v1/investment/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })).data;
}
