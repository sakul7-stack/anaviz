"""SQL package. Exports DDL and named query constants."""
from pathlib import Path
from .queries import *  # noqa: F401,F403

SCHEMA_DDL = (Path(__file__).parent / "schema.sql").read_text()
