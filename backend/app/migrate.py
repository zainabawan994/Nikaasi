"""Apply Alembic migrations programmatically (called on startup and by `python -m app.cli migrate`)."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def alembic_config(engine) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.attributes["connection_engine"] = engine
    return cfg


def upgrade(engine, revision: str = "head") -> None:
    cfg = alembic_config(engine)
    tables = set(inspect(engine).get_table_names())
    if "alembic_version" not in tables and "runs" in tables:
        # database created before migrations existed: it matches revision 0001
        command.stamp(cfg, "0001")
    command.upgrade(cfg, revision)
