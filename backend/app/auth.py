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
    "health",
    "resource",
    "crm",
    "employeeWorktime",
    "sync",
    "settings",
)


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
                    created_at, updated_at
                ) VALUES (?, ?, 1, ?, 1, ?, ?)
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
        is_admin = _boolean(payload.get("is_admin", False), "管理员标记")
        permissions = _permissions(payload.get("permissions", []), is_admin)
        now = _now()
        with self.database.connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, is_admin, permissions_json, is_active,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?)
                    """,
                    (username, _hash_password(password), int(is_admin), json.dumps(permissions), now, now),
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
            is_admin = _boolean(payload.get("is_admin", current["is_admin"]), "管理员标记")
            permissions = _permissions(payload.get("permissions", json.loads(current["permissions_json"])), is_admin)
            is_active = _boolean(payload.get("is_active", current["is_active"]), "启用状态")
            password_hash = current["password_hash"]
            if payload.get("password"):
                password_hash = _hash_password(_password(payload["password"]))
            if username == "admin" and (not is_admin or not is_active):
                raise ValueError("初始管理员不能被停用或移除管理员权限")
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, is_admin = ?, permissions_json = ?, is_active = ?, updated_at = ?
                WHERE username = ?
                """,
                (password_hash, int(is_admin), json.dumps(permissions), int(is_active), _now(), username),
            )
            if not is_active:
                connection.execute("DELETE FROM auth_sessions WHERE username = ?", (username,))
            connection.commit()
        return self.get_user(username)

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
    }
    if include_meta:
        payload.update({"created_at": row["created_at"], "updated_at": row["updated_at"]})
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
