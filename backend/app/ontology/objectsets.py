"""Object sets: OSDK-style composable queries over the ontology.

Request shapes follow Palantir Foundry's Ontology v2 API (``ObjectSet``,
``SearchJsonQueryV2``, ``AggregationV2``, ``AggregationGroupByV2``) so that
clients and mental models transfer.  Documented deviations:

* ``static`` object sets reference objects by primary key and therefore also
  carry ``objectType`` (Foundry uses object RIDs).
* Numeric aggregation results are decimal strings, not doubles, to keep money
  exact (project rule: no floating point amounts).
* Array properties are "exploded" by ``exact`` grouping: each element forms
  its own group, so e.g. projects can be counted per risk reason.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Callable

from .model import ObjectTypeDef, PropertyDef

if TYPE_CHECKING:
    from .runtime import OntologyContext, OntologyService


NUMERIC = {"integer", "decimal", "money", "percent"}
ORDERED = NUMERIC | {"date", "datetime", "string", "enum"}
MAX_DEPTH = 12
MAX_PAGE_SIZE = 500
MAX_GROUPS = 1000


class ObjectSetError(ValueError):
    """A malformed object set, where clause or aggregation request."""


@dataclass
class EvaluatedSet:
    object_type: ObjectTypeDef
    objects: list[dict[str, Any]]


Predicate = Callable[[dict[str, Any]], bool]


class ObjectSetEngine:
    def __init__(self, service: "OntologyService"):
        self.service = service
        self.ontology = service.ontology

    # -------------------------------------------------------------- evaluation
    def evaluate(self, ctx: "OntologyContext", spec: Any, depth: int = 0) -> EvaluatedSet:
        if depth > MAX_DEPTH:
            raise ObjectSetError(f"对象集嵌套超过 {MAX_DEPTH} 层")
        if not isinstance(spec, dict) or "type" not in spec:
            raise ObjectSetError("objectSet 必须是带 type 字段的对象")
        kind = spec["type"]
        if kind == "base":
            definition = self.service._readable_type(ctx, _require(spec, "objectType", str))
            return EvaluatedSet(definition, list(ctx.objects(definition.api_name)))
        if kind == "static":
            definition = self.service._readable_type(ctx, _require(spec, "objectType", str))
            keys = _require(spec, "objects", list)
            found = [ctx.get(definition.api_name, key) for key in dict.fromkeys(str(k) for k in keys)]
            return EvaluatedSet(definition, [obj for obj in found if obj is not None])
        if kind == "filter":
            source = self.evaluate(ctx, _require(spec, "objectSet", dict), depth + 1)
            predicate = self.compile_where(ctx, source.object_type, _require(spec, "where", dict))
            return EvaluatedSet(source.object_type, [obj for obj in source.objects if predicate(obj)])
        if kind in {"union", "intersect", "subtract"}:
            operands = [self.evaluate(ctx, s, depth + 1) for s in _require(spec, "objectSets", list)]
            if len(operands) < 2:
                raise ObjectSetError(f"{kind} 至少需要两个对象集")
            types = {o.object_type.api_name for o in operands}
            if len(types) != 1:
                raise ObjectSetError(f"{kind} 的对象集必须是同一对象类型，实际为 {sorted(types)}")
            definition = operands[0].object_type
            key = definition.primary_key
            if kind == "union":
                merged: dict[str, dict[str, Any]] = {}
                for operand in operands:
                    for obj in operand.objects:
                        merged.setdefault(str(obj[key]), obj)
                return EvaluatedSet(definition, list(merged.values()))
            others = [{str(obj[key]) for obj in o.objects} for o in operands[1:]]
            if kind == "intersect":
                keep = [obj for obj in operands[0].objects if all(str(obj[key]) in s for s in others)]
            else:
                excluded = set().union(*others)
                keep = [obj for obj in operands[0].objects if str(obj[key]) not in excluded]
            return EvaluatedSet(definition, keep)
        if kind == "searchAround":
            source = self.evaluate(ctx, _require(spec, "objectSet", dict), depth + 1)
            link_name = _require(spec, "link", str)
            match = next(
                (
                    (link, forward)
                    for link, forward in self.ontology.links_for(source.object_type.api_name)
                    if (link.api_name if forward else link.reverse_api_name) == link_name
                ),
                None,
            )
            if match is None:
                available = [
                    link.api_name if forward else link.reverse_api_name
                    for link, forward in self.ontology.links_for(source.object_type.api_name)
                ]
                raise ObjectSetError(
                    f"{source.object_type.api_name} 没有关系 {link_name}，可用：{', '.join(available)}"
                )
            link, forward = match
            target = self.service._readable_type(ctx, link.target_type if forward else link.source_type)
            merged = {}
            for obj in source.objects:
                for linked in self.service._resolve_link(ctx, link, forward, obj):
                    merged.setdefault(str(linked[target.primary_key]), linked)
            return EvaluatedSet(target, list(merged.values()))
        raise ObjectSetError(
            f"不支持的对象集类型 {kind!r}，可用：base、static、filter、union、intersect、subtract、searchAround"
        )

    def object_type_of(self, ctx: "OntologyContext", spec: Any) -> ObjectTypeDef:
        return self.evaluate(ctx, spec).object_type

    # ------------------------------------------------------------ where clauses
    def compile_where(self, ctx: "OntologyContext", definition: ObjectTypeDef, clause: Any) -> Predicate:
        if not isinstance(clause, dict) or "type" not in clause:
            raise ObjectSetError("where 条件必须是带 type 字段的对象")
        kind = clause["type"]
        if kind in {"and", "or"}:
            parts = [self.compile_where(ctx, definition, c) for c in _require(clause, "value", list)]
            if kind == "and":
                return lambda obj: all(p(obj) for p in parts)
            return lambda obj: any(p(obj) for p in parts)
        if kind == "not":
            inner = self.compile_where(ctx, definition, _require(clause, "value", dict))
            return lambda obj: not inner(obj)

        prop = self._field(ctx, definition, clause)
        name = prop.api_name
        value = clause.get("value")
        if kind == "isNull":
            if not isinstance(value, bool):
                raise ObjectSetError("isNull 的 value 必须是布尔值")
            return lambda obj: _is_null(obj.get(name)) == value
        if kind == "eq":
            expected = _coerce(prop, value)
            return lambda obj: _normalize(prop, obj.get(name)) == expected
        if kind == "in":
            if not isinstance(value, list):
                raise ObjectSetError("in 的 value 必须是数组")
            options = {_hashable(_coerce(prop, v)) for v in value}
            return lambda obj: _hashable(_normalize(prop, obj.get(name))) in options
        if kind in {"gt", "gte", "lt", "lte"}:
            if prop.data_type not in ORDERED:
                raise ObjectSetError(f"属性 {name}（{prop.data_type}）不支持大小比较")
            bound = _coerce(prop, value)
            compare = {
                "gt": lambda a: a > bound, "gte": lambda a: a >= bound,
                "lt": lambda a: a < bound, "lte": lambda a: a <= bound,
            }[kind]

            def ordered(obj: dict[str, Any]) -> bool:
                current = _normalize(prop, obj.get(name))
                return current is not None and compare(current)

            return ordered
        if kind == "startsWith":
            prefix = _text(value)
            return lambda obj: str(obj.get(name) or "").casefold().startswith(prefix)
        if kind == "contains":
            def contains(obj: dict[str, Any]) -> bool:
                current = obj.get(name)
                if isinstance(current, list):
                    return any(_normalize_scalar(item) == _normalize_scalar(value) for item in current)
                return current is not None and _text(value) in str(current).casefold()

            return contains
        if kind in {"containsAnyTerm", "containsAllTerms"}:
            terms = [t for t in _text(value).split() if t]
            if not terms:
                raise ObjectSetError(f"{kind} 至少需要一个检索词")
            combine = any if kind == "containsAnyTerm" else all

            def text_match(obj: dict[str, Any]) -> bool:
                current = obj.get(name)
                haystack = " ".join(map(str, current)) if isinstance(current, list) else str(current or "")
                haystack = haystack.casefold()
                return combine(term in haystack for term in terms)

            return text_match
        raise ObjectSetError(
            f"不支持的 where 类型 {kind!r}，可用：eq、in、gt、gte、lt、lte、isNull、startsWith、contains、"
            "containsAnyTerm、containsAllTerms、and、or、not"
        )

    def _field(self, ctx: "OntologyContext", definition: ObjectTypeDef, clause: dict[str, Any]) -> PropertyDef:
        field = clause.get("field")
        if not isinstance(field, str):
            raise ObjectSetError(f"{clause.get('type')} 条件需要 field")
        return self.service._filterable_property(ctx, definition, field)

    # ------------------------------------------------------------------- load
    def load(
        self,
        ctx: "OntologyContext",
        spec: Any,
        *,
        select: list[str] | None = None,
        order_by: dict[str, Any] | None = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        result = self.evaluate(ctx, spec)
        definition = result.object_type
        objects = result.objects
        if order_by:
            fields = _require(order_by, "fields", list)
            for ordering in reversed(fields):
                if not isinstance(ordering, dict):
                    raise ObjectSetError("orderBy.fields 的元素必须是对象")
                prop = self.service._filterable_property(ctx, definition, _require(ordering, "field", str))
                direction = ordering.get("direction", "asc")
                if direction not in {"asc", "desc"}:
                    raise ObjectSetError("direction 只能是 asc 或 desc")
                objects = _sort(objects, prop, direction == "desc")
        if select is not None:
            unknown = [name for name in select if definition.property(name) is None]
            if unknown:
                raise ObjectSetError(f"{definition.api_name} 没有属性 {', '.join(unknown)}")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ObjectSetError(f"pageSize 必须在 1 到 {MAX_PAGE_SIZE} 之间")
        offset = _decode_token(page_token)
        page = objects[offset:offset + page_size]
        data = []
        for obj in page:
            serialized = self.service._serialize(ctx, definition, obj)
            if select is not None:
                keep = set(select) | {definition.primary_key, definition.title_property}
                serialized["properties"] = {k: v for k, v in serialized["properties"].items() if k in keep}
                serialized["redactedProperties"] = [p for p in serialized["redactedProperties"] if p in keep]
            data.append(serialized)
        next_offset = offset + page_size
        return {
            "data": data,
            "nextPageToken": _encode_token(next_offset) if next_offset < len(objects) else None,
            "totalCount": len(objects),
            "objectType": definition.api_name,
        }

    # -------------------------------------------------------------- aggregate
    def aggregate(
        self,
        ctx: "OntologyContext",
        spec: Any,
        aggregation: list[dict[str, Any]],
        group_by: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        result = self.evaluate(ctx, spec)
        definition = result.object_type
        if not isinstance(aggregation, list) or not aggregation:
            raise ObjectSetError("aggregation 至少需要一个指标")
        metrics = [self._metric(ctx, definition, m) for m in aggregation]
        groupers = [self._grouper(ctx, definition, g) for g in (group_by or [])]

        groups: dict[tuple, list[dict[str, Any]]] = {}
        group_values: dict[tuple, dict[str, Any]] = {}
        excluded = 0
        for obj in result.objects:
            combos: list[list[tuple[str, Any]]] = [[]]
            for field, extract in groupers:
                values = extract(obj)
                if not values:
                    combos = []
                    break
                combos = [combo + [(field, value)] for combo in combos for value in values]
            if not combos:
                excluded += 1
                continue
            for combo in combos:
                key = tuple(_hashable(value) for _, value in combo)
                groups.setdefault(key, []).append(obj)
                group_values.setdefault(key, dict(combo))

        if not groupers:
            groups = {(): result.objects}
            group_values = {(): {}}
        ordered_keys = sorted(groups, key=lambda k: (-len(groups[k]), json.dumps(k, default=str, ensure_ascii=False)))
        limit = min(
            [g.get("maxGroupCount", 100) for g in (group_by or []) if g.get("type") == "exact"] or [MAX_GROUPS]
        )
        if not isinstance(limit, int) or not 1 <= limit <= MAX_GROUPS:
            raise ObjectSetError(f"maxGroupCount 必须在 1 到 {MAX_GROUPS} 之间")
        kept, dropped = ordered_keys[:limit], ordered_keys[limit:]
        excluded += sum(len(groups[k]) for k in dropped)
        return {
            "accuracy": "ACCURATE",
            "objectType": definition.api_name,
            "totalCount": len(result.objects),
            "excludedItems": excluded,
            "data": [
                {
                    "group": group_values[key],
                    "metrics": [{"name": name, "value": compute(groups[key])} for name, compute in metrics],
                }
                for key in kept
            ],
        }

    def _metric(self, ctx: "OntologyContext", definition: ObjectTypeDef, spec: Any):
        if not isinstance(spec, dict) or "type" not in spec:
            raise ObjectSetError("aggregation 的元素必须是带 type 字段的对象")
        kind = spec["type"]
        if kind == "count":
            return spec.get("name") or "count", len
        prop = self._field(ctx, definition, spec)
        name = spec.get("name") or f"{kind}({prop.api_name})"
        field = prop.api_name
        if kind in {"exactDistinct", "approximateDistinct"}:
            return name, lambda objs: len({_hashable(o.get(field)) for o in objs if not _is_null(o.get(field))})
        if kind in {"sum", "avg"}:
            if prop.data_type not in NUMERIC:
                raise ObjectSetError(f"{kind} 只能用于数值属性，{field} 是 {prop.data_type}")

            def numeric(objs: list[dict[str, Any]]) -> str | None:
                values = [_decimal(o.get(field)) for o in objs]
                values = [v for v in values if v is not None]
                if not values:
                    return None
                total = sum(values, Decimal("0"))
                return _format(prop, total if kind == "sum" else total / len(values))

            return name, numeric
        if kind in {"min", "max"}:
            if prop.data_type not in ORDERED:
                raise ObjectSetError(f"{kind} 不支持 {prop.data_type} 属性 {field}")
            pick = min if kind == "min" else max

            def extreme(objs: list[dict[str, Any]]) -> Any:
                values = [_normalize(prop, o.get(field)) for o in objs]
                values = [v for v in values if v is not None]
                if not values:
                    return None
                value = pick(values)
                return _format(prop, value) if isinstance(value, Decimal) else value

            return name, extreme
        raise ObjectSetError(
            f"不支持的聚合类型 {kind!r}，可用：count、sum、avg、min、max、exactDistinct、approximateDistinct"
        )

    def _grouper(self, ctx: "OntologyContext", definition: ObjectTypeDef, spec: Any):
        if not isinstance(spec, dict) or "type" not in spec:
            raise ObjectSetError("groupBy 的元素必须是带 type 字段的对象")
        prop = self._field(ctx, definition, spec)
        field = prop.api_name
        kind = spec["type"]
        if kind == "exact":
            def exact(obj: dict[str, Any]) -> list[Any]:
                value = obj.get(field)
                if isinstance(value, list):
                    return list(dict.fromkeys(map(_hashable, value))) or [None]
                return [None if _is_null(value) else value]

            return field, exact
        if kind == "fixedWidth":
            width = spec.get("fixedWidth")
            if prop.data_type not in NUMERIC or not isinstance(width, int) or width <= 0:
                raise ObjectSetError("fixedWidth 需要数值属性和正整数 fixedWidth")

            def bucket(obj: dict[str, Any]) -> list[Any]:
                value = _decimal(obj.get(field))
                if value is None:
                    return []
                start = (value // width) * width
                return [{"startValue": _format(prop, start), "endValue": _format(prop, start + width)}]

            return field, bucket
        if kind == "ranges":
            ranges = _require(spec, "ranges", list)
            parsed = []
            for item in ranges:
                if not isinstance(item, dict):
                    raise ObjectSetError("ranges 的元素必须是 {startValue, endValue}")
                start = _coerce(prop, item.get("startValue")) if item.get("startValue") is not None else None
                end = _coerce(prop, item.get("endValue")) if item.get("endValue") is not None else None
                parsed.append((start, end, {"startValue": item.get("startValue"), "endValue": item.get("endValue")}))

            def in_ranges(obj: dict[str, Any]) -> list[Any]:
                value = _normalize(prop, obj.get(field))
                if value is None:
                    return []
                return [label for start, end, label in parsed
                        if (start is None or value >= start) and (end is None or value < end)]

            return field, in_ranges
        raise ObjectSetError(f"不支持的 groupBy 类型 {kind!r}，可用：exact、fixedWidth、ranges")


# ---------------------------------------------------------------- helpers
def _require(spec: dict[str, Any], key: str, kind: type) -> Any:
    value = spec.get(key)
    if not isinstance(value, kind):
        raise ObjectSetError(f"{spec.get('type', '对象')} 需要字段 {key}（{kind.__name__}）")
    return value


def _is_null(value: Any) -> bool:
    return value is None or value == "" or value == []


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _coerce(prop: PropertyDef, value: Any) -> Any:
    """Convert a query literal to the comparable form of the property."""
    if value is None:
        raise ObjectSetError(f"属性 {prop.api_name} 的比较值不能为空")
    if prop.data_type in NUMERIC:
        number = _decimal(value)
        if number is None:
            raise ObjectSetError(f"属性 {prop.api_name} 需要数值，收到 {value!r}")
        return number
    if prop.data_type == "boolean":
        if not isinstance(value, bool):
            raise ObjectSetError(f"属性 {prop.api_name} 需要布尔值")
        return value
    if prop.data_type == "enum":
        allowed = {v for v, _ in prop.enum_values}
        if str(value) not in allowed:
            raise ObjectSetError(f"属性 {prop.api_name} 的取值必须是 {', '.join(sorted(allowed))} 之一")
    return str(value)


def _normalize(prop: PropertyDef, value: Any) -> Any:
    if _is_null(value):
        return None
    if prop.data_type in NUMERIC:
        return _decimal(value)
    if prop.data_type == "boolean":
        return bool(value)
    if isinstance(value, list):
        return value
    return str(value)


def _normalize_scalar(value: Any) -> Any:
    number = _decimal(value)
    return number if number is not None else str(value)


def _hashable(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ObjectSetError("文本条件的 value 必须是非空字符串")
    return value.casefold()


def _format(prop: PropertyDef, value: Decimal) -> str:
    if prop.data_type == "integer" and value == value.to_integral_value():
        return str(int(value))
    return format(value.quantize(Decimal("0.01")), "f")


def _sort(objects: list[dict[str, Any]], prop: PropertyDef, descending: bool) -> list[dict[str, Any]]:
    present = [o for o in objects if not _is_null(o.get(prop.api_name))]
    missing = [o for o in objects if _is_null(o.get(prop.api_name))]

    def key(obj: dict[str, Any]) -> Any:
        value = _normalize(prop, obj.get(prop.api_name))
        return json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value

    return sorted(present, key=key, reverse=descending) + missing


def _encode_token(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}).encode()).decode()


def _decode_token(token: str | None) -> int:
    if not token:
        return 0
    try:
        offset = json.loads(base64.urlsafe_b64decode(token.encode()).decode())["offset"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ObjectSetError("pageToken 无效") from exc
    if not isinstance(offset, int) or offset < 0:
        raise ObjectSetError("pageToken 无效")
    return offset
