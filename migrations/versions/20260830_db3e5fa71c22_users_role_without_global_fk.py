"""users.role_code sem chave estrangeira global

Revision ID: db3e5fa71c22
Revises: ca2d4fb90f19
Create Date: 2026-08-30

Com papeis proprios por cliente (D-040), `users.role_code` pode apontar para um
papel que existe so naquele tenant. A chave estrangeira para a tabela global
`roles` recusaria exatamente esses casos.

A protecao nao desaparece — ela muda de lugar e fica mais forte:

- a CLI valida o papel contra os papeis efetivos **daquele cliente**;
- o Runtime resolve papel desconhecido para **nenhuma permissao**, e a Policy
  Layer nega tudo. Falha fechada: um `role_code` invalido nao concede acesso,
  ele remove todo acesso.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "db3e5fa71c22"
down_revision: str | Sequence[str] | None = "ca2d4fb90f19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("fk_users_role_code_roles", "users", type_="foreignkey")


def downgrade() -> None:
    op.create_foreign_key(
        "fk_users_role_code_roles", "users", "roles", ["role_code"], ["code"],
        ondelete="RESTRICT",
    )
