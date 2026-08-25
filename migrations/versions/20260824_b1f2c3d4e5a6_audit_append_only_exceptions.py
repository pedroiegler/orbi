"""audit logs: allow only anonymization and feedback

Revision ID: b1f2c3d4e5a6
Revises: cbae5d424f49
Create Date: 2026-08-24

`audit_logs` continua append-only: nenhum campo que a corrente de hash cobre
pode mudar, e DELETE nunca e permitido. Duas excecoes precisas ficam abertas
porque o produto depende delas e nenhuma delas altera o `row_hash`:

- `message_text`, para o `erase_user_data` da LGPD (ORBI.md secao 11);
- `feedback`, para a reacao 👍/👎 que chega por webhook (secao 14).

A aplicacao continua sem GRANT de UPDATE: as duas rodam com o papel
administrativo.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b1f2c3d4e5a6"
down_revision: str | Sequence[str] | None = "cbae5d424f49"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION orbi_audit_logs_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'audit_logs e append-only: DELETE nao e permitido';
    END IF;

    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
        OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
        OR NEW.trace_id IS DISTINCT FROM OLD.trace_id
        OR NEW.user_id IS DISTINCT FROM OLD.user_id
        OR NEW.channel IS DISTINCT FROM OLD.channel
        OR NEW.tool_name IS DISTINCT FROM OLD.tool_name
        OR NEW.tool_args IS DISTINCT FROM OLD.tool_args
        OR NEW.resolved_entity IS DISTINCT FROM OLD.resolved_entity
        OR NEW.policy_decision IS DISTINCT FROM OLD.policy_decision
        OR NEW.reason_code IS DISTINCT FROM OLD.reason_code
        OR NEW.policy_version_hash IS DISTINCT FROM OLD.policy_version_hash
        OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
        OR NEW.llm_provider IS DISTINCT FROM OLD.llm_provider
        OR NEW.llm_model IS DISTINCT FROM OLD.llm_model
        OR NEW.tokens_in IS DISTINCT FROM OLD.tokens_in
        OR NEW.tokens_out IS DISTINCT FROM OLD.tokens_out
        OR NEW.cost_usd IS DISTINCT FROM OLD.cost_usd
        OR NEW.latencies_ms IS DISTINCT FROM OLD.latencies_ms
        OR NEW.status IS DISTINCT FROM OLD.status
        OR NEW.erp_payload_hash IS DISTINCT FROM OLD.erp_payload_hash
        OR NEW.key_fields IS DISTINCT FROM OLD.key_fields
        OR NEW.prev_hash IS DISTINCT FROM OLD.prev_hash
        OR NEW.row_hash IS DISTINCT FROM OLD.row_hash
    THEN
        RAISE EXCEPTION
            'audit_logs e append-only: so message_text e feedback podem mudar';
    END IF;

    RETURN NEW;
END;
$$
"""

ORIGINAL_FUNCTION = """
CREATE OR REPLACE FUNCTION orbi_audit_logs_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs e append-only: % nao e permitido', TG_OP;
END;
$$
"""


def upgrade() -> None:
    op.execute(APPEND_ONLY_FUNCTION)


def downgrade() -> None:
    op.execute(ORIGINAL_FUNCTION)
