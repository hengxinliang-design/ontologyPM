from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .config import Settings


class SnapshotSource(Protocol):
    async def fetch(self) -> dict[str, Any]: ...


class FileSnapshotSource:
    def __init__(self, path: Path):
        self.path = path

    async def fetch(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read)

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))


class HttpSnapshotSource:
    def __init__(self, url: str, token: str | None):
        self.url = url
        self.token = token

    async def fetch(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._request)

    def _request(self) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))


def build_source(settings: Settings) -> SnapshotSource:
    if settings.source_kind == "file":
        return FileSnapshotSource(settings.source_file)
    if settings.source_kind == "http":
        if not settings.source_url:
            raise ValueError("XTCRM_SOURCE_URL is required when source kind is http")
        return HttpSnapshotSource(settings.source_url, settings.source_token)
    raise ValueError(f"unsupported source kind: {settings.source_kind}")
