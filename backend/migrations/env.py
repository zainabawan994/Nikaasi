"""Alembic environment. The engine is passed in by app.migrate (or built from DATABASE_URL for the CLI)."""
import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import _normalize, metadata  # noqa: E402

config = context.config
target_metadata = metadata


def run_migrations_online() -> None:
    engine = config.attributes.get("connection_engine")
    if engine is None:
        engine = create_engine(_normalize(os.environ["DATABASE_URL"]))
    with engine.begin() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        context.run_migrations()


run_migrations_online()
