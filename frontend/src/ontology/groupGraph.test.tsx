import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { graphEdges, graphNodes, GroupGraph, layout, nodeWidth } from "./GroupGraph";
import type { OntologyLinkType, OntologyObjectType, OntologySchema } from "./types";

// D67 (user 2026-11-16): the ontology map by object type group.
const type = (apiName: string, displayName: string) => ({
  apiName, displayName, pluralDisplayName: displayName, description: `${displayName}说明`, primaryKey: "id", titleProperty: "id",
  icon: "", listProperties: [], backingDatasets: [], readPermissions: [], metadataSource: "", datasource: "", properties: []
}) as unknown as OntologyObjectType;
const link = (apiName: string, source: string, target: string): OntologyLinkType => ({
  apiName, reverseApiName: `${apiName}Of`, displayName: apiName, reverseDisplayName: `${apiName} 反向`, sourceObjectType: source, targetObjectType: target,
  cardinality: "one_to_many", description: "", sourceKey: null, targetKey: null, via: null
});

const schema = {
  apiName: "tsg", displayName: "TSG", version: "8.10.0", checksum: "", description: "", valueTypes: [], markings: [], actionTypes: [], functions: [],
  objectTypes: [type("Project", "项目"), type("Contract", "合同/订单"), type("Milestone", "里程碑"), type("Employee", "员工"), type("TimeEntry", "工时记录")],
  objectTypeGroups: [
    { apiName: "projectContract", displayName: "项目与合同", description: "项目与合同。", members: ["Project", "Contract"] },
    { apiName: "deliveryPayment", displayName: "交付与回款", description: "交付。", members: ["Milestone"] },
    { apiName: "workResource", displayName: "工时与资源", description: "工时。", members: ["Employee", "TimeEntry"] }
  ],
  linkTypes: [
    link("contracts", "Project", "Contract"), link("milestones", "Project", "Milestone"),
    link("team", "Project", "Employee"), link("timeEntries", "Project", "TimeEntry"), link("entries", "Employee", "TimeEntry"), link("employee", "TimeEntry", "Employee")
  ]
} satisfies OntologySchema;

describe("group graph", () => {
  it("shows a group's members and, greyed, the other groups' types linked to them", () => {
    const nodes = graphNodes(schema, "deliveryPayment", new Set());
    expect(nodes.map(n => [n.type.apiName, n.external])).toEqual([["Project", true], ["Milestone", false]]);
    expect(graphNodes(schema, "all", new Set(["Contract"])).map(n => n.type.apiName)).toEqual(["Project", "Milestone", "Employee", "TimeEntry"]);
  });

  it("draws one edge per linked pair with every link type, and none between two other-group types", () => {
    const shown = new Map(graphNodes(schema, "workResource", new Set()).map(n => [n.type.apiName, n.external]));
    const edges = graphEdges(schema.linkTypes, shown);
    expect(edges.map(e => [e.key, e.links.length, e.external])).toEqual([
      ["Employee|Project", 1, true], ["Project|TimeEntry", 1, true], ["Employee|TimeEntry", 2, false]
    ]);
  });

  it("lays boxes out the same way every time, inside the canvas and apart from each other", () => {
    const all = graphNodes(schema, "all", new Set()).map(n => ({ apiName: n.type.apiName, width: nodeWidth(n.type.displayName), external: n.external }));
    const edges = graphEdges(schema.linkTypes, new Map(all.map(n => [n.apiName, false])));
    const first = layout(all, edges), again = layout(all, edges);
    expect([...first.entries()]).toEqual([...again.entries()]);
    const boxes = all.map(n => ({ ...first.get(n.apiName)!, w: n.width }));
    boxes.forEach(b => {
      expect(b.x - b.w / 2).toBeGreaterThanOrEqual(0); expect(b.x + b.w / 2).toBeLessThanOrEqual(1000);
      expect(b.y).toBeGreaterThan(0); expect(b.y).toBeLessThan(560);
    });
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const apart = Math.abs(boxes[i].x - boxes[j].x) >= (boxes[i].w + boxes[j].w) / 2 || Math.abs(boxes[i].y - boxes[j].y) >= 38;
      expect(apart, `${all[i].apiName} overlaps ${all[j].apiName}`).toBe(true);
    }
  });

  it("renders the group chips, the link badges and the guidance", () => {
    const html = renderToStaticMarkup(<GroupGraph schema={schema} onSelect={() => undefined} onExplore={() => undefined} />);
    expect(html).toContain("项目与合同");
    expect(html).toContain("↔ 2");
    expect(html).toContain("全部 5 个对象类型");
    expect(nodeWidth("运营口径参数")).toBeGreaterThan(nodeWidth("项目"));
  });
});

describe("hub layout", () => {
  it("keeps a hub's spokes and unlinked types spread over the canvas instead of squeezed in the middle", () => {
    // The real ontology: 项目 has 12 of 27 link types and two types have none, which bunched a force layout.
    const names = ["Hub", "A", "B", "C", "D", "E", "F", "G", "H", "Lone1", "Lone2"];
    const nodes = names.map(apiName => ({ apiName, width: nodeWidth(apiName === "Hub" ? "项目" : "负荷预测模型版本"), external: false }));
    const edges = graphEdges(names.slice(1, 9).map(name => link(`to${name}`, "Hub", name)), new Map(names.map(n => [n, false])));
    const canvas = { w: 1200, h: 560 };
    const pos = layout(nodes, edges, 0, canvas);
    const xs = nodes.map(n => pos.get(n.apiName)!.x), ys = nodes.map(n => pos.get(n.apiName)!.y);
    expect(pos.get("Hub")).toEqual({ x: 600, y: 280 });
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(canvas.w * 0.6);
    expect(Math.max(...ys) - Math.min(...ys)).toBeGreaterThan(canvas.h * 0.6);
    expect(layout(nodes, edges, 1, canvas)).not.toEqual(pos);  // 重新布局 gives another arrangement
  });
});
