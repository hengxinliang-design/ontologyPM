from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    database_path: Path
    source_kind: str
    source_file: Path
    source_url: str | None
    source_token: str | None
    api_token: str | None
    record_id_secret: str
    cors_origins: tuple[str, ...]
    auto_import_on_empty: bool
    scheduler_enabled: bool
    sync_hour: int
    timezone: str

    @classmethod
    def from_env(cls) -> "Settings":
        base_dir = Path(__file__).resolve().parents[1]
        data_dir = base_dir / "data"
        origins = os.getenv(
            "XTCRM_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
        return cls(
            base_dir=base_dir,
            database_path=Path(os.getenv("XTCRM_DATABASE_PATH", data_dir / "xtcrm.sqlite3")),
            source_kind=os.getenv("XTCRM_SOURCE_KIND", "file").lower(),
            source_file=Path(
                os.getenv("XTCRM_SOURCE_FILE", data_dir / "source_snapshot.example.json")
            ),
            source_url=os.getenv("XTCRM_SOURCE_URL"),
            source_token=os.getenv("XTCRM_SOURCE_TOKEN"),
            api_token=os.getenv("XTCRM_API_TOKEN"),
            record_id_secret=os.getenv("XTCRM_RECORD_ID_SECRET", "local-development-only"),
            cors_origins=tuple(x.strip() for x in origins.split(",") if x.strip()),
            auto_import_on_empty=os.getenv("XTCRM_AUTO_IMPORT_ON_EMPTY", "true").lower()
            in {"1", "true", "yes"},
            scheduler_enabled=os.getenv("XTCRM_SCHEDULER_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            sync_hour=int(os.getenv("XTCRM_SYNC_HOUR", "3")),
            timezone=os.getenv("XTCRM_TIMEZONE", "America/Detroit"),
        )
