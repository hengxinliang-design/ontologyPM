import { describe, expect, it } from "vitest";
import { matches, pageOf } from "./listQuery";

const items = Array.from({ length: 23 }, (_, i) => ({ code: `OPP${String(i + 1).padStart(3, "0")}`, customer: i % 2 ? "武汉帝尔" : "仓储客户" }));
const text = (item: (typeof items)[number]) => `${item.code} ${item.customer}`;

describe("investment list search and paging", () => {
  it("pages of 10, clamped to the last page", () => {
    expect(pageOf(items, "", 1, text)).toMatchObject({ total: 23, pages: 3, page: 1 });
    expect(pageOf(items, "", 3, text).visible.map(i => i.code)).toEqual(["OPP021", "OPP022", "OPP023"]);
    expect(pageOf(items, "", 9, text).page).toBe(3);
  });

  it("searches code, name and customer; every word must match", () => {
    expect(pageOf(items, "武汉", 1, text).total).toBe(11);
    expect(pageOf(items, "opp00 武汉", 1, text).visible.map(i => i.code)).toEqual(["OPP002", "OPP004", "OPP006", "OPP008"]);
    expect(matches("WDR101 武汉帝尔激光", "wdr 激光")).toBe(true);
    expect(pageOf(items, "不存在", 1, text)).toMatchObject({ total: 0, pages: 1, visible: [] });
  });
});
