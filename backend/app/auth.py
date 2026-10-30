from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database


ALL_MENU_PERMISSIONS = (
    "projects",
    "department",
    "health",
    "resource",
    "crm",
    "investment",
    "employeeWorktime",
    "sync",
    "ontology",
    "settings",
)


# Roles (requirements baseline D9): a role sets the data scope and the default menus; an administrator may
# still add or remove menus per account. A project manager sees only their own projects (D2): the projects
# whose project manager equals the account's name (D10), so the role keeps to the project menus.
ROLES: dict[str, dict[str, Any]] = {
    "admin": {"label": "管理员", "scope": "all", "menus": ALL_MENU_PERMISSIONS},
    "pmo": {"label": "PMO", "scope": "all",
            "menus": ("projects", "investment", "department", "resource", "crm", "employeeWorktime", "sync")},
    "department_head": {"label": "部门负责人", "scope": "all", "menus": ("health", "department", "resource", "investment", "crm")},
    "project_manager": {"label": "项目经理", "scope": "own", "menus": ("projects",)},
    "analyst": {"label": "分析人员", "scope": "all", "menus": ("health", "department", "resource", "investment", "crm", "ontology")},
}
PROJECT_MANAGER_MENUS = frozenset({"projects", "health"})


class AuthenticationError(ValueError):
    pass


class AuthorizationError(ValueError):
    pass


class AuthService:
    def __init__(self, database: Database, *, session_hours: int = 12):
        self.database = database
        self.session_hours = session_hours

    def ensure_initial_admin(self, username: str, password: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT username FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row:
                return
            now = _now()
            connection.execute(
                """
                INSERT INTO users (
                    username, password_hash, is_admin, permissions_json, is_active,
                    created_at, updated_at, role
                ) VALUES (?, ?, 1, ?, 1, ?, ?, 'admin')
                """,
                (username, _hash_password(password), json.dumps(list(ALL_MENU_PERMISSIONS)), now, now),
            )
            connection.commit()

    def login(self, username: str, password: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not row or not row["is_active"] or not _verify_password(password, row["password_hash"]):
                raise AuthenticationError("用户名或密码错误")
            raw_token = secrets.token_urlsafe(40)
            now = _now()
            expires_at = (datetime.now(timezone.utc) + timedelta(hours=self.session_hours)).isoformat()
            connection.execute(
                """
                INSERT INTO auth_sessions (token_hash, username, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (_token_hash(raw_token), row["username"], now, expires_at),
            )
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            connection.commit()
        return {"token": raw_token, "user": _user_payload(row), "expires_at": expires_at}

    def logout(self, token: str) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))
            connection.commit()

    def current_user(self, token: str) -> dict[str, Any]:
        now = _now()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT users.* FROM auth_sessions
                JOIN users ON users.username = auth_sessions.username
                WHERE auth_sessions.token_hash = ?
                  AND auth_sessions.expires_at > ?
                  AND users.is_active = 1
                """,
                (_token_hash(token), now),
            ).fetchone()
        if not row:
            raise AuthenticationError("登录已失效，请重新登录")
        return _user_payload(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY is_admin DESC, username COLLATE NOCASE"
            ).fetchall()
        return [_user_payload(row, include_meta=True) for row in rows]

    def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        username = _username(payload.get("username"))
        password = _password(payload.get("password"))
        role, is_admin = _role(payload.get("role"), _boolean(payload.get("is_admin", False), "管理员标记"))
        permissions = _permissions(payload.get("permissions", list(ROLES[role]["menus"]) if role else []), is_admin)
        person_name = _person_name(payload.get("person_name"))
        now = _now()
        with self.database.connect() as connection:
            _check_role(connection, username, role, permissions, person_name)
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, is_admin, permissions_json, is_active,
                        created_at, updated_at, role, person_name
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (username, _hash_password(password), int(is_admin), json.dumps(permissions), now, now, role, person_name),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    raise ValueError("用户名已存在") from exc
                raise
            connection.commit()
        return self.get_user(username)

    def update_user(self, username: str, payload: dict[str, Any]) -> dict[str, Any]:
        username = _username(username)
        with self.database.connect() as connection:
            current = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not current:
                raise ValueError("用户不存在")
            admin_flag = _boolean(payload.get("is_admin", current["is_admin"]), "管理员标记")
            if "role" in payload:
                requested = payload["role"]
            elif "is_admin" in payload:  # former clients only toggle the administrator flag
                requested = "admin" if admin_flag else (None if current["role"] == "admin" else current["role"])
            else:
                requested = current["role"]
            role, is_admin = _role(requested, admin_flag)
            role_changed = role != current["role"]
            default_menus = list(ROLES[role]["menus"]) if role_changed and role else json.loads(current["permissions_json"])
            permissions = _permissions(payload.get("permissions", default_menus), is_admin)
            person_name = _person_name(payload["person_name"] if "person_name" in payload else current["person_name"])
            is_active = _boolean(payload.get("is_active", current["is_active"]), "启用状态")
            password_hash = current["password_hash"]
            if payload.get("password"):
                password_hash = _hash_password(_password(payload["password"]))
            if username == "admin" and (not is_admin or not is_active):
                raise ValueError("初始管理员不能被停用或移除管理员权限")
            _check_role(connection, username, role, permissions, person_name)
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, is_admin = ?, permissions_json = ?, is_active = ?, updated_at = ?,
                    role = ?, person_name = ?
                WHERE username = ?
                """,
                (password_hash, int(is_admin), json.dumps(permissions), int(is_active), _now(),
                 role, person_name, username),
            )
            if not is_active:
                connection.execute("DELETE FROM auth_sessions WHERE username = ?", (username,))
            connection.commit()
        return self.get_user(username)

    def project_manager_names(self) -> list[str]:
        """Names of active project-manager accounts: the choices for Project.projectManager (D10)."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT person_name FROM users WHERE role = 'project_manager' AND is_active = 1 "
                "AND person_name IS NOT NULL ORDER BY person_name"
            ).fetchall()
        return [row["person_name"] for row in rows]

    def get_user(self, username: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        return _user_payload(row, include_meta=True)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(value).decode("ascii")


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_raw, value_raw = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(salt_raw.encode("ascii"))
        expected = base64.b64decode(value_raw.encode("ascii"))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _username(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("用户名不能为空")
    username = value.strip()
    if len(username) < 3 or len(username) > 40:
        raise ValueError("用户名长度应为 3 到 40 个字符")
    if not all(character.isalnum() or character in {"_", "-", "."} for character in username):
        raise ValueError("用户名仅支持字母、数字、下划线、连字符或点号")
    return username


def _password(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 8:
        raise ValueError("密码至少需要 8 个字符")
    return value


def _permissions(value: Any, is_admin: bool) -> list[str]:
    if is_admin:
        return list(ALL_MENU_PERMISSIONS)
    if not isinstance(value, list) or any(item not in ALL_MENU_PERMISSIONS for item in value):
        raise ValueError("菜单权限设置无效")
    return sorted(set(value))


def _role(value: Any, is_admin: bool) -> tuple[str | None, bool]:
    """The account's role and its administrator flag; without a role the former flag still decides."""
    if value in (None, ""):
        return ("admin", True) if is_admin else (None, False)
    if value not in ROLES:
        raise ValueError("角色设置无效")
    return value, value == "admin"


def _person_name(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
        raise ValueError("姓名应为 1 到 40 个字符")
    return value.strip()


def _check_role(connection: Any, username: str, role: str | None, permissions: list[str], person_name: str | None) -> None:
    if role == "project_manager":
        if not person_name:
            raise ValueError("项目经理角色必须填写姓名，姓名与项目的「项目经理」字段一致")
        if not set(permissions) <= PROJECT_MANAGER_MENUS:
            raise ValueError("项目经理只能访问本人项目，菜单只能是「项目管理」或「项目管理（只读）」")
    if person_name:
        taken = connection.execute(
            "SELECT username FROM users WHERE person_name = ? AND username <> ?", (person_name, username)
        ).fetchone()
        if taken:
            raise ValueError(f"姓名「{person_name}」已被账号 {taken['username']} 使用，请加以区分")


def _boolean(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError(f"{label}必须是布尔值")


def _user_payload(row: Any, *, include_meta: bool = False) -> dict[str, Any]:
    payload = {
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "permissions": list(ALL_MENU_PERMISSIONS) if row["is_admin"] else json.loads(row["permissions_json"]),
        "is_active": bool(row["is_active"]),
        "role": row["role"],
        "role_label": ROLES[row["role"]]["label"] if row["role"] in ROLES else "未设角色",
        "person_name": row["person_name"],
        "data_scope": ROLES[row["role"]]["scope"] if row["role"] in ROLES else "all",
    }
    if include_meta:
        payload.update({"created_at": row["created_at"], "updated_at": row["updated_at"]})
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
