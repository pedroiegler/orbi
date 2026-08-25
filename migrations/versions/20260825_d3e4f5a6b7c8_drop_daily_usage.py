"""drop daily_usage

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-08-25

`daily_usage` foi criada como consolidado diario, mas o resumo do dia le direto
de `audit_logs` — que e a fonte da verdade e ja esta particionada por mes. Tabela
que ninguem escreve e ninguem le e codigo morto com custo de manutencao.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("daily_usage")


def downgrade() -> None:
    op.create_table(
        "daily_usage",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("questions", sa.Integer(), nullable=False),
        sa.Column("ambiguous", sa.Integer(), nullable=False),
        sa.Column("not_found", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("tenant_id", "day"),
    )
    op.execute("ALTER TABLE daily_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE daily_usage FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY daily_usage_tenant_isolation ON daily_usage
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
