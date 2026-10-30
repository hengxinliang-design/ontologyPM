"""Ontology meta-model: the vocabulary used to describe TSG PM business semantics.

The semantic layer never stores its own copy of business data.  Object types
declare which backing dataset materializes them, properties declare where each
value comes from, and actions/functions declare how the business may change
or interpret those objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from .runtime import OntologyContext


DataType = Literal[
    "string", "integer", "decimal", "money", "percent", "date", "datetime",
    "boolean", "enum", "json", "object_ref",
]
SourceKind = Literal["system", "manual", "derived"]
Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]


@dataclass(frozen=True)
class Marking:
    """A security classification attached to properties.

    A viewer may read a marked property when they hold any of the listed
    permissions (or are an administrator).
    """

    api_name: str
    display_name: str
    description: str
    permissions: tuple[str, ...]


UNIT_LABELS = {
    "yuan": "元", "personDay": "人天", "hour": "小时", "day": "天", "month": "个月", "percent": "%",
    "percentagePoint": "个百分点", "person": "人", "count": "个", "none": "",
}


@dataclass(frozen=True)
class PropertyDef:
    api_name: str
    display_name: str
    data_type: DataType
    source_kind: SourceKind
    source: str
    description: str = ""
    marking: str | None = None
    enum_values: tuple[tuple[str, str], ...] = ()
    edited_by: tuple[str, ...] = ()
    derived_from: str | None = None
    searchable: bool = False
    unit: str | None = None
    periodic: bool = False

    @property
    def unit_label(self) -> str:
        return UNIT_LABELS.get(self.unit or "", "")


@dataclass(frozen=True)
class Period:
    """Statistics period of an object type (months). ``active_property`` > 0 marks objects active in the period."""
    unit: str
    display_name: str
    description: str
    active_property: str | None
    options_type: str | None
    options_property: str | None
    date_property: str | None = None
    default_view: str = "all"
    start_property: str | None = None
    end_property: str | None = None
    range: bool = False

    def contains(self, item: dict, first: str, last: str) -> bool:
        """Whether ``item`` belongs to the months ``first``..``last`` (YYYY-MM, inclusive)."""
        if self.date_property:
            value = str(item.get(self.date_property) or "")[:7]
            return bool(value) and first <= value <= last
        if self.start_property and self.end_property:
            end = str(item.get(self.end_property) or "")[:7]
            start = str(item.get(self.start_property) or "")[:7] or end
            return bool(end) and start <= last and end >= first
        return (item.get(self.active_property) or 0) > 0


@dataclass(frozen=True)
class MaintenanceRequirement:
    api_name: str
    display_name: str
    action: str
    property: str | None = None
    missing_values: tuple[str, ...] = ()
    link: str | None = None
    min_count: int = 1
    # (property, values, applies when the value is in them: True for ``in``, False for ``notIn``); all must hold.
    conditions: tuple[tuple[str, tuple[str, ...], bool], ...] = ()

    def applies_to(self, item: dict) -> bool:
        return all((item.get(prop) in values) is inside for prop, values, inside in self.conditions)


@dataclass(frozen=True)
class ObjectTypeDef:
    api_name: str
    display_name: str
    plural_display_name: str
    description: str
    primary_key: str
    title_property: str
    backing_datasets: tuple[str, ...]
    properties: tuple[PropertyDef, ...]
    read_permissions: tuple[str, ...]
    loader: Callable[["OntologyContext"], list[dict[str, Any]]]
    icon: str = "cube"
    default_order: str | None = None
    datasource: str = ""
    derivations: tuple[str, ...] = ()
    maintenance_requirements: tuple[MaintenanceRequirement, ...] = ()
    metadata_source: str = ""
    period: Period | None = None
    list_properties: tuple[str, ...] = ()
    project_scope: str | None = None

    def property(self, api_name: str) -> PropertyDef | None:
        return next((p for p in self.properties if p.api_name == api_name), None)


@dataclass(frozen=True)
class LinkTypeDef:
    """A typed, bidirectional relationship between two object types.

    Simple foreign-key links match ``source[source_key] == target[target_key]``.
    Links that need an intermediate object (many-to-many) supply ``resolver``
    for the forward side and ``reverse_resolver`` for the reverse side.
    """

    api_name: str
    reverse_api_name: str
    display_name: str
    reverse_display_name: str
    source_type: str
    target_type: str
    cardinality: Cardinality
    description: str
    source_key: str | None = None
    target_key: str | None = None
    via: str | None = None
    resolver: Callable[["OntologyContext", dict[str, Any]], list[dict[str, Any]]] | None = None
    reverse_resolver: Callable[["OntologyContext", dict[str, Any]], list[dict[str, Any]]] | None = None
    period_property: str | None = None


@dataclass(frozen=True)
class ParameterDef:
    api_name: str
    display_name: str
    data_type: DataType
    required: bool = False
    description: str = ""
    enum_values: tuple[tuple[str, str], ...] = ()
    object_type: str | None = None
    option_details: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionCriterion:
    """A business precondition evaluated before an action may write.

    ``check`` returns ``None`` when satisfied, or a user-facing reason.
    """

    description: str
    check: Callable[["OntologyContext", dict[str, Any], dict[str, Any] | None], str | None]


@dataclass(frozen=True)
class ActionTypeDef:
    api_name: str
    display_name: str
    description: str
    object_type: str
    target_parameter: str
    parameters: tuple[ParameterDef, ...]
    required_permissions: tuple[str, ...]
    edits: tuple[str, ...]
    writeback_datasets: tuple[str, ...]
    handler: Callable[["OntologyContext", dict[str, Any], dict[str, Any] | None, str], dict[str, Any]]
    submission_criteria: tuple[SubmissionCriterion, ...] = ()
    creates_object: bool = False
    tone: Literal["default", "confirm", "danger"] = "default"


@dataclass(frozen=True)
class FunctionDef:
    api_name: str
    display_name: str
    description: str
    parameters: tuple[ParameterDef, ...]
    output_type: str
    required_permissions: tuple[str, ...]
    implementation: Callable[["OntologyContext", dict[str, Any]], Any]
    data_scope: str | None = None


@dataclass(frozen=True)
class ValueTypeDef:
    api_name: str
    display_name: str
    description: str
    values: tuple[tuple[str, str], ...]


@dataclass
class Ontology:
    api_name: str
    display_name: str
    version: str
    description: str
    markings: tuple[Marking, ...] = ()
    object_types: tuple[ObjectTypeDef, ...] = ()
    link_types: tuple[LinkTypeDef, ...] = ()
    action_types: tuple[ActionTypeDef, ...] = ()
    functions: tuple[FunctionDef, ...] = ()
    value_types: tuple[ValueTypeDef, ...] = ()
    checksum: str = ""
    documents: tuple[dict[str, Any], ...] = field(default=(), repr=False)
    _object_index: dict[str, ObjectTypeDef] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._object_index = {item.api_name: item for item in self.object_types}

    def object_type(self, api_name: str) -> ObjectTypeDef | None:
        return self._object_index.get(api_name)

    def action_type(self, api_name: str) -> ActionTypeDef | None:
        return next((a for a in self.action_types if a.api_name == api_name), None)

    def function(self, api_name: str) -> FunctionDef | None:
        return next((f for f in self.functions if f.api_name == api_name), None)

    def marking(self, api_name: str) -> Marking | None:
        return next((m for m in self.markings if m.api_name == api_name), None)

    def links_for(self, object_type: str) -> list[tuple[LinkTypeDef, bool]]:
        """Return (link, is_forward) pairs traversable from ``object_type``."""
        result: list[tuple[LinkTypeDef, bool]] = []
        for link in self.link_types:
            if link.source_type == object_type:
                result.append((link, True))
            if link.target_type == object_type:
                result.append((link, False))
        return result

    def validate(self) -> None:
        """Fail fast when definitions reference unknown types or properties."""
        for object_type in self.object_types:
            names = [p.api_name for p in object_type.properties]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate property in {object_type.api_name}")
            for required in (object_type.primary_key, object_type.title_property):
                if required not in names:
                    raise ValueError(f"{object_type.api_name} is missing property {required}")
            for prop in object_type.properties:
                if prop.marking and not self.marking(prop.marking):
                    raise ValueError(f"unknown marking {prop.marking}")
                for action in prop.edited_by:
                    if not self.action_type(action):
                        raise ValueError(f"{object_type.api_name}.{prop.api_name} edited by unknown action {action}")
        for object_type in self.object_types:
            side_names = [
                link.api_name if forward else link.reverse_api_name
                for link, forward in self.links_for(object_type.api_name)
            ]
            if len(side_names) != len(set(side_names)):
                raise ValueError(f"{object_type.api_name} has duplicate link names")
        for link in self.link_types:
            for type_name in (link.source_type, link.target_type):
                if not self.object_type(type_name):
                    raise ValueError(f"link {link.api_name} references unknown type {type_name}")
            if link.resolver is None:
                source = self.object_type(link.source_type)
                target = self.object_type(link.target_type)
                if not source.property(link.source_key or "") or not target.property(link.target_key or ""):
                    raise ValueError(f"link {link.api_name} has invalid join keys")
        for action in self.action_types:
            if not self.object_type(action.object_type):
                raise ValueError(f"action {action.api_name} targets unknown type")
            if not any(p.api_name == action.target_parameter for p in action.parameters):
                raise ValueError(f"action {action.api_name} lacks its target parameter")
