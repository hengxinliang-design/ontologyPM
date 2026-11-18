import { renderToStaticMarkup } from "react-dom/server";
import { beforeAll, describe, expect, it } from "vitest";

let RevenueSources: typeof import("./DepartmentOperations")["RevenueSources"];
beforeAll(async () => {
  (globalThis as unknown as { window: unknown }).window ??= { localStorage: { getItem: () => null, setItem: () => undefined, removeItem: () => undefined } };
  ({ RevenueSources } = await import("./DepartmentOperations"));
});

// D68 (user 2026-11-18): 经营收入 says where it comes from, and why it is empty when it is.
describe("经营收入 sources", () => {
  it("lists only the sources that contribute, in 万", () => {
    const html = renderToStaticMarkup(<RevenueSources sources={{ confirmed: "0.00", crm_changes: "200000.00", opening_estimate: "12455000.00" }} />);
    expect(html).toContain("CRM 回款变动 ¥20 万");
    expect(html).toContain("估算 ¥1,245.5 万");
    expect(html).not.toContain("系统确认到账");
  });

  it("explains an empty revenue instead of a bare ¥0", () => {
    const html = renderToStaticMarkup(<RevenueSources sources={{ confirmed: "0", crm_changes: "0", opening_estimate: "0" }} />);
    expect(html).toContain("本年尚无回款");
  });
});
