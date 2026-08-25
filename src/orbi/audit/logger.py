"""Auditoria append-only com encadeamento de hash (ORBI.md secao 11).

Cada linha carrega o `prev_hash` da anterior daquele tenant e seu proprio
`row_hash` — adulteracao vira detectavel com um scan.

Do payload do ERP guarda-se **hash e campos-chave**, nunca o conteudo completo:
payload inteiro e passivo de LGPD sem contrapartida.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from orbi.db.base import rows_affected

ANONYMIZED_TEXT = "[anonimizado]"


@dataclass
class AuditRecord:
    """Uma linha de auditoria — um turno."""

    tenant_id: str
    trace_id: str
    channel: str
    status: str
    policy_decision: str
    policy_version_hash: str
    user_id: str | None = None
    message_text: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    resolved_entity: dict[str, Any] | None = None
    reason_code: str | None = None
    prompt_version: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: Decimal | float | None = None
    latencies_ms: dict[str, int] = field(default_factory=dict)
    erp_payload_hash: str | None = None
    key_fields: dict[str, Any] | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def hash_payload(payload: Any) -> str:
    """Hash do que o ERP devolveu. Guardar o payload inteiro seria passivo."""
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_row_hash(record: AuditRecord, prev_hash: str | None) -> str:
    payload = {
        "prev": prev_hash or "",
        "tenant_id": record.tenant_id,
        "trace_id": record.trace_id,
        "user_id": record.user_id,
        "channel": record.channel,
        "occurred_at": record.occurred_at.isoformat(),
        "tool_name": record.tool_name,
        "tool_args": record.tool_args,
        "resolved_entity": record.resolved_entity,
        "policy_decision": record.policy_decision,
        "reason_code": record.reason_code,
        "policy_version_hash": record.policy_version_hash,
        "status": record.status,
        "erp_payload_hash": record.erp_payload_hash,
        "key_fields": record.key_fields,
    }
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write(session: Session, record: AuditRecord) -> str:
    """Grava a linha encadeada e devolve o `row_hash`.

    Um lock consultivo por tenant serializa a leitura do elo anterior: sem ele,
    dois turnos simultaneos gravariam o mesmo `prev_hash` e a corrente quebraria.
    """
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
        {"tenant_id": record.tenant_id},
    )

    prev_hash = session.execute(
        text(
            """
            SELECT row_hash
            FROM audit_logs
            WHERE tenant_id = :tenant_id
            ORDER BY occurred_at DESC, row_hash DESC
            LIMIT 1
            """
        ),
        {"tenant_id": record.tenant_id},
    ).scalar_one_or_none()

    row_hash = compute_row_hash(record, prev_hash)

    session.execute(
        text(
            """
            INSERT INTO audit_logs (
                id, occurred_at, tenant_id, trace_id, user_id, channel, message_text,
                tool_name, tool_args, resolved_entity, policy_decision, reason_code,
                policy_version_hash, prompt_version, llm_provider, llm_model,
                tokens_in, tokens_out, cost_usd, latencies_ms, status,
                erp_payload_hash, key_fields, prev_hash, row_hash
            ) VALUES (
                :id, :occurred_at, :tenant_id, :trace_id, :user_id, :channel, :message_text,
                :tool_name, CAST(:tool_args AS jsonb), CAST(:resolved_entity AS jsonb),
                :policy_decision, :reason_code,
                :policy_version_hash, :prompt_version, :llm_provider, :llm_model,
                :tokens_in, :tokens_out, :cost_usd, CAST(:latencies_ms AS jsonb), :status,
                :erp_payload_hash, CAST(:key_fields AS jsonb), :prev_hash, :row_hash
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "occurred_at": record.occurred_at,
            "tenant_id": record.tenant_id,
            "trace_id": record.trace_id,
            "user_id": record.user_id,
            "channel": record.channel,
            "message_text": record.message_text,
            "tool_name": record.tool_name,
            "tool_args": _json(record.tool_args),
            "resolved_entity": _json(record.resolved_entity),
            "policy_decision": record.policy_decision,
            "reason_code": record.reason_code,
            "policy_version_hash": record.policy_version_hash,
            "prompt_version": record.prompt_version,
            "llm_provider": record.llm_provider,
            "llm_model": record.llm_model,
            "tokens_in": record.tokens_in,
            "tokens_out": record.tokens_out,
            "cost_usd": record.cost_usd,
            "latencies_ms": _json(record.latencies_ms) or "{}",
            "status": record.status,
            "erp_payload_hash": record.erp_payload_hash,
            "key_fields": _json(record.key_fields),
            "prev_hash": prev_hash,
            "row_hash": row_hash,
        },
    )
    return row_hash


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


@dataclass(frozen=True)
class ChainReport:
    tenant_id: str
    rows: int
    valid: bool
    broken_at: str | None = None

    def __str__(self) -> str:
        if self.valid:
            return f"cadeia integra: {self.rows} linhas"
        return f"cadeia quebrada em {self.broken_at} ({self.rows} linhas verificadas)"


def verify_chain(session: Session, tenant_id: str) -> ChainReport:
    """Percorre a corrente e diz se alguem mexeu."""
    rows = session.execute(
        text(
            """
            SELECT trace_id, user_id, channel, occurred_at, tool_name, tool_args,
                   resolved_entity, policy_decision, reason_code, policy_version_hash,
                   status, erp_payload_hash, key_fields, prev_hash, row_hash
            FROM audit_logs
            WHERE tenant_id = :tenant_id
            ORDER BY occurred_at ASC, row_hash ASC
            """
        ),
        {"tenant_id": tenant_id},
    ).all()

    expected_prev: str | None = None
    for index, row in enumerate(rows):
        record = AuditRecord(
            tenant_id=tenant_id,
            trace_id=row.trace_id,
            channel=row.channel,
            status=row.status,
            policy_decision=row.policy_decision,
            policy_version_hash=row.policy_version_hash,
            user_id=str(row.user_id) if row.user_id else None,
            tool_name=row.tool_name,
            tool_args=row.tool_args,
            resolved_entity=row.resolved_entity,
            reason_code=row.reason_code,
            erp_payload_hash=row.erp_payload_hash,
            key_fields=row.key_fields,
            occurred_at=row.occurred_at,
        )
        if row.prev_hash != expected_prev:
            return ChainReport(tenant_id, index, False, row.trace_id)
        if compute_row_hash(record, expected_prev) != row.row_hash:
            return ChainReport(tenant_id, index, False, row.trace_id)
        expected_prev = row.row_hash

    return ChainReport(tenant_id, len(rows), True)


def erase_user_data(session: Session, tenant_id: str, user_id: str) -> int:
    """LGPD: anonimiza o texto original preservando as metricas (secao 11).

    `audit_logs` e append-only para a aplicacao; o direito ao esquecimento e
    executado com o papel administrativo, e a linha continua na corrente porque
    o `row_hash` nao cobre `message_text`.
    """
    result = session.execute(
        text(
            """
            UPDATE audit_logs
            SET message_text = :anonymized
            WHERE tenant_id = :tenant_id AND user_id = :user_id AND message_text IS NOT NULL
            """
        ),
        {"anonymized": ANONYMIZED_TEXT, "tenant_id": tenant_id, "user_id": user_id},
    )
    return rows_affected(result)


def record_feedback(session: Session, trace_id: str, feedback: str) -> int:
    """Reacao 👍/👎 chega por webhook e e gravada contra o `trace_id`.

    Roda com o papel administrativo pela mesma razao do `erase_user_data`.
    """
    result = session.execute(
        text("UPDATE audit_logs SET feedback = :feedback WHERE trace_id = :trace_id"),
        {"feedback": feedback, "trace_id": trace_id},
    )
    return rows_affected(result)


def as_dict(record: AuditRecord) -> dict[str, Any]:
    return asdict(record)
