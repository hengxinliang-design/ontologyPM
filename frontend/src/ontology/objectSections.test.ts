import { describe, expect, it } from "vitest";
import { isEmptyValue, propertySections } from "./objectSections";
import type { OntologyObjectType, OntologyProperty, SourceKind } from "./types";

function prop(apiName: string, sourceKind: SourceKind = "system"): OntologyProperty {
  return { apiName, displayName: apiName, dataType: "string", sourceKind, source: "", description: "", marking: null, readable: true, enumValues: [], editedBy: [], derivedFrom: null, searchable: false };
}

function type(apiName: string, names: string[], kinds: SourceKind[] = []): OntologyObjectType {
  return { apiName, primaryKey: names[0], titleProperty: names[1], properties: names.map((name, i) => prop(name, kinds[i] ?? "system")) } as unknown as OntologyObjectType;
}

describe("propertySections", () => {
  it("groups configured properties, keeps unlisted ones under 其他 and leaves out key and title", () => {
    const sections = propertySections(type("Project", ["projectNumber", "subject", "riskLevel", "contractValue", "reportedPersonDays", "brandNewProperty"]));
    expect(sections.map(s => s.title)).toEqual(["状态概览", "合同与回款", "预算与工时", "其他"]);
    const shown = sections.flatMap(s => s.properties.map(p => p.apiName));
    expect(shown).toEqual(["riskLevel", "contractValue", "reportedPersonDays", "brandNewProperty"]);
  });

  it("falls back to one section for small types and to source kinds for large ones", () => {
    expect(propertySections(type("Milestone", ["id", "name", "a", "b"])).map(s => s.title)).toEqual(["属性"]);
    const names = Array.from({ length: 20 }, (_, i) => `p${i}`);
    const kinds = names.map((_, i) => (["system", "manual", "derived"] as SourceKind[])[i % 3]);
    const sections = propertySections(type("Large", names, kinds));
    expect(sections.map(s => s.title)).toEqual(["系统接口数据", "人工维护数据", "派生计算"]);
    expect(sections.reduce((n, s) => n + s.properties.length, 0)).toBe(18);
  });

  it("treats null, blank and empty lists as empty but not zero", () => {
    expect([null, undefined, "", []].every(isEmptyValue)).toBe(true);
    expect(isEmptyValue(0) || isEmptyValue("0.00")).toBe(false);
  });
});
