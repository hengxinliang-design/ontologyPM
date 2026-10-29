import { request } from "../api";
import type { ActionLogPage, ActionResult, ObjectActionAvailability, ObjectPage, OntologyObjectDetail, OntologySchema, OntologyVersion, VersionDiff, ObjectSetSpec, ObjectSetPage, AggregationResult, IndexStatus } from "./types";

const BASE = "/api/v1/ontology";
const enc = encodeURIComponent;

export async function fetchOntologySchema(): Promise<OntologySchema> {
  return (await request<{ data: OntologySchema }>(`${BASE}/schema`)).data;
}

export function fetchObjects(objectType: string, filters: { q?: string; page: number; pageSize: number; orderBy?: string; where?: Record<string, string>; month?: string; monthTo?: string }): Promise<ObjectPage> {
  const params = new URLSearchParams({ page: String(filters.page), pageSize: String(filters.pageSize) });
  if (filters.q) params.set("q", filters.q);
  if (filters.month) params.set("month", filters.month);
  if (filters.month && filters.monthTo) params.set("monthTo", filters.monthTo);
  for (const [field, value] of Object.entries(filters.where ?? {})) params.set(`where.${field}`, value);
  if (filters.orderBy) params.set("orderBy", filters.orderBy);
  return request<ObjectPage>(`${BASE}/objects/${enc(objectType)}?${params}`);
}

export async function fetchObject(objectType: string, primaryKey: string, month?: string): Promise<OntologyObjectDetail> {
  const query = month ? `?month=${enc(month)}` : "";
  return (await request<{ data: OntologyObjectDetail }>(`${BASE}/objects/${enc(objectType)}/${enc(primaryKey)}${query}`)).data;
}

export function fetchLinkedObjects(objectType: string, primaryKey: string, link: string, page = 1, pageSize = 200, month?: string): Promise<ObjectPage> {
  const period = month ? `&month=${enc(month)}` : "";
  return request<ObjectPage>(`${BASE}/objects/${enc(objectType)}/${enc(primaryKey)}/links/${enc(link)}?page=${page}&pageSize=${pageSize}${period}`);
}

export async function fetchObjectActions(objectType: string, primaryKey: string): Promise<ObjectActionAvailability[]> {
  return (await request<{ data: ObjectActionAvailability[] }>(`${BASE}/objects/${enc(objectType)}/${enc(primaryKey)}/actions`)).data;
}

export async function applyAction(actionType: string, parameters: Record<string, unknown>, validateOnly = false): Promise<ActionResult> {
  return (await request<{ data: ActionResult }>(`${BASE}/actions/${enc(actionType)}/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parameters, validateOnly })
  })).data;
}

export async function executeFunction(functionName: string, parameters: Record<string, unknown>): Promise<unknown> {
  return (await request<{ data: { result: unknown } }>(`${BASE}/functions/${enc(functionName)}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parameters })
  })).data.result;
}

export function fetchActionLog(filters: { objectType?: string; primaryKey?: string; page: number; pageSize: number }): Promise<ActionLogPage> {
  const params = new URLSearchParams({ page: String(filters.page), pageSize: String(filters.pageSize) });
  if (filters.objectType) params.set("objectType", filters.objectType);
  if (filters.primaryKey) params.set("primaryKey", filters.primaryKey);
  return request<ActionLogPage>(`${BASE}/action-log?${params}`);
}

export async function fetchOntologyVersions(): Promise<OntologyVersion[]> {
  return (await request<{ data: OntologyVersion[] }>(`${BASE}/versions`)).data;
}

export async function fetchOntologyDiff(from: string, to: string): Promise<VersionDiff> {
  const params = new URLSearchParams({ from, to });
  return (await request<{ data: VersionDiff }>(`${BASE}/versions/diff?${params}`)).data;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export function loadObjectSet(objectSet: ObjectSetSpec, options: { pageSize?: number; pageToken?: string | null; orderBy?: { fields: { field: string; direction: "asc" | "desc" }[] } } = {}): Promise<ObjectSetPage> {
  return post<ObjectSetPage>(`${BASE}/objectSets/loadObjects`, { objectSet, pageSize: options.pageSize ?? 20, pageToken: options.pageToken ?? undefined, orderBy: options.orderBy });
}

export function aggregateObjectSet(objectSet: ObjectSetSpec, aggregation: Record<string, unknown>[], groupBy: Record<string, unknown>[]): Promise<AggregationResult> {
  return post<AggregationResult>(`${BASE}/objectSets/aggregate`, { objectSet, aggregation, groupBy });
}

export async function fetchIndexStatus(): Promise<IndexStatus> {
  return (await request<{ data: IndexStatus }>(`${BASE}/index`)).data;
}

export async function refreshIndex(): Promise<IndexStatus> {
  return (await post<{ data: IndexStatus }>(`${BASE}/index/refresh`, {})).data;
}
