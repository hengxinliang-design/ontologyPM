import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { OntologyLinkType, OntologyObjectType, OntologyObjectTypeGroup, OntologySchema } from "./types";

// Ontology map by object type group (D67, user 2026-11-16, after the Palantir 「在图上探索对象类型组」 view):
// a group's types and the links between them; types of other groups that link in are grey with dashed links.
// One badge per linked pair says how many link types join them and lists them on click; a type opens a menu
// (预览 / 开始探索 / 在图中隐藏). Presentation only: groups, links and types all come from the ontology schema.

const NODE_H = 38;
/** Canvas in CSS pixels, so labels keep their size; the layout spreads to the panel's width. */
export type Canvas = { w: number; h: number };
const DEFAULT_CANVAS: Canvas = { w: 1000, h: 560 };
const CARDINALITY: Record<string, string> = { one_to_one: "一对一", one_to_many: "一对多", many_to_one: "多对一", many_to_many: "多对多" };
export const GROUP_COLORS = ["#2f6fde", "#16967a", "#8a5cd6", "#c98314", "#cf4a7c", "#5b7290"];

export interface GraphNode { apiName: string; label: string; width: number; external: boolean; x: number; y: number }
export interface GraphEdge { key: string; a: string; b: string; links: OntologyLinkType[]; external: boolean }

/** Width of a node box: CJK glyphs are about a font size wide, Latin about half. */
export function nodeWidth(label: string): number {
  const units = [...label].reduce((sum, ch) => sum + (/[⺀-鿿＀-￯]/.test(ch) ? 1 : 0.58), 0);
  return Math.max(92, Math.round(units * 13 + 40));
}

/** The types a view shows: the group's members, plus types of other groups linked to them (external). */
export function graphNodes(schema: OntologySchema, group: string, hidden: ReadonlySet<string>): Array<{ type: OntologyObjectType; external: boolean }> {
  const members = group === "all" ? null : new Set(schema.objectTypeGroups.find(g => g.apiName === group)?.members ?? []);
  const linked = new Set<string>();
  if (members) for (const link of schema.linkTypes) {
    if (members.has(link.sourceObjectType) && !members.has(link.targetObjectType)) linked.add(link.targetObjectType);
    if (members.has(link.targetObjectType) && !members.has(link.sourceObjectType)) linked.add(link.sourceObjectType);
  }
  return schema.objectTypes
    .filter(t => !hidden.has(t.apiName) && (!members || members.has(t.apiName) || linked.has(t.apiName)))
    .map(type => ({ type, external: !!members && !members.has(type.apiName) }));
}

/** One edge per unordered pair of shown types, carrying every link type between them; links between two external types are left out. */
export function graphEdges(links: OntologyLinkType[], shown: Map<string, boolean>): GraphEdge[] {
  const edges = new Map<string, GraphEdge>();
  for (const link of links) {
    const a = link.sourceObjectType, b = link.targetObjectType;
    if (a === b || !shown.has(a) || !shown.has(b) || (shown.get(a) && shown.get(b))) continue;
    const [x, y] = [a, b].sort();
    const key = `${x}|${y}`;
    const edge = edges.get(key) ?? { key, a: x, b: y, links: [], external: !!(shown.get(a) || shown.get(b)) };
    edge.links.push(link);
    edges.set(key, edge);
  }
  return [...edges.values()];
}

type Box = { apiName: string; width: number; external: boolean };
type Point = { x: number; y: number };

/**
 * Hub layout, deterministic: the same view always looks the same.
 * The most linked type of the view sits in the middle (项目 links to 12 of the 27 link types); the view's other types
 * sit on a ring (two staggered rings when many) in group order, so a group reads as one arc; types of other groups sit
 * on an outer ring, each next to the types it links to. 48 rotations, ring sizes and ring orders are tried and scored (boxes that
 * touch, links that run through a box, badges on a box); the best is used, and 「重新布局」 steps to the next best.
 * (A force layout was tried first: the two types without links drifted to the edges and squeezed the rest.)
 */
export function layout(nodes: Box[], edges: GraphEdge[], turn = 0, canvas: Canvas = DEFAULT_CANVAS): Map<string, Point> {
  if (!nodes.length) return new Map();
  const candidates: Array<{ pos: Map<string, Point>; score: number }> = [];
  for (const chained of [false, true]) for (let step = 0; step < 12; step++) for (const size of [1, 1.12]) {
    const pos = arrange(nodes, edges, (step / 12) * Math.PI * 2, size, canvas, chained);
    candidates.push({ pos, score: layoutScore(nodes, edges, pos) });
  }
  candidates.sort((a, b) => a.score - b.score);
  return candidates[turn % candidates.length].pos;
}

/** Ring order that puts linked types next to each other, so a link between two ring types does not cross the middle. */
function chain(members: Box[], edges: GraphEdge[]): Box[] {
  const linked = (a: string, b: string) => edges.some(e => (e.a === a && e.b === b) || (e.a === b && e.b === a));
  const order: Box[] = [];
  const left = [...members];
  while (left.length) {
    const last = order[order.length - 1];
    const next = last ? left.findIndex(n => linked(last.apiName, n.apiName)) : 0;
    order.push(left.splice(next >= 0 ? next : 0, 1)[0]);
  }
  return order;
}

/** Lower is better: touching boxes weigh most, then links through a box, then badges on a box. */
export function layoutScore(nodes: Box[], edges: GraphEdge[], pos: Map<string, Point>): number {
  let score = 0;
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = pos.get(nodes[i].apiName)!, b = pos.get(nodes[j].apiName)!;
    if (Math.abs(a.x - b.x) < (nodes[i].width + nodes[j].width) / 2 + 8 && Math.abs(a.y - b.y) < NODE_H + 6) score += 100;
  }
  const boxes = nodes.map(n => ({ ...pos.get(n.apiName)!, width: n.width, apiName: n.apiName }));
  for (const edge of edges) {
    const a = pos.get(edge.a)!, b = pos.get(edge.b)!;
    for (const box of boxes) {
      if (box.apiName === edge.a || box.apiName === edge.b) continue;
      for (let k = 1; k < 30; k++) {
        const x = a.x + ((b.x - a.x) * k) / 30, y = a.y + ((b.y - a.y) * k) / 30;
        if (Math.abs(x - box.x) < box.width / 2 + 4 && Math.abs(y - box.y) < NODE_H / 2 + 4) { score += 10; break; }
      }
    }
    const badge = badgePoint(a, b, boxes);
    if (boxes.some(box => Math.abs(box.x - badge.x) < box.width / 2 + 20 && Math.abs(box.y - badge.y) < NODE_H / 2 + 10)) score += 6;
  }
  return score;
}

function arrange(nodes: Box[], edges: GraphEdge[], rotation: number, size: number, { w: W, h: H }: Canvas, chained = false): Map<string, Point> {
  const pos = new Map<string, Point>();
  const degree = new Map<string, number>();
  for (const edge of edges) for (const end of [edge.a, edge.b]) degree.set(end, (degree.get(end) ?? 0) + 1);
  const inner = nodes.filter(n => !n.external);
  const outer = nodes.filter(n => n.external);
  const hub = inner.length > 2 ? [...inner].sort((a, b) => (degree.get(b.apiName) ?? 0) - (degree.get(a.apiName) ?? 0))[0] : undefined;
  const members = inner.filter(n => n !== hub);
  const ring = chained ? chain(members, edges) : members;
  const cx = W / 2, cy = H / 2;
  const start = -Math.PI / 2 + rotation;
  const outerRx = W / 2 - 105, outerRy = H / 2 - 62;
  const spread = outer.length ? { rx: outerRx * 0.62 * size, ry: outerRy * 0.59 * size, stagger: 0.8 } : { rx: W / 2 - 125, ry: H / 2 - 70, stagger: 0.76 / size };
  if (hub) pos.set(hub.apiName, { x: cx, y: cy });
  ring.forEach((node, i) => {
    const angle = start + (i / ring.length) * Math.PI * 2;
    const r = ring.length > 6 && i % 2 === 1 ? spread.stagger : 1;
    pos.set(node.apiName, { x: cx + Math.cos(angle) * spread.rx * r, y: cy + Math.sin(angle) * spread.ry * r });
  });
  // Outer types go where the types they link to are, half a ring step off so they sit between ring types.
  const anchor = (apiName: string) => {
    const linked = edges.filter(e => e.a === apiName || e.b === apiName).map(e => pos.get(e.a === apiName ? e.b : e.a)).filter(Boolean) as Point[];
    if (!linked.length) return Math.PI;
    const x = linked.reduce((sum, p) => sum + p.x - cx, 0), y = linked.reduce((sum, p) => sum + p.y - cy, 0);
    return Math.abs(x) + Math.abs(y) < 1 ? start : Math.atan2(y / spread.ry, x / spread.rx);
  };
  const offset = ring.length ? Math.PI / ring.length : 0;
  const ordered = outer.map(node => ({ node, angle: anchor(node.apiName) + offset })).sort((a, b) => a.angle - b.angle);
  const slot = (Math.PI * 2) / Math.max(1, ordered.length);
  let previous = -Infinity;
  ordered.forEach(({ node, angle }, i) => {
    const a = Math.max(angle, previous + slot * 0.85, ordered[0].angle + i * slot * 0.85);
    previous = a;
    const r = ordered.length > 8 && i % 2 === 1 ? 0.9 : 1;
    pos.set(node.apiName, { x: cx + Math.cos(a) * outerRx * r, y: cy + Math.sin(a) * outerRy * r });
  });
  separate(nodes, pos, { w: W, h: H }, hub?.apiName);
  return pos;
}

/** Where a pair's 「↔ N」 badge goes: the middle of the line, or the nearest point along it that is clear of every box. */
export function badgePoint(a: { x: number; y: number }, b: { x: number; y: number },
  boxes: Array<{ x: number; y: number; width: number }>): { x: number; y: number } {
  const at = (t: number) => ({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  const clear = (p: { x: number; y: number }) => boxes.every(box => Math.abs(box.x - p.x) >= box.width / 2 + 22 || Math.abs(box.y - p.y) >= NODE_H / 2 + 12);
  for (const t of [0.5, 0.42, 0.58, 0.35, 0.65, 0.3, 0.7, 0.25, 0.75]) if (clear(at(t))) return at(t);
  return at(0.5);
}

/** Pushes apart boxes closer than a gap (along the axis that needs less movement) and keeps every box inside the canvas. */
function separate(nodes: Array<{ apiName: string; width: number }>, pos: Map<string, { x: number; y: number }>, { w: W, h: H }: Canvas, fixed?: string) {
  const pad = 14, gapX = 18, gapY = 16;
  const clamp = (node: { apiName: string; width: number }) => {
    const p = pos.get(node.apiName)!;
    p.x = Math.min(W - pad - node.width / 2, Math.max(pad + node.width / 2, p.x));
    p.y = Math.min(H - pad - NODE_H / 2, Math.max(pad + NODE_H / 2, p.y));
  };
  for (let round = 0; round < 80; round++) {
    let moved = false;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = pos.get(nodes[i].apiName)!, b = pos.get(nodes[j].apiName)!;
      const ox = (nodes[i].width + nodes[j].width) / 2 + gapX - Math.abs(a.x - b.x);
      const oy = NODE_H + gapY - Math.abs(a.y - b.y);
      if (ox <= 0 || oy <= 0) continue;
      moved = true;
      const shares = nodes[i].apiName === fixed ? [0, 1] : nodes[j].apiName === fixed ? [1, 0] : [0.5, 0.5];
      if (oy < ox) {
        const dir = a.y <= b.y ? -1 : 1;
        a.y += dir * oy * shares[0]; b.y -= dir * oy * shares[1];
      } else {
        const dir = a.x <= b.x ? -1 : 1;
        a.x += dir * ox * shares[0]; b.x -= dir * ox * shares[1];
      }
    }
    nodes.forEach(clamp);
    if (!moved) break;
  }
}

type Overlay = { kind: "node"; apiName: string } | { kind: "edge"; key: string } | null;

export function GroupGraph({ schema, selected, onSelect, onExplore }: {
  schema: OntologySchema; selected?: string; onSelect: (type: string) => void; onExplore: (type: string) => void;
}) {
  const groups = schema.objectTypeGroups;
  const [group, setGroup] = useState<string>("all");
  const [view, setView] = useState<"graph" | "list">("graph");
  const [hidden, setHidden] = useState<Set<string>>(() => new Set());
  const [turn, setTurn] = useState(0);
  const [overlay, setOverlay] = useState<Overlay>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [view2d, setView2d] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number; moved: boolean } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [canvas, setCanvas] = useState<Canvas>(DEFAULT_CANVAS);
  const { w: W, h: H } = canvas;
  useEffect(() => {
    const element = box.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.min(1600, Math.max(640, Math.round(entry.contentRect.width)));
      const h = Math.max(380, Math.round(entry.contentRect.height));
      setCanvas(c => (Math.abs(c.w - w) > 24 || Math.abs(c.h - h) > 24 ? { w, h } : c));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [view]);

  const types = useMemo(() => new Map(schema.objectTypes.map(t => [t.apiName, t])), [schema]);
  const groupOf = useMemo(() => {
    const map = new Map<string, OntologyObjectTypeGroup[]>();
    groups.forEach(g => g.members.forEach(m => map.set(m, [...(map.get(m) ?? []), g])));
    return map;
  }, [groups]);
  const colorOf = (apiName: string) => {
    const first = groupOf.get(apiName)?.[0];
    return first ? GROUP_COLORS[groups.indexOf(first) % GROUP_COLORS.length] : GROUP_COLORS[5];
  };

  const shown = useMemo(() => graphNodes(schema, group, hidden), [schema, group, hidden]);
  const shownMap = useMemo(() => new Map(shown.map(s => [s.type.apiName, s.external])), [shown]);
  const edges = useMemo(() => graphEdges(schema.linkTypes, shownMap), [schema, shownMap]);
  const nodes: GraphNode[] = useMemo(() => {
    const base = shown.map(s => ({ apiName: s.type.apiName, label: s.type.displayName, width: nodeWidth(s.type.displayName), external: s.external }));
    const placed = layout(base, edges, turn, canvas);
    return base.map(node => ({ ...node, ...placed.get(node.apiName)! }));
  }, [shown, edges, turn, canvas]);
  const at = useMemo(() => new Map(nodes.map(n => [n.apiName, n])), [nodes]);
  const badges = useMemo(() => new Map(edges.map(edge => [edge.key, badgePoint(at.get(edge.a)!, at.get(edge.b)!, nodes)])), [edges, at, nodes]);

  useEffect(() => { setOverlay(null); setView2d({ x: 0, y: 0, k: 1 }); }, [group, turn]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") { setOverlay(null); setPreview(null); } };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);

  // Overlays sit over the SVG in percent of the canvas, which keeps its aspect ratio.
  const place = (x: number, y: number) => ({ left: `${((x * view2d.k + view2d.x) / W) * 100}%`, top: `${((y * view2d.k + view2d.y) / H) * 100}%` });
  const zoom = (factor: number) => setView2d(v => {
    const k = Math.min(2.5, Math.max(0.5, v.k * factor));
    return { k, x: W / 2 - ((W / 2 - v.x) / v.k) * k, y: H / 2 - ((H / 2 - v.y) / v.k) * k };
  });
  const toCanvas = (event: ReactPointerEvent) => {
    const rect = box.current?.getBoundingClientRect();
    return rect ? { x: ((event.clientX - rect.left) / rect.width) * W, y: ((event.clientY - rect.top) / rect.height) * H } : { x: 0, y: 0 };
  };
  const startPan = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (event.target !== event.currentTarget) return;
    const p = toCanvas(event);
    drag.current = { x: p.x, y: p.y, ox: view2d.x, oy: view2d.y, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const pan = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const p = toCanvas(event);
    const dx = p.x - drag.current.x, dy = p.y - drag.current.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.current.moved = true;
    setView2d(v => ({ ...v, x: drag.current!.ox + dx, y: drag.current!.oy + dy }));
  };
  const endPan = () => {
    if (drag.current && !drag.current.moved) setOverlay(null);
    drag.current = null;
  };

  const hide = (apiName: string) => { setHidden(h => new Set(h).add(apiName)); setOverlay(null); if (preview === apiName) setPreview(null); };
  const currentGroup = groups.find(g => g.apiName === group);
  const memberCount = shown.filter(s => !s.external).length;
  const externalCount = shown.length - memberCount;
  const previewType = preview ? types.get(preview) : undefined;
  const openEdge = overlay?.kind === "edge" ? edges.find(e => e.key === overlay.key) : undefined;
  const openNode = overlay?.kind === "node" ? at.get(overlay.apiName) : undefined;

  return <div className="group-graph">
    <div className="gg-bar">
      <div className="gg-groups" role="tablist" aria-label="对象类型组">
        <button role="tab" aria-selected={group === "all"} className={group === "all" ? "active" : ""} onClick={() => setGroup("all")}>
          全部<em>{schema.objectTypes.length}</em></button>
        {groups.map((g, i) => <button key={g.apiName} role="tab" aria-selected={group === g.apiName} className={group === g.apiName ? "active" : ""}
          title={g.description} onClick={() => setGroup(g.apiName)}>
          <i style={{ background: GROUP_COLORS[i % GROUP_COLORS.length] }} />{g.displayName}<em>{g.members.length}</em></button>)}
      </div>
      <div className="gg-view" role="group" aria-label="显示方式">
        <button className={view === "graph" ? "active" : ""} aria-pressed={view === "graph"} onClick={() => setView("graph")}>图</button>
        <button className={view === "list" ? "active" : ""} aria-pressed={view === "list"} onClick={() => setView("list")}>列表</button>
      </div>
    </div>
    <p className="gg-caption">{currentGroup ? `${currentGroup.description}本组 ${memberCount} 个对象类型${externalCount ? `，灰色为与本组有关系的其他组对象类型（${externalCount} 个）` : ""}。` : `全部 ${memberCount} 个对象类型，按组着色。`}
      点击对象类型打开菜单，点击「↔ 数字」查看两者之间的关系类型。</p>

    {view === "list" ? <GroupList schema={schema} nodes={shown} groupOf={groupOf} selected={selected} onSelect={onSelect} onExplore={onExplore} /> :
      <div className="gg-canvas" ref={box}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${currentGroup?.displayName ?? "全部"}对象类型关系图`}
          onPointerDown={startPan} onPointerMove={pan} onPointerUp={endPan} onPointerCancel={endPan}>
          <g transform={`translate(${view2d.x} ${view2d.y}) scale(${view2d.k})`}>
            {edges.map(edge => {
              const a = at.get(edge.a)!, b = at.get(edge.b)!;
              const active = selected === edge.a || selected === edge.b || openEdge?.key === edge.key;
              return <line key={edge.key} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`gg-edge ${edge.external ? "external" : ""} ${active ? "active" : ""}`} />;
            })}
            {nodes.map(node => {
              const active = selected === node.apiName || preview === node.apiName;
              return <g key={node.apiName} className={`gg-node ${node.external ? "external" : ""} ${active ? "active" : ""}`}
                transform={`translate(${node.x - node.width / 2} ${node.y - NODE_H / 2})`} role="button" tabIndex={0}
                aria-label={`${node.label}${node.external ? "（其他组）" : ""}，打开菜单`}
                onClick={() => setOverlay({ kind: "node", apiName: node.apiName })}
                onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setOverlay({ kind: "node", apiName: node.apiName }); } }}>
                <rect width={node.width} height={NODE_H} rx={8} />
                <rect width={5} height={NODE_H - 12} x={8} y={6} rx={2.5} className="gg-swatch" style={{ fill: node.external ? "#a9b6c6" : colorOf(node.apiName) }} />
                <text x={22} y={NODE_H / 2 + 4.5}>{node.label}</text>
              </g>;
            })}
            {edges.map(edge => {
              const point = badges.get(edge.key)!;
              const label = `↔ ${edge.links.length}`;
              const open = openEdge?.key === edge.key;
              return <g key={`badge-${edge.key}`} className={`gg-badge ${open ? "active" : ""} ${edge.external ? "external" : ""}`}
                transform={`translate(${point.x} ${point.y})`} role="button" tabIndex={0}
                aria-label={`${types.get(edge.a)?.displayName} 与 ${types.get(edge.b)?.displayName}：${edge.links.length} 种关系`}
                onClick={() => setOverlay({ kind: "edge", key: edge.key })}
                onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setOverlay({ kind: "edge", key: edge.key }); } }}>
                <rect x={-20} y={-10} width={40} height={20} rx={10} />
                <text y={4}>{label}</text>
              </g>;
            })}
          </g>
        </svg>

        <div className="gg-tools">
          <button onClick={() => setTurn(t => t + 1)} title="换一种排布">重新布局</button>
          {hidden.size > 0 && <button onClick={() => setHidden(new Set())} title={[...hidden].map(h => types.get(h)?.displayName).join("、")}>显示已隐藏（{hidden.size}）</button>}
        </div>
        <div className="gg-zoom" role="group" aria-label="缩放">
          <button onClick={() => zoom(1.2)} aria-label="放大">＋</button>
          <button onClick={() => zoom(1 / 1.2)} aria-label="缩小">－</button>
          <button onClick={() => setView2d({ x: 0, y: 0, k: 1 })} aria-label="适应画布">⤢</button>
        </div>
        {group !== "all" && <div className="gg-legend">
          <span><i className="member" />本组对象类型</span>
          <span><i className="external" />其他组的对象类型</span>
        </div>}

        {/* Near the bottom the menu opens upwards so it is not cut off by the canvas. */}
        {openNode && <div className={`gg-pop gg-menu ${openNode.y * view2d.k + view2d.y > H * 0.62 ? "up" : ""}`}
          style={place(openNode.x - openNode.width / 2, openNode.y * view2d.k + view2d.y > H * 0.62 ? openNode.y - NODE_H / 2 - 6 : openNode.y + NODE_H / 2 + 6)} role="menu">
          <strong>{openNode.label}</strong>
          <button role="menuitem" onClick={() => { setPreview(openNode.apiName); setOverlay(null); }}>预览</button>
          <button role="menuitem" onClick={() => onExplore(openNode.apiName)}>开始探索<small>浏览对象</small></button>
          {openNode.external && groupOf.get(openNode.apiName)?.map(g => <button key={g.apiName} role="menuitem" onClick={() => setGroup(g.apiName)}>打开「{g.displayName}」</button>)}
          <button role="menuitem" onClick={() => hide(openNode.apiName)}>在图中隐藏</button>
        </div>}

        {openEdge && <LinkList edge={openEdge} types={types} style={place(badges.get(openEdge.key)!.x, badges.get(openEdge.key)!.y + 14)} />}

        {previewType && <TypePreview type={previewType} schema={schema} groups={groupOf.get(previewType.apiName) ?? []}
          close={() => setPreview(null)} preview={setPreview}
          detail={() => { onSelect(previewType.apiName); setPreview(null); document.querySelector(".ontology-type-detail")?.scrollIntoView({ behavior: "smooth" }); }}
          explore={() => onExplore(previewType.apiName)} />}
      </div>}
  </div>;
}

function LinkList({ edge, types, style }: { edge: GraphEdge; types: Map<string, OntologyObjectType>; style: { left: string; top: string } }) {
  const name = (apiName: string) => types.get(apiName)?.displayName ?? apiName;
  return <div className="gg-pop gg-links" style={style} role="dialog" aria-label="关系类型">
    <strong>{name(edge.a)} ↔ {name(edge.b)} · {edge.links.length} 种关系</strong>
    <ul>{edge.links.map(link => <li key={`${link.sourceObjectType}.${link.apiName}`}>
      <span>{name(link.sourceObjectType)} → {name(link.targetObjectType)}</span>
      <b>{link.displayName}</b><em>{CARDINALITY[link.cardinality] ?? link.cardinality}</em>
      <small>反向：{link.reverseDisplayName}{link.via ? ` · 经由 ${link.via}` : ""}</small>
      <code>{link.apiName} / {link.reverseApiName}</code>
    </li>)}</ul>
  </div>;
}

function TypePreview({ type, schema, groups, close, preview, detail, explore }: {
  type: OntologyObjectType; schema: OntologySchema; groups: OntologyObjectTypeGroup[];
  close: () => void; preview: (apiName: string) => void; detail: () => void; explore: () => void;
}) {
  const links = schema.linkTypes.filter(l => l.sourceObjectType === type.apiName || l.targetObjectType === type.apiName);
  const actions = schema.actionTypes.filter(a => a.objectType === type.apiName);
  const name = (apiName: string) => schema.objectTypes.find(t => t.apiName === apiName)?.displayName ?? apiName;
  const counts = { system: 0, manual: 0, derived: 0 } as Record<string, number>;
  type.properties.forEach(p => { counts[p.sourceKind] = (counts[p.sourceKind] ?? 0) + 1; });
  return <aside className="gg-preview" aria-label={`${type.displayName}预览`}>
    <header>
      <div><small>{groups.map(g => g.displayName).join(" · ") || "未分组"}</small><h3>{type.displayName}</h3><code>{type.apiName}</code></div>
      <button onClick={close} aria-label="关闭预览">×</button>
    </header>
    <p>{type.description}</p>
    <dl>
      <div><dt>属性</dt><dd>{type.properties.length}<small>系统 {counts.system} · 人工 {counts.manual} · 派生 {counts.derived}</small></dd></div>
      <div><dt>关系</dt><dd>{links.length}</dd></div>
      <div><dt>动作</dt><dd>{actions.length}</dd></div>
    </dl>
    {links.length > 0 && <section><h4>关系</h4><ul>{links.map(link => {
      const forward = link.sourceObjectType === type.apiName;
      const other = forward ? link.targetObjectType : link.sourceObjectType;
      return <li key={`${link.sourceObjectType}.${link.apiName}`}>
        {/* A link named after its target (项目 → 项目) shows the type once. */}
        {(forward ? link.displayName : link.reverseDisplayName) !== name(other) && <span>{forward ? link.displayName : link.reverseDisplayName}</span>}
        <button className="link-button" onClick={() => preview(other)}>{name(other)}</button>
        <em>{CARDINALITY[forward ? link.cardinality : reverse(link.cardinality)]}</em>
      </li>;
    })}</ul></section>}
    {actions.length > 0 && <section><h4>动作</h4><p className="gg-actions">{actions.map(a => a.displayName).join("、")}</p></section>}
    <footer>
      <button className="button secondary" onClick={detail}>查看完整定义</button>
      <button className="button primary" onClick={explore}>开始探索</button>
    </footer>
  </aside>;
}

function reverse(cardinality: string): string {
  return cardinality === "one_to_many" ? "many_to_one" : cardinality === "many_to_one" ? "one_to_many" : cardinality;
}

function GroupList({ schema, nodes, groupOf, selected, onSelect, onExplore }: {
  schema: OntologySchema; nodes: Array<{ type: OntologyObjectType; external: boolean }>; groupOf: Map<string, OntologyObjectTypeGroup[]>;
  selected?: string; onSelect: (type: string) => void; onExplore: (type: string) => void;
}) {
  return <div className="table-wrap"><table className="gg-list">
    <thead><tr><th>对象类型</th><th>所属组</th><th>属性</th><th>关系</th><th>动作</th><th>说明</th><th /></tr></thead>
    <tbody>{nodes.map(({ type, external }) => <tr key={type.apiName} className={`${selected === type.apiName ? "selected" : ""} ${external ? "external" : ""}`}>
      <td><button className="link-button" onClick={() => onSelect(type.apiName)}>{type.displayName}</button><small><code>{type.apiName}</code></small></td>
      <td>{(groupOf.get(type.apiName) ?? []).map(g => g.displayName).join("、")}{external && <small>其他组</small>}</td>
      <td>{type.properties.length}</td>
      <td>{schema.linkTypes.filter(l => l.sourceObjectType === type.apiName || l.targetObjectType === type.apiName).length}</td>
      <td>{schema.actionTypes.filter(a => a.objectType === type.apiName).length}</td>
      <td className="gg-desc" title={type.description}>{type.description}</td>
      <td><button className="button secondary" onClick={() => onExplore(type.apiName)}>开始探索</button></td>
    </tr>)}</tbody>
  </table></div>;
}
