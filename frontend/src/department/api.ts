// Department analytics are derived in the ontology; annual plans are base data maintained over REST.
import { ontologyAction, ontologyFunction } from "../api";
import type { DepartmentReview, PlanField, ResourceHealthResponse } from "./types";

export async function fetchDepartmentReview(year?: number, month?: string): Promise<DepartmentReview> {
  return ontologyFunction<DepartmentReview>("departmentOperatingReview", { year, month });
}

export async function fetchResourceHealth(month?: string): Promise<ResourceHealthResponse> {
  return ontologyFunction<ResourceHealthResponse>("resourceHealth", { month });
}

export async function saveAnnualPlan(year: number, payload: Partial<Record<PlanField, string | null>> & { notes?: string | null }): Promise<void> {
  // Annual targets are maintained through the ontology action setAnnualOperatingPlan.
  await ontologyAction("setAnnualOperatingPlan", {
    policy: "default", year, newContractTarget: payload.new_contract_target, collectionTarget: payload.collection_target,
    capacityPersonDaysTarget: payload.capacity_person_days_target, headcountTarget: payload.headcount_target,
    utilizationTargetPercent: payload.utilization_target_percent, notes: payload.notes
  });
}
