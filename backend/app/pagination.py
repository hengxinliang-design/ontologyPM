from __future__ import annotations

import base64
import json
from typing import Any


class CursorError(ValueError):
    """Raised when a client supplies an invalid or incompatible list cursor."""


def encode_cursor(kind: str, **values: Any) -> str:
    """Create an opaque, URL-safe cursor for a deterministic list ordering."""
    raw = json.dumps({"kind": kind, **values}, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None, expected_kind: str) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise CursorError("cursor is invalid") from exc
    if not isinstance(payload, dict) or payload.get("kind") != expected_kind:
        raise CursorError("cursor does not match this list")
    return payload
