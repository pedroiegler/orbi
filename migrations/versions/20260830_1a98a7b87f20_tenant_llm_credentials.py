"""Chave de LLM por cliente, cifrada

Revision ID: 1a98a7b87f20
Revises: db3e5fa71c22
Create Date: 2026-08-30

Coluna anulavel de proposito: nulo significa "usa a chave global", que continua
sendo o caso da maioria. Cliente que ganha chave propria ganha teto de gasto
proprio no provedor e para de dividir cota com os outros (D-041).

Nao ha GRANT novo aqui — a coluna nasce numa tabela que ja tem RLS e permissao.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1a98a7b87f20"
down_revision: str | Sequence[str] | None = "db3e5fa71c22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_settings",
        sa.Column("llm_credentials_encrypted", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_settings", "llm_credentials_encrypted")
