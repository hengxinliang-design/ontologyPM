import { describe, expect, it } from "vitest";
import { ROLE_OPTIONS, defaultMenus, menuAllowed, projectManagerHint, roleOption } from "./roles";

// Roles and data scope (ontology 7.7.0, build package 2026-10-30-roles-and-data-scope.yaml, R1 and R5).
describe("roles", () => {
  it("offers the five roles of requirements baseline D9 with their data scope", () => {
    expect(ROLE_OPTIONS.map(option => option.value)).toEqual(["admin", "pmo", "department_head", "project_manager", "analyst"]);
    expect(ROLE_OPTIONS.filter(option => option.scope === "own").map(option => option.value)).toEqual(["project_manager"]);
    expect(roleOption("analyst")?.menus).toContain("ontology");
    expect(roleOption("pmo")?.menus).not.toContain("ontology");
  });

  it("gives a role its default menus as a fresh list", () => {
    const menus = defaultMenus("department_head");
    expect(menus).toEqual(["health", "department", "resource", "investment", "crm"]);
    menus.push("sync");
    expect(defaultMenus("department_head")).not.toContain("sync");
  });

  it("keeps a project manager to the project menus", () => {
    expect(menuAllowed("project_manager", "projects")).toBe(true);
    expect(menuAllowed("project_manager", "health")).toBe(true);
    expect(menuAllowed("project_manager", "crm")).toBe(false);
    expect(menuAllowed("pmo", "crm")).toBe(true);
    expect(menuAllowed(null, "crm")).toBe(true);
  });
});

describe("project manager hint", () => {
  it("warns only when a typed name has no project-manager account", () => {
    expect(projectManagerHint("刘浪", ["刘浪"])).toBeNull();
    expect(projectManagerHint(" 刘浪 ", ["刘浪"])).toBeNull();
    expect(projectManagerHint("", ["刘浪"])).toBeNull();
    expect(projectManagerHint("外包经理", ["刘浪"])).toBe("该项目经理没有账号，登录后看不到本项目");
  });
});
