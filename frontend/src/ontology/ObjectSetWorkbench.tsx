import { useCallback, useEffect, useMemo, useState } from "react";
import { aggregateObjectSet, fetchIndexStatus, loadObjectSet, refreshIndex } from "./api";
import { displayColumns, errorMessage, formatTime, PropertyValue } from "./shared";
import { buildObjectSet, groupLabel, linksFor, OPERATORS, presets, typeAt } from "./objectSetBuilder";
import type { Condition, Operator, Step } from "./objectSetBuilder";
import type { AggregationResult, IndexStatus, ObjectSetPage, ObjectSetSpec, OntologyObjectType, OntologySchema } from "./types";

const METRICS = [
  { value: "count", label: "计数" },
  { value: "sum", label: "求和", numeric: true },
  { value: "avg", label: "平均", numeric: true },
  { value: "min", label: "最小" },
  { value: "max", label: "最大" },
  { value: "exactDistinct", label: "去重计数" }
];
const NUMERIC = ["integer", "decimal", "money", "percent"];

export function ObjectSetWorkbench({ schema, openObject }: { schema: OntologySchema; openObject: (type: string, pk: string) => void }) {
  const [start, setStart] = useState("Project");
  const [steps, setSteps] = useState<Step[]>([]);
  const [manual, setManual] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [result, setResult] = useState<ObjectSetPage | null>(null);
  const [pages, setPages] = useState<Array<string | null>>([null]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [ranSpec, setRanSpec] = useState<ObjectSetSpec | null>(null);

  const built = useMemo(() => buildObjectSet(schema, start, steps), [schema, start, steps]);
  useEffect(() => { if (!manual) setJsonText(JSON.stringify(built.objectSet, null, 2)); }, [built, manual]);

  const resultType = schema.objectTypes.find(t => t.apiName === result?.objectType);

  const run = useCallback(async (pageToken: string | null = null, pageIndex = 0) => {
    let spec: ObjectSetSpec;
    try { spec = JSON.parse(jsonText) as ObjectSetSpec; }
    catch { setError("JSON 格式不正确"); return; }
    setRunning(true); setError(null);
    const started = performance.now();
    try {
      const page = await loadObjectSet(spec, { pageSize: 20, pageToken });
      setElapsed(Math.round(performance.now() - started));
      setResult(page);
      setRanSpec(spec);
      setPages(previous => pageIndex === 0 ? [null, page.nextPageToken] : [...previous.slice(0, pageIndex + 1), page.nextPageToken]);
    } catch (err) {
      setError(errorMessage(err, "查询失败"));
    } finally {
      setRunning(false);
    }
  }, [jsonText]);

  const [pageIndex, setPageIndex] = useState(0);

  return <div className="workbench">
    <IndexPanel />
    <div className="workbench-grid">
      <section className="panel workbench-builder">
        <div className="panel-head"><div><h2>对象集构建器</h2><p>按 Palantir OSDK 的 ObjectSet 结构组合：起点 → 过滤 → 沿关系检索；复杂的集合运算可直接编辑 JSON 或选用示例。</p></div></div>
        <div className="builder-body">
          <label className="builder-row">
            <span>示例</span>
            <select value="" onChange={e => {
              const preset = presets()[Number(e.target.value)];
              if (!preset) return;
              setManual(true); setJsonText(JSON.stringify(preset.objectSet, null, 2)); setResult(null);
            }}>
              <option value="">选择一个示例查询…</option>
              {presets().map((p, i) => <option key={p.label} value={i}>{p.label} — {p.description}</option>)}
            </select>
          </label>
          <fieldset disabled={manual} className="builder-steps">
            <label className="builder-row">
              <span>起点</span>
              <select value={start} onChange={e => { setStart(e.target.value); setSteps([]); }}>
                {schema.objectTypes.map(t => <option key={t.apiName} value={t.apiName}>{t.displayName}（{t.apiName}）</option>)}
              </select>
            </label>
            {steps.map((step, index) => {
              const current = typeAt(schema, start, steps, index);
              const type = schema.objectTypes.find(t => t.apiName === current);
              const update = (next: Step) => setSteps(all => all.map((s, i) => i === index ? next : s));
              const remove = () => setSteps(all => all.slice(0, index));
              return <div key={index} className="builder-step">
                <div className="builder-step-head">
                  <strong>{index + 1}. {step.kind === "filter" ? `过滤 ${type?.displayName ?? current}` : "沿关系检索"}</strong>
                  <button type="button" className="link-button" onClick={remove}>删除此步及之后</button>
                </div>
                {step.kind === "searchAround"
                  ? <select value={step.link} onChange={e => update({ kind: "searchAround", link: e.target.value })}>
                    {linksFor(schema, current).map(l => <option key={l.apiName} value={l.apiName}>{l.displayName} → {schema.objectTypes.find(t => t.apiName === l.target)?.displayName ?? l.target}（{l.apiName}）</option>)}
                  </select>
                  : <FilterEditor type={type} step={step} update={update} />}
              </div>;
            })}
            <div className="builder-actions">
              <button type="button" className="button secondary" onClick={() => setSteps(all => [...all, { kind: "filter", mode: "and", conditions: [{ field: "", op: "eq", value: "" }] }])}>+ 过滤</button>
              <button type="button" className="button secondary" disabled={!linksFor(schema, built.resultType).length}
                onClick={() => setSteps(all => [...all, { kind: "searchAround", link: linksFor(schema, built.resultType)[0]?.apiName ?? "" }])}>+ 沿关系检索</button>
            </div>
          </fieldset>
          <div className="builder-json">
            <div className="builder-step-head">
              <strong>ObjectSet JSON</strong>
              <label className="toggle"><input type="checkbox" checked={manual} onChange={e => setManual(e.target.checked)} /> 手动编辑</label>
            </div>
            <textarea value={jsonText} readOnly={!manual} spellCheck={false} onChange={e => setJsonText(e.target.value)} rows={12} />
          </div>
          <button type="button" className="button primary run-button" disabled={running} onClick={() => { setPageIndex(0); void run(null, 0); }}>{running ? "查询中…" : "运行查询"}</button>
          {error && <p className="action-message error">{error}</p>}
        </div>
      </section>

      <section className="panel workbench-results">
        <div className="panel-head"><div>
          <h2>{resultType ? `${resultType.pluralDisplayName}` : "查询结果"}</h2>
          <p>{result ? `共 ${result.totalCount} 个对象${elapsed !== null ? ` · ${elapsed} ms` : ""}` : "运行查询后在此显示对象，点击可打开对象详情"}</p>
        </div></div>
        {result && resultType && <>
          <ResultTable page={result} type={resultType} openObject={openObject} />
          {(pageIndex > 0 || result.nextPageToken) && <div className="pagination">
            <button disabled={pageIndex === 0 || running} onClick={() => { const i = pageIndex - 1; setPageIndex(i); void run(pages[i], i); }}>上一页</button>
            <span>第 {pageIndex + 1} 页</span>
            <button disabled={!result.nextPageToken || running} onClick={() => { const i = pageIndex + 1; setPageIndex(i); void run(result.nextPageToken, i); }}>下一页</button>
          </div>}
          {ranSpec && <AggregationPanel key={JSON.stringify(ranSpec)} objectSet={ranSpec} type={resultType} />}
        </>}
      </section>
    </div>
  </div>;
}

function FilterEditor({ type, step, update }: { type?: OntologyObjectType; step: Extract<Step, { kind: "filter" }>; update: (next: Step) => void }) {
  const properties = (type?.properties ?? []).filter(p => p.readable);
  const setCondition = (index: number, patch: Partial<Condition>) =>
    update({ ...step, conditions: step.conditions.map((c, i) => i === index ? { ...c, ...patch } : c) });
  return <div className="filter-editor">
    {step.conditions.length > 1 && <div className="segmented">
      {(["and", "or"] as const).map(mode => <button key={mode} type="button" className={step.mode === mode ? "active" : ""} onClick={() => update({ ...step, mode })}>{mode === "and" ? "全部满足" : "任一满足"}</button>)}
    </div>}
    {step.conditions.map((condition, index) => {
      const prop = properties.find(p => p.apiName === condition.field);
      const operators = OPERATORS.filter(op => !op.types || (prop && op.types.includes(prop.dataType)));
      const needsValue = condition.op !== "isNull" && condition.op !== "notNull";
      return <div key={index} className="condition-row">
        <select value={condition.field} onChange={e => setCondition(index, { field: e.target.value, op: "eq", value: "" })}>
          <option value="">选择属性…</option>
          {properties.map(p => <option key={p.apiName} value={p.apiName}>{p.displayName}</option>)}
        </select>
        <select value={condition.op} onChange={e => setCondition(index, { op: e.target.value as Operator })}>
          {operators.map(op => <option key={op.value} value={op.value}>{op.label}</option>)}
        </select>
        {needsValue && (prop?.dataType === "enum" && condition.op === "eq"
          ? <select value={condition.value} onChange={e => setCondition(index, { value: e.target.value })}>
            <option value="">选择…</option>
            {prop.enumValues.map(v => <option key={v.value} value={v.value}>{v.label}</option>)}
          </select>
          : prop?.dataType === "boolean"
            ? <select value={condition.value} onChange={e => setCondition(index, { value: e.target.value })}><option value="">选择…</option><option value="true">是</option><option value="false">否</option></select>
            : <input type={prop?.dataType === "date" ? "date" : "text"} value={condition.value} placeholder={prop?.dataType === "enum" ? prop.enumValues.map(v => v.value).join(",") : "值"} onChange={e => setCondition(index, { value: e.target.value })} />)}
        <button type="button" className="link-button" onClick={() => update({ ...step, conditions: step.conditions.filter((_, i) => i !== index) })}>移除</button>
      </div>;
    })}
    <button type="button" className="link-button" onClick={() => update({ ...step, conditions: [...step.conditions, { field: "", op: "eq", value: "" }] })}>+ 条件</button>
  </div>;
}

function ResultTable({ page, type, openObject }: { page: ObjectSetPage; type: OntologyObjectType; openObject: (type: string, pk: string) => void }) {
  const columns = displayColumns(type);
  return <div className="table-wrap"><table>
    <thead><tr><th>{type.properties.find(p => p.apiName === type.titleProperty)?.displayName ?? "标题"}</th>{columns.map(c => <th key={c.apiName}>{c.displayName}</th>)}</tr></thead>
    <tbody>{page.data.length ? page.data.map(obj => <tr key={obj.primaryKey} className="clickable" tabIndex={0} onClick={() => openObject(obj.objectType, obj.primaryKey)}
      onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openObject(obj.objectType, obj.primaryKey); } }}>
      <td><strong className="ontology-title">{obj.title}</strong><small>{obj.primaryKey}</small></td>
      {columns.map(c => <td key={c.apiName}><PropertyValue property={c} value={obj.properties[c.apiName]} redacted={obj.redactedProperties.includes(c.apiName)} /></td>)}
    </tr>) : <tr><td colSpan={columns.length + 1} className="empty-cell">没有匹配的对象</td></tr>}</tbody>
  </table></div>;
}

function AggregationPanel({ objectSet, type }: { objectSet: ObjectSetSpec; type: OntologyObjectType }) {
  const readable = type.properties.filter(p => p.readable);
  const groupable = readable.filter(p => p.apiName !== type.primaryKey && ["enum", "string", "boolean", "json", "date"].includes(p.dataType));
  const [groupField, setGroupField] = useState(groupable.find(p => p.dataType === "enum")?.apiName ?? "");
  const [metric, setMetric] = useState("count");
  const [metricField, setMetricField] = useState(readable.find(p => NUMERIC.includes(p.dataType))?.apiName ?? "");
  const [result, setResult] = useState<AggregationResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const metricDef = METRICS.find(m => m.value === metric)!;
  const metricFields = readable.filter(p => !metricDef.numeric || NUMERIC.includes(p.dataType));

  async function run() {
    setError(null);
    try {
      const aggregation = metric === "count" ? [{ type: "count", name: "value" }] : [{ type: metric, field: metricField, name: "value" }];
      setResult(await aggregateObjectSet(objectSet, aggregation, groupField ? [{ type: "exact", field: groupField, maxGroupCount: 20 }] : []));
    } catch (err) {
      setError(errorMessage(err, "聚合失败"));
    }
  }

  useEffect(() => { void run(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const prop = type.properties.find(p => p.apiName === groupField);
  const valueProp = type.properties.find(p => p.apiName === metricField);
  const rows = result?.data ?? [];
  const max = Math.max(1, ...rows.map(r => Number(r.metrics[0]?.value ?? 0)));

  return <div className="aggregation-panel">
    <div className="aggregation-controls">
      <strong>聚合</strong>
      <label>分组<select value={groupField} onChange={e => setGroupField(e.target.value)}>
        <option value="">不分组</option>
        {groupable.map(p => <option key={p.apiName} value={p.apiName}>{p.displayName}</option>)}
      </select></label>
      <label>指标<select value={metric} onChange={e => setMetric(e.target.value)}>
        {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
      </select></label>
      {metric !== "count" && <label>属性<select value={metricField} onChange={e => setMetricField(e.target.value)}>
        {metricFields.map(p => <option key={p.apiName} value={p.apiName}>{p.displayName}</option>)}
      </select></label>}
      <button type="button" className="button secondary" onClick={() => void run()}>计算</button>
    </div>
    {error && <p className="action-message error">{error}</p>}
    {result && <>
      <ul className="aggregation-bars">{rows.map((row, index) => {
        const raw = row.metrics[0]?.value;
        const numeric = Number(raw ?? 0);
        const display = raw === null || raw === undefined ? "—" : valueProp?.dataType === "money" && metric !== "count" && metric !== "exactDistinct"
          ? `¥${numeric.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : String(raw);
        return <li key={index}>
          <span className="bar-label">{groupField ? groupLabel(prop, row.group[groupField]) : "全部"}</span>
          <span className="bar-track"><span className="bar-fill" style={{ width: `${Math.max(2, (numeric / max) * 100)}%` }} /></span>
          <span className="bar-value">{display}</span>
        </li>;
      })}</ul>
      <p className="legend">共 {result.totalCount} 个对象{result.excludedItems ? `，${result.excludedItems} 个未计入显示的分组` : ""} · 精度 {result.accuracy}</p>
    </>}
  </div>;
}

function IndexPanel() {
  const [status, setStatus] = useState<IndexStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchIndexStatus().then(setStatus).catch(err => setError(errorMessage(err, "索引状态加载失败")));
  }, []);
  useEffect(() => { load(); }, [load]);

  const fresh = status?.objectTypes.filter(t => t.fresh).length ?? 0;
  const total = status?.objectTypes.length ?? 0;
  const objects = status?.objectTypes.reduce((sum, t) => sum + (t.objectCount ?? 0), 0) ?? 0;

  return <section className="panel index-panel">
    <div className="index-summary">
      <div><strong>对象索引</strong><span>{status ? `${fresh}/${total} 个对象类型为最新 · 共 ${objects} 个对象 · 数据导入或动作写回后自动失效并重建` : "加载中…"}</span></div>
      <div className="index-buttons">
        <button type="button" className="link-button" onClick={() => { setOpen(o => !o); load(); }}>{open ? "收起" : "查看详情"}</button>
        <button type="button" className="button secondary" disabled={busy} onClick={async () => {
          setBusy(true); setError(null);
          try { setStatus(await refreshIndex()); setOpen(true); } catch (err) { setError(errorMessage(err, "重建失败")); } finally { setBusy(false); }
        }}>{busy ? "重建中…" : "全部重建"}</button>
      </div>
    </div>
    {error && <p className="action-message error">{error}</p>}
    {open && status && <div className="table-wrap"><table>
      <thead><tr><th>对象类型</th><th>状态</th><th>对象数</th><th>构建耗时</th><th>构建次数</th><th>缓存命中</th><th>数据集版本</th><th>构建时间</th></tr></thead>
      <tbody>{status.objectTypes.map(t => <tr key={t.objectType}>
        <td><strong>{t.displayName}</strong><small>{t.objectType}</small></td>
        <td>{t.fresh ? <span className="state success">最新</span> : t.indexed ? <span className="state unfinished">待刷新</span> : <span className="muted">未构建</span>}</td>
        <td>{t.objectCount ?? "—"}</td>
        <td>{t.buildMs !== null ? `${t.buildMs} ms` : "—"}</td>
        <td>{t.builds}</td>
        <td>{t.hits}</td>
        <td className="ontology-source">{Object.entries(t.backingDatasets).map(([d, v]) => `${d}@${v}`).join(" · ")}</td>
        <td>{t.builtAt ? formatTime(t.builtAt) : "—"}</td>
      </tr>)}</tbody>
    </table></div>}
  </section>;
}
