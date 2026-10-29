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

    @classmethod
    def from_user(cls, user: dict[str, Any]) -> "Principal":
        return cls(
            username=user.get("username") or "anonymous",
            is_admin=bool(user.get("is_admin")),
            permissions=frozenset(user.get("permissions") or ()),
        )

    def holds_any(self, permissions: tuple[str, ...]) -> bool:
        if self.is_admin or not permissions:
            return True
        return any(permission in self.permissions for permission in permissions)


class SecurityPolicy:
    def __init__(self, ontology: Ontology):
        self.ontology = ontology

    def can_read_type(self, principal: Principal, object_type: ObjectTypeDef) -> bool:
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
        return principal.holds_any(function.required_permissions)

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
