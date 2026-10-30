import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FunctionResultView, makeLabeler, money, RawJson } from "./functionResults";
import type { OntologyValueType } from "./types";

const valueTypes: OntologyValueType[] = [
  { apiName: "RiskLevel", displayName: "", description: "", values: [{ value: "critical", label: "严重" }, { value: "warning", label: "关注" }] },
  { apiName: "ContractType", displayName: "", description: "", values: [{ value: "construction", label: "系统实施" }, { value: "sales", label: "售前阶段" }] },
  { apiName: "LifecycleStatus", displayName: "", description: "", values: [{ value: "active", label: "执行中" }, { value: "planned", label: "计划中" }] }
];

function render(outputType: string, result: unknown): string {
  return renderToStaticMarkup(<FunctionResultView outputType={outputType} result={result} valueTypes={valueTypes} />);
}

describe("formatting helpers", () => {
  it("formats money and maps enum labels", () => {
    expect(money("149900")).toBe("¥149,900.00");
    expect(money(null)).toBe("—");
    expect(money("n/a")).toBe("n/a");
    const label = makeLabeler(valueTypes);
    expect(label("RiskLevel", "critical")).toBe("严重");
    expect(label("RiskLevel", "unknown")).toBe("unknown");
    expect(label("Missing", null)).toBe("—");
  });
});

describe("FunctionResultView", () => {
  it("renders risk assessments with severity and alerts", () => {
    const html = render("RiskAssessment", {
      riskLevel: "critical", riskReasons: ["2 个里程碑逾期"], maintenanceComplete: false, maintenanceReasons: ["制定里程碑计划"],
      overdueMilestones: 2, overduePaymentNodes: 0,
      anomalyAlerts: [{ code: "MILESTONE_DUE", severity: "critical", title: "接口方案确认 · 已延期 106 天", detail: "里程碑尚未确认完成" }]
    });
    expect(html).toContain("风险等级：严重");
    expect(html).toContain("接口方案确认 · 已延期 106 天");
    expect(html).toContain("待补充");
    expect(html).toContain("制定里程碑计划");
    expect(html).not.toContain("anomalyAlerts");
  });

  it("renders budget status as metrics and a usage meter", () => {
    const html = render("BudgetStatus", {
      contractType: "construction", hoursPerPersonDay: "8.00", plannedPersonDays: "30.00", budgetHours: "240.00",
      reportedPersonDays: "3.00", remainingPersonDays: "27.00", budgetUtilization: 10, customerConfirmedPersonDays: "2.25",
      confirmationGapPersonDays: "0.75", billablePersonDays: null, status: "正常"
    });
    expect(html).toContain("系统实施");
    expect(html).toContain("预算使用率");
    expect(html).toContain("width:10%");
    expect(html).toContain("27.00 人天");
    expect(html).not.toContain("可结算人天");
  });

  it("renders payment status with amounts and the next node deadline", () => {
    const html = render("PaymentStatus", {
      contractStatus: "进行中", contractValue: "149900.00", invoicedAmount: "74950.00", crmReceivedAmount: "0.00",
      outstandingReceivableAmount: "149900.00", collectionRate: 0, overduePaymentNodes: 2,
      paymentPlan: { total_nodes: 2, paid_nodes: 0, planned_amount: "149900.00", actual_amount: "0.00",
        next_node: { name: "首期款", planned_date: "2026-07-20", planned_amount: "74950.00", status: "invoiced", deadline: { urgency: "overdue", label: "已延期 96 天" } } }
    });
    expect(html).toContain("¥149,900.00");
    expect(html).toContain("2 个回款节点逾期");
    expect(html).toContain("首期款");
    expect(html).toContain("已开票");
    expect(html).toContain("已延期 96 天");
  });

  it("renders project health and handles empty periods", () => {
    const html = render("ProjectHealth", {
      period: { start_month: "2026-07", end_month: "2026-07" },
      health: { health_status: "需关注", overrun_warning: "正常", project_status: "active", burn_rate_percent: "10.0",
        cumulative_actual_person_days: "3.00", budget_person_days: "30.00", period_actual_person_days: "3.00", period_actual_hours: "24.00",
        participant_count: 2, budget_exhaustion_forecast: { label: "预计 2027-04 耗尽", basis: "按统计期月均 3.00 人天推算" },
        previous_period: { start_month: "2026-06", end_month: "2026-06" }, previous_period_person_days: "1.00", risk_reasons: ["2 个里程碑逾期"],
        current_milestone: { name: "接口方案确认", planned_end: "2026-07-10", status: "in_progress", deadline: { urgency: "overdue", label: "已延期 106 天" } } }
    });
    expect(html).toContain("需关注");
    expect(html).toContain("执行中");
    expect(html).toContain("预计 2027-04 耗尽");
    expect(html).toContain("对比 2026-06 ~ 2026-06");
    expect(html).toContain("接口方案确认");
    expect(render("ProjectHealth", { period: { start_month: "2026-01", end_month: "2026-03" }, health: null })).toContain("2026-01 ~ 2026-03 没有该项目的健康度数据");
  });

  it("renders employee utilization and portfolio distributions", () => {
    const utilization = render("EmployeeUtilization", {
      employeeName: "Zhang San", period: "2026-07", baselineHours: "153.00",
      assessment: { status: "严重不足", utilization_percent: "10.5", reported_hours: "16.00", baseline_hours: "153.00", approved_hours: "12.00", confirmation_gap_hours: "4.00", overtime_hours: "0.00", entry_count: 2, project_count: 1 }
    });
    expect(utilization).toContain("严重不足");
    expect(utilization).toContain("16.00 小时");
    expect(render("EmployeeUtilization", { employeeName: "Li Si", period: "2026-08", assessment: null })).toContain("没有工时记录");

    const portfolio = render("PortfolioSummary", {
      total_projects: 3, contract_value: "169900.00", received_amount: "0.00", critical_projects: 1, warning_projects: 2,
      healthy_projects: 0, incomplete_maintenance: 2, by_contract_type: { construction: 1, sales: 2 }, by_lifecycle_status: { active: 2, planned: 1 }
    });
    expect(portfolio).toContain("¥169,900.00");
    expect(portfolio).toContain("售前阶段");
    expect(portfolio).toContain("计划中");
  });

  it("renders investment profiles by outcome", () => {
    const open = render("InvestmentProfile", {
      outcome: "open", registration: "opportunity", total_person_days: "2.00", at_risk_person_days: "2.00",
      expected_sunk_person_days: "1.60", probability_source: "CRM 赢率", win_probability: 20, presales_age_days: 120,
      stalled: true, employee_count: 1, data_flags: ["CRM 未填写赢率"]
    });
    expect(open).toContain("在途投入");
    expect(open).toContain("1.60 人天");
    expect(open).toContain("疑似停滞");
    expect(open).toContain("CRM 未填写赢率");
    const won = render("InvestmentProfile", { outcome: "won", registration: "contracted", conversion_date: "2026-06-16", total_person_days: "3.00", presales_converted_person_days: "1.00", delivery_person_days: "2.00", cost_to_win_share: 33.3, employee_count: 2 });
    expect(won).toContain("签约日 2026-06-16");
    expect(won).toContain("占比 33.3%");
    expect(won).not.toContain("在途投入");
    const analysis = render("InvestmentAnalysis", { totals: { total_person_days: "7.50", sunk_person_days: "1.00" }, ratios: { sunk_rate: 50, win_rate_by_count: 66.7 } });
    expect(analysis).toContain("沉没成本率 50%");
    expect(analysis).toContain("66.7%");
  });

  it("renders the working calendar", () => {
    const calendar = render("WorkingCalendar", { start_date: "2026-10-01", end_date: "2026-10-31", working_days: 16, holiday_excluded_days: 6, makeup_workdays: 1, available: true, stale: false, baseline_hours: "128.00" });
    expect(calendar).toContain("16 天");
    expect(calendar).toContain("中国法定节假日日历");
    expect(render("WorkingCalendar", { start_date: "2026-10-01", end_date: "2026-10-31", working_days: 22, available: false, baseline_hours: "176.00" })).toContain("仅按周一至周五");
  });

  it("renders the customer annual summary", () => {
    const html = render("CustomerAnnualSummary", { year: 2026, undated_contract_count: 1, years: [{ year: 2026, contract_count: 1, project_count: 1, customer_count: 1,
      signed_amount: "149900.00", invoiced_amount: "74950.00", received_amount: "0.00",
      customers: [{ customer_name: "TPL", contract_count: 1, project_count: 1, signed_amount: "149900.00", invoiced_amount: "74950.00", received_amount: "0.00" }] }] });
    expect(html).toContain("2026 年 · 1 份合同");
    expect(html).toContain("¥149,900.00");
    expect(html).toContain("1 份合同没有签约时间");
    expect(render("CustomerAnnualSummary", { year: 2030, years: [], undated_contract_count: 0 })).toContain("该年度没有签订的合同");
  });

  it("falls back to a readable generic view", () => {
    const html = render("Unknown", { total_projects: 3, tags: ["a", "b"], nested: { flag: true }, empty: null });
    expect(html).toContain("项目数");
    expect(html).toContain("a、b");
    expect(html).toContain("是");
    expect(render("Unknown", null)).toContain("没有返回结果");
  });

  it("keeps raw JSON collapsed by default", () => {
    const html = renderToStaticMarkup(<RawJson value={{ a: 1 }} />);
    expect(html).toContain("<details");
    expect(html).not.toContain("open");
    expect(html).toContain("查看原始 JSON");
  });

  it("ranks strategy signals in the department review", () => {
    const review = render("DepartmentOperatingReview", {
      year: 2026, month: "2026-09", headcount: 12, kpis: { collections: { label: "回款", actual: "600000.00", target: "1000000.00", completion_percent: 60, time_progress_percent: 75, status: "behind" } },
      signals: [{ signal: "push_acceptance", label: "推动验收回款", priority: 1, evidence: ["SEY103：1 个回款节点逾期未到账"] }]
    });
    expect(review).toContain("落后 · 目标 1000000.00 · 完成 60% / 时间 75%");
    expect(review).toContain("1. 推动验收回款");
  });

  it("shows the resource forecast per month with the projects behind a shortage and the data gaps", () => {
    const html = render("ResourceForecast", {
      period_start: "2026-10-31", period_end: "2026-12-31", headcount: 9, headcount_month: "2026-10",
      months: [
        { month: "2026-11", working_days: 21, capacity_person_days: "141.75", demand_person_days: "180.00", gap_person_days: "38.25", status: "shortage",
          top_projects: [{ project_number: "SEY101", person_days: "120.00" }] },
        { month: "2026-12", working_days: 23, capacity_person_days: "155.25", demand_person_days: "100.00", gap_person_days: "-55.25", status: "surplus", top_projects: [] }
      ],
      projects: [{ project_number: "SEY101", method: "budget", basis: "剩余 200 人天", months: { "2026-11": "120.00", "2026-12": "80.00" }, total: "200.00" }],
      data_gaps: [{ project_number: "TPL149", reason: "计划结束 2026-10-15 已过，项目仍在执行", remaining_person_days: "60.00" }],
      assumptions: { capacity: "每人每月可用人天 = 测算期内工作日 × 最低利用率目标", minimum_utilization_percent: "75.00", demand: "有预算按剩余" },
      limitations: "需求不分摊到个人。"
    });
    expect(html).toContain("缺口 38.25 人天");
    expect(html).toContain("富余 55.25 人天");
    expect(html).toContain("SEY101 120.00 人天");
    expect(html).toContain("预算剩余");
    expect(html).toContain("计划结束 2026-10-15 已过，项目仍在执行（剩余 60.00 人天）");
    expect(html).toContain("最低利用率 75.00%");
  });

  it("lists objects that still need maintenance with per-requirement counts", () => {
    const html = render("MaintenanceWorklist", {
      summary: [{ objectType: "Project", requirement: "planMilestones", label: "制定里程碑计划", missing: 2, maintained: 1, applicable: 3 }],
      data: [{ objectType: "Project", primaryKey: "WDR101", title: "运维支持", pending: ["选择项目类型", "制定里程碑计划"] }]
    });
    expect(html).toContain("1 个对象待维护");
    expect(html).toContain("已维护 1 / 适用 3");
    expect(html).toContain("选择项目类型、制定里程碑计划");
  });
});
