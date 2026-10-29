import { describe, expect, it } from "vitest";
import { buildObjectSet, conditionClause, groupLabel, linksFor, presets, typeAt } from "./objectSetBuilder";
import type { Step } from "./objectSetBuilder";
import type { OntologyProperty, OntologySchema } from "./types";

function prop(apiName: string, dataType: OntologyProperty["dataType"], enumValues: OntologyProperty["enumValues"] = []): OntologyProperty {
  return {
    apiName, displayName: apiName, dataType, sourceKind: "system", source: "", description: "",
    marking: null, readable: true, enumValues, editedBy: [], derivedFrom: null, searchable: false
  };
}

const schema = {
  apiName: "tsg-pm", displayName: "", version: "1.1.0", checksum: "", description: "",
  valueTypes: [], markings: [], actionTypes: [], functions: [],
  objectTypes: [
    { apiName: "Project", displayName: "项目", pluralDisplayName: "项目", description: "", primaryKey: "projectNumber", titleProperty: "subject",
      backingDatasets: [], datasource: "projects", metadataSource: "", readPermissions: [], icon: "",
      properties: [prop("projectNumber", "string"), prop("riskLevel", "enum", [{ value: "critical", label: "严重" }]), prop("contractValue", "money"), prop("maintenanceComplete", "boolean")] },
    { apiName: "Employee", displayName: "员工", pluralDisplayName: "员工", description: "", primaryKey: "employeeId", titleProperty: "employeeName",
      backingDatasets: [], datasource: "employees", metadataSource: "", readPermissions: [], icon: "", properties: [prop("employeeId", "string")] },
    { apiName: "Milestone", displayName: "里程碑", pluralDisplayName: "里程碑", description: "", primaryKey: "milestoneId", titleProperty: "name",
      backingDatasets: [], datasource: "milestones", metadataSource: "", readPermissions: [], icon: "", properties: [prop("milestoneId", "string")] }
  ],
  linkTypes: [
    { apiName: "team", reverseApiName: "workedProjects", displayName: "项目团队", reverseDisplayName: "参与项目", sourceObjectType: "Project", targetObjectType: "Employee", cardinality: "many_to_many", description: "", sourceKey: null, targetKey: null, via: "TimeEntry" },
    { apiName: "milestones", reverseApiName: "project", displayName: "里程碑", reverseDisplayName: "所属项目", sourceObjectType: "Project", targetObjectType: "Milestone", cardinality: "one_to_many", description: "", sourceKey: "projectNumber", targetKey: "projectNumber", via: null }
  ]
} satisfies OntologySchema;

describe("linksFor", () => {
  it("returns forward and reverse link names for an object type", () => {
    expect(linksFor(schema, "Project").map(l => l.apiName)).toEqual(["team", "milestones"]);
    expect(linksFor(schema, "Employee")).toEqual([{ apiName: "workedProjects", displayName: "参与项目", target: "Project" }]);
    expect(linksFor(schema, "Unknown")).toEqual([]);
  });
});

describe("conditionClause", () => {
  it("maps builder operators to OSDK where clauses", () => {
    const risk = schema.objectTypes[0].properties[1];
    expect(conditionClause(risk, { field: "riskLevel", op: "eq", value: " critical " })).toEqual({ type: "eq", field: "riskLevel", value: "critical" });
    expect(conditionClause(risk, { field: "riskLevel", op: "isNull", value: "" })).toEqual({ type: "isNull", field: "riskLevel", value: true });
    expect(conditionClause(risk, { field: "riskLevel", op: "notNull", value: "" })).toEqual({ type: "isNull", field: "riskLevel", value: false });
    expect(conditionClause(risk, { field: "riskLevel", op: "in", value: "critical，warning, ,healthy" }))
      .toEqual({ type: "in", field: "riskLevel", value: ["critical", "warning", "healthy"] });
  });

  it("converts boolean literals", () => {
    const flag = schema.objectTypes[0].properties[3];
    expect(conditionClause(flag, { field: "maintenanceComplete", op: "eq", value: "true" }).value).toBe(true);
    expect(conditionClause(flag, { field: "maintenanceComplete", op: "eq", value: "false" }).value).toBe(false);
  });
});

describe("buildObjectSet", () => {
  it("starts from a base object set", () => {
    expect(buildObjectSet(schema, "Project", [])).toEqual({ objectSet: { type: "base", objectType: "Project" }, resultType: "Project" });
  });

  it("wraps filters and search-arounds in order and tracks the result type", () => {
    const steps: Step[] = [
      { kind: "filter", mode: "and", conditions: [{ field: "riskLevel", op: "eq", value: "critical" }] },
      { kind: "searchAround", link: "team" }
    ];
    expect(buildObjectSet(schema, "Project", steps)).toEqual({
      resultType: "Employee",
      objectSet: {
        type: "searchAround", link: "team",
        objectSet: { type: "filter", objectSet: { type: "base", objectType: "Project" }, where: { type: "eq", field: "riskLevel", value: "critical" } }
      }
    });
  });

  it("combines several conditions with the selected mode and skips incomplete ones", () => {
    const steps: Step[] = [{ kind: "filter", mode: "or", conditions: [
      { field: "riskLevel", op: "eq", value: "critical" },
      { field: "contractValue", op: "gt", value: "1000" },
      { field: "contractValue", op: "lt", value: "  " },
      { field: "", op: "eq", value: "x" }
    ] }];
    const { objectSet } = buildObjectSet(schema, "Project", steps);
    expect(objectSet.where).toEqual({ type: "or", value: [
      { type: "eq", field: "riskLevel", value: "critical" },
      { type: "gt", field: "contractValue", value: "1000" }
    ] });
  });

  it("ignores empty filter steps and unknown links", () => {
    const steps: Step[] = [{ kind: "filter", mode: "and", conditions: [] }, { kind: "searchAround", link: "friends" }];
    expect(buildObjectSet(schema, "Project", steps)).toEqual({ objectSet: { type: "base", objectType: "Project" }, resultType: "Project" });
  });
});

describe("typeAt", () => {
  it("returns the object type a step operates on", () => {
    const steps: Step[] = [{ kind: "searchAround", link: "milestones" }, { kind: "filter", mode: "and", conditions: [] }, { kind: "searchAround", link: "project" }];
    expect(typeAt(schema, "Project", steps, 0)).toBe("Project");
    expect(typeAt(schema, "Project", steps, 1)).toBe("Milestone");
    expect(typeAt(schema, "Project", steps, 3)).toBe("Project");
  });
});

describe("presets", () => {
  it("only uses supported object set and where types", () => {
    const setTypes = new Set(["base", "static", "filter", "union", "intersect", "subtract", "searchAround"]);
    const whereTypes = new Set(["eq", "in", "gt", "gte", "lt", "lte", "isNull", "startsWith", "contains", "containsAnyTerm", "containsAllTerms", "and", "or", "not"]);
    const visit = (node: unknown, kinds: Set<string>): void => {
      if (!node || typeof node !== "object") return;
      const record = node as Record<string, unknown>;
      if (typeof record.type === "string") expect(kinds.has(record.type)).toBe(true);
      if (record.objectSet) visit(record.objectSet, setTypes);
      if (Array.isArray(record.objectSets)) record.objectSets.forEach(s => visit(s, setTypes));
      if (record.where) visit(record.where, whereTypes);
      if (Array.isArray(record.value)) record.value.forEach(v => visit(v, whereTypes));
      if (record.type === "not") visit(record.value, whereTypes);
    };
    const list = presets();
    expect(list.length).toBeGreaterThanOrEqual(5);
    list.forEach(preset => visit(preset.objectSet, setTypes));
  });
});

describe("groupLabel", () => {
  it("labels enum values, ranges and empty groups", () => {
    const risk = schema.objectTypes[0].properties[1];
    expect(groupLabel(risk, "critical")).toBe("严重");
    expect(groupLabel(risk, "other")).toBe("other");
    expect(groupLabel(undefined, null)).toBe("（空）");
    expect(groupLabel(undefined, { startValue: 0, endValue: null })).toBe("0 ~ +∞");
  });
});
