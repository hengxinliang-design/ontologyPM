import type { ObjectSetSpec, OntologyProperty, OntologySchema } from "./types";

export type Operator = "eq" | "in" | "gt" | "gte" | "lt" | "lte" | "isNull" | "notNull" | "startsWith" | "containsAnyTerm" | "contains";
export interface Condition { field: string; op: Operator; value: string }
export type Step =
  | { kind: "filter"; mode: "and" | "or"; conditions: Condition[] }
  | { kind: "searchAround"; link: string };

export const OPERATORS: Array<{ value: Operator; label: string; types?: string[] }> = [
  { value: "eq", label: "等于" },
  { value: "in", label: "属于（逗号分隔）" },
  { value: "gt", label: "大于", types: ["integer", "decimal", "money", "percent", "date", "datetime"] },
  { value: "gte", label: "大于等于", types: ["integer", "decimal", "money", "percent", "date", "datetime"] },
  { value: "lt", label: "小于", types: ["integer", "decimal", "money", "percent", "date", "datetime"] },
  { value: "lte", label: "小于等于", types: ["integer", "decimal", "money", "percent", "date", "datetime"] },
  { value: "isNull", label: "为空" },
  { value: "notNull", label: "不为空" },
  { value: "startsWith", label: "开头是", types: ["string", "enum"] },
  { value: "containsAnyTerm", label: "包含任一词", types: ["string"] },
  { value: "contains", label: "包含元素", types: ["json"] }
];
export function today(): string { return new Date().toISOString().slice(0, 10); }

export function presets(): Array<{ label: string; description: string; objectSet: ObjectSetSpec }> {
  const projects = { type: "base", objectType: "Project" };
  const critical = { type: "filter", objectSet: projects, where: { type: "eq", field: "riskLevel", value: "critical" } };
  return [
    { label: "严重风险项目", description: "filter：riskLevel = critical", objectSet: critical },
    {
      label: "逾期里程碑 → 所属项目 → 项目团队",
      description: "过滤里程碑后两次 searchAround，回答“哪些人在逾期项目上投入”",
      objectSet: {
        type: "searchAround", link: "team",
        objectSet: {
          type: "searchAround", link: "project",
          objectSet: {
            type: "filter", objectSet: { type: "base", objectType: "Milestone" },
            where: { type: "and", value: [
              { type: "lt", field: "plannedEnd", value: today() },
              { type: "not", value: { type: "eq", field: "status", value: "completed" } }
            ] }
          }
        }
      }
    },
    {
      label: "系统实施/TSnT365 且未设预算",
      description: "and + in + isNull：需要补人工维护的项目",
      objectSet: { type: "filter", objectSet: projects, where: { type: "and", value: [
        { type: "in", field: "contractType", value: ["construction", "maintenance"] },
        { type: "isNull", field: "plannedPersonDays", value: true }
      ] } }
    },
    {
      label: "（严重风险 ∪ 开口合同）− 售前阶段",
      description: "集合运算：union + subtract",
      objectSet: { type: "subtract", objectSets: [
        { type: "union", objectSets: [critical, { type: "filter", objectSet: projects, where: { type: "eq", field: "contractType", value: "open_ended" } }] },
        { type: "filter", objectSet: projects, where: { type: "eq", field: "contractType", value: "sales" } }
      ] }
    },
    {
      label: "客户 → 项目 → 回款节点",
      description: "从客户沿关系检索到全部回款计划",
      objectSet: { type: "searchAround", link: "paymentNodes", objectSet: { type: "searchAround", link: "projects", objectSet: { type: "base", objectType: "Customer" } } }
    }
  ];
}

export function linksFor(schema: OntologySchema, objectType: string) {
  const result: Array<{ apiName: string; displayName: string; target: string }> = [];
  for (const link of schema.linkTypes) {
    if (link.sourceObjectType === objectType) result.push({ apiName: link.apiName, displayName: link.displayName, target: link.targetObjectType });
    if (link.targetObjectType === objectType) result.push({ apiName: link.reverseApiName, displayName: link.reverseDisplayName, target: link.sourceObjectType });
  }
  return result;
}

export function conditionClause(prop: OntologyProperty | undefined, condition: Condition): Record<string, unknown> {
  const literal = (raw: string): unknown => {
    if (prop?.dataType === "boolean") return raw === "true";
    return raw.trim();
  };
  switch (condition.op) {
    case "isNull": return { type: "isNull", field: condition.field, value: true };
    case "notNull": return { type: "isNull", field: condition.field, value: false };
    case "in": return { type: "in", field: condition.field, value: condition.value.split(/[,，]/).map(v => literal(v)).filter(v => v !== "") };
    default: return { type: condition.op, field: condition.field, value: literal(condition.value) };
  }
}

export function buildObjectSet(schema: OntologySchema, start: string, steps: Step[]): { objectSet: ObjectSetSpec; resultType: string } {
  let objectSet: ObjectSetSpec = { type: "base", objectType: start };
  let current = start;
  for (const step of steps) {
    if (step.kind === "searchAround") {
      const link = linksFor(schema, current).find(l => l.apiName === step.link);
      if (!link) continue;
      objectSet = { type: "searchAround", objectSet, link: link.apiName };
      current = link.target;
    } else {
      const type = schema.objectTypes.find(t => t.apiName === current);
      const clauses = step.conditions
        .filter(c => c.field && (c.op === "isNull" || c.op === "notNull" || c.value.trim() !== ""))
        .map(c => conditionClause(type?.properties.find(p => p.apiName === c.field), c));
      if (!clauses.length) continue;
      objectSet = { type: "filter", objectSet, where: clauses.length === 1 ? clauses[0] : { type: step.mode, value: clauses } };
    }
  }
  return { objectSet, resultType: current };
}

export function typeAt(schema: OntologySchema, start: string, steps: Step[], index: number): string {
  let current = start;
  for (const step of steps.slice(0, index)) {
    if (step.kind === "searchAround") current = linksFor(schema, current).find(l => l.apiName === step.link)?.target ?? current;
  }
  return current;
}

export function groupLabel(prop: OntologyProperty | undefined, value: unknown): string {
  if (value === null || value === undefined) return "（空）";
  if (typeof value === "object") {
    const range = value as { startValue?: unknown; endValue?: unknown };
    return `${range.startValue ?? "−∞"} ~ ${range.endValue ?? "+∞"}`;
  }
  return prop?.enumValues.find(v => v.value === value)?.label ?? String(value);
}
