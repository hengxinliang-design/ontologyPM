import { describe, expect, it } from "vitest";
import { whenUsed } from "./time";

describe("when a conversation was last used", () => {
  const now = new Date(2026, 10, 9, 15, 0);
  it("says today and yesterday with the time, older ones by date", () => {
    expect(whenUsed(new Date(2026, 10, 9, 9, 5).toISOString(), now)).toBe("今天 09:05");
    expect(whenUsed(new Date(2026, 10, 8, 23, 30).toISOString(), now)).toBe("昨天 23:30");
    expect(whenUsed(new Date(2026, 9, 31, 8, 0).toISOString(), now)).toBe("2026-10-31");
  });
  it("shows nothing for a bad timestamp", () => {
    expect(whenUsed("not a date", now)).toBe("");
  });
});
