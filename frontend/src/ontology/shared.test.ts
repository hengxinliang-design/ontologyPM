import { afterEach, describe, expect, it, vi } from "vitest";
import { displayColumns, errorMessage, formatTime, periodParams, unitSuffix } from "./shared";
import type { OntologyObjectType, OntologyProperty } from "./types";

function prop(apiName: string, dataType: OntologyProperty["dataType"]): OntologyProperty {
  return {
    apiName, displayName: apiName, dataType, sourceKind: "system", source: "", description: "",
    marking: null, readable: true, enumValues: [], editedBy: [], derivedFrom: null, searchable: false
  };
}

describe("shared helpers", () => {
  it("prefers error messages and falls back otherwise", () => {
    expect(errorMessage(new Error("boom"), "fallback")).toBe("boom");
    expect(errorMessage("boom", "fallback")).toBe("fallback");
  });

  it("formats ISO timestamps and leaves invalid values untouched", () => {
    expect(formatTime("not a date")).toBe("not a date");
    expect(formatTime("2026-10-23T08:00:00Z")).toMatch(/2026/);
  });

  it("picks up to four display columns, skipping key, title and JSON properties", () => {
    const type = {
      apiName: "Project", primaryKey: "id", titleProperty: "title",
      properties: [prop("id", "string"), prop("title", "string"), prop("a", "string"), prop("reasons", "json"), prop("b", "money"), prop("c", "date"), prop("d", "enum"), prop("e", "integer")]
    } as unknown as OntologyObjectType;
    expect(displayColumns(type).map(p => p.apiName)).toEqual(["a", "b", "c", "d"]);
  });

  it("uses declared list columns unless a period view asks for periodic ones", () => {
    const type = {
      apiName: "Milestone", primaryKey: "id", titleProperty: "title", listProperties: ["project", "status", "seq", "start", "end"],
      properties: [prop("id", "string"), prop("title", "string"), prop("project", "string"), prop("seq", "integer"), prop("start", "date"), prop("end", "date"), prop("status", "enum")]
    } as unknown as OntologyObjectType;
    expect(displayColumns(type).map(p => p.apiName)).toEqual(["project", "status", "seq", "start", "end"]);
  });

  it("sends month parameters only to types with a statistics period", () => {
    const plain = { apiName: "Milestone", period: null } as unknown as OntologyObjectType;
    const single = { apiName: "Employee", period: { range: false } } as unknown as OntologyObjectType;
    const ranged = { apiName: "Milestone", period: { range: true } } as unknown as OntologyObjectType;
    expect(periodParams(plain, "2026-09", "")).toEqual({});
    expect(periodParams(single, "2026-09", "2026-10")).toEqual({ month: "2026-09" });
    expect(periodParams(ranged, "2026-10", "2026-12")).toEqual({ month: "2026-10", monthTo: "2026-12" });
    expect(periodParams(ranged, "", "2026-12")).toEqual({});
  });

  it("shows periodic columns when a month is selected", () => {
    const periodic = (apiName: string) => ({ ...prop(apiName, "decimal"), periodic: true });
    const type = {
      apiName: "Employee", primaryKey: "id", titleProperty: "title",
      properties: [prop("id", "string"), prop("title", "string"), prop("total", "decimal"), periodic("month"), periodic("hours"), prop("count", "integer")]
    } as unknown as OntologyObjectType;
    expect(displayColumns(type).map(p => p.apiName)).toEqual(["total", "month", "hours", "count"]);
    expect(displayColumns(type, true).map(p => p.apiName)).toEqual(["month", "hours"]);
  });
});

describe("api client", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

  it("attaches the stored session token and surfaces problem details", async () => {
    const storage = new Map<string, string>([["tsg-pm-session-token", "abc"]]);
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => storage.get(key) ?? null,
        setItem: (key: string, value: string) => storage.set(key, value),
        removeItem: (key: string) => storage.delete(key)
      }
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { version: "1.1.0" } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "参数错误", errors: { parameterErrors: [{ parameter: "x" }] } }), { status: 422 }));
    vi.stubGlobal("fetch", fetchMock);
    const { request, ApiError } = await import("../api");

    await expect(request("/api/v1/ontology/schema")).resolves.toEqual({ data: { version: "1.1.0" } });
    expect(fetchMock.mock.calls[0][1].headers).toMatchObject({ Authorization: "Bearer abc" });

    const failure = await request("/api/v1/ontology/actions/x/apply").catch((err: InstanceType<typeof ApiError>) => err) as InstanceType<typeof ApiError> & { body: { errors: { parameterErrors: { parameter: string }[] } } };
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.status).toBe(422);
    expect(failure.message).toBe("参数错误");
    expect(failure.body.errors.parameterErrors[0].parameter).toBe("x");
  });

  it("takes the unit suffix from the declared dimension, not from the property name", () => {
    const withUnit = (unit: string, unitLabel: string) => ({ ...prop("budget", "decimal"), unit, unitLabel });
    expect(unitSuffix(withUnit("personDay", "人天"))).toBe(" 人天");
    expect(unitSuffix(withUnit("percentagePoint", "个百分点"))).toBe(" 个百分点");
    expect(unitSuffix(withUnit("count", "个"))).toBe("");
    expect(unitSuffix(withUnit("none", ""))).toBe("");
    expect(unitSuffix(prop("costAheadPoints", "decimal"))).toBe(""); // a name alone no longer implies a unit
  });
});
