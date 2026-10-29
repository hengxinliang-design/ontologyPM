"""Ontology version governance: registry, history and structural diff.

Every startup registers the compiled metadata under its semantic version.
A version is immutable once registered: changing metadata without bumping
``Ontology.version`` is rejected, and new versions must be higher than any
version registered before (re-activating an existing version is allowed).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..db import Database
from .model import Ontology


class OntologyVersionConflict(RuntimeError):
    pass


def semver_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def document_key(doc: dict[str, Any]) -> str:
    definition = doc["definition"]
    if doc["kind"] == "LinkType":
        return f"LinkType:{definition['source']}.{definition['apiName']}"
    return f"{doc['kind']}:{definition.get('apiName', '')}"


class OntologyVersionRegistry:
    def __init__(self, database: Database):
        self.database = database

    def register(self, ontology: Ontology) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT version, checksum FROM ontology_versions").fetchall()
            existing = {row["version"]: row["checksum"] for row in rows}
            if ontology.version in existing:
                if existing[ontology.version] != ontology.checksum:
                    connection.rollback()
                    raise OntologyVersionConflict(
                        f"本体版本 {ontology.version} 已登记且内容不同（已登记 {existing[ontology.version][:12]}，"
                        f"当前 {ontology.checksum[:12]}）。修改元数据后请升级 ontology.yaml 中的 version。"
                    )
                connection.execute(
                    "UPDATE ontology_versions SET last_activated_at = ? WHERE version = ?",
                    (now, ontology.version),
                )
                status = "unchanged"
            else:
                highest = max(existing, key=semver_key) if existing else None
                if highest and semver_key(ontology.version) < semver_key(highest):
                    connection.rollback()
                    raise OntologyVersionConflict(
                        f"新本体版本 {ontology.version} 低于已登记的最高版本 {highest}，版本号必须递增。"
                    )
                connection.execute(
                    """
                    INSERT INTO ontology_versions(
                        version, checksum, api_name, display_name, description,
                        documents_json, registered_at, last_activated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ontology.version, ontology.checksum, ontology.api_name,
                        ontology.display_name, ontology.description,
                        json.dumps(list(ontology.documents), ensure_ascii=False),
                        now, now,
                    ),
                )
                status = "registered"
            connection.execute(
                """
                INSERT INTO service_state(key, value) VALUES('active_ontology_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (ontology.version,),
            )
            connection.commit()
        return {"version": ontology.version, "checksum": ontology.checksum, "status": status}

    def active_version(self) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value FROM service_state WHERE key = 'active_ontology_version'"
            ).fetchone()
        return row["value"] if row else None

    def list_versions(self) -> list[dict[str, Any]]:
        active = self.active_version()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT version, checksum, api_name, display_name, description, documents_json, "
                "registered_at, last_activated_at FROM ontology_versions"
            ).fetchall()
        versions = [
            {
                "version": row["version"],
                "checksum": row["checksum"],
                "apiName": row["api_name"],
                "displayName": row["display_name"],
                "description": row["description"],
                "documentCount": len(json.loads(row["documents_json"])),
                "registeredAt": row["registered_at"],
                "lastActivatedAt": row["last_activated_at"],
                "active": row["version"] == active,
            }
            for row in rows
        ]
        versions.sort(key=lambda item: semver_key(item["version"]), reverse=True)
        return versions

    def documents(self, version: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT documents_json FROM ontology_versions WHERE version = ?", (version,)
            ).fetchone()
        if row is None:
            raise KeyError(f"本体版本 {version} 不存在")
        return json.loads(row["documents_json"])

    def diff(self, from_version: str, to_version: str) -> dict[str, Any]:
        return diff_documents(self.documents(from_version), self.documents(to_version), from_version, to_version)


def diff_documents(
    before_docs: list[dict[str, Any]], after_docs: list[dict[str, Any]], from_version: str, to_version: str
) -> dict[str, Any]:
    before = {document_key(doc): doc for doc in before_docs}
    after = {document_key(doc): doc for doc in after_docs}
    changes = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        kind, _, name = key.partition(":")
        if old is None:
            changes.append({"kind": kind, "apiName": name, "change": "added", "breaking": False, "details": [], "source": new.get("source")})
        elif new is None:
            changes.append({"kind": kind, "apiName": name, "change": "removed", "breaking": kind != "Ontology", "details": [], "source": old.get("source")})
        else:
            details = _definition_details(kind, old["definition"], new["definition"])
            if details:
                changes.append(
                    {
                        "kind": kind,
                        "apiName": name,
                        "change": "changed",
                        "breaking": any(d["breaking"] for d in details),
                        "details": details,
                        "source": new.get("source"),
                    }
                )
    return {
        "from": from_version,
        "to": to_version,
        "summary": {
            "added": sum(c["change"] == "added" for c in changes),
            "removed": sum(c["change"] == "removed" for c in changes),
            "changed": sum(c["change"] == "changed" for c in changes),
            "breaking": sum(c["breaking"] for c in changes),
        },
        "changes": changes,
    }


KEYED_LISTS = {"properties": "apiName", "parameters": "apiName", "submissionCriteria": "description", "values": "value"}


def _definition_details(kind: str, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    details = []
    for field in sorted(set(old) | set(new)):
        if kind == "Ontology" and field == "version":
            continue
        before, after = old.get(field), new.get(field)
        if before == after:
            continue
        key = KEYED_LISTS.get(field)
        if key and isinstance(before, list) and isinstance(after, list):
            details.extend(_list_details(field, key, before, after))
        elif _scalar_list(before) and _scalar_list(after):
            removed = [item for item in before if item not in after]
            added = [item for item in after if item not in before]
            details.append(
                {
                    "path": field,
                    "change": "changed",
                    "before": removed or None,
                    "after": added or None,
                    "breaking": bool(removed) and field in {"permissions", "readPermissions"},
                }
            )
        else:
            details.append(
                {
                    "path": field,
                    "change": "changed",
                    "before": before,
                    "after": after,
                    "breaking": field in {"primaryKey", "type", "source", "target", "objectType", "targetParameter", "cardinality", "join"},
                }
            )
    return details


def _scalar_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, (str, int, float, bool)) for item in value)


def _list_details(field: str, key: str, before: list[dict], after: list[dict]) -> list[dict[str, Any]]:
    old = {item[key]: item for item in before}
    new = {item[key]: item for item in after}
    details = []
    for name in sorted(set(old) | set(new)):
        path = f"{field}.{name}"
        if name not in old:
            breaking = field == "parameters" and bool(new[name].get("required"))
            details.append({"path": path, "change": "added", "before": None, "after": new[name], "breaking": breaking})
        elif name not in new:
            details.append({"path": path, "change": "removed", "before": old[name], "after": None, "breaking": field in {"properties", "parameters", "values"}})
        elif old[name] != new[name]:
            changed = sorted(k for k in set(old[name]) | set(new[name]) if old[name].get(k) != new[name].get(k))
            breaking = "type" in changed or (field == "parameters" and new[name].get("required") and not old[name].get("required"))
            details.append(
                {
                    "path": path,
                    "change": "changed",
                    "before": {k: old[name].get(k) for k in changed},
                    "after": {k: new[name].get(k) for k in changed},
                    "breaking": bool(breaking),
                }
            )
    return details
