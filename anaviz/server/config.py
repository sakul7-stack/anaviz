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
    config_dir: Path = Path(__file__).resolve().parent.parent / "configs"

    @property
    def frontend_path(self) -> Path:
        return Path(__file__).resolve().parent.parent / "frontend"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_url=os.environ.get("DCSVIZ_DB", cls.db_url),
            config_dir=Path(os.environ.get(
                "DCSVIZ_CONFIG_DIR", cls.config_dir)),
        )
