"""Object index: materialized ontology objects cached per object type.

Each object type is rebuilt only when one of its backing datasets changed
(tracked by SQLite triggers in ``ontology_dataset_versions``) or the calendar
day rolled over (overdue/due-soon properties depend on today's date).
Rebuilds happen lazily on read, or eagerly via ``warm()`` after data imports.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from .model import Ontology, ObjectTypeDef

if TYPE_CHECKING:
    from .runtime import OntologyContext


@dataclass
class IndexEntry:
    signature: tuple
    objects: list[dict[str, Any]]
    by_key: dict[str, dict[str, Any]]
    built_at: str
    build_ms: float
    builds: int = 1


@dataclass
class IndexStats:
    hits: int = 0
    misses: int = 0


class ObjectIndex:
    def __init__(self, ontology: Ontology):
        self.ontology = ontology
        self._entries: dict[str, IndexEntry] = {}
        self._locks: dict[str, threading.Lock] = {t.api_name: threading.Lock() for t in ontology.object_types}
        self._stats: dict[str, IndexStats] = {t.api_name: IndexStats() for t in ontology.object_types}

    @staticmethod
    def signature(definition: ObjectTypeDef, versions: dict[str, int]) -> tuple:
        return (date.today().isoformat(), tuple(versions.get(d, -1) for d in definition.backing_datasets))

    def entry(self, definition: ObjectTypeDef, ctx: "OntologyContext") -> IndexEntry:
        name = definition.api_name
        signature = self.signature(definition, ctx.dataset_versions())
        current = self._entries.get(name)
        if current is not None and current.signature == signature:
            self._stats[name].hits += 1
            return current
        with self._locks[name]:
            current = self._entries.get(name)
            if current is not None and current.signature == signature:
                self._stats[name].hits += 1
                return current
            started = time.perf_counter()
            objects = definition.loader(ctx)
            entry = IndexEntry(
                signature=signature,
                objects=objects,
                by_key={str(item[definition.primary_key]): item for item in objects},
                built_at=datetime.now(timezone.utc).isoformat(),
                build_ms=round((time.perf_counter() - started) * 1000, 1),
                builds=(current.builds + 1) if current else 1,
            )
            self._entries[name] = entry
            self._stats[name].misses += 1
            return entry

    def invalidate(self, object_types: list[str] | None = None) -> None:
        for name in object_types or list(self._entries):
            self._entries.pop(name, None)

    def warm(self, ctx: "OntologyContext") -> list[str]:
        """Rebuild every stale object type now; returns the types rebuilt."""
        versions = ctx.dataset_versions()
        before = dict(self._entries)
        for definition in self.ontology.object_types:
            current = self._entries.get(definition.api_name)
            if current is None or current.signature != self.signature(definition, versions):
                self.entry(definition, ctx)
        # A derivation may build another type first (Project reads Milestones for its maintenance status),
        # so report every entry that changed, not only the ones this loop asked for.
        return [t.api_name for t in self.ontology.object_types
                if self._entries.get(t.api_name) is not before.get(t.api_name)]

    def status(self, versions: dict[str, int]) -> list[dict[str, Any]]:
        result = []
        for definition in self.ontology.object_types:
            entry = self._entries.get(definition.api_name)
            stats = self._stats[definition.api_name]
            result.append(
                {
                    "objectType": definition.api_name,
                    "displayName": definition.display_name,
                    "backingDatasets": {d: versions.get(d) for d in definition.backing_datasets},
                    "indexed": entry is not None,
                    "fresh": entry is not None and entry.signature == self.signature(definition, versions),
                    "objectCount": len(entry.objects) if entry else None,
                    "builtAt": entry.built_at if entry else None,
                    "buildMs": entry.build_ms if entry else None,
                    "builds": entry.builds if entry else 0,
                    "hits": stats.hits,
                    "misses": stats.misses,
                }
            )
        return result
