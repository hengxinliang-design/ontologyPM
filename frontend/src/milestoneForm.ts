import type { MilestoneInput } from "./api";

/**
 * Milestone form → editMilestone / createMilestone input.
 *
 * editMilestone unlinks a payment term only when it receives an explicit empty paymentTermId; an omitted one means
 * "unchanged". So when the user clears the link of a milestone that had one, send null (its payment node goes).
 * A milestone that never had a link keeps omitting it, so a legacy payment event (ontology 7.0.0 no longer creates
 * them) stays as it is.
 */
export function milestonePayload(form: MilestoneInput, linkedTermId?: string | null): MilestoneInput {
  if (form.payment_term_id) return form;
  if (linkedTermId) return { ...form, payment_term_id: null };
  return { ...form, payment_term_id: undefined };
}

/** The legacy-payment note describes payment events kept without a payment term, not a link the user is clearing. */
export function showsLegacyPaymentNote(form: MilestoneInput, linkedTermId?: string | null): boolean {
  return form.is_payment_node && !form.payment_term_id && !linkedTermId;
}
