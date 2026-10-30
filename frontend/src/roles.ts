import type { MenuPermission, UserRole } from "./types";

// Roles (requirements baseline D9): a role sets the data scope and the default menus; an administrator may still
// adjust menus per account. Mirrors backend/app/auth.py ROLES — the backend validates, this only guides the form.
export const ROLE_OPTIONS: { value: UserRole; label: string; scope: "all" | "own"; menus: MenuPermission[]; hint: string }[] = [
  { value: "admin", label: "管理员", scope: "all", menus: [], hint: "全部菜单与账户管理；唯一能在本体浏览器执行动作的角色" },
  { value: "pmo", label: "PMO", scope: "all", menus: ["projects", "investment", "department", "resource", "crm", "employeeWorktime", "sync"], hint: "全部项目，查看并维护" },
  { value: "department_head", label: "部门负责人", scope: "all", menus: ["health", "department", "resource", "investment", "crm"], hint: "全部项目，管理视角只读" },
  { value: "project_manager", label: "项目经理", scope: "own", menus: ["projects"], hint: "只看、只维护「项目经理」等于其姓名的项目" },
  { value: "analyst", label: "分析人员", scope: "all", menus: ["health", "department", "resource", "investment", "crm", "ontology"], hint: "全部项目只读，可用本体浏览器查询" }
];

// A project manager sees only their own projects, so the role keeps to the project menus.
export const PROJECT_MANAGER_MENUS: MenuPermission[] = ["projects", "health"];

export function roleOption(role: UserRole | null | undefined) {
  return ROLE_OPTIONS.find(option => option.value === role);
}

export function defaultMenus(role: UserRole): MenuPermission[] {
  return [...(roleOption(role)?.menus ?? [])];
}

export function menuAllowed(role: UserRole | null | undefined, menu: MenuPermission): boolean {
  return role !== "project_manager" || PROJECT_MANAGER_MENUS.includes(menu);
}

// D10: the project manager field is matched to an account's name exactly; a name without an account is allowed
// (outsourced or temporary managers) but that person will not see the project after logging in.
export function projectManagerHint(name: string, accountNames: string[]): string | null {
  const value = name.trim();
  if (!value || accountNames.includes(value)) return null;
  return "该项目经理没有账号，登录后看不到本项目";
}
