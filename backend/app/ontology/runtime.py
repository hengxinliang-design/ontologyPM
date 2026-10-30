"""Ontology runtime: materializes objects, traverses links, runs actions and functions."""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from ..db import Database, active_filter
from ..project_repository import ProjectRepository
from ..repository import SnapshotRepository
from .index import ObjectIndex
from .model import ActionTypeDef, LinkTypeDef, ObjectTypeDef, Ontology, ParameterDef
from .security import OntologyPermissionError, Principal, SecurityPolicy, in_scope


NUMERIC_TYPES = {"integer", "decimal", "money", "percent"}


class OntologyNotFoundError(LookupError):
    pass


class ActionValidationError(ValueError):
    def __init__(self, message: str, details: dict[str, Any]):
        super().__init__(message)
        self.details = details


class OntologyContext:
    """Per-request view of the ontology.

    Raw datasets are cached for the lifetime of the request; materialized
    objects come from the shared ``ObjectIndex`` when one is attached.
    """

    def __init__(
        self,
        ontology: Ontology,
        database: Database,
        projects: ProjectRepository,
        records: SnapshotRepository,
        principal: Principal,
        object_index: ObjectIndex | None = None,
        period: str | None = None,
        unscoped: "OntologyContext | None" = None,
    ):
        self.ontology = ontology
        # A scoped principal (project manager, 7.7.0) reads objects through an unscoped inner context, so
        # loaders, derivations and the shared index always see every row; only results are filtered.
        self.unscoped = unscoped
        # Selected statistics period (YYYY-MM) for object types that declare one; None = their default period.
        self.period = period
        self.database = database
        self.projects = projects
        self.records = records
        self.principal = principal
        self.object_index = object_index
        self._datasets: dict[str, Any] = unscoped._datasets if unscoped is not None else {}
        self._objects: dict[str, list[dict[str, Any]]] = {}
        self._index: dict[str, dict[str, dict[str, Any]]] = {}
        self._dataset_versions: dict[str, int] | None = None

    def dataset_versions(self) -> dict[str, int]:
        if self._dataset_versions is None:
            self._dataset_versions = self.database.dataset_versions()
        return self._dataset_versions

    def dataset(self, name: str, loader: Callable[[], Any]) -> Any:
        if name not in self._datasets:
            self._datasets[name] = loader()
        return self._datasets[name]

    def table(self, table: str, order_by: str) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            with self.database.connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE {active_filter(table)} ORDER BY {order_by}"
                ).fetchall()
            return [dict(row) for row in rows]

        return self.dataset(f"table:{table}", load)

    def crm_records(self) -> list[dict[str, Any]]:
        def load() -> list[dict[str, Any]]:
            snapshot = self.records.export_records()
            return snapshot["records"] if snapshot else []

        return self.dataset("crm_records", load)

    def project_facts(self) -> list[dict[str, Any]]:
        """Every project's derived facts (former repository payload), from the cached Project objects."""
        return [item["__facts"] for item in self.objects("Project")]

    def project_fact(self, project_number: str) -> dict[str, Any] | None:
        item = self.get("Project", project_number)
        return item["__facts"] if item else None

    def objects(self, object_type: str) -> list[dict[str, Any]]:
        if object_type not in self._objects and self.unscoped is not None:
            definition = self._require_type(object_type)
            self._objects[object_type] = [
                item for item in self.unscoped.objects(object_type) if in_scope(self.principal, definition, item)
            ]
        if object_type not in self._objects:
            definition = self._require_type(object_type)
            if definition.period is not None and self.period is not None and any(p.periodic for p in definition.properties):
                # Period views are computed per request; the shared index holds the default period.
                self._objects[object_type] = definition.loader(self)
            elif self.object_index is not None:
                entry = self.object_index.entry(definition, self)
                self._objects[object_type] = entry.objects
                self._index[object_type] = entry.by_key
            else:
                self._objects[object_type] = definition.loader(self)
        return self._objects[object_type]

    def get(self, object_type: str, primary_key: Any) -> dict[str, Any] | None:
        if self.unscoped is not None:
            item = self.unscoped.get(object_type, primary_key)
            return item if item is not None and in_scope(self.principal, self._require_type(object_type), item) else None
        if object_type not in self._index:
            definition = self._require_type(object_type)
            objects = self.objects(object_type)
            if object_type not in self._index:
                self._index[object_type] = {str(item[definition.primary_key]): item for item in objects}
        return self._index[object_type].get(str(primary_key))

    def _require_type(self, object_type: str) -> ObjectTypeDef:
        definition = self.ontology.object_type(object_type)
        if definition is None:
            raise OntologyNotFoundError(f"未知对象类型：{object_type}")
        return definition


class OntologyService:
    def __init__(
        self,
        ontology: Ontology,
        database: Database,
        projects: ProjectRepository,
        records: SnapshotRepository,
    ):
        from .versioning import OntologyVersionRegistry

        ontology.validate()
        self.ontology = ontology
        self.database = database
        self.projects = projects
        self.records = records
        self.security = SecurityPolicy(ontology)
        self.versions = OntologyVersionRegistry(database)
        self.index = ObjectIndex(ontology)

    def register_version(self) -> dict[str, Any]:
        return self.versions.register(self.ontology)

    # --------------------------------------------------------------- versions
    def _require_metadata_access(self, user: dict[str, Any]) -> None:
        if not Principal.from_user(user).holds_any(("ontology",)):
            raise OntologyPermissionError("当前用户无权查看本体元数据版本")

    def list_versions(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        self._require_metadata_access(user)
        return self.versions.list_versions()

    def version_documents(self, user: dict[str, Any], version: str) -> list[dict[str, Any]]:
        self._require_metadata_access(user)
        try:
            return self.versions.documents(version)
        except KeyError as exc:
            raise OntologyNotFoundError(str(exc.args[0])) from exc

    def diff_versions(self, user: dict[str, Any], from_version: str, to_version: str) -> dict[str, Any]:
        self._require_metadata_access(user)
        try:
            return self.versions.diff(from_version, to_version)
        except KeyError as exc:
            raise OntologyNotFoundError(str(exc.args[0])) from exc

    def context(self, user: dict[str, Any], period: str | None = None) -> OntologyContext:
        principal = Principal.from_user(user)
        if not principal.scoped:
            return OntologyContext(
                self.ontology, self.database, self.projects, self.records, principal, self.index, period,
            )
        # A project manager's own projects: Project.projectManager equals the account's name (D10).
        base = OntologyContext(
            self.ontology, self.database, self.projects, self.records,
            Principal.from_user(self.SYSTEM_USER), self.index, period,
        )
        own = frozenset(
            str(p["projectNumber"]) for p in base.objects("Project")
            if principal.person_name and p.get("projectManager") == principal.person_name
        )
        return OntologyContext(
            self.ontology, self.database, self.projects, self.records,
            replace(principal, project_scope=own), self.index, period, unscoped=base,
        )

    def project_scope(self, user: dict[str, Any]) -> frozenset[str] | None:
        """The projects a user may see; None = all projects."""
        return self.context(user).principal.project_scope if Principal.from_user(user).scoped else None

    def _period_context(
        self, user: dict[str, Any], object_type: str, month: str | None, month_to: str | None = None,
    ) -> tuple[OntologyContext, ObjectTypeDef]:
        """A request context for ``object_type`` viewed for ``month`` (only types that declare a period take one)."""
        definition = self.ontology.object_type(object_type)
        if (month is not None or month_to is not None) and definition is not None:
            if definition.period is None:
                raise ValueError(f"{definition.display_name}没有统计期间，不能按月份查看")
            for value in (month, month_to):
                if value is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
                    raise ValueError("月份必须是 YYYY-MM 格式")
            if month_to is not None:
                if not definition.period.range:
                    raise ValueError(f"{definition.display_name}只能按单个月份查看")
                if month is None or month_to < month:
                    raise ValueError("结束月份必须在起始月份之后")
        ctx = self.context(user, month)
        return ctx, self._readable_type(ctx, object_type)

    def _period_meta(self, ctx: OntologyContext, definition: ObjectTypeDef) -> dict[str, Any] | None:
        period = definition.period
        if period is None:
            return None
        if period.start_property and period.end_property:
            items = ctx.objects(definition.api_name)
            ends = [str(i.get(period.end_property) or "")[:7] for i in items if i.get(period.end_property)]
            starts = [str(i.get(period.start_property) or i.get(period.end_property))[:7] for i in items if i.get(period.end_property)]
            months = _month_span(min(starts), max(ends)) if ends else []
        else:
            source, prop = (period.options_type, period.options_property) if period.options_type else (definition.api_name, period.date_property)
            months = sorted({str(item.get(prop) or "")[:7] for item in ctx.objects(source)} - {""}, reverse=True)
        return {"month": ctx.period, "defaultMonth": months[0] if months else None, "options": months,
                "defaultView": period.default_view, "range": period.range}

    # ------------------------------------------------------------- object sets
    def load_object_set(self, user: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        from .objectsets import ObjectSetEngine, ObjectSetError

        ctx = self.context(user)
        select = request.get("select")
        if select is not None and (not isinstance(select, list) or not all(isinstance(x, str) for x in select)):
            raise ActionValidationError("select 必须是属性名数组", {})
        page_size = request.get("pageSize", 50)
        if not isinstance(page_size, int) or isinstance(page_size, bool):
            raise ActionValidationError("pageSize 必须是整数", {})
        try:
            return ObjectSetEngine(self).load(
                ctx,
                request.get("objectSet"),
                select=select,
                order_by=request.get("orderBy"),
                page_size=page_size,
                page_token=request.get("pageToken"),
            )
        except ObjectSetError as exc:
            raise ActionValidationError(str(exc), {"objectSet": str(exc)}) from exc

    def aggregate_object_set(self, user: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        from .objectsets import ObjectSetEngine, ObjectSetError

        ctx = self.context(user)
        group_by = request.get("groupBy", [])
        if not isinstance(group_by, list):
            raise ActionValidationError("groupBy 必须是数组", {})
        try:
            return ObjectSetEngine(self).aggregate(
                ctx, request.get("objectSet"), request.get("aggregation"), group_by
            )
        except ObjectSetError as exc:
            raise ActionValidationError(str(exc), {"objectSet": str(exc)}) from exc

    # ------------------------------------------------------------ object index
    SYSTEM_USER = {"username": "system", "is_admin": True, "permissions": []}

    def warm_index(self) -> list[str]:
        """Rebuild stale object types eagerly (called after imports and at startup)."""
        return self.index.warm(self.context(self.SYSTEM_USER))

    def index_status(self, user: dict[str, Any]) -> dict[str, Any]:
        self._require_metadata_access(user)
        versions = self.database.dataset_versions()
        return {"datasetVersions": versions, "objectTypes": self.index.status(versions)}

    def refresh_index(self, user: dict[str, Any]) -> dict[str, Any]:
        self._require_metadata_access(user)
        self.index.invalidate()
        rebuilt = self.warm_index()
        return {"rebuilt": rebuilt, **self.index_status(user)}

    # ------------------------------------------------------------------ schema
    def schema(self, user: dict[str, Any]) -> dict[str, Any]:
        principal = Principal.from_user(user)
        visible_types = [
            t for t in self.ontology.object_types if self.security.can_read_type(principal, t)
        ]
        visible_names = {t.api_name for t in visible_types}
        return {
            "apiName": self.ontology.api_name,
            "displayName": self.ontology.display_name,
            "version": self.ontology.version,
            "checksum": self.ontology.checksum,
            "description": self.ontology.description,
            "valueTypes": [
                {
                    "apiName": v.api_name,
                    "displayName": v.display_name,
                    "description": v.description,
                    "values": [{"value": value, "label": label} for value, label in v.values],
                }
                for v in self.ontology.value_types
            ],
            "markings": [
                {
                    "apiName": m.api_name,
                    "displayName": m.display_name,
                    "description": m.description,
                    "permissions": list(m.permissions),
                    "granted": principal.holds_any(m.permissions),
                }
                for m in self.ontology.markings
            ],
            "objectTypes": [self._object_type_schema(principal, t) for t in visible_types],
            "linkTypes": [
                _link_schema(link)
                for link in self.ontology.link_types
                if link.source_type in visible_names and link.target_type in visible_names
            ],
            "actionTypes": [
                {
                    **_action_schema(action),
                    "permitted": self.security.can_apply_in_browser(principal, action),
                }
                for action in self.ontology.action_types
                if action.object_type in visible_names
            ],
            "functions": [
                {
                    "apiName": f.api_name,
                    "displayName": f.display_name,
                    "description": f.description,
                    "parameters": [_parameter_schema(p) for p in f.parameters],
                    "outputType": f.output_type,
                    "requiredPermissions": list(f.required_permissions),
                    "permitted": self.security.can_execute(principal, f),
                }
                for f in self.ontology.functions
            ],
        }

    def _object_type_schema(self, principal: Principal, definition: ObjectTypeDef) -> dict[str, Any]:
        return {
            "apiName": definition.api_name,
            "displayName": definition.display_name,
            "pluralDisplayName": definition.plural_display_name,
            "description": definition.description,
            "primaryKey": definition.primary_key,
            "titleProperty": definition.title_property,
            "backingDatasets": list(definition.backing_datasets),
            "datasource": definition.datasource,
            "derivations": list(definition.derivations),
            "metadataSource": definition.metadata_source,
            "readPermissions": list(definition.read_permissions),
            "icon": definition.icon,
            "listProperties": list(definition.list_properties),
            "period": None if definition.period is None else {
                "unit": definition.period.unit,
                "displayName": definition.period.display_name,
                "description": definition.period.description,
                "activeProperty": definition.period.active_property,
                "dateProperty": definition.period.date_property,
                "startProperty": definition.period.start_property,
                "endProperty": definition.period.end_property,
                "range": definition.period.range,
                "defaultView": definition.period.default_view,
            },
            "properties": [
                {
                    "apiName": p.api_name,
                    "displayName": p.display_name,
                    "dataType": p.data_type,
                    "sourceKind": p.source_kind,
                    "source": p.source,
                    "description": p.description,
                    "marking": p.marking,
                    "readable": self.security.can_read_marking(principal, p.marking),
                    "enumValues": [{"value": v, "label": label} for v, label in p.enum_values],
                    "editedBy": list(p.edited_by),
                    "derivedFrom": p.derived_from,
                    "searchable": p.searchable,
                    "periodic": p.periodic,
                    "unit": p.unit,
                    "unitLabel": p.unit_label,
                }
                for p in definition.properties
            ],
        }

    # ----------------------------------------------------------------- objects
    def list_objects(
        self,
        user: dict[str, Any],
        object_type: str,
        *,
        query: str | None = None,
        where: dict[str, str] | None = None,
        order_by: str | None = None,
        page: int = 1,
        page_size: int = 20,
        month: str | None = None,
        month_to: str | None = None,
    ) -> dict[str, Any]:
        ctx, definition = self._period_context(user, object_type, month, month_to)
        items = ctx.objects(object_type)
        if month is not None and definition.period is not None:
            items = [item for item in items if definition.period.contains(item, month, month_to or month)]
        for prop_name, expected in (where or {}).items():
            prop = self._filterable_property(ctx, definition, prop_name)
            items = [item for item in items if _matches(item.get(prop.api_name), expected)]
        if query:
            needle = query.casefold()
            searchable = [
                p.api_name
                for p in definition.properties
                if (p.searchable or p.api_name in {definition.primary_key, definition.title_property})
                and self.security.can_read_marking(ctx.principal, p.marking)
            ]
            items = [
                item for item in items
                if needle in " ".join(str(item.get(name) or "") for name in searchable).casefold()
            ]
        order = order_by or definition.default_order
        if order:
            descending = order.startswith("-")
            prop = self._filterable_property(ctx, definition, order.lstrip("-"))
            items = _sorted(items, prop.api_name, prop.data_type, descending)
        total = len(items)
        start = (page - 1) * page_size
        return {
            "data": [self._serialize(ctx, definition, item) for item in items[start:start + page_size]],
            "meta": {
                "objectType": object_type,
                "page": page,
                "pageSize": page_size,
                "totalObjects": total,
                "totalPages": max(1, math.ceil(total / page_size)),
                "period": self._period_meta(ctx, definition),
            },
        }

    def get_object(self, user: dict[str, Any], object_type: str, primary_key: str, *, month: str | None = None) -> dict[str, Any]:
        ctx, definition = self._period_context(user, object_type, month)
        item = ctx.get(object_type, primary_key)
        if item is None:
            raise OntologyNotFoundError(f"未找到对象 {object_type}:{primary_key}")
        serialized = self._serialize(ctx, definition, item)
        links = []
        for link, forward in self.ontology.links_for(object_type):
            target_type = link.target_type if forward else link.source_type
            target_definition = self.ontology.object_type(target_type)
            if not self.security.can_read_type(ctx.principal, target_definition):
                continue
            links.append(
                {
                    "apiName": link.api_name if forward else link.reverse_api_name,
                    "displayName": link.display_name if forward else link.reverse_display_name,
                    "targetObjectType": target_type,
                    "targetDisplayName": target_definition.display_name,
                    "cardinality": _side_cardinality(link.cardinality, forward),
                    "count": len(self._resolve_link(ctx, link, forward, item)),
                }
            )
        serialized["links"] = links
        serialized["references"] = self._references(ctx, definition, item)
        serialized["period"] = self._period_meta(ctx, definition)
        return serialized

    def _references(self, ctx: OntologyContext, definition: ObjectTypeDef, item: dict[str, Any]) -> dict[str, Any]:
        """Properties that hold another object's primary key (a join link's key), with that object's title.

        Lets the detail panel show 「验收款」 instead of ``pay_2ae2…`` for Milestone.paymentNodeId.
        """
        references: dict[str, Any] = {}
        for link, forward in self.ontology.links_for(definition.api_name):
            if link.source_key is None:
                continue
            own_key, other_key, other_type = (
                (link.source_key, link.target_key, link.target_type) if forward
                else (link.target_key, link.source_key, link.source_type)
            )
            other = self.ontology.object_type(other_type)
            if (other is None or other_key != other.primary_key or own_key == definition.primary_key
                    or own_key in references or item.get(own_key) in (None, "")
                    or not self.security.can_read_type(ctx.principal, other)):
                continue
            target = ctx.get(other_type, item[own_key])
            if target is not None:
                references[own_key] = {
                    "objectType": other_type, "primaryKey": str(item[own_key]),
                    "title": target.get(other.title_property),
                }
        return references

    def traverse(
        self,
        user: dict[str, Any],
        object_type: str,
        primary_key: str,
        link_api_name: str,
        *,
        page: int = 1,
        page_size: int = 50,
        month: str | None = None,
    ) -> dict[str, Any]:
        ctx, _ = self._period_context(user, object_type, month)
        item = ctx.get(object_type, primary_key)
        if item is None:
            raise OntologyNotFoundError(f"未找到对象 {object_type}:{primary_key}")
        match = next(
            (
                (link, forward)
                for link, forward in self.ontology.links_for(object_type)
                if (link.api_name if forward else link.reverse_api_name) == link_api_name
            ),
            None,
        )
        if match is None:
            raise OntologyNotFoundError(f"{object_type} 没有关系 {link_api_name}")
        link, forward = match
        target_type = link.target_type if forward else link.source_type
        target_definition = self._readable_type(ctx, target_type)
        targets = self._resolve_link(ctx, link, forward, item)
        total = len(targets)
        start = (page - 1) * page_size
        return {
            "data": [self._serialize(ctx, target_definition, t) for t in targets[start:start + page_size]],
            "meta": {
                "linkType": link.api_name,
                "linkApiName": link_api_name,
                "sourceObjectType": object_type,
                "targetObjectType": target_type,
                "cardinality": _side_cardinality(link.cardinality, forward),
                "page": page,
                "pageSize": page_size,
                "totalObjects": total,
            },
        }

    def _resolve_link(
        self, ctx: OntologyContext, link: LinkTypeDef, forward: bool, item: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if forward and link.resolver is not None:
            return link.resolver(ctx, item)
        if not forward and link.reverse_resolver is not None:
            return link.reverse_resolver(ctx, item)
        own_key, other_key, other_type = (
            (link.source_key, link.target_key, link.target_type)
            if forward
            else (link.target_key, link.source_key, link.source_type)
        )
        value = item.get(own_key)
        if value in (None, ""):
            return []
        targets = [obj for obj in ctx.objects(other_type) if obj.get(other_key) == value]
        own_type = self.ontology.object_type(link.source_type if forward else link.target_type)
        if ctx.period and link.period_property and own_type is not None and own_type.period is not None:
            targets = [obj for obj in targets if str(obj.get(link.period_property) or "").startswith(ctx.period)]
        return targets

    # ----------------------------------------------------------------- actions
    def available_actions(
        self, user: dict[str, Any], object_type: str, primary_key: str
    ) -> list[dict[str, Any]]:
        ctx = self.context(user)
        definition = self._readable_type(ctx, object_type)
        target = ctx.get(object_type, primary_key)
        if target is None:
            raise OntologyNotFoundError(f"未找到对象 {object_type}:{primary_key}")
        result = []
        for action in self.ontology.action_types:
            if action.object_type != object_type:
                continue
            params = {action.target_parameter: primary_key}
            criteria = _evaluate_criteria(ctx, action, params, target)
            schema = _action_schema(action)
            schema["parameters"] = [
                {**payload, **self._reference_choices(ctx, definition, param, target, primary_key)}
                for payload, param in zip(schema["parameters"], action.parameters)
            ]
            result.append(
                {
                    **schema,
                    "permitted": self.security.can_apply_in_browser(ctx.principal, action),
                    "criteria": criteria,
                    "available": self.security.can_apply_in_browser(ctx.principal, action)
                    and all(c["passed"] for c in criteria),
                }
            )
        return result

    def _reference_choices(
        self, ctx: OntologyContext, target_type: ObjectTypeDef, param: ParameterDef, target: dict[str, Any], primary_key: str,
    ) -> dict[str, Any]:
        """Choices for an object-reference parameter, so forms offer a list instead of a raw ID.

        Candidates are the readable objects of the referenced type in the target's project (both carry projectNumber);
        ``currentValue`` is the candidate that already points at the target, e.g. the payment term linked to a milestone.
        """
        ref_type = self.ontology.object_type(param.object_type) if param.data_type == "object_ref" else None
        if ref_type is None or ref_type.api_name == target_type.api_name or not self.security.can_read_type(ctx.principal, ref_type):
            return {}
        items = ctx.objects(ref_type.api_name)
        scope = "projectNumber"
        scoped = target.get(scope) is not None and any(p.api_name == scope for p in ref_type.properties)
        if scoped:
            items = [item for item in items if item.get(scope) == target[scope]]
        current = next((
            str(item[ref_type.primary_key]) for item in items
            if any(key not in (scope, ref_type.primary_key) and value == primary_key for key, value in item.items())
        ), None)
        details = [
            ref_type.property(name) for name in param.option_details
            if self.security.can_read_marking(ctx.principal, ref_type.property(name).marking)
        ]
        return {
            "options": [
                {"value": str(item[ref_type.primary_key]), "label": str(item.get(ref_type.title_property) or item[ref_type.primary_key]),
                 "details": [{"label": p.display_name, "value": item.get(p.api_name), "unit": p.unit} for p in details]}
                for item in items[:200]
            ],
            "currentValue": current,
        }

    def apply_action(
        self,
        user: dict[str, Any],
        action_type: str,
        parameters: dict[str, Any],
        *,
        validate_only: bool = False,
        include_record: bool = False,
        channel: str = "ontology",
    ) -> dict[str, Any]:
        """``include_record`` returns the written base row; only the compatibility endpoints use it.

        ``channel`` is where the request comes from: "business" pages or the "ontology" browser, where
        actions are for administrators only (requirements baseline D3, D12).
        """
        action = self.ontology.action_type(action_type)
        if action is None:
            raise OntologyNotFoundError(f"未知动作类型：{action_type}")
        ctx = self.context(user)
        self.security.require_apply(ctx.principal, action)
        if channel != "business" and not self.security.can_apply_in_browser(ctx.principal, action):
            raise OntologyPermissionError("本体浏览器中的动作只有管理员可以执行，日常维护请在业务页面进行")
        if not isinstance(parameters, dict):
            raise ActionValidationError(
                "parameters 必须是对象",
                {"parameterErrors": [{"parameter": "*", "message": "parameters 必须是对象"}], "criteria": []},
            )
        values, parameter_errors = _validate_parameters(ctx, action.parameters, parameters)
        target = (
            ctx.get(action.object_type, values[action.target_parameter])
            if action.target_parameter in values
            else None
        )
        criteria = (
            _evaluate_criteria(ctx, action, values, target) if not parameter_errors else []
        )
        valid = not parameter_errors and all(c["passed"] for c in criteria)
        details = {"parameterErrors": parameter_errors, "criteria": criteria}
        if validate_only:
            return {"actionType": action_type, "valid": valid, **details}
        target_key = str(parameters.get(action.target_parameter) or "")
        if not valid:
            reasons = [e["message"] for e in parameter_errors] + [
                c["reason"] for c in criteria if not c["passed"]
            ]
            self.log_action(
                action_type, action.object_type, target_key, parameters,
                actor=ctx.principal.username, status="rejected", channel=channel, message="；".join(reasons),
            )
            raise ActionValidationError("；".join(reasons), details)
        try:
            outcome = action.handler(ctx, values, target, ctx.principal.username)
        except (KeyError, ValueError) as exc:
            self.log_action(
                action_type, action.object_type, target_key, parameters,
                actor=ctx.principal.username, status="failed", channel=channel, message=str(exc),
            )
            raise ActionValidationError(str(exc), details) from exc
        edits = outcome.get("edits", [])
        log_id = self.log_action(
            action_type, action.object_type, target_key, parameters,
            actor=ctx.principal.username, status="succeeded",
            message=outcome.get("message"), edits=edits, channel=channel,
        )
        fresh = self.context(user)
        edited_objects = []
        for edit in edits:
            if edit["operation"] == "delete":
                continue
            definition = self.ontology.object_type(edit["objectType"])
            obj = fresh.get(edit["objectType"], edit["primaryKey"])
            if definition and obj and self.security.can_read_type(fresh.principal, definition):
                edited_objects.append(self._serialize(fresh, definition, obj))
        return {
            "actionLogId": log_id,
            "actionType": action_type,
            "status": "succeeded",
            "edits": edits,
            "objects": edited_objects,
            **details,
            **({"record": outcome.get("record")} if include_record else {}),
        }

    def log_action(
        self,
        action_type: str,
        object_type: str,
        object_pk: str,
        parameters: dict[str, Any],
        *,
        actor: str,
        status: str,
        message: str | None = None,
        edits: list[dict[str, Any]] | None = None,
        channel: str = "ontology",
    ) -> str:
        log_id = "olog_" + uuid.uuid4().hex[:16]
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO ontology_action_log(
                    log_id, action_type, object_type, object_pk, parameters_json,
                    edits_json, status, message, actor, channel, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id, action_type, object_type, object_pk,
                    json.dumps(parameters, ensure_ascii=False, default=str),
                    json.dumps(edits or [], ensure_ascii=False),
                    status, message, actor, channel,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        return log_id

    def action_log(
        self,
        user: dict[str, Any],
        *,
        object_type: str | None = None,
        primary_key: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        principal = Principal.from_user(user)
        visible = {
            t.api_name for t in self.ontology.object_types
            if self.security.can_read_type(principal, t)
        }
        clauses, params = [], []
        if object_type:
            clauses.append("(object_type = ? OR edits_json LIKE ?)")
            params.extend([object_type, f'%"objectType": "{object_type}"%'])
        if primary_key:
            clauses.append("(object_pk = ? OR edits_json LIKE ?)")
            params.extend([primary_key, f'%"primaryKey": "{primary_key}"%'])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ontology_action_log {where} ORDER BY executed_at DESC, log_id DESC",
                params,
            ).fetchall()
        entries = [
            {
                "logId": row["log_id"],
                "actionType": row["action_type"],
                "actionDisplayName": (
                    self.ontology.action_type(row["action_type"]).display_name
                    if self.ontology.action_type(row["action_type"]) else row["action_type"]
                ),
                "objectType": row["object_type"],
                "primaryKey": row["object_pk"],
                "parameters": json.loads(row["parameters_json"]),
                "edits": json.loads(row["edits_json"]),
                "status": row["status"],
                "message": row["message"],
                "actor": row["actor"],
                "channel": row["channel"],
                "executedAt": row["executed_at"],
            }
            for row in rows
            if row["object_type"] in visible and (not principal.scoped or row["actor"] == principal.username)
        ]
        total = len(entries)
        start = (page - 1) * page_size
        return {
            "data": entries[start:start + page_size],
            "meta": {
                "page": page,
                "pageSize": page_size,
                "totalEntries": total,
                "totalPages": max(1, math.ceil(total / page_size)),
            },
        }

    # --------------------------------------------------------------- functions
    def execute_function(
        self, user: dict[str, Any], function_name: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        function = self.ontology.function(function_name)
        if function is None:
            raise OntologyNotFoundError(f"未知函数：{function_name}")
        ctx = self.context(user)
        self.security.require_execute(ctx.principal, function)
        values, errors = _validate_parameters(ctx, function.parameters, parameters)
        if errors:
            raise ActionValidationError(
                "；".join(e["message"] for e in errors), {"parameterErrors": errors}
            )
        for param in function.parameters:
            if param.object_type and param.api_name in values:
                self._readable_type(ctx, param.object_type)
        try:
            result = function.implementation(ctx, values)
        except ValueError as exc:
            raise ActionValidationError(str(exc), {"parameterErrors": []}) from exc
        return {"function": function_name, "outputType": function.output_type, "result": result}

    # ---------------------------------------------------------------- helpers
    def _readable_type(self, ctx: OntologyContext, object_type: str) -> ObjectTypeDef:
        definition = self.ontology.object_type(object_type)
        if definition is None:
            raise OntologyNotFoundError(f"未知对象类型：{object_type}")
        self.security.require_read_type(ctx.principal, definition)
        return definition

    def _filterable_property(self, ctx: OntologyContext, definition: ObjectTypeDef, name: str):
        prop = definition.property(name)
        if prop is None:
            raise ActionValidationError(
                f"{definition.api_name} 没有属性 {name}", {"property": name}
            )
        if not self.security.can_read_marking(ctx.principal, prop.marking):
            raise OntologyPermissionError(f"当前用户无权按属性「{prop.display_name}」筛选或排序")
        return prop

    def _serialize(
        self, ctx: OntologyContext, definition: ObjectTypeDef, item: dict[str, Any]
    ) -> dict[str, Any]:
        properties, redacted = self.security.redact(ctx.principal, definition, item)
        return {
            "objectType": definition.api_name,
            "primaryKey": str(item[definition.primary_key]),
            "title": item.get(definition.title_property) or str(item[definition.primary_key]),
            "properties": properties,
            "redactedProperties": redacted,
        }


def _validate_parameters(
    ctx: OntologyContext, definitions: tuple[ParameterDef, ...], raw: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not isinstance(raw, dict):
        return {}, [{"parameter": "*", "message": "parameters 必须是对象"}]
    known = {p.api_name for p in definitions}
    errors = [
        {"parameter": name, "message": f"未知参数 {name}"} for name in raw if name not in known
    ]
    values: dict[str, Any] = {}
    for param in definitions:
        if param.api_name not in raw or raw[param.api_name] in (None, ""):
            if param.required:
                errors.append({"parameter": param.api_name, "message": f"{param.display_name}为必填项"})
            elif param.api_name in raw:
                values[param.api_name] = None
            continue
        try:
            values[param.api_name] = _coerce(ctx, param, raw[param.api_name])
        except ValueError as exc:
            errors.append({"parameter": param.api_name, "message": f"{param.display_name}：{exc}"})
    return values, errors


def _coerce(ctx: OntologyContext, param: ParameterDef, value: Any) -> Any:
    kind = param.data_type
    if kind in {"string", "enum"}:
        if not isinstance(value, str):
            raise ValueError("必须是文本")
        value = value.strip()
        if kind == "enum" and value not in {v for v, _ in param.enum_values}:
            raise ValueError(f"取值必须是 {', '.join(v for v, _ in param.enum_values)} 之一")
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("必须是整数")
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("必须是整数") from exc
    if kind in {"decimal", "money", "percent"}:
        if isinstance(value, bool):
            raise ValueError("必须是数字")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("必须是数字") from exc
        if not number.is_finite():
            raise ValueError("必须是有限数字")
        return format(number.quantize(Decimal("0.01")), "f")
    if kind == "date":
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError as exc:
            raise ValueError("日期格式应为 YYYY-MM-DD") from exc
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("必须是布尔值")
        return value
    if kind == "object_ref":
        key = str(value).strip()
        if ctx.get(param.object_type or "", key) is None:
            raise ValueError(f"对象 {param.object_type}:{key} 不存在")
        return key
    return value


def _evaluate_criteria(
    ctx: OntologyContext, action: ActionTypeDef, values: dict[str, Any], target: dict[str, Any] | None
) -> list[dict[str, Any]]:
    results = []
    for criterion in action.submission_criteria:
        reason = criterion.check(ctx, values, target)
        results.append(
            {"description": criterion.description, "passed": reason is None, "reason": reason}
        )
    return results


def _action_schema(action: ActionTypeDef) -> dict[str, Any]:
    return {
        "apiName": action.api_name,
        "displayName": action.display_name,
        "description": action.description,
        "objectType": action.object_type,
        "targetParameter": action.target_parameter,
        "parameters": [_parameter_schema(p) for p in action.parameters],
        "requiredPermissions": list(action.required_permissions),
        "edits": list(action.edits),
        "writebackDatasets": list(action.writeback_datasets),
        "submissionCriteria": [c.description for c in action.submission_criteria],
        "createsObject": action.creates_object,
        "tone": action.tone,
    }


def _parameter_schema(param: ParameterDef) -> dict[str, Any]:
    return {
        "apiName": param.api_name,
        "displayName": param.display_name,
        "dataType": param.data_type,
        "required": param.required,
        "description": param.description,
        "enumValues": [{"value": v, "label": label} for v, label in param.enum_values],
        "objectType": param.object_type,
        "optionDetails": list(param.option_details),
    }


def _link_schema(link: LinkTypeDef) -> dict[str, Any]:
    return {
        "apiName": link.api_name,
        "reverseApiName": link.reverse_api_name,
        "displayName": link.display_name,
        "reverseDisplayName": link.reverse_display_name,
        "sourceObjectType": link.source_type,
        "targetObjectType": link.target_type,
        "cardinality": link.cardinality,
        "description": link.description,
        "sourceKey": link.source_key,
        "targetKey": link.target_key,
        "via": link.via,
        "periodProperty": link.period_property,
    }


def _month_span(first: str, last: str) -> list[str]:
    """Every month from ``last`` back to ``first`` (YYYY-MM), newest first."""
    months, year, month = [], int(last[:4]), int(last[5:7])
    while f"{year:04d}-{month:02d}" >= first:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return months


def _side_cardinality(cardinality: str, forward: bool) -> str:
    if forward:
        return cardinality
    return {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}.get(cardinality, cardinality)


def _matches(value: Any, expected: str) -> bool:
    if isinstance(value, bool):
        return str(value).lower() == expected.lower()
    return value is not None and str(value) == expected


def _sorted(items: list[dict[str, Any]], prop: str, data_type: str, descending: bool) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> Any:
        value = item.get(prop)
        if data_type in NUMERIC_TYPES and value not in (None, ""):
            try:
                return Decimal(str(value))
            except InvalidOperation:
                return Decimal("0")
        return str(value)

    present = [item for item in items if item.get(prop) not in (None, "")]
    missing = [item for item in items if item.get(prop) in (None, "")]
    return sorted(present, key=key, reverse=descending) + missing
