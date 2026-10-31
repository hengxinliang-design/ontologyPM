import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError } from "../api";
import { aggregateObjectSet, applyAction, executeFunction, fetchActionLog, fetchLinkedObjects, fetchObject, fetchObjectActions, fetchObjects, fetchOntologyDiff, fetchOntologySchema, fetchOntologyVersions } from "./api";
import "./ontology.css";
import { ObjectSetWorkbench } from "./ObjectSetWorkbench";
import { displayColumns, errorMessage, formatTime, periodParams, PropertyValue } from "./shared";
import { FunctionResultView, money, RawJson } from "./functionResults";
import { isEmptyValue, isWideProperty, propertySections } from "./objectSections";
import type { ActionLogEntry, ActionLogPage, ActionResult, CriterionResult, ObjectActionAvailability, ObjectPage, ObjectReference, OntologyFunction, OntologyObject, OntologyObjectDetail, OntologyObjectType, OntologyParameter, OntologyProperty, OntologySchema, OntologyValueType, OntologyVersion, VersionChange, VersionDiff } from "./types";

type Tab = "model" | "objects" | "objectSets" | "metadata" | "audit";
type Focus = { objectType: string; primaryKey: string };

const SOURCE_KIND_LABEL: Record<string, string> = { system: "系统接口", manual: "人工维护", derived: "派生计算" };
const CARDINALITY_LABEL: Record<string, string> = { one_to_one: "1:1", one_to_many: "1:N", many_to_one: "N:1", many_to_many: "M:N" };
const STATUS_LABEL: Record<string, string> = { succeeded: "成功", rejected: "被拒绝", failed: "失败" };
const PAGE_SIZE = 15;
const LINK_PAGE_SIZE = 10;

export function OntologyExplorer() {
  const [schema, setSchema] = useState<OntologySchema | null>(null);
  const [tab, setTab] = useState<Tab>("model");
  const [error, setError] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState("Project");
  const [focus, setFocus] = useState<Focus | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    fetchOntologySchema()
      .then(data => {
        setSchema(data);
        if (data.objectTypes.length && !data.objectTypes.some(t => t.apiName === "Project")) setSelectedType(data.objectTypes[0].apiName);
      })
      .catch(err => setError(errorMessage(err, "本体模型加载失败")));
  }, []);

  const openObject = useCallback((objectType: string, primaryKey: string) => {
    setSelectedType(objectType);
    setFocus({ objectType, primaryKey });
    setTab("objects");
  }, []);

  if (error) return <div className="error-banner">{error}</div>;
  if (!schema) return <section className="panel"><p className="empty">正在加载本体模型…</p></section>;

  return <div className="ontology">
    <section className="panel ontology-hero">
      <div>
        <p className="eyebrow">SEMANTIC LAYER · v{schema.version} · {schema.checksum.slice(0, 8)}</p>
        <h2>{schema.displayName}</h2>
        <p>{schema.description}</p>
      </div>
      <dl className="ontology-stats">
        <div><dt>对象类型</dt><dd>{schema.objectTypes.length}</dd></div>
        <div><dt>关系类型</dt><dd>{schema.linkTypes.length}</dd></div>
        <div><dt>动作类型</dt><dd>{schema.actionTypes.length}</dd></div>
        <div><dt>函数</dt><dd>{schema.functions.length}</dd></div>
        <div><dt>安全标记</dt><dd>{schema.markings.length}</dd></div>
      </dl>
    </section>

    <div className="ontology-tabs" role="tablist">
      {([["model", "本体模型"], ["objects", "对象浏览"], ["objectSets", "对象集查询"], ["metadata", "元数据与版本"], ["audit", "动作审计"]] as Array<[Tab, string]>).map(([value, label]) =>
        <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{label}</button>)}
    </div>

    {tab === "model" && <ModelView schema={schema} selectedType={selectedType} setSelectedType={setSelectedType}
      browse={type => { setSelectedType(type); setFocus(null); setTab("objects"); }} />}
    {tab === "objects" && <ObjectsView schema={schema} objectType={selectedType}
      setObjectType={type => { setSelectedType(type); setFocus(null); }}
      focus={focus} openObject={openObject} revision={revision} bump={() => setRevision(value => value + 1)} />}
    {tab === "objectSets" && <ObjectSetWorkbench schema={schema} openObject={openObject} />}
    {tab === "metadata" && <MetadataView schema={schema} />}
    {tab === "audit" && <AuditView openObject={openObject} revision={revision} schema={schema} />}
  </div>;
}

// ------------------------------------------------------------------ model view
function ModelView({ schema, selectedType, setSelectedType, browse }: {
  schema: OntologySchema; selectedType: string; setSelectedType: (type: string) => void; browse: (type: string) => void;
}) {
  const type = schema.objectTypes.find(t => t.apiName === selectedType) ?? schema.objectTypes[0];
  const links = schema.linkTypes.filter(l => l.sourceObjectType === type?.apiName || l.targetObjectType === type?.apiName);
  const actions = schema.actionTypes.filter(a => a.objectType === type?.apiName);
  return <>
    <section className="panel">
      <div className="panel-head"><div><h2>本体关系图</h2><p>点击对象类型查看其属性、关系与动作。连线标注基数，悬停可查看关系名称。</p></div></div>
      <OntologyGraph schema={schema} selected={type?.apiName} select={setSelectedType} />
    </section>

    {type && <section className="panel ontology-type-detail">
      <div className="panel-head">
        <div>
          <p className="eyebrow">OBJECT TYPE · {type.apiName}</p>
          <h2>{type.displayName}</h2>
          <p>{type.description}</p>
        </div>
        <button className="button primary" onClick={() => browse(type.apiName)}>浏览对象</button>
      </div>
      <div className="ontology-facts">
        <span><b>主键</b>{type.primaryKey}</span>
        <span><b>标题属性</b>{type.titleProperty}</span>
        <span><b>后端数据集</b>{type.backingDatasets.join("、")}</span>
        <span><b>可见权限</b>{type.readPermissions.join(" / ")}</span>
        <span><b>元数据文件</b><code>{type.metadataSource}</code></span>
        <span><b>数据源规则</b><code>{type.datasource}</code></span>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>属性</th><th>API 名</th><th>类型</th><th>来源</th><th>物理映射 / 规则</th><th>安全标记</th><th>由谁修改</th></tr></thead>
          <tbody>{type.properties.map(prop => <tr key={prop.apiName}>
            <td><strong>{prop.displayName}</strong>{prop.apiName === type.primaryKey && <small>主键</small>}</td>
            <td><code>{prop.apiName}</code></td>
            <td>{prop.dataType}{prop.unitLabel ? ` · ${prop.unitLabel}` : ""}{prop.enumValues.length > 0 && <small className="wrap-text">{prop.enumValues.map(v => v.label).join(" / ")}</small>}</td>
            <td><span className={`source-kind ${prop.sourceKind}`}>{SOURCE_KIND_LABEL[prop.sourceKind]}</span></td>
            <td className="ontology-source">{prop.source}{prop.derivedFrom && <small>函数：{prop.derivedFrom}</small>}</td>
            <td>{prop.marking ? <span className={`marking ${prop.readable ? "" : "locked"}`}>{prop.marking}</span> : "—"}</td>
            <td>{prop.editedBy.length ? prop.editedBy.join("、") : <span className="muted">只读</span>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <div className="ontology-split">
        <div>
          <h3>关系类型</h3>
          <ul className="ontology-list">{links.map(link => {
            const forward = link.sourceObjectType === type.apiName;
            return <li key={`${link.sourceObjectType}-${link.apiName}`}>
              <strong>{forward ? link.displayName : link.reverseDisplayName}</strong>
              <code>{forward ? link.apiName : link.reverseApiName}</code>
              <span>→ {forward ? link.targetObjectType : link.sourceObjectType} · {CARDINALITY_LABEL[forward ? link.cardinality : reverseCardinality(link.cardinality)]}{link.via ? ` · 经由 ${link.via}` : ""}</span>
            </li>;
          })}</ul>
        </div>
        <div>
          <h3>动作类型</h3>
          {actions.length ? <ul className="ontology-list">{actions.map(action => <li key={action.apiName}>
            <strong>{action.displayName}</strong><code>{action.apiName}</code>
            <span>写回 {action.writebackDatasets.join("、")}{action.permitted ? "" : " · 当前用户无权执行"}</span>
          </li>)}</ul> : <p className="muted">该对象类型只读，没有动作。</p>}
        </div>
      </div>
    </section>}

    <section className="panel">
      <div className="panel-head"><div><h2>动作类型</h2><p>动作是修改人工维护数据的唯一入口：权限 → 参数校验 → 提交条件 → 写回 → 审计。</p></div></div>
      <div className="table-wrap"><table>
        <thead><tr><th>动作</th><th>目标对象</th><th>提交条件</th><th>写回数据集</th><th>所需权限</th></tr></thead>
        <tbody>{schema.actionTypes.map(action => <tr key={action.apiName}>
          <td><strong>{action.displayName}</strong><small><code>{action.apiName}</code></small></td>
          <td>{action.objectType}</td>
          <td>{action.submissionCriteria.length ? <ul className="plain-list">{action.submissionCriteria.map(c => <li key={c}>{c}</li>)}</ul> : "—"}</td>
          <td>{action.writebackDatasets.join("、")}</td>
          <td>{action.requiredPermissions.join(" / ")}{!action.permitted && <small className="locked-text">无权执行</small>}</td>
        </tr>)}</tbody>
      </table></div>
    </section>

    {schema.functions.some(fn => fn.permitted && !fn.parameters.some(p => p.dataType === "object_ref")) && <section className="panel">
      <div className="panel-head"><div><h2>运行全局函数</h2><p>不针对单个对象的函数（如资源健康、资源测算），按你的权限计算；针对对象的函数在对象详情中运行。</p></div></div>
      <div className="detail-block">{schema.functions.filter(fn => fn.permitted && !fn.parameters.some(p => p.dataType === "object_ref"))
        .map(fn => <FunctionRunner key={fn.apiName} fn={fn} valueTypes={schema.valueTypes} />)}</div>
    </section>}

    <div className="ontology-split two-panels">
      <section className="panel">
        <div className="panel-head"><div><h2>函数</h2><p>可复用的业务规则，派生属性均由函数定义。</p></div></div>
        <ul className="ontology-list padded">{schema.functions.map(fn => <li key={fn.apiName}>
          <strong>{fn.displayName}</strong><code>{fn.apiName}({fn.parameters.map(p => p.apiName).join(", ")}) → {fn.outputType}</code>
          <span>{fn.description}{!fn.permitted && " · 当前用户无权调用"}</span>
        </li>)}</ul>
      </section>
      <section className="panel">
        <div className="panel-head"><div><h2>安全标记（Safe）</h2><p>带标记的属性仅对具备相应权限的用户可见，其余用户看到脱敏值。</p></div></div>
        <ul className="ontology-list padded">{schema.markings.map(marking => <li key={marking.apiName}>
          <strong><span className={`marking ${marking.granted ? "" : "locked"}`}>{marking.apiName}</span> {marking.displayName}</strong>
          <span>{marking.description}</span>
          <span>需要权限：{marking.permissions.join(" / ")} · {marking.granted ? "你可以查看" : "你无权查看"}</span>
        </li>)}</ul>
      </section>
    </div>
  </>;
}

function reverseCardinality(value: string): string {
  return value === "one_to_many" ? "many_to_one" : value === "many_to_one" ? "one_to_many" : value;
}

function OntologyGraph({ schema, selected, select }: { schema: OntologySchema; selected?: string; select: (type: string) => void }) {
  const width = 820, height = 420, nodeW = 132, nodeH = 46;
  const positions = useMemo(() => {
    const result: Record<string, { x: number; y: number }> = {};
    const center = schema.objectTypes.find(t => t.apiName === "Project") ?? schema.objectTypes[0];
    const others = schema.objectTypes.filter(t => t !== center);
    if (center) result[center.apiName] = { x: width / 2, y: height / 2 };
    others.forEach((type, index) => {
      const angle = -Math.PI / 2 + (index / others.length) * Math.PI * 2;
      result[type.apiName] = { x: width / 2 + Math.cos(angle) * 320, y: height / 2 + Math.sin(angle) * 160 };
    });
    return result;
  }, [schema]);
  return <div className="ontology-graph">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="本体对象类型关系图">
      {schema.linkTypes.map(link => {
        const a = positions[link.sourceObjectType], b = positions[link.targetObjectType];
        if (!a || !b) return null;
        const active = selected === link.sourceObjectType || selected === link.targetObjectType;
        return <g key={`${link.sourceObjectType}-${link.apiName}-${link.targetObjectType}`} className={`graph-edge ${active ? "active" : ""}`}>
          <title>{`${link.sourceObjectType}.${link.apiName} → ${link.targetObjectType}（反向 ${link.reverseApiName}）`}</title>
          <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeDasharray={link.cardinality === "many_to_many" ? "5 4" : undefined} />
          <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 4}>{CARDINALITY_LABEL[link.cardinality]}</text>
        </g>;
      })}
      {schema.objectTypes.map(type => {
        const p = positions[type.apiName];
        if (!p) return null;
        return <g key={type.apiName} className={`graph-node ${selected === type.apiName ? "active" : ""}`} transform={`translate(${p.x - nodeW / 2}, ${p.y - nodeH / 2})`}
          onClick={() => select(type.apiName)} role="button" tabIndex={0} onKeyDown={event => { if (event.key === "Enter") select(type.apiName); }}>
          <rect width={nodeW} height={nodeH} rx={10} />
          <text x={nodeW / 2} y={20} className="graph-node-title">{type.displayName}</text>
          {/* Long API names (e.g. AnnualOperatingPlan) are condensed to stay inside the box. */}
          <text x={nodeW / 2} y={36} className="graph-node-api"
            {...(type.apiName.length > 16 ? { textLength: nodeW - 14, lengthAdjust: "spacingAndGlyphs" } : {})}>{type.apiName}</text>
        </g>;
      })}
    </svg>
  </div>;
}

// ---------------------------------------------------------------- object view
// Types that carry a signing year (contracts, projects) get a year filter; the options are the years in the data.
const YEAR_FIELD = "signedYear";

function ObjectsView({ schema, objectType, setObjectType, focus, openObject, revision, bump }: {
  schema: OntologySchema; objectType: string; setObjectType: (type: string) => void;
  focus: Focus | null; openObject: (type: string, pk: string) => void; revision: number; bump: () => void;
}) {
  const type = schema.objectTypes.find(t => t.apiName === objectType) ?? schema.objectTypes[0];
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [year, setYear] = useState("");
  const [years, setYears] = useState<string[]>([]);
  // Statistics period (ontology 7.2.0): "" = all months; the detail panel follows the same month.
  const [month, setMonth] = useState("");
  const [monthTo, setMonthTo] = useState("");
  const [months, setMonths] = useState<string[]>([]);
  // Types whose period defaults to the latest month start there; "全部月份" stays selectable afterwards.
  const [monthReady, setMonthReady] = useState("");
  const [result, setResult] = useState<ObjectPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hasYear = Boolean(type?.properties.some(p => p.apiName === YEAR_FIELD));

  useEffect(() => { setPage(1); setQuery(""); setQueryInput(""); setYear(""); setMonth(""); setMonthTo(""); setMonths([]); setMonthReady(""); }, [objectType]);
  useEffect(() => {
    setYears([]);
    if (!type || !hasYear) return;
    aggregateObjectSet({ type: "base", objectType: type.apiName }, [{ type: "count", name: "value" }], [{ type: "exact", field: YEAR_FIELD }])
      .then(res => setYears(res.data.map(row => row.group[YEAR_FIELD]).filter(v => v != null).map(String).sort().reverse()))
      .catch(() => setYears([]));
  }, [type, hasYear, revision]);
  useEffect(() => {
    if (!type) return;
    setLoading(true); setError(null);
    if (type.period?.defaultView === "latest" && monthReady !== type.apiName) {
      fetchObjects(type.apiName, { page: 1, pageSize: 1 })
        .then(first => { setMonths(first.meta.period?.options ?? []); setMonth(first.meta.period?.defaultMonth ?? ""); setMonthReady(type.apiName); })
        .catch(err => { setError(errorMessage(err, "对象加载失败")); setLoading(false); });
      return;
    }
    const period = periodParams(type, month, monthTo);
    fetchObjects(type.apiName, { q: query || undefined, page, pageSize: PAGE_SIZE, where: year ? { [YEAR_FIELD]: year } : undefined, ...period })
      .then(page => { setResult(page); if (page.meta.period) setMonths(page.meta.period.options); })
      .catch(err => setError(errorMessage(err, "对象加载失败")))
      .finally(() => setLoading(false));
  }, [type, query, year, month, monthTo, monthReady, page, revision]);

  if (!type) return null;
  const columns = displayColumns(type, Boolean(month) && type.properties.some(p => p.periodic));
  const totalPages = Math.max(1, Math.ceil((result?.meta.totalObjects ?? 0) / PAGE_SIZE));

  return <div className="ontology-objects">
    <div className="ontology-chips">{schema.objectTypes.map(t =>
      <button key={t.apiName} className={t.apiName === type.apiName ? "active" : ""} onClick={() => setObjectType(t.apiName)}>{t.displayName}</button>)}
    </div>
    <div className={`ontology-browser ${focus ? "with-detail" : ""}`}>
      <section className="panel">
        <div className="panel-head">
          <div><h2>{type.pluralDisplayName}</h2><p>{result ? `共 ${result.meta.totalObjects} 个对象` : "加载中"} · 主键 {type.primaryKey}</p></div>
          {type.period && <label className="year-filter" title={type.period.description}>{type.period.range ? "起始月份" : type.period.displayName}<select value={month} onChange={e => { setPage(1); setMonth(e.target.value); if (!e.target.value || e.target.value > monthTo) setMonthTo(""); }}>
            <option value="">全部月份</option>{(type.period.range ? [...months].reverse() : months).map(m => <option key={m} value={m}>{m}</option>)}
          </select></label>}
          {type.period?.range && <label className="year-filter" title="不选则只看起始月份">结束月份<select value={monthTo} disabled={!month} onChange={e => { setPage(1); setMonthTo(e.target.value); }}>
            <option value="">同起始月份</option>{[...months].reverse().filter(m => m >= month).map(m => <option key={m} value={m}>{m}</option>)}
          </select></label>}
          {hasYear && <label className="year-filter">签约年度<select value={year} onChange={e => { setPage(1); setYear(e.target.value); }}>
            <option value="">全部年度</option>{years.map(y => <option key={y} value={y}>{y} 年</option>)}
          </select></label>}
          <form className="search" onSubmit={(event: FormEvent) => { event.preventDefault(); setPage(1); setQuery(queryInput.trim()); }}>
            <input value={queryInput} onChange={e => setQueryInput(e.target.value)} placeholder={`搜索${type.displayName}`} />
            <button type="submit">搜索</button>
          </form>
        </div>
        {error && <div className="error-banner inset">{error}</div>}
        <div className="table-wrap"><table>
          <thead><tr><th>{type.properties.find(p => p.apiName === type.titleProperty)?.displayName ?? "标题"}</th>{columns.map(c => <th key={c.apiName}>{c.displayName}</th>)}</tr></thead>
          <tbody>
            {loading && !result ? <tr><td colSpan={columns.length + 1} className="empty-cell">加载中…</td></tr>
              : result?.data.length ? result.data.map(obj => <tr key={obj.primaryKey}
                className={`clickable ${focus?.primaryKey === obj.primaryKey && focus.objectType === obj.objectType ? "selected-row" : ""}`}
                onClick={() => openObject(obj.objectType, obj.primaryKey)} tabIndex={0}
                onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openObject(obj.objectType, obj.primaryKey); } }}>
                <td><strong className="ontology-title">{obj.title}</strong><small>{obj.primaryKey}</small></td>
                {columns.map(c => <td key={c.apiName}><PropertyValue property={c} value={obj.properties[c.apiName]} redacted={obj.redactedProperties.includes(c.apiName)} /></td>)}
              </tr>)
              : <tr><td colSpan={columns.length + 1} className="empty-cell">没有匹配的对象</td></tr>}
          </tbody>
        </table></div>
        {totalPages > 1 && <div className="pagination">
          <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
          <span>第 {page} / {totalPages} 页</span>
          <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>下一页</button>
        </div>}
      </section>
      {focus && <ObjectDetail key={`${focus.objectType}:${focus.primaryKey}:${revision}:${month}`} schema={schema} focus={focus} openObject={openObject} changed={bump}
        month={focus.objectType === type.apiName && type.period ? month || undefined : undefined} />}
    </div>
  </div>;
}

function ObjectDetail({ schema, focus, openObject, changed, month }: {
  schema: OntologySchema; focus: Focus; openObject: (type: string, pk: string) => void; changed: () => void; month?: string;
}) {
  const type = schema.objectTypes.find(t => t.apiName === focus.objectType);
  const [object, setObject] = useState<OntologyObjectDetail | null>(null);
  const [actions, setActions] = useState<ObjectActionAvailability[]>([]);
  const [history, setHistory] = useState<ActionLogEntry[]>([]);
  const [activeLink, setActiveLink] = useState<string | null>(null);
  const [linked, setLinked] = useState<ObjectPage | null>(null);
  const [linkPage, setLinkPage] = useState(1);
  const [activeAction, setActiveAction] = useState<ObjectActionAvailability | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hideEmpty, setHideEmpty] = useState(false);

  useEffect(() => {
    setError(null);
    Promise.all([
      fetchObject(focus.objectType, focus.primaryKey, month),
      fetchObjectActions(focus.objectType, focus.primaryKey),
      fetchActionLog({ objectType: focus.objectType, primaryKey: focus.primaryKey, page: 1, pageSize: 10 })
    ]).then(([obj, available, log]) => { setObject(obj); setActions(available); setHistory(log.data); })
      .catch(err => setError(errorMessage(err, "对象加载失败")));
  }, [focus]);

  useEffect(() => { setLinkPage(1); }, [focus, activeLink]);
  useEffect(() => {
    if (!activeLink) { setLinked(null); return; }
    fetchLinkedObjects(focus.objectType, focus.primaryKey, activeLink, linkPage, LINK_PAGE_SIZE, month)
      .then(setLinked)
      .catch(err => setError(errorMessage(err, "关系加载失败")));
  }, [focus, activeLink, linkPage, month]);
  const linkPages = Math.max(1, Math.ceil((linked?.meta.totalObjects ?? 0) / LINK_PAGE_SIZE));

  const functions = schema.functions.filter(fn => fn.parameters.some(p => p.dataType === "object_ref" && p.objectType === focus.objectType));

  if (error) return <section className="panel ontology-detail"><div className="error-banner inset">{error}</div></section>;
  if (!object || !type) return <section className="panel ontology-detail"><p className="empty">加载中…</p></section>;
  const statMonth = type.period ? (month ?? object.period?.defaultMonth ?? null) : null;

  return <section className="panel ontology-detail">
    <div className="panel-head">
      <div><p className="eyebrow">{type.displayName} · {object.primaryKey}</p><h2>{object.title}</h2></div>
    </div>

    <div className="detail-block">
      <div className="property-head">
        <h3>属性</h3>
        <p className="legend"><span className="source-dot system" />系统接口 <span className="source-dot manual" />人工维护 <span className="source-dot derived" />派生计算</p>
        <label className="hide-empty"><input type="checkbox" checked={hideEmpty} onChange={e => setHideEmpty(e.target.checked)} />隐藏空值</label>
      </div>
      {propertySections(type).map(section => {
        const rows = section.properties.filter(prop => !hideEmpty || object.redactedProperties.includes(prop.apiName) || !isEmptyValue(object.properties[prop.apiName]));
        if (!rows.length) return null;
        const periodic = section.properties.every(prop => prop.periodic);
        return <details key={section.title} className="property-section" open>
          <summary>{section.title}{periodic && statMonth && <em className="period-tag">{month ? statMonth : `${statMonth}（最近有工时的月份）`}</em>}<small>{rows.length}</small></summary>
          <dl className="property-rows">{rows.map(prop => <div key={prop.apiName} className={isWideProperty(prop) ? "wide" : undefined}>
            <dt title={`${SOURCE_KIND_LABEL[prop.sourceKind]} · ${prop.apiName}${prop.source ? `\n${prop.source}` : ""}`}><span className={`source-dot ${prop.sourceKind}`} />{prop.displayName}</dt>
            <dd>{object.references?.[prop.apiName] && !object.redactedProperties.includes(prop.apiName)
              ? <ReferenceValue reference={object.references[prop.apiName]} openObject={openObject} />
              : <PropertyValue property={prop} value={object.properties[prop.apiName]} redacted={object.redactedProperties.includes(prop.apiName)} />}</dd>
          </div>)}</dl>
        </details>;
      })}
    </div>

    <div className="detail-block">
      <h3>关系</h3>
      {object.links.length ? <div className="ontology-chips small">{object.links.map(link =>
        <button key={link.apiName} className={activeLink === link.apiName ? "active" : ""} disabled={link.count === 0}
          onClick={() => setActiveLink(activeLink === link.apiName ? null : link.apiName)}>
          {link.displayName} <b>{link.count}</b>
        </button>)}</div> : <p className="muted">没有可见的关系。</p>}
      {linked && <div className="linked-panel">
        <ul className="linked-list">{linked.data.map(item =>
          <li key={item.primaryKey}><button onClick={() => openObject(item.objectType, item.primaryKey)}>
            <strong>{item.title}</strong><small>{item.objectType} · {item.primaryKey}</small>
          </button></li>)}</ul>
        {linkPages > 1 && <div className="pagination compact">
          <button disabled={linkPage <= 1} onClick={() => setLinkPage(p => p - 1)}>上一页</button>
          <span>共 {linked.meta.totalObjects} 条 · 第 {linkPage} / {linkPages} 页</span>
          <button disabled={linkPage >= linkPages} onClick={() => setLinkPage(p => p + 1)}>下一页</button>
        </div>}
      </div>}
    </div>

    {actions.length > 0 && !actions.some(action => action.permitted) && <div className="detail-block">
      <h3>动作</h3>
      <p className="detail-empty">本体浏览器只读：动作仅管理员可在这里执行，日常维护请在业务页面进行。</p>
    </div>}
    {actions.some(action => action.permitted) && <div className="detail-block">
      <h3>动作</h3>
      <div className="action-buttons">{actions.filter(action => action.permitted).map(action =>
        <button key={action.apiName} className={`action-button ${action.tone} ${activeAction?.apiName === action.apiName ? "active" : ""}`}
          disabled={!action.permitted}
          title={action.available ? action.description : action.criteria.filter(c => !c.passed).map(c => c.reason).join("；") || "当前用户无权执行"}
          onClick={() => setActiveAction(activeAction?.apiName === action.apiName ? null : action)}>
          {action.displayName}{!action.available && <span className="blocked">受限</span>}
        </button>)}</div>
      {activeAction && <ActionForm key={activeAction.apiName} action={activeAction} type={type} object={object}
        close={() => setActiveAction(null)}
        done={result => {
          setActiveAction(null);
          const deleted = result.edits?.some(e => e.operation === "delete" && e.primaryKey === object.primaryKey);
          const created = result.edits?.find(e => e.operation === "create");
          changed();
          if (deleted) return;
          if (created) openObject(created.objectType, created.primaryKey);
        }} />}
    </div>}

    {functions.length > 0 && <div className="detail-block">
      <h3>函数</h3>
      {functions.map(fn => <FunctionRunner key={fn.apiName} fn={fn} objectType={focus.objectType} primaryKey={focus.primaryKey} valueTypes={schema.valueTypes} month={month} />)}
    </div>}

    <div className="detail-block">
      <h3>动作历史</h3>
      {history.length ? <ul className="history-list">{history.map(entry => <li key={entry.logId}>
        <span className={`log-status ${entry.status}`}>{STATUS_LABEL[entry.status]}</span>
        <div><strong>{entry.actionDisplayName}</strong><small>{entry.actor} · {formatTime(entry.executedAt)}{entry.channel === "legacy_api" ? " · 原有接口" : ""}</small>
          {entry.message && <small>{entry.message}</small>}</div>
      </li>)}</ul> : <p className="muted">暂无动作记录。</p>}
    </div>
  </section>;
}

// --------------------------------------------------------------- action form
function ActionForm({ action, type, object, close, done }: {
  action: ObjectActionAvailability; type: OntologyObjectType; object: OntologyObjectDetail;
  close: () => void; done: (result: ActionResult) => void;
}) {
  const editable = action.parameters.filter(p => p.apiName !== action.targetParameter);
  const [values, setValues] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const param of editable) if (param.currentValue) initial[param.apiName] = param.currentValue;
    if (action.createsObject) return initial;
    for (const param of editable) {
      const current = object.properties[param.apiName];
      if (current !== undefined && current !== null && type.properties.some(p => p.apiName === param.apiName)) {
        initial[param.apiName] = Array.isArray(current) ? current.join("、") : String(current);
      }
    }
    return initial;
  });
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const [criteria, setCriteria] = useState<CriterionResult[]>(action.criteria);
  const [paramErrors, setParamErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => { formRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, []);

  function parameters(): Record<string, unknown> {
    const result: Record<string, unknown> = { [action.targetParameter]: object.primaryKey };
    for (const param of editable) {
      if (!dirty.has(param.apiName)) continue;
      const raw = values[param.apiName] ?? "";
      if (param.dataType === "boolean") { if (raw) result[param.apiName] = raw === "true"; continue; }
      if (param.options) { if (raw === CLEAR_REFERENCE) result[param.apiName] = null; else if (raw) result[param.apiName] = raw; continue; }
      result[param.apiName] = raw === "" ? null : raw;
    }
    return result;
  }

  async function run(validateOnly: boolean) {
    setBusy(true); setMessage(null); setParamErrors({});
    try {
      const result = await applyAction(action.apiName, parameters(), validateOnly);
      setCriteria(result.criteria);
      setParamErrors(Object.fromEntries(result.parameterErrors.map(e => [e.parameter, e.message])));
      if (validateOnly) setMessage(result.valid ? "预检通过，可以提交。" : "预检未通过，请查看下方条件。");
      else done(result);
    } catch (err) {
      const body = err instanceof ApiError ? err.body as { errors?: { criteria?: CriterionResult[]; parameterErrors?: { parameter: string; message: string }[] } } : undefined;
      if (body?.errors?.criteria?.length) setCriteria(body.errors.criteria);
      if (body?.errors?.parameterErrors) setParamErrors(Object.fromEntries(body.errors.parameterErrors.map(e => [e.parameter, e.message])));
      setMessage(errorMessage(err, "动作执行失败"));
    } finally {
      setBusy(false);
    }
  }

  return <form ref={formRef} className="action-form" onSubmit={event => { event.preventDefault(); void run(false); }}>
    <div className="action-form-head">
      <div><strong>{action.displayName}</strong><p>{action.description}</p></div>
      <button type="button" className="link-button" onClick={close}>关闭</button>
    </div>
    {editable.length > 0 && <div className="action-fields">{editable.map(param =>
      <ParameterInput key={param.apiName} param={param} value={values[param.apiName] ?? ""} error={paramErrors[param.apiName]}
        onChange={value => { setValues(v => ({ ...v, [param.apiName]: value })); setDirty(d => new Set(d).add(param.apiName)); }} />)}
    </div>}
    {criteria.length > 0 && <ul className="criteria-list">{criteria.map(c =>
      <li key={c.description} className={c.passed ? "passed" : "failed"} title={c.description}>{c.passed ? `✓ ${c.description}` : `✕ ${c.reason ?? c.description}`}</li>)}</ul>}
    <p className="action-meta">写回：{action.writebackDatasets.join("、")} · 修改：{action.edits.join("、")}</p>
    {message && <p className="action-message">{message}</p>}
    <div className="action-form-buttons">
      <button type="button" className="button secondary" disabled={busy} onClick={() => void run(true)}>预检</button>
      <button type="submit" className={`button ${action.tone === "danger" ? "danger" : "primary"}`} disabled={busy}>{busy ? "执行中…" : "提交"}</button>
    </div>
  </form>;
}

/** A key pointing at another object: its title, with the key underneath, opening that object. */
function ReferenceValue({ reference, openObject }: { reference: ObjectReference; openObject: (type: string, pk: string) => void }) {
  return <button type="button" className="reference-value" onClick={() => openObject(reference.objectType, reference.primaryKey)}>
    <strong>{reference.title || reference.primaryKey}</strong>{reference.title && <small>{reference.primaryKey}</small>}
  </button>;
}

function detailText(value: unknown, unit: string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  if (unit === "yuan") return money(value);
  return unit === "percent" ? `${value}%` : String(value);
}

// An object-reference choice that removes the link (sent as null); an empty choice leaves the parameter out.
const CLEAR_REFERENCE = "__clear__";

function ParameterInput({ param, value, error, onChange }: { param: OntologyParameter; value: string; error?: string; onChange: (value: string) => void }) {
  const label = <span>{param.displayName}{param.required && <em>*</em>}</span>;
  let control;
  if (param.options) {
    control = <select value={value} onChange={e => onChange(e.target.value)}>
      <option value="">{param.required ? "请选择" : param.currentValue ? "不修改" : "不关联"}</option>
      {!param.required && param.currentValue && <option value={CLEAR_REFERENCE}>解除关联</option>}
      {param.options.map(o => <option key={o.value} value={o.value}>{o.label}{o.value === param.currentValue ? "（当前）" : ""}</option>)}
    </select>;
    const chosen = param.options.find(o => o.value === value);
    if (param.options.length === 0) control = <>{control}<small className="action-hint">本项目还没有可选的{param.displayName.replace(/^关联/, "")}</small></>;
    else if (chosen?.details?.length) control = <>{control}<small className="action-hint">{chosen.details.map(d => `${d.label} ${detailText(d.value, d.unit)}`).join(" · ")}{param.description ? `；${param.description}` : ""}</small></>;
  } else if (param.dataType === "enum") {
    control = <select value={value} onChange={e => onChange(e.target.value)}>
      <option value="">{param.required ? "请选择" : "不修改"}</option>
      {param.enumValues.map(v => <option key={v.value} value={v.value}>{v.label}</option>)}
    </select>;
  } else if (param.dataType === "boolean") {
    control = <select value={value} onChange={e => onChange(e.target.value)}>
      <option value="">不修改</option><option value="true">是</option><option value="false">否</option>
    </select>;
  } else {
    const inputType = param.dataType === "date" ? "date" : "text";
    const mode = ["integer", "decimal", "money", "percent"].includes(param.dataType) ? "decimal" : undefined;
    control = <input type={inputType} inputMode={mode} value={value} placeholder={param.description || undefined} onChange={e => onChange(e.target.value)} />;
  }
  return <label className={`action-field ${error ? "invalid" : ""}`}>{label}{control}{error && <small>{error}</small>}</label>;
}

// ------------------------------------------------------------ function runner
// Runs a function on the focused object, or a global function (no object parameter) when objectType is omitted.
function FunctionRunner({ fn, objectType, primaryKey, valueTypes, month }: { fn: OntologyFunction; objectType?: string; primaryKey?: string; valueTypes: OntologyValueType[]; month?: string }) {
  const objectParam = objectType ? fn.parameters.find(p => p.dataType === "object_ref" && p.objectType === objectType) : undefined;
  const extra = fn.parameters.filter(p => p !== objectParam);
  const thisMonth = month ?? new Date().toISOString().slice(0, 7);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(extra.filter(p => p.required).map(p => [p.apiName, p.apiName.toLowerCase().includes("month") ? thisMonth : ""])));
  const [result, setResult] = useState<unknown>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true); setError(null);
    try {
      const params: Record<string, unknown> = objectParam ? { [objectParam.apiName]: primaryKey } : {};
      for (const [key, value] of Object.entries(values)) if (value) params[key] = value;
      setResult(await executeFunction(fn.apiName, params));
    } catch (err) {
      setError(errorMessage(err, "函数执行失败"));
    } finally {
      setBusy(false);
    }
  }

  return <div className="function-runner">
    <div className="function-head">
      <div><strong>{fn.displayName}</strong><code>{fn.apiName} → {fn.outputType}</code></div>
      <div className="function-controls">
        {extra.map(p => <input key={p.apiName} value={values[p.apiName] ?? ""} placeholder={`${p.displayName}${p.description ? `（${p.description}）` : ""}`}
          onChange={e => setValues(v => ({ ...v, [p.apiName]: e.target.value }))} />)}
        <button className="button secondary" disabled={busy || !fn.permitted} title={fn.permitted ? fn.description : "当前用户无权调用"} onClick={() => void run()}>{busy ? "计算中…" : "运行"}</button>
      </div>
    </div>
    {error && <p className="action-message error">{error}</p>}
    {result !== undefined && <div className="function-output">
      <FunctionResultView outputType={fn.outputType} result={result} valueTypes={valueTypes} />
      <RawJson value={result} />
    </div>}
  </div>;
}

// ----------------------------------------------------------------- audit view
function AuditView({ schema, openObject, revision }: { schema: OntologySchema; openObject: (type: string, pk: string) => void; revision: number }) {
  const [objectType, setObjectType] = useState("");
  const [page, setPage] = useState(1);
  const [log, setLog] = useState<ActionLogPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchActionLog({ objectType: objectType || undefined, page, pageSize: 20 })
      .then(setLog)
      .catch(err => setError(errorMessage(err, "审计日志加载失败")));
  }, [objectType, page, revision]);

  const visibleActionTypes = useMemo(() => new Set(schema.objectTypes.map(t => t.apiName)), [schema]);

  return <section className="panel">
    <div className="panel-head">
      <div><h2>动作审计日志</h2><p>本体动作与原有 REST 写接口的每一次写入（以及被拒绝的尝试）都会记录在此。</p></div>
    </div>
    <div className="filters">
      <select value={objectType} onChange={e => { setObjectType(e.target.value); setPage(1); }}>
        <option value="">全部对象类型</option>
        {schema.objectTypes.map(t => <option key={t.apiName} value={t.apiName}>{t.displayName}</option>)}
      </select>
    </div>
    {error && <div className="error-banner inset">{error}</div>}
    <div className="table-wrap"><table>
      <thead><tr><th>时间</th><th>动作</th><th>对象</th><th>执行人</th><th>渠道</th><th>结果</th><th>说明</th></tr></thead>
      <tbody>{log?.data.length ? log.data.map(entry => <tr key={entry.logId}>
        <td>{formatTime(entry.executedAt)}</td>
        <td><strong>{entry.actionDisplayName}</strong><small><code>{entry.actionType}</code></small></td>
        <td>{visibleActionTypes.has(entry.objectType)
          ? <button className="link-button" onClick={() => openObject(entry.objectType, entry.primaryKey)}>{entry.objectType} · {entry.primaryKey}</button>
          : `${entry.objectType} · ${entry.primaryKey}`}
          {entry.edits.length > 0 && <small>{entry.edits.map(e => `${e.operation} ${e.objectType}`).join("、")}</small>}</td>
        <td>{entry.actor}</td>
        <td>{entry.channel === "legacy_api" ? "原有接口" : "本体动作"}</td>
        <td><span className={`log-status ${entry.status}`}>{STATUS_LABEL[entry.status]}</span></td>
        <td className="ontology-source">{entry.message ?? "—"}</td>
      </tr>) : <tr><td colSpan={7} className="empty-cell">暂无动作记录</td></tr>}</tbody>
    </table></div>
    {(log?.meta.totalPages ?? 1) > 1 && <div className="pagination">
      <button disabled={page <= 1} onClick={() => setPage(p => p - 1)}>上一页</button>
      <span>第 {page} / {log?.meta.totalPages} 页 · 共 {log?.meta.totalEntries} 条</span>
      <button disabled={!log || page >= log.meta.totalPages} onClick={() => setPage(p => p + 1)}>下一页</button>
    </div>}
  </section>;
}

// -------------------------------------------------------------- metadata view
const CHANGE_LABEL: Record<string, string> = { added: "新增", removed: "删除", changed: "修改" };
const KIND_LABEL: Record<string, string> = {
  Ontology: "本体", ValueType: "值类型", Marking: "安全标记", ObjectType: "对象类型",
  LinkType: "关系类型", ActionType: "动作类型", Function: "函数"
};

function compact(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 160 ? `${text.slice(0, 157)}…` : text;
}

function MetadataView({ schema }: { schema: OntologySchema }) {
  const [versions, setVersions] = useState<OntologyVersion[] | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchOntologyVersions()
      .then(list => {
        setVersions(list);
        if (list.length > 1) { setTo(list[0].version); setFrom(list[1].version); }
      })
      .catch(err => setError(errorMessage(err, "版本历史加载失败")));
  }, []);

  useEffect(() => {
    if (!from || !to || from === to) { setDiff(null); return; }
    fetchOntologyDiff(from, to).then(setDiff).catch(err => setError(errorMessage(err, "版本差异加载失败")));
  }, [from, to]);

  const files = useMemo(() => {
    const grouped = new Map<string, string[]>();
    for (const type of schema.objectTypes) grouped.set(type.metadataSource, [...(grouped.get(type.metadataSource) ?? []), type.displayName]);
    return [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [schema]);

  return <>
    <section className="panel">
      <div className="panel-head"><div>
        <h2>元数据驱动</h2>
        <p>本体结构写在 <code>backend/app/ontology/metadata/*.yaml</code>（kind / version / definition 文档），业务规则写在 <code>rules.py</code> 并按名称引用。启动时编译校验，内容变更必须升级版本号。</p>
      </div></div>
      <div className="ontology-facts">
        <span><b>当前版本</b>v{schema.version}</span>
        <span><b>内容指纹</b><code>{schema.checksum.slice(0, 16)}</code></span>
        <span><b>值类型</b>{schema.valueTypes.length}</span>
      </div>
      <div className="ontology-split">
        <div>
          <h3>对象类型定义文件</h3>
          <ul className="ontology-list">{files.map(([file, types]) => <li key={file}><code>{file}</code><span>{types.join("、")}</span></li>)}</ul>
        </div>
        <div>
          <h3>值类型（枚举）</h3>
          <ul className="ontology-list">{schema.valueTypes.map(v => <li key={v.apiName}>
            <strong>{v.displayName} <code>{v.apiName}</code></strong>
            <span>{v.values.map(x => `${x.label}（${x.value}）`).join(" · ")}</span>
          </li>)}</ul>
        </div>
      </div>
    </section>

    <section className="panel">
      <div className="panel-head"><div><h2>版本历史</h2><p>每个版本登记后不可修改：同一版本号内容不同会拒绝启动，新版本号必须递增。</p></div></div>
      {error && <div className="error-banner inset">{error}</div>}
      <div className="table-wrap"><table>
        <thead><tr><th>版本</th><th>状态</th><th>内容指纹</th><th>文档数</th><th>登记时间</th><th>最近启用</th></tr></thead>
        <tbody>{versions?.length ? versions.map(v => <tr key={v.version}>
          <td><strong>v{v.version}</strong></td>
          <td>{v.active ? <span className="state success">当前</span> : <span className="muted">历史</span>}</td>
          <td><code>{v.checksum.slice(0, 12)}</code></td>
          <td>{v.documentCount}</td>
          <td>{formatTime(v.registeredAt)}</td>
          <td>{formatTime(v.lastActivatedAt)}</td>
        </tr>) : <tr><td colSpan={6} className="empty-cell">{versions ? "暂无版本记录" : "加载中…"}</td></tr>}</tbody>
      </table></div>
    </section>

    <section className="panel">
      <div className="panel-head">
        <div><h2>版本差异</h2><p>按对象、属性、参数逐项比较；删除属性、修改类型、新增必填参数会标记为破坏性变更。</p></div>
        {(versions?.length ?? 0) > 1 && <div className="version-picker">
          <select value={from} onChange={e => setFrom(e.target.value)}>{versions!.map(v => <option key={v.version} value={v.version}>v{v.version}</option>)}</select>
          <span>→</span>
          <select value={to} onChange={e => setTo(e.target.value)}>{versions!.map(v => <option key={v.version} value={v.version}>v{v.version}</option>)}</select>
        </div>}
      </div>
      {(versions?.length ?? 0) <= 1 ? <p className="empty">目前只有一个版本。修改 YAML 元数据并升级 ontology.yaml 中的 version，重启服务后即可在这里比较差异。</p>
        : from === to ? <p className="empty">请选择两个不同的版本。</p>
        : diff && <>
          <div className="ontology-facts">
            <span><b>新增</b>{diff.summary.added}</span><span><b>删除</b>{diff.summary.removed}</span>
            <span><b>修改</b>{diff.summary.changed}</span><span><b>破坏性</b>{diff.summary.breaking}</span>
          </div>
          {diff.changes.length ? <ul className="change-list">{diff.changes.map(change => <ChangeItem key={`${change.kind}:${change.apiName}`} change={change} />)}</ul>
            : <p className="empty">两个版本的结构完全相同。</p>}
        </>}
    </section>
  </>;
}

function ChangeItem({ change }: { change: VersionChange }) {
  return <li>
    <div className="change-head">
      <span className={`change-tag ${change.change}`}>{CHANGE_LABEL[change.change]}</span>
      <span className="kind-tag">{KIND_LABEL[change.kind] ?? change.kind}</span>
      <strong>{change.apiName}</strong>
      {change.breaking && <span className="breaking-tag">破坏性</span>}
      {change.source && <code>{change.source}</code>}
    </div>
    {change.details.length > 0 && <table className="change-details"><tbody>{change.details.map(detail => <tr key={detail.path}>
      <td><code>{detail.path}</code>{detail.breaking && <span className="breaking-tag small">破坏性</span>}</td>
      <td>{CHANGE_LABEL[detail.change]}</td>
      <td className="ontology-source">{detail.change === "added" ? "—" : <><small className="diff-label">{Array.isArray(detail.before) ? "移除" : "原值"}</small>{compact(detail.before)}</>}</td>
      <td className="ontology-source">{detail.change === "removed" ? "—" : <><small className="diff-label">{Array.isArray(detail.after) ? "新增" : "新值"}</small>{compact(detail.after)}</>}</td>
    </tr>)}</tbody></table>}
  </li>;
}
