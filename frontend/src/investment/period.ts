// Period: this year so far by default (January to the current month); one month, a month range or all periods on demand.
export type PeriodMode = "ytd" | "month" | "range" | "all";
export const PERIOD_LABELS: Record<PeriodMode, string> = { ytd: "本年至今", month: "单月", range: "月份区间", all: "全部期间" };

export function currentMonth(): string {
  const today = new Date();
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
}

/** The month before ``month``: the default for 单月, since the current month is still being filled in. */
export function previousMonth(month = currentMonth()): string {
  const [year, value] = month.split("-").map(Number);
  return value === 1 ? `${year - 1}-12` : `${year}-${String(value - 1).padStart(2, "0")}`;
}

export function periodRange(mode: PeriodMode, month: string, startMonth: string, endMonth: string, now = currentMonth()): { startMonth?: string; endMonth?: string } {
  if (mode === "ytd") return { startMonth: `${now.slice(0, 4)}-01`, endMonth: now };
  if (mode === "month") return month ? { startMonth: month, endMonth: month } : {};
  if (mode === "range") return startMonth && endMonth ? { startMonth, endMonth } : {};
  return {};
}

export function periodText(mode: PeriodMode, period: { start_month: string | null; end_month: string | null }): string {
  if (!period.start_month) return "全部期间";
  if (mode === "ytd") return `${period.start_month} 至 ${period.end_month}（本年至今）`;
  if (period.start_month === period.end_month) return `${period.start_month}（单月）`;
  return `${period.start_month} 至 ${period.end_month}`;
}
