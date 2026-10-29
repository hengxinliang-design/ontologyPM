import type { OntologyObjectType, OntologyProperty, SourceKind } from "./types";

// Display layout for the object detail panel: properties grouped into business sections.
// Presentation only: the ontology stays the source of names, types and provenance.
// Properties not listed land in 「其他」, so a new property is never hidden.
const SECTIONS: Record<string, Array<[string, string[]]>> = {
  Project: [
    ["状态概览", ["riskLevel", "lifecycleStatus", "budgetUtilization", "maintenanceComplete", "riskReasons"]],
    ["基本信息", ["customerName", "crmOwner", "projectManager", "masterCategory", "signedYear", "category", "contractType", "lifecycleSource", "plannedStart", "plannedEnd", "hoursPerPersonDay", "notes"]],
    ["合同与回款", ["contractCount", "opportunityCount", "contractValue", "invoicedAmount", "crmReceivedAmount", "outstandingReceivableAmount", "revenueSettlementBasis", "receivedAmount", "paymentTermCount", "unlinkedPaymentTermCount", "paymentNodeTotal", "overduePaymentNodes"]],
    ["预算与工时", ["plannedPersonDays", "reportedPersonDays", "customerConfirmedPersonDays", "confirmationGapPersonDays"]],
    ["里程碑与干预", ["milestoneTotal", "milestoneCompleted", "overdueMilestones", "openInterventions"]],
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

/** Long values (reason lists, JSON) take a full row instead of a half-width cell. */
export function isWideProperty(property: OntologyProperty): boolean {
  return property.dataType === "json" || property.apiName === "notes" || property.apiName.endsWith("Reason");
}

export function isEmptyValue(value: unknown): boolean {
  return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
}
