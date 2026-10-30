"""Safe: semantic security policies for the ontology.

Menu permissions remain the coarse identity grant.  This module turns them
into ontology decisions: which object types are visible, which marked
properties are redacted, and which actions/functions a user may run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model import ActionTypeDef, FunctionDef, ObjectTypeDef, Ontology


class OntologyPermissionError(PermissionError):
    pass


@dataclass(frozen=True)
class Principal:
    username: str
    is_admin: bool
    permissions: frozenset[str]
    # Data scope (7.7.0, requirements baseline D2): a project manager sees only their own projects, the
    # ones whose project manager is ``person_name``. ``project_scope`` is filled in by the context.
    role: str | None = None
    person_name: str | None = None
    project_scope: frozenset[str] | None = None

    @classmethod
    def from_user(cls, user: dict[str, Any]) -> "Principal":
        return cls(
            username=user.get("username") or "anonymous",
            is_admin=bool(user.get("is_admin")),
            permissions=frozenset(user.get("permissions") or ()),
            role=user.get("role"),
            person_name=user.get("person_name"),
        )

    @property
    def scoped(self) -> bool:
        return self.role == "project_manager" and not self.is_admin

    def holds_any(self, permissions: tuple[str, ...]) -> bool:
        if self.is_admin or not permissions:
            return True
        return any(permission in self.permissions for permission in permissions)


def in_scope(principal: Principal, object_type: ObjectTypeDef, item: dict[str, Any]) -> bool:
    """Row-level scope (D2): the one place that decides whether a scoped principal may see an object."""
    if not principal.scoped:
        return True
    if not object_type.project_scope:
        return False
    return str(item.get(object_type.project_scope) or "") in (principal.project_scope or frozenset())


class SecurityPolicy:
    def __init__(self, ontology: Ontology):
        self.ontology = ontology

    def can_read_type(self, principal: Principal, object_type: ObjectTypeDef) -> bool:
        if principal.scoped and not object_type.project_scope:
            return False
        return principal.holds_any(object_type.read_permissions)

    def require_read_type(self, principal: Principal, object_type: ObjectTypeDef) -> None:
        if not self.can_read_type(principal, object_type):
            raise OntologyPermissionError(
                f"当前用户无权查看对象类型「{object_type.display_name}」"
            )

    def can_read_marking(self, principal: Principal, marking: str | None) -> bool:
        if marking is None:
            return True
        definition = self.ontology.marking(marking)
        return definition is not None and principal.holds_any(definition.permissions)

    def can_apply(self, principal: Principal, action: ActionTypeDef) -> bool:
        object_type = self.ontology.object_type(action.object_type)
        return (
            object_type is not None
            and self.can_read_type(principal, object_type)
            and principal.holds_any(action.required_permissions)
        )

    def require_apply(self, principal: Principal, action: ActionTypeDef) -> None:
        if not self.can_apply(principal, action):
            raise OntologyPermissionError(f"当前用户无权执行动作「{action.display_name}」")

    def can_execute(self, principal: Principal, function: FunctionDef) -> bool:
        if principal.scoped and function.data_scope is None:
            return False
        return principal.holds_any(function.required_permissions)

    def can_apply_in_browser(self, principal: Principal, action: ActionTypeDef) -> bool:
        """Requirements baseline D12: actions in the ontology browser are for administrators only."""
        return principal.is_admin and self.can_apply(principal, action)

    def require_execute(self, principal: Principal, function: FunctionDef) -> None:
        if not self.can_execute(principal, function):
            raise OntologyPermissionError(f"当前用户无权调用函数「{function.display_name}」")

    def redact(
        self, principal: Principal, object_type: ObjectTypeDef, properties: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        """Null out marked properties the principal may not read."""
        visible: dict[str, Any] = {}
        redacted: list[str] = []
        for prop in object_type.properties:
            if self.can_read_marking(principal, prop.marking):
                visible[prop.api_name] = properties.get(prop.api_name)
            else:
                visible[prop.api_name] = None
                redacted.append(prop.api_name)
        return visible, redacted
