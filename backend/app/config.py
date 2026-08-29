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
    openai_api_key: str | None
    pm_assistant_model: str
    auth_enabled: bool = True
    initial_admin_username: str = "admin"
    initial_admin_password: str = "Tsnt@2026"
    session_hours: int = 12
    require_crm_project_match: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        base_dir = Path(__file__).resolve().parents[1]
        _load_local_env(base_dir)
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
            auto_import_on_empty=os.getenv("XTCRM_AUTO_IMPORT_ON_EMPTY", "false").lower()
            in {"1", "true", "yes"},
            scheduler_enabled=os.getenv("XTCRM_SCHEDULER_ENABLED", "false").lower()
            in {"1", "true", "yes"},
            sync_hour=int(os.getenv("XTCRM_SYNC_HOUR", "3")),
            timezone=os.getenv("XTCRM_TIMEZONE", "America/Detroit"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            pm_assistant_model=os.getenv("TSG_PM_ASSISTANT_MODEL", "gpt-5.2"),
            auth_enabled=os.getenv("TSG_PM_AUTH_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            initial_admin_username=os.getenv("TSG_PM_INITIAL_ADMIN_USERNAME", "admin"),
            initial_admin_password=os.getenv("TSG_PM_INITIAL_ADMIN_PASSWORD", "Tsnt@2026"),
            session_hours=int(os.getenv("TSG_PM_SESSION_HOURS", "12")),
            require_crm_project_match=os.getenv(
                "TSG_PM_REQUIRE_CRM_PROJECT_MATCH", "true"
            ).lower() in {"1", "true", "yes"},
        )


def _load_local_env(base_dir: Path) -> None:
    """Load local development env files without overwriting process environment."""
    for filename in (".env", ".env.local"):
        path = base_dir / filename
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")
