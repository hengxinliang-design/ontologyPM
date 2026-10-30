import type { OntologyObjectType, OntologyProperty } from "./types";

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

/**
 * Month parameters for an object list. Only types with a statistics period take a month (right after switching
 * types the previous type's month is still in state), and only range periods take an end month.
 */
export function periodParams(type: OntologyObjectType, month: string, monthTo: string): { month?: string; monthTo?: string } {
  if (!type.period || !month) return {};
  return type.period.range && monthTo ? { month, monthTo } : { month };
}

/** List columns: the declared listProperties, else the first four plain properties; the first four periodic ones when a period is selected. */
export function displayColumns(type: OntologyObjectType, periodic = false): OntologyProperty[] {
  if (!periodic && type.listProperties?.length) {
    return type.listProperties.flatMap(name => type.properties.filter(p => p.apiName === name));
  }
  return type.properties
    .filter(p => p.apiName !== type.primaryKey && p.apiName !== type.titleProperty && p.dataType !== "json")
    .filter(p => !periodic || p.periodic)
    .slice(0, 4);
}

const STATUS_TONE: Record<string, Record<string, string>> = {
  // Lifecycle status of milestones, payment nodes and interventions: finished is green, anything else neutral.
  status: { completed: "healthy", paid: "healthy", closed: "healthy" },
  healthStatus: { overloaded: "critical", fatigue: "warning", fragmented: "warning", idle: "baseline", first_month: "baseline", healthy: "healthy" },
};

/** Suffix from the property's declared dimension; counts and identifiers read best without one. */
export function unitSuffix(property: OntologyProperty): string {
  if (!property.unitLabel || property.unit === "count" || property.unit === "none") return "";
  return ` ${property.unitLabel}`;
}

/** JSON values (reason lists, {reasons, dataFlags}) read as a list of lines, not raw JSON. */
function jsonLines(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(jsonLines);
  if (value && typeof value === "object") return Object.values(value as Record<string, unknown>).flatMap(item => typeof item === "object" ? jsonLines(item) : []);
  return value === null || value === undefined || value === "" ? [] : [String(value)];
}

export function PropertyValue({ property, value, redacted }: { property: OntologyProperty; value: unknown; redacted: boolean }) {
  if (redacted) return <span className="redacted" title={`需要 ${property.marking} 标记权限`}>🔒 无权查看</span>;
  if (value === null || value === undefined || value === "") return <span className="muted">—</span>;
  switch (property.dataType) {
    case "money": return <span className="money">¥{Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>;
    case "percent": return <>{String(value)}%</>;
    case "boolean": return <>{value ? "是" : "否"}</>;
    case "enum": {
      const label = property.enumValues.find(v => v.value === value)?.label ?? String(value);
      if (property.apiName === "riskLevel") return <span className={`risk ${String(value)}`}>{label}</span>;
      const tone = STATUS_TONE[property.apiName]?.[String(value)] ?? (property.apiName === "status" ? "baseline" : undefined);
      return tone ? <span className={`health-tag ${tone}`}>{label}</span> : <>{label}</>;
    }
    case "datetime": return <>{formatTime(String(value))}</>;
    case "json": {
      if (property.apiName.endsWith("rojectEffortShares") && Array.isArray(value)) return <EffortShares rows={value as EffortShare[]} />;
      const lines = jsonLines(value);
      if (!lines.length) return <span className="muted">—</span>;
      if (lines.length === 1 || lines.every(line => line.length <= 12)) return <>{lines.join("、")}</>;
      return <ul className="value-list">{lines.map((line, index) => <li key={index}>{line}</li>)}</ul>;
    }
    default: return <>{String(value)}{unitSuffix(property)}</>;
  }
}

interface EffortShare { projectNumber: string; projectName: string | null; internal: boolean; hours: string; sharePercent: string | null }

/** Employee.projectEffortShares: hours per project code and its share of the employee's effective hours. */
export function EffortShares({ rows }: { rows: EffortShare[] }) {
  if (!rows.length) return <span className="muted">—</span>;
  return <table className="effort-shares"><thead><tr><th>项目</th><th>工时</th><th>占比</th></tr></thead><tbody>
    {rows.map(row => <tr key={row.projectNumber}>
      <td><strong>{row.projectNumber}</strong>{row.internal && <em className="internal-tag">内部</em>}{row.projectName && <small title={row.projectName}>{row.projectName}</small>}</td>
      <td>{row.hours} 小时</td>
      <td><span className="share-bar"><i style={{ width: `${Math.min(100, Number(row.sharePercent ?? 0))}%` }} className={row.internal ? "internal" : undefined} /></span>{row.sharePercent ?? "—"}%</td>
    </tr>)}
  </tbody></table>;
}

export function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}
