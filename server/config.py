"""Settings loaded from environment variables (or .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Settings:
    db_url: str = "postgresql://dcs:dcs@localhost:5433/dcs"
    archive_url: str = "http://localhost:9000"
    data_source: str = "http"  # "http" (default) | "hlt" | "simulator"

    @property
    def frontend_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "frontend"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_url=os.environ.get("DCSVIZ_DB", cls.db_url),
            archive_url=os.environ.get("DCSVIZ_ARCHIVE", cls.archive_url),
            data_source=os.environ.get("DATA_SOURCE", cls.data_source),
        )
