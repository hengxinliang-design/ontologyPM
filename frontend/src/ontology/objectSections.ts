import type { OntologyObjectType, OntologyProperty, SourceKind } from "./types";

// Display layout for the object detail panel: properties grouped into business sections.
// Presentation only: the ontology stays the source of names, types and provenance.
// Properties not listed land in 「其他」, so a new property is never hidden.
const SECTIONS: Record<string, Array<[string, string[]]>> = {
  Project: [
    ["状态概览", ["riskLevel", "lifecycleStatus", "budgetUtilization", "maintenanceComplete", "riskReasons", "aiAttention"]],
    ["人工维护", ["maintenanceGapCount", "maintenanceChecklist"]],
    ["基本信息", ["customerName", "crmOwner", "projectManager", "masterCategory", "signedYear", "category", "contractType", "lifecycleSource", "plannedStart", "plannedEnd", "hoursPerPersonDay", "notes"]],
    ["合同与回款", ["contractCount", "opportunityCount", "contractValue", "invoicedAmount", "crmReceivedAmount", "outstandingReceivableAmount", "revenueSettlementBasis", "receivedAmount", "crmSyncPendingAmount", "paymentTermCount", "unlinkedPaymentTermCount", "paymentNodeTotal", "overduePaymentNodes"]],
    ["预算与工时", ["plannedPersonDays", "reportedPersonDays", "customerConfirmedPersonDays", "confirmationGapPersonDays"]],
    ["里程碑、干预与 AI", ["milestoneTotal", "milestoneCompleted", "overdueMilestones", "openInterventions", "overdueInterventions", "activeAiRiskFlags", "pendingAiProposals"]],
    ["投入成本", ["conversionDate", "opportunityOutcome", "costToWinPersonDays", "deliveryPersonDays", "atRiskPersonDays", "sunkPersonDays"]],
  ],
  CostObject: [
    ["商机结论", ["registration", "customerName", "opportunityOutcome", "outcomeSource", "outcomeReason", "outcomeDate", "winProbability", "conversionDate", "stalled", "presalesAgeDays"]],
    ["投入构成", ["totalPersonDays", "presalesPersonDays", "costToWinPersonDays", "costToWinShare", "deliveryPersonDays", "atRiskPersonDays", "sunkPersonDays", "expectedSunkPersonDays", "unregisteredPersonDays", "internalPersonDays", "estimatedCost"]],
    ["投入周期", ["firstWorkDate", "lastWorkDate", "employeeCount", "dataFlags"]],
  ],
  Employee: [
    ["月度工时", ["statMonth", "monthEntryCount", "monthReportedHours", "monthApprovedHours", "monthProjectCount", "monthProjectEffortShares"]],
    ["资源健康", ["healthStatus", "monthUtilizationPercent", "overtimeSharePercent", "consecutiveOverloadMonths", "concurrentProjectCount", "healthReasons"]],
    ["工时汇总（累计）", ["entryCount", "reportedHours", "approvedHours", "projectCount", "firstWorkDate", "lastWorkDate"]],
    ["项目投入占比（累计）", ["projectEffortShares"]],
  ],
  Customer: [
    ["CRM 汇总", ["contractCount", "opportunityCount", "projectCount", "totalContractValue", "totalReceivedAmount", "totalInvoicedAmount", "latestSignedAt", "crmOwners"]],
    ["本年度（按签约年度）", ["currentYearContractValue", "currentYearReceivedAmount", "currentYearInvoicedAmount"]],
  ],
  // 8.6.0-8.8.0 AI write-back: what it says and rests on, where it stands, who decided (user 2026-11-10: 再优化布局).
  AiProposal: [
    ["建议内容", ["kind", "projectNumber", "category", "proposedCategory", "detail", "parameters", "basis"]],
    ["状态与依据复核", ["status", "expiresOn", "basisHolds", "basisNote", "checkedAt"]],
    ["提交与审批", ["submittedBy", "submittedAt", "source", "sourceRef", "decidedBy", "decidedAt", "decisionNote", "resultId"]],
  ],
  AiRiskFlag: [
    ["风险内容", ["projectNumber", "detail", "basis"]],
    ["状态与依据复核", ["status", "validUntil", "basisHolds", "basisNote", "checkedAt"]],
    ["批准与关闭", ["proposalId", "approvedBy", "approvedAt", "closedBy", "closedAt", "closedReason"]],
  ],
  AiStrategyNote: [
    ["策略内容", ["category", "proposedCategory", "detail", "basis"]],
    ["状态与依据复核", ["status", "validUntil", "basisHolds", "basisNote", "checkedAt"]],
    ["批准与关闭", ["proposalId", "approvedBy", "approvedAt", "closedBy", "closedAt", "closedReason"]],
  ],
  Intervention: [
    ["干预", ["projectNumber", "interventionType", "status", "overdue", "owner", "dueDate", "notes"]],
    ["结果与来源", ["conclusion", "sourceProposalId", "updatedAt"]],
  ],
  OperatingPolicy: [
    ["工时与利用率", ["standardHoursPerDay", "standardWorkDaysPerYear", "attentionUtilizationPercent", "minimumUtilizationPercent", "excellentUtilizationPercent", "overloadUtilizationPercent"]],
    ["预算与商机", ["budgetAttentionPercent", "budgetHighRiskPercent", "stallDays", "lostStatuses", "internalCodes"]],
    ["年度计划", ["planLagTolerancePoints"]],
    ["资源健康", ["overtimeShareAttentionPercent", "overloadConsecutiveMonths", "maxConcurrentProjects"]],
  ],
};

const SOURCE_SECTION: Record<SourceKind, string> = { system: "系统接口数据", manual: "人工维护数据", derived: "派生计算" };

export interface PropertySection {
  title: string;
  properties: OntologyProperty[];
}

/** Sections for one object type. The primary key and title are already in the panel header and are left out. */
export function propertySections(type: OntologyObjectType): PropertySection[] {
  const shown = type.properties.filter(p => p.apiName !== type.primaryKey && p.apiName !== type.titleProperty);
  const byName = new Map(shown.map(p => [p.apiName, p]));
  const layout = SECTIONS[type.apiName];
  if (!layout) {
    if (shown.length <= 16) return [{ title: "属性", properties: shown }];
    return (["system", "manual", "derived"] as SourceKind[])
      .map(kind => ({ title: SOURCE_SECTION[kind], properties: shown.filter(p => p.sourceKind === kind) }))
      .filter(section => section.properties.length);
  }
  const used = new Set<string>();
  const sections = layout.map(([title, names]) => ({
    title,
    properties: names.flatMap(name => {
      const prop = byName.get(name);
      if (!prop || used.has(name)) return [];
      used.add(name);
      return [prop];
    }),
  })).filter(section => section.properties.length);
  const rest = shown.filter(p => !used.has(p.apiName));
  return rest.length ? [...sections, { title: "其他", properties: rest }] : sections;
}

// Free-text properties that usually run to sentences; they take a full row whatever their length.
const LONG_TEXT = new Set(["notes", "detail", "basisNote", "decisionNote", "conclusion", "description"]);
const WIDE_TEXT_CHARS = 40;

/** Long values (reason lists, JSON, sentences) take a full row instead of a half-width cell, which squeezed a
 * 300-character 说明 into a tall narrow column (user 2026-11-10). */
export function isWideProperty(property: OntologyProperty, value?: unknown): boolean {
  if (property.dataType === "json" || LONG_TEXT.has(property.apiName) || property.apiName.endsWith("Reason")) return true;
  return typeof value === "string" && value.length > WIDE_TEXT_CHARS;
}

export function isEmptyValue(value: unknown): boolean {
  return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
}
