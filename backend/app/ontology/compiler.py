"""Metadata compiler: YAML documents → validated, executable Ontology.

Pipeline (in the spirit of Hasura's metadata resolve step):

1. Parse every ``*.yaml`` under the metadata directory as a document stream.
2. Validate each ``{kind, version, definition}`` document against its schema.
3. Resolve references: value types, markings, permissions, object types,
   properties, and rule names in the code registry (``rules.py``).
4. Derive what metadata should not repeat (``editedBy`` from action edits).
5. Fingerprint the canonical metadata so versions can be governed.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from ..auth import ALL_MENU_PERMISSIONS
from ..db import OBJECT_DATASETS
from .metadata_schema import (
    KIND_MODELS,
    NUMERIC_TYPES,
    SUPPORTED_DOCUMENT_VERSIONS,
    ActionTypeDoc,
    FunctionDoc,
    LinkTypeDoc,
    MarkingDoc,
    MetadataModel,
    ObjectTypeDoc,
    OntologyDoc,
    ParameterDoc,
    PropertyDoc,
    ValueTypeDoc,
)
from .model import (
    ActionTypeDef,
    FunctionDef,
    LinkTypeDef,
    MaintenanceRequirement,
    Marking,
    ObjectTypeDef,
    Ontology,
    ParameterDef,
    Period,
    PropertyDef,
    SubmissionCriterion,
    ValueTypeDef,
)
from .rules import RULES, RuleRegistry


DEFAULT_METADATA_DIR = Path(__file__).resolve().parent / "metadata"


class OntologyMetadataError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("本体元数据无效：\n" + "\n".join(f"  - {e}" for e in errors))
        self.errors = errors


@dataclass(frozen=True)
class MetadataDocument:
    kind: str
    version: str
    definition: MetadataModel
    source: str

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "version": self.version,
            "definition": self.definition.model_dump(by_alias=True, mode="json", exclude_defaults=False),
        }

    @property
    def key(self) -> str:
        definition = self.definition
        if isinstance(definition, LinkTypeDoc):
            return f"{self.kind}:{definition.source}.{definition.api_name}"
        return f"{self.kind}:{getattr(definition, 'api_name', '')}"


def load_documents(metadata_dir: Path = DEFAULT_METADATA_DIR) -> list[MetadataDocument]:
    files = sorted(metadata_dir.rglob("*.yaml"))
    if not files:
        raise OntologyMetadataError([f"{metadata_dir} 下没有找到 *.yaml 元数据文件"])
    errors: list[str] = []
    documents: list[MetadataDocument] = []
    for path in files:
        relative = path.relative_to(metadata_dir).as_posix()
        try:
            raw_documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            errors.append(f"{relative}: YAML 解析失败：{exc}")
            continue
        for index, raw in enumerate(raw_documents, start=1):
            if raw is None:
                continue
            where = f"{relative}#{index}"
            if not isinstance(raw, dict) or set(raw) != {"kind", "version", "definition"}:
                errors.append(f"{where}: 文档必须且只能包含 kind、version、definition")
                continue
            model = KIND_MODELS.get(raw["kind"])
            if model is None:
                errors.append(f"{where}: 未知 kind {raw['kind']!r}，可用：{', '.join(KIND_MODELS)}")
                continue
            if raw["version"] not in SUPPORTED_DOCUMENT_VERSIONS:
                errors.append(f"{where}: 不支持的文档版本 {raw['version']!r}")
                continue
            try:
                definition = model.model_validate(raw["definition"])
            except ValidationError as exc:
                for err in exc.errors():
                    location = ".".join(str(part) for part in err["loc"]) or "definition"
                    errors.append(f"{where} {raw['kind']}.{location}: {err['msg']}")
                continue
            documents.append(MetadataDocument(raw["kind"], raw["version"], definition, relative))
    if errors:
        raise OntologyMetadataError(errors)
    return documents


def checksum(documents: list[MetadataDocument]) -> str:
    canonical = sorted((doc.canonical() for doc in documents), key=_canonical_sort_key)
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_sort_key(doc: dict[str, Any]) -> tuple[str, str, str]:
    definition = doc["definition"]
    return (doc["kind"], definition.get("source", ""), definition.get("apiName", ""))


def load_ontology(metadata_dir: Path = DEFAULT_METADATA_DIR, rules: RuleRegistry = RULES) -> Ontology:
    documents = load_documents(metadata_dir)
    return compile_documents(documents, rules)


def compile_documents(documents: list[MetadataDocument], rules: RuleRegistry = RULES) -> Ontology:
    errors: list[str] = []
    by_kind: dict[str, list[MetadataDocument]] = {kind: [] for kind in KIND_MODELS}
    for doc in documents:
        by_kind[doc.kind].append(doc)

    seen: dict[str, str] = {}
    for doc in documents:
        if doc.kind == "Ontology":
            continue
        if doc.key in seen:
            errors.append(f"{doc.source}: {doc.key} 与 {seen[doc.key]} 重复定义")
        seen.setdefault(doc.key, doc.source)

    manifests = by_kind["Ontology"]
    if len(manifests) != 1:
        raise OntologyMetadataError([f"必须且只能有一个 Ontology 文档，实际 {len(manifests)} 个"])
    manifest: OntologyDoc = manifests[0].definition  # type: ignore[assignment]

    value_types = {d.definition.api_name: d.definition for d in by_kind["ValueType"]}
    markings = {d.definition.api_name: d.definition for d in by_kind["Marking"]}
    object_docs = {
        d.definition.api_name: d
        for d in sorted(by_kind["ObjectType"], key=lambda d: (d.definition.display_order, d.definition.api_name))
    }
    function_docs = {d.definition.api_name: d for d in by_kind["Function"]}
    action_docs = [d for d in by_kind["ActionType"]]
    permissions = set(ALL_MENU_PERMISSIONS)

    def check_permissions(where: str, values: tuple[str, ...]) -> None:
        for value in values:
            if value not in permissions:
                errors.append(f"{where}: 未知权限 {value!r}，可用：{', '.join(sorted(permissions))}")

    def enum_values(where: str, value_type: str | None, data_type: str, allowed: tuple[str, ...] | None = None):
        if data_type != "enum":
            if value_type or allowed:
                errors.append(f"{where}: 只有 enum 类型可以声明 valueType/allowedValues")
            return ()
        if not value_type:
            errors.append(f"{where}: enum 类型必须声明 valueType")
            return ()
        definition: ValueTypeDoc | None = value_types.get(value_type)
        if definition is None:
            errors.append(f"{where}: 未知 valueType {value_type!r}")
            return ()
        pairs = tuple((v.value, v.label) for v in definition.values)
        if allowed:
            unknown = set(allowed) - {v for v, _ in pairs}
            if unknown:
                errors.append(f"{where}: allowedValues 包含 {sorted(unknown)}，不在 {value_type} 中")
            pairs = tuple(pair for pair in pairs if pair[0] in allowed)
        return pairs

    def parameters(where: str, params: tuple[ParameterDoc, ...]) -> tuple[ParameterDef, ...]:
        result = []
        names = [p.api_name for p in params]
        if len(names) != len(set(names)):
            errors.append(f"{where}: 参数重复")
        for param in params:
            param_where = f"{where}.{param.api_name}"
            if param.type == "object_ref":
                if param.object_type not in object_docs:
                    errors.append(f"{param_where}: object_ref 必须引用已定义的 objectType")
            elif param.object_type:
                errors.append(f"{param_where}: 只有 object_ref 参数可以声明 objectType")
            if param.option_details and param.type != "object_ref":
                errors.append(f"{param_where}: 只有 object_ref 参数可以声明 optionDetails")
            elif param.option_details and param.object_type in object_docs:
                known = {p.api_name for p in object_docs[param.object_type].definition.properties}
                for detail in param.option_details:
                    if detail not in known:
                        errors.append(f"{param_where}: optionDetails 的 {detail} 不是 {param.object_type} 的属性")
            result.append(
                ParameterDef(
                    api_name=param.api_name,
                    display_name=param.display_name,
                    data_type=param.type,
                    required=param.required,
                    description=param.description,
                    enum_values=enum_values(param_where, param.value_type, param.type, param.allowed_values),
                    object_type=param.object_type,
                    option_details=tuple(param.option_details or ()),
                )
            )
        return tuple(result)

    # Edits are declared once on the action; properties derive "edited by".
    edited_by: dict[tuple[str, str], list[str]] = {}
    for doc in action_docs:
        action: ActionTypeDoc = doc.definition  # type: ignore[assignment]
        for edit in action.edits:
            type_name, _, prop = edit.partition(".")
            object_doc = object_docs.get(type_name)
            if object_doc is None or not prop:
                errors.append(f"{doc.source}: {action.api_name}.edits 引用了未知对象 {edit!r}")
                continue
            if prop == "*":
                continue
            if not any(p.api_name == prop for p in object_doc.definition.properties):
                errors.append(f"{doc.source}: {action.api_name}.edits 引用了未知属性 {edit!r}")
                continue
            edited_by.setdefault((type_name, prop), []).append(action.api_name)

    object_types = []
    for name, doc in object_docs.items():
        definition: ObjectTypeDoc = doc.definition  # type: ignore[assignment]
        where = f"{doc.source} ObjectType {name}"
        loader = rules.datasources.get(definition.datasource)
        if loader is None:
            errors.append(f"{where}: datasource {definition.datasource!r} 未在 rules.py 注册")
        for derivation in definition.derivations:
            if derivation not in rules.derivations:
                errors.append(f"{where}: derivation {derivation!r} 未在 rules.py 注册")
        check_permissions(where, definition.read_permissions)
        for dataset in definition.backing_datasets:
            if dataset not in OBJECT_DATASETS:
                errors.append(
                    f"{where}: backingDatasets 包含未登记的数据集 {dataset!r}（可用：{', '.join(OBJECT_DATASETS)}），"
                    "对象索引无法感知它的变更"
                )
        props = []
        for prop in definition.properties:
            prop_where = f"{where}.{prop.api_name}"
            if prop.marking and prop.marking not in markings:
                errors.append(f"{prop_where}: 未知 marking {prop.marking!r}")
            if prop.type in NUMERIC_TYPES and prop.unit is None:
                errors.append(f"{prop_where}: 数值属性必须声明量纲 unit（yuan、personDay、hour、day、month、percent、percentagePoint、person、count 或 none）")
            if prop.type not in NUMERIC_TYPES and prop.unit is not None:
                errors.append(f"{prop_where}: 只有数值属性可以声明量纲 unit")
            if (prop.type == "money") != (prop.unit == "yuan"):
                errors.append(f"{prop_where}: money 类型与量纲 yuan 必须同时使用")
            if (prop.type == "percent") != (prop.unit == "percent"):
                errors.append(f"{prop_where}: percent 类型与量纲 percent 必须同时使用")
            if definition.derivations:
                if prop.source_kind == "derived" and prop.derived_from not in definition.derivations:
                    errors.append(f"{prop_where}: 派生属性的 derivedFrom 必须是本对象的推导规则之一（{'、'.join(definition.derivations)}）")
                if prop.source_kind != "derived" and prop.derived_from:
                    errors.append(f"{prop_where}: 只有派生属性可以写 derivedFrom")
            elif prop.derived_from and prop.derived_from not in function_docs:
                errors.append(f"{prop_where}: derivedFrom 引用了未知函数 {prop.derived_from!r}")
            props.append(
                _property(
                    prop,
                    enum_values(prop_where, prop.value_type, prop.type),
                    tuple(edited_by.get((name, prop.api_name), ())),
                )
            )
        requirements = []
        prop_by_name = {p.api_name: p for p in definition.properties}
        action_by_name = {d.definition.api_name: d.definition for d in action_docs}
        links_from = {l.definition.api_name: l.definition for l in by_kind["LinkType"] if l.definition.source == name}
        if definition.maintenance_requirements and "maintenanceStatus" not in definition.derivations:
            errors.append(f"{where}: 声明了维护要求的对象必须在 derivations 中登记 maintenanceStatus")
        for req in definition.maintenance_requirements:
            req_where = f"{where} 维护要求 {req.api_name}"
            action = action_by_name.get(req.action)
            if action is None:
                errors.append(f"{req_where}: action {req.action!r} 不存在")
            if req.property is not None:
                prop = prop_by_name.get(req.property)
                if prop is None:
                    errors.append(f"{req_where}: 属性 {req.property!r} 不存在")
                elif prop.source_kind != "manual":
                    errors.append(f"{req_where}: 只有人工维护的属性才能作为维护要求（{req.property} 是 {prop.source_kind}）")
                elif action is not None and not ({f"{name}.{req.property}", f"{name}.*"} & set(action.edits)):
                    errors.append(f"{req_where}: 动作 {req.action} 没有修改 {name}.{req.property}")
            else:
                link = links_from.get(req.link)
                if link is None:
                    errors.append(f"{req_where}: 关系 {req.link!r} 不存在，或不是从 {name} 出发的关系")
                elif link.join is None:
                    errors.append(f"{req_where}: 关系 {req.link} 需要用 join（sourceKey、targetKey）声明才能计数")
                elif action is not None and not any(e.split(".")[0] == link.target for e in action.edits):
                    errors.append(f"{req_where}: 动作 {req.action} 不会创建或修改 {link.target}")
            conditions = []
            for cond in [] if req.when is None else req.when if isinstance(req.when, list) else [req.when]:
                values, inside = (cond.in_, True) if cond.in_ is not None else (cond.not_in, False)
                cond_prop = prop_by_name.get(cond.property)
                if cond_prop is None:
                    errors.append(f"{req_where}: when 引用的属性 {cond.property!r} 不存在")
                elif cond_prop.type == "enum" and cond_prop.value_type in value_types:
                    unknown = set(values) - {v.value for v in value_types[cond_prop.value_type].values}
                    if unknown:
                        errors.append(f"{req_where}: when 中 {cond.property} 的取值 {sorted(unknown)} 不在 {cond_prop.value_type} 中")
                conditions.append((cond.property, values, inside))
            requirements.append(MaintenanceRequirement(
                api_name=req.api_name, display_name=req.display_name, action=req.action,
                property=req.property, missing_values=req.missing_values, link=req.link, min_count=req.min_count,
                conditions=tuple(conditions),
            ))
        if definition.default_order and not any(
            p.api_name == definition.default_order.lstrip("-") for p in definition.properties
        ):
            errors.append(f"{where}: defaultOrder 引用了未知属性 {definition.default_order!r}")
        unknown_columns = [c for c in definition.list_properties or () if not any(p.api_name == c for p in definition.properties)]
        if unknown_columns:
            errors.append(f"{where}: listProperties 引用了未知属性 {unknown_columns}")
        period = None
        periodic = {p.api_name: p for p in definition.properties if p.periodic}
        if definition.period is None and periodic:
            errors.append(f"{where}: 属性 {sorted(periodic)} 标记了 periodic，但对象类型没有声明 period")
        elif definition.period is not None:
            spec = definition.period
            dates = {p.api_name for p in definition.properties if p.type == "date"}
            ways = [spec.active_property is not None, spec.date_property is not None,
                    spec.start_property is not None or spec.end_property is not None]
            if sum(ways) != 1:
                errors.append(f"{where}: period 必须且只能写 activeProperty、dateProperty、startProperty + endProperty 之一")
            elif ways[2]:
                if spec.start_property not in dates or spec.end_property not in dates:
                    errors.append(f"{where}: period.startProperty 与 endProperty 都必须是本类型的日期属性")
            elif spec.active_property is not None:
                active = periodic.get(spec.active_property)
                if active is None or active.type not in ("integer", "decimal"):
                    errors.append(f"{where}: period.activeProperty {spec.active_property!r} 必须是本类型标记了 periodic 的数值属性")
            elif not any(p.api_name == spec.date_property and p.type == "date" for p in definition.properties):
                errors.append(f"{where}: period.dateProperty {spec.date_property!r} 必须是本类型的日期属性")
            if spec.range and periodic:
                errors.append(f"{where}: 有按月计算（periodic）属性的类型不能声明 range")
            if spec.options is not None:
                options_doc = object_docs.get(spec.options.object_type)
                options_prop = next((p for p in options_doc.definition.properties if p.api_name == spec.options.property), None) if options_doc else None
                if options_prop is None or options_prop.type != "date":
                    errors.append(f"{where}: period.options 必须指向已定义对象类型的日期属性")
            elif spec.active_property is not None:
                errors.append(f"{where}: 用 activeProperty 的 period 必须写 options（可选月份的来源）")
            period = Period(spec.unit, spec.display_name, spec.description, spec.active_property,
                            spec.options.object_type if spec.options else None, spec.options.property if spec.options else None,
                            spec.date_property, spec.default_view, spec.start_property, spec.end_property, spec.range)
        object_types.append(
            ObjectTypeDef(
                api_name=name,
                display_name=definition.display_name,
                plural_display_name=definition.plural_display_name,
                description=definition.description,
                primary_key=definition.primary_key,
                title_property=definition.title_property,
                backing_datasets=definition.backing_datasets,
                properties=tuple(props),
                read_permissions=definition.read_permissions,
                loader=_compose_loader(
                    name, loader or _missing_loader, definition.primary_key,
                    [(d, rules.derivations.get(d), frozenset(p.api_name for p in definition.properties if p.derived_from == d))
                     for d in definition.derivations],
                ),
                icon=definition.icon,
                default_order=definition.default_order,
                datasource=definition.datasource,
                derivations=definition.derivations,
                maintenance_requirements=tuple(requirements),
                metadata_source=doc.source,
                period=period,
                list_properties=tuple(definition.list_properties or ()),
            )
        )

    link_types = []
    for doc in by_kind["LinkType"]:
        link: LinkTypeDoc = doc.definition  # type: ignore[assignment]
        where = f"{doc.source} LinkType {link.source}.{link.api_name}"
        forward = reverse = None
        if link.resolver:
            forward = rules.resolvers.get(link.resolver.forward)
            reverse = rules.resolvers.get(link.resolver.reverse)
            for name, fn in ((link.resolver.forward, forward), (link.resolver.reverse, reverse)):
                if fn is None:
                    errors.append(f"{where}: resolver {name!r} 未在 rules.py 注册")
        if link.via and link.via not in object_docs:
            errors.append(f"{where}: via 引用了未知对象类型 {link.via!r}")
        if link.period_property:
            dated = link.via if link.resolver else link.target
            dated_doc = object_docs.get(dated)
            if dated_doc is None or not any(
                p.api_name == link.period_property and p.type == "date" for p in dated_doc.definition.properties
            ):
                errors.append(f"{where}: periodProperty {link.period_property!r} 必须是 {dated} 的日期属性")
        link_types.append(
            LinkTypeDef(
                api_name=link.api_name,
                reverse_api_name=link.reverse_api_name,
                display_name=link.display_name,
                reverse_display_name=link.reverse_display_name,
                source_type=link.source,
                target_type=link.target,
                cardinality=link.cardinality,
                description=link.description,
                source_key=link.join.source_key if link.join else None,
                target_key=link.join.target_key if link.join else None,
                via=link.via,
                resolver=forward,
                reverse_resolver=reverse,
                period_property=link.period_property,
            )
        )

    action_types = []
    for doc in action_docs:
        action: ActionTypeDoc = doc.definition  # type: ignore[assignment]
        where = f"{doc.source} ActionType {action.api_name}"
        handler = rules.handlers.get(action.handler)
        if handler is None:
            errors.append(f"{where}: handler {action.handler!r} 未在 rules.py 注册")
        check_permissions(where, action.permissions)
        criteria = []
        for criterion in action.submission_criteria:
            check = rules.criteria.get(criterion.rule)
            if check is None:
                errors.append(f"{where}: 提交条件 rule {criterion.rule!r} 未在 rules.py 注册")
                continue
            try:
                inspect.signature(check).bind(None, None, None, **criterion.args)
            except TypeError as exc:
                errors.append(f"{where}: 提交条件 {criterion.rule} 的 args 不匹配：{exc}")
                continue
            criteria.append(SubmissionCriterion(criterion.description, _bind(check, criterion.args)))
        action_types.append(
            ActionTypeDef(
                api_name=action.api_name,
                display_name=action.display_name,
                description=action.description,
                object_type=action.object_type,
                target_parameter=action.target_parameter,
                parameters=parameters(where, action.parameters),
                required_permissions=action.permissions,
                edits=action.edits,
                writeback_datasets=action.writeback,
                handler=handler or _missing_handler,
                submission_criteria=tuple(criteria),
                creates_object=action.creates_object,
                tone=action.tone,
            )
        )

    functions = []
    for name, doc in function_docs.items():
        function: FunctionDoc = doc.definition  # type: ignore[assignment]
        where = f"{doc.source} Function {name}"
        implementation = rules.functions.get(function.implementation)
        if implementation is None:
            errors.append(f"{where}: implementation {function.implementation!r} 未在 rules.py 注册")
        check_permissions(where, function.permissions)
        functions.append(
            FunctionDef(
                api_name=name,
                display_name=function.display_name,
                description=function.description,
                parameters=parameters(where, function.parameters),
                output_type=function.output_type,
                required_permissions=function.permissions,
                implementation=implementation or _missing_implementation,
            )
        )

    for marking in markings.values():
        check_permissions(f"Marking {marking.api_name}", marking.permissions)

    if errors:
        raise OntologyMetadataError(errors)

    ontology = Ontology(
        api_name=manifest.api_name,
        display_name=manifest.display_name,
        version=manifest.version,
        description=manifest.description,
        markings=tuple(_marking(m) for m in markings.values()),
        object_types=tuple(object_types),
        link_types=tuple(link_types),
        action_types=tuple(action_types),
        functions=tuple(functions),
        value_types=tuple(
            ValueTypeDef(v.api_name, v.display_name, v.description, tuple((x.value, x.label) for x in v.values))
            for v in value_types.values()
        ),
        checksum=checksum(documents),
        documents=tuple({**doc.canonical(), "source": doc.source} for doc in documents),
    )
    try:
        ontology.validate()
    except ValueError as exc:
        raise OntologyMetadataError([str(exc)]) from exc
    return ontology


def _property(prop: PropertyDoc, enum_pairs: tuple[tuple[str, str], ...], edited_by: tuple[str, ...]) -> PropertyDef:
    return PropertyDef(
        api_name=prop.api_name,
        display_name=prop.display_name,
        data_type=prop.type,
        source_kind=prop.source_kind,
        source=prop.source,
        description=prop.description,
        marking=prop.marking,
        enum_values=enum_pairs,
        edited_by=edited_by,
        derived_from=prop.derived_from,
        searchable=prop.searchable,
        unit=prop.unit,
        periodic=prop.periodic,
    )


def _marking(doc: MarkingDoc) -> Marking:
    return Marking(doc.api_name, doc.display_name, doc.description, doc.permissions)


def _bind(check, args: dict[str, Any]):
    def bound(ctx, values, target):
        return check(ctx, values, target, **args)

    return bound


def _missing_loader(ctx):  # pragma: no cover - compile fails before this is reachable
    raise RuntimeError("datasource not registered")


def _missing_handler(ctx, values, target, actor):  # pragma: no cover
    raise RuntimeError("handler not registered")


def _missing_implementation(ctx, params):  # pragma: no cover
    raise RuntimeError("function not registered")


class DerivationError(RuntimeError):
    pass


def _compose_loader(object_type: str, datasource, primary_key: str, steps):
    """Materialize objects with the datasource, then run each derivation in metadata order.

    Every derivation must return exactly the properties whose derivedFrom names it, so the metadata
    is the contract for what each rule computes.
    """
    if not steps:
        return datasource

    def load(ctx):
        objects = datasource(ctx)
        for name, derive, declared in steps:
            values = derive(ctx, objects, object_type)
            for item in objects:
                derived = values.get(item[primary_key])
                if derived is None:
                    raise DerivationError(f"{object_type}: 推导规则 {name} 没有为 {item[primary_key]} 给出结果")
                produced = {key for key in derived if not key.startswith("__")}
                if produced != declared:
                    raise DerivationError(
                        f"{object_type}: 推导规则 {name} 的输出与元数据不一致："
                        f"缺少 {sorted(declared - produced)}，多出 {sorted(produced - declared)}")
                item.update(derived)
        return objects

    return load

