import { describe, expect, it } from "vitest";
import { periodRange, previousMonth } from "./period";

describe("investment period", () => {
  it("defaults to this year so far: January to the current month", () => {
    expect(periodRange("ytd", "", "", "", "2026-11")).toEqual({ startMonth: "2026-01", endMonth: "2026-11" });
    expect(periodRange("ytd", "", "", "", "2027-01")).toEqual({ startMonth: "2027-01", endMonth: "2027-01" });
  });

  it("a single month, a month range, or all periods", () => {
    expect(periodRange("month", "2026-07", "", "", "2026-11")).toEqual({ startMonth: "2026-07", endMonth: "2026-07" });
    expect(periodRange("range", "", "2025-10", "2026-03", "2026-11")).toEqual({ startMonth: "2025-10", endMonth: "2026-03" });
    expect(periodRange("range", "", "2025-10", "", "2026-11")).toEqual({}), "an unfinished range does not query";
    expect(periodRange("all", "2026-07", "2025-10", "2026-03", "2026-11")).toEqual({});
  });

  it("单月 starts on the previous, complete month", () => {
    expect(previousMonth("2026-11")).toBe("2026-10");
    expect(previousMonth("2027-01")).toBe("2026-12");
  });
});
