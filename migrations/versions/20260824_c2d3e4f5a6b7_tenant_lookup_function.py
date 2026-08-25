"""tenant lookup by channel address

Revision ID: c2d3e4f5a6b7
Revises: b1f2c3d4e5a6
Create Date: 2026-08-24

O Runtime precisa descobrir o tenant a partir do numero de destino ANTES de
poder emitir `SET LOCAL app.tenant_id` — e a RLS, corretamente, esconde a tabela
`tenants` de quem ainda nao declarou o tenant.

A saida nao e dar credencial administrativa a aplicacao: e uma unica funcao
`SECURITY DEFINER`, com escopo minimo, que devolve so o que a identificacao
precisa, para um endereco de canal por vez. A excecao fica escrita, versionada e
revisavel — em vez de espalhada como privilegio amplo.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1f2c3d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION orbi_tenant_by_channel(
            p_channel text,
            p_address text
        )
        RETURNS TABLE (
            id uuid,
            slug text,
            name text,
            status text,
            plan text,
            monthly_query_cap integer,
            debug_mode boolean
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        STABLE
        AS $$
            SELECT t.id, t.slug::text, t.name::text, t.status::text, t.plan::text,
                   t.monthly_query_cap, t.debug_mode
            FROM tenants t
            WHERE t.channel = p_channel
              AND (t.channel_phone_number_id = p_address OR t.channel_address = p_address)
            LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION orbi_tenant_by_channel(text, text) FROM PUBLIC")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbi_app') THEN
                EXECUTE 'GRANT EXECUTE ON FUNCTION orbi_tenant_by_channel(text, text) TO orbi_app';
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS orbi_tenant_by_channel(text, text)")
