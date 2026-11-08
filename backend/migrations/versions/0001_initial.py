"""Initial schema: simulation runs and everything recorded during them.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def run_fk():
    return sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("scenario", sa.String(80)),
        sa.Column("started_at", TS, nullable=False),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer, primary_key=True),
        run_fk(),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("sector", sa.String(8), nullable=False),
        sa.Column("reporter_id", sa.String(64)),
        sa.Column("name", sa.String(40)),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("is_bot", sa.Boolean, nullable=False),
        sa.Column("sim_min", sa.Integer, nullable=False),
        sa.Column("clock", sa.String(5)),
        sa.Column("flood_evidence", sa.Float),
        sa.Column("hazard", sa.String(32)),
        sa.Column("ai_engine", sa.String(64)),
        sa.Column("ai", pg.JSONB),
        sa.Column("lat", sa.Float),
        sa.Column("lng", sa.Float),
        sa.Column("photo", sa.LargeBinary),
        sa.Column("photo_mime", sa.String(32)),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("run_id", "code"),
    )
    op.create_index("ix_reports_run_id", "reports", ["run_id"])
    op.create_index("ix_reports_sector", "reports", ["sector"])
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer, primary_key=True),
        run_fk(),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("sector", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("peak_severity", sa.String(16), nullable=False),
        sa.Column("score", sa.Integer),
        sa.Column("confidence", sa.String(8)),
        sa.Column("explanation", sa.Text),
        sa.Column("opened_sim_min", sa.Integer),
        sa.Column("opened_clock", sa.String(5)),
        sa.Column("resolved_sim_min", sa.Integer),
        sa.Column("plan", pg.JSONB),
        sa.Column("impact", pg.JSONB),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("run_id", "code"),
    )
    op.create_index("ix_incidents_run_id", "incidents", ["run_id"])
    op.create_index("ix_incidents_sector", "incidents", ["sector"])
    op.create_table(
        "incident_trail",
        sa.Column("id", sa.Integer, primary_key=True),
        run_fk(),
        sa.Column("incident_code", sa.String(16), nullable=False),
        sa.Column("sim_min", sa.Integer),
        sa.Column("clock", sa.String(5)),
        sa.Column("kind", sa.String(16)),
        sa.Column("text", sa.Text),
        sa.Column("snapshot", pg.JSONB),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer, primary_key=True),
        run_fk(),
        sa.Column("code", sa.String(24), nullable=False),
        sa.Column("incident_code", sa.String(16)),
        sa.Column("sector", sa.String(8)),
        sa.Column("kind", sa.String(16)),
        sa.Column("team", sa.String(64)),
        sa.Column("priority", sa.String(4)),
        sa.Column("eta_min", sa.Integer),
        sa.Column("status", sa.String(32)),
        sa.Column("history", pg.JSONB),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("run_id", "code"),
    )
    op.create_index("ix_tickets_run_id", "tickets", ["run_id"])
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer, primary_key=True),
        run_fk(),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("incident_code", sa.String(16)),
        sa.Column("kind", sa.String(16)),
        sa.Column("severity", sa.String(16)),
        sa.Column("sectors", pg.ARRAY(sa.String(8))),
        sa.Column("message_en", sa.Text),
        sa.Column("message_ur", sa.Text),
        sa.Column("recipients", sa.Integer),
        sa.Column("delivered", sa.Integer),
        sa.Column("failed", sa.Integer),
        sa.Column("clock", sa.String(5)),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_broadcasts_run_id", "broadcasts", ["run_id"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        run_fk(),
        sa.Column("sim_min", sa.Integer),
        sa.Column("clock", sa.String(5)),
        sa.Column("type", sa.String(16)),
        sa.Column("sector", sa.String(8)),
        sa.Column("text", sa.Text),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_events_run_id", "events", ["run_id"])


def downgrade() -> None:
    for t in ("events", "broadcasts", "tickets", "incident_trail", "incidents", "reports", "runs"):
        op.drop_table(t)
