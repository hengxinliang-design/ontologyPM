export type OntologyDataType =
  | "string" | "integer" | "decimal" | "money" | "percent" | "date" | "datetime"
  | "boolean" | "enum" | "json" | "object_ref";

export type SourceKind = "system" | "manual" | "derived";

export interface EnumValue { value: string; label: string }

export interface OntologyMarking {
  apiName: string;
  displayName: string;
  description: string;
  permissions: string[];
  granted: boolean;
}

export interface OntologyProperty {
  apiName: string;
  displayName: string;
  dataType: OntologyDataType;
  sourceKind: SourceKind;
  source: string;
  description: string;
  marking: string | null;
  readable: boolean;
  enumValues: EnumValue[];
  editedBy: string[];
  derivedFrom: string | null;
  searchable: boolean;
  /** Dimension of a numeric property (ontology 4.1.0): yuan, personDay, hour, day, month, percent, percentagePoint, person, count or none. */
  unit?: string | null;
  unitLabel?: string;
  /** Computed for the selected statistics period (ontology 7.2.0); otherwise cumulative. */
  periodic?: boolean;
}

/** Statistics period an object type can be viewed by (ontology 7.2.0, months only). */
export interface PeriodSpec {
  unit: "month"; displayName: string; description: string;
  activeProperty: string | null; dateProperty?: string | null; defaultView?: "all" | "latest";
  startProperty?: string | null; endProperty?: string | null; range?: boolean;
}
export interface PeriodMeta { month: string | null; defaultMonth: string | null; options: string[]; defaultView?: "all" | "latest"; range?: boolean }
/** A property holding another object's primary key, resolved to that object's title (detail only). */
export interface ObjectReference { objectType: string; primaryKey: string; title: string | null }

export interface OntologyObjectType {
  apiName: string;
  displayName: string;
  pluralDisplayName: string;
  description: string;
  primaryKey: string;
  titleProperty: string;
  backingDatasets: string[];
  datasource: string;
  metadataSource: string;
  readPermissions: string[];
  icon: string;
  /** Declared list columns (ontology 7.4.0); empty = the first four plain properties. */
  listProperties?: string[];
  period?: PeriodSpec | null;
  properties: OntologyProperty[];
}

export interface OntologyLinkType {
  apiName: string;
  reverseApiName: string;
  displayName: string;
  reverseDisplayName: string;
  sourceObjectType: string;
  targetObjectType: string;
  cardinality: string;
  description: string;
  sourceKey: string | null;
  targetKey: string | null;
  via: string | null;
}

export interface OntologyParameter {
  apiName: string;
  displayName: string;
  dataType: OntologyDataType;
  required: boolean;
  description: string;
  enumValues: EnumValue[];
  objectType: string | null;
  /** Object actions only: the referenced objects to choose from, and the one already linked to the target. */
  options?: ReferenceOption[];
  currentValue?: string | null;
  /** Properties of the referenced object shown under the choice (read-only). */
  optionDetails?: string[];
}

export interface ReferenceOption extends EnumValue {
  details?: { label: string; value: unknown; unit: string | null }[];
}

export interface OntologyActionType {
  apiName: string;
  displayName: string;
  description: string;
  objectType: string;
  targetParameter: string;
  parameters: OntologyParameter[];
  requiredPermissions: string[];
  edits: string[];
  writebackDatasets: string[];
  submissionCriteria: string[];
  createsObject: boolean;
  tone: "default" | "confirm" | "danger";
  permitted: boolean;
}

export interface CriterionResult { description: string; passed: boolean; reason: string | null }

export interface ObjectActionAvailability extends OntologyActionType {
  criteria: CriterionResult[];
  available: boolean;
}

export interface OntologyFunction {
  apiName: string;
  displayName: string;
  description: string;
  parameters: OntologyParameter[];
  outputType: string;
  requiredPermissions: string[];
  permitted: boolean;
}

export interface OntologyValueType {
  apiName: string;
  displayName: string;
  description: string;
  values: EnumValue[];
}

export interface OntologySchema {
  apiName: string;
  displayName: string;
  version: string;
  checksum: string;
  description: string;
  valueTypes: OntologyValueType[];
  markings: OntologyMarking[];
  objectTypes: OntologyObjectType[];
  linkTypes: OntologyLinkType[];
  actionTypes: OntologyActionType[];
  functions: OntologyFunction[];
}

export interface OntologyObject {
  objectType: string;
  primaryKey: string;
  title: string;
  properties: Record<string, unknown>;
  redactedProperties: string[];
  references?: Record<string, ObjectReference>;
}

export interface ObjectLinkSummary {
  apiName: string;
  displayName: string;
  targetObjectType: string;
  targetDisplayName: string;
  cardinality: string;
  count: number;
}

export interface OntologyObjectDetail extends OntologyObject { links: ObjectLinkSummary[]; period?: PeriodMeta | null }

export interface ObjectPage {
  data: OntologyObject[];
  meta: { objectType?: string; page: number; pageSize: number; totalObjects: number; totalPages?: number; period?: PeriodMeta | null };
}

export interface ObjectEdit { objectType: string; primaryKey: string; operation: "create" | "update" | "delete" }

export interface ActionResult {
  actionLogId?: string;
  actionType: string;
  status?: string;
  valid?: boolean;
  edits?: ObjectEdit[];
  objects?: OntologyObject[];
  parameterErrors: { parameter: string; message: string }[];
  criteria: CriterionResult[];
}

export interface ActionLogEntry {
  logId: string;
  actionType: string;
  actionDisplayName: string;
  objectType: string;
  primaryKey: string;
  parameters: Record<string, unknown>;
  edits: ObjectEdit[];
  status: "succeeded" | "rejected" | "failed";
  message: string | null;
  actor: string;
  channel: "ontology" | "legacy_api";
  executedAt: string;
}

export interface ActionLogPage {
  data: ActionLogEntry[];
  meta: { page: number; pageSize: number; totalEntries: number; totalPages: number };
}

export interface OntologyVersion {
  version: string;
  checksum: string;
  apiName: string;
  displayName: string;
  description: string;
  documentCount: number;
  registeredAt: string;
  lastActivatedAt: string;
  active: boolean;
}

export interface VersionChangeDetail {
  path: string;
  change: "added" | "removed" | "changed";
  before: unknown;
  after: unknown;
  breaking: boolean;
}

export interface VersionChange {
  kind: string;
  apiName: string;
  change: "added" | "removed" | "changed";
  breaking: boolean;
  source: string | null;
  details: VersionChangeDetail[];
}

export interface VersionDiff {
  from: string;
  to: string;
  summary: { added: number; removed: number; changed: number; breaking: number };
  changes: VersionChange[];
}

export type ObjectSetSpec = Record<string, unknown> & { type: string };

export interface ObjectSetPage {
  data: OntologyObject[];
  nextPageToken: string | null;
  totalCount: number;
  objectType: string;
}

export interface AggregationRow {
  group: Record<string, unknown>;
  metrics: { name: string; value: unknown }[];
}

export interface AggregationResult {
  accuracy: string;
  objectType: string;
  totalCount: number;
  excludedItems: number;
  data: AggregationRow[];
}

export interface IndexTypeStatus {
  objectType: string;
  displayName: string;
  backingDatasets: Record<string, number | null>;
  indexed: boolean;
  fresh: boolean;
  objectCount: number | null;
  builtAt: string | null;
  buildMs: number | null;
  builds: number;
  hits: number;
  misses: number;
}

export interface IndexStatus {
  datasetVersions: Record<string, number>;
  objectTypes: IndexTypeStatus[];
  rebuilt?: string[];
}
