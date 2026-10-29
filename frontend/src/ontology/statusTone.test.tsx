import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PropertyValue } from "./shared";
import type { OntologyProperty } from "./types";

const status = {
  apiName: "status", displayName: "状态", dataType: "enum", sourceKind: "manual", source: "", description: "", marking: null,
  readable: true, editedBy: [], derivedFrom: null, searchable: false,
  enumValues: [{ value: "planned", label: "计划中" }, { value: "completed", label: "已完成" }],
} as OntologyProperty;

describe("status tone", () => {
  it("shows finished statuses green and the others neutral", () => {
    expect(renderToStaticMarkup(<PropertyValue property={status} value="completed" redacted={false} />)).toContain('class="health-tag healthy"');
    expect(renderToStaticMarkup(<PropertyValue property={status} value="planned" redacted={false} />)).toContain('class="health-tag baseline"');
  });
});
