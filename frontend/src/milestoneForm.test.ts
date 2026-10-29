import { describe, expect, it } from "vitest";
import type { MilestoneInput } from "./api";
import { milestonePayload, showsLegacyPaymentNote } from "./milestoneForm";

const form: MilestoneInput = {
  name: "验收", planned_start: "2026-12-01", planned_end: "2026-12-20", owner: "", deliverable: "",
  is_payment_node: true, payment_date: "2026-12-20", payment_amount: "5000.00", payment_term_id: ""
};

describe("milestonePayload", () => {
  it("unlinks a linked milestone explicitly when the user picks 不关联", () => {
    expect(milestonePayload(form, "term_1").payment_term_id).toBeNull();
  });

  it("keeps the chosen payment term", () => {
    expect(milestonePayload({ ...form, payment_term_id: "term_2" }, "term_1").payment_term_id).toBe("term_2");
  });

  it("leaves legacy payment milestones and new milestones without a term untouched", () => {
    const legacy = milestonePayload(form, null);
    expect(legacy.payment_term_id).toBeUndefined();
    expect(legacy.is_payment_node).toBe(true);
    expect(milestonePayload({ ...form, is_payment_node: false }).payment_term_id).toBeUndefined();
  });
});

describe("showsLegacyPaymentNote", () => {
  it("is shown for legacy payment milestones only", () => {
    expect(showsLegacyPaymentNote(form, null)).toBe(true);
    expect(showsLegacyPaymentNote(form, "term_1")).toBe(false);
    expect(showsLegacyPaymentNote({ ...form, payment_term_id: "term_1" }, "term_1")).toBe(false);
  });
});
