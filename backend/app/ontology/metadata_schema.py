"""Metadata document schema for ontology definitions.

Inspired by Hasura's Open Data Domain Specification: every YAML document is
``{kind, version, definition}``.  Structure lives in metadata; behaviour
(datasources, link resolvers, submission criteria, action handlers and
function implementations) is referenced by name and implemented in code.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
API_NAME = r"^[A-Za-z][A-Za-z0-9]*$"

DataType = Literal[
    "string", "integer", "decimal", "money", "percent", "date", "datetime",
    "boolean", "enum", "json", "object_ref",
]
# Dimension of a numeric property (situation awareness: values carry units). Every numeric property declares
# one; "none" is for identifiers such as a year or a sequence number.
Unit = Literal["yuan", "personDay", "hour", "day", "month", "percent", "percentagePoint", "person", "count", "none"]
NUMERIC_TYPES = frozenset({"integer", "decimal", "money", "percent"})


class MetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True, frozen=True)


class OntologyDoc(MetadataModel):
    api_name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    display_name: str
    version: str
    description: str = ""

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if not SEMVER.match(value):
            raise ValueError("version 必须是语义化版本号，例如 1.2.0")
        return value


class EnumValueDoc(MetadataModel):
    value: str
    label: str


class ValueTypeDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    display_name: str
    description: str = ""
    values: tuple[EnumValueDoc, ...] = Field(min_length=1)


class MarkingDoc(MetadataModel):
    api_name: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str
    description: str = ""
    permissions: tuple[str, ...] = Field(min_length=1)


class PropertyDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    display_name: str
    type: DataType
    source_kind: Literal["system", "manual", "derived"]
    source: str
    description: str = ""
    marking: str | None = None
    value_type: str | None = None
    derived_from: str | None = None
    searchable: bool = False
    unit: Unit | None = None
    # Computed for the selected statistics period (the object type's ``period``); unmarked properties are cumulative.
    periodic: bool = False


class RequirementConditionDoc(MetadataModel):
    """The requirement applies when the property's value is ``in`` the list, or is not ``notIn`` it (one of the two)."""

    property: str
    in_: tuple[str, ...] | None = Field(default=None, alias="in", min_length=1)
    not_in: tuple[str, ...] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def one_test(self) -> "RequirementConditionDoc":
        if (self.in_ is None) == (self.not_in is None):
            raise ValueError("适用条件必须且只能写 in 或 notIn 之一")
        return self


class MaintenanceRequirementDoc(MetadataModel):
    """A field or relation that people must maintain after import ("what still needs maintenance").

    Exactly one of ``property`` (a manual property must hold a value, other than ``missingValues``) or ``link``
    (at least ``minCount`` linked objects) is checked, only when ``when`` holds (one condition, or a list that must
    all hold). ``action`` maintains it.
    """

    api_name: str = Field(pattern=API_NAME)
    display_name: str
    property: str | None = None
    missing_values: tuple[str, ...] = ()
    link: str | None = None
    min_count: int = Field(default=1, ge=1)
    when: RequirementConditionDoc | list[RequirementConditionDoc] | None = None
    action: str

    @model_validator(mode="after")
    def one_target(self) -> "MaintenanceRequirementDoc":
        if (self.property is None) == (self.link is None):
            raise ValueError("维护要求必须且只能写 property 或 link 之一")
        return self


class PeriodOptionsDoc(MetadataModel):
    object_type: str
    property: str


class PeriodDoc(MetadataModel):
    """A statistics period the object type can be viewed by. Only months (YYYY-MM) for now."""
    unit: Literal["month"]
    display_name: str
    description: str = ""
    # An object is in a month when its activeProperty > 0 (aggregates), its dateProperty falls in it (dated
    # records), or its startProperty–endProperty span overlaps it (planned spans).
    active_property: str | None = None
    date_property: str | None = None
    start_property: str | None = None
    end_property: str | None = None
    # A range of months (from–to) may be chosen, not only one month.
    range: bool = False
    # What the browser shows before a month is chosen: all months, or the latest month with data.
    default_view: Literal["all", "latest"] = "all"
    # Where the selectable months come from; only needed with activeProperty (otherwise the type's own dates).
    options: PeriodOptionsDoc | None = None


class ObjectTypeDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    display_name: str
    plural_display_name: str
    description: str = ""
    primary_key: str
    title_property: str
    datasource: str
    # Derivation rules (rules.py) that compute the derived properties, in order. A type that lists
    # derivations must name one of them in derivedFrom for every derived property.
    derivations: tuple[str, ...] = ()
    maintenance_requirements: tuple[MaintenanceRequirementDoc, ...] = ()
    backing_datasets: tuple[str, ...] = Field(min_length=1)
    read_permissions: tuple[str, ...] = Field(min_length=1)
    icon: str = "cube"
    default_order: str | None = None
    display_order: int = 100
    period: PeriodDoc | None = None
    # Columns of the object list, in order; omitted = the first four plain properties.
    list_properties: tuple[str, ...] | None = None
    properties: tuple[PropertyDoc, ...] = Field(min_length=1)


class JoinDoc(MetadataModel):
    source_key: str
    target_key: str


class ResolverDoc(MetadataModel):
    forward: str
    reverse: str


class LinkTypeDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    reverse_api_name: str = Field(pattern=API_NAME)
    display_name: str
    reverse_display_name: str
    source: str
    target: str
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]
    description: str = ""
    join: JoinDoc | None = None
    via: str | None = None
    resolver: ResolverDoc | None = None
    # From an object viewed for a period: keep targets with activity in it (a date property of the target or ``via``).
    period_property: str | None = None

    @model_validator(mode="after")
    def _join_or_resolver(self) -> "LinkTypeDoc":
        if (self.join is None) == (self.resolver is None):
            raise ValueError(f"link {self.api_name} 必须且只能声明 join 或 resolver 之一")
        return self


class ParameterDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    display_name: str
    type: DataType
    required: bool = False
    description: str = ""
    value_type: str | None = None
    allowed_values: tuple[str, ...] | None = None
    object_type: str | None = None
    # object_ref only: properties of the referenced object shown under the choice (read-only, not submitted).
    option_details: tuple[str, ...] | None = None


class CriterionDoc(MetadataModel):
    description: str
    rule: str
    args: dict[str, Any] = Field(default_factory=dict)


class ActionTypeDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    display_name: str
    description: str = ""
    object_type: str
    target_parameter: str
    parameters: tuple[ParameterDoc, ...] = Field(min_length=1)
    permissions: tuple[str, ...] = Field(min_length=1)
    edits: tuple[str, ...] = ()
    writeback: tuple[str, ...] = ()
    handler: str
    submission_criteria: tuple[CriterionDoc, ...] = ()
    creates_object: bool = False
    tone: Literal["default", "confirm", "danger"] = "default"


class FunctionDoc(MetadataModel):
    api_name: str = Field(pattern=API_NAME)
    display_name: str
    description: str = ""
    parameters: tuple[ParameterDoc, ...] = ()
    output_type: str
    permissions: tuple[str, ...] = Field(min_length=1)
    implementation: str


KIND_MODELS: dict[str, type[MetadataModel]] = {
    "Ontology": OntologyDoc,
    "ValueType": ValueTypeDoc,
    "Marking": MarkingDoc,
    "ObjectType": ObjectTypeDoc,
    "LinkType": LinkTypeDoc,
    "ActionType": ActionTypeDoc,
    "Function": FunctionDoc,
}
SUPPORTED_DOCUMENT_VERSIONS = {"v1"}
