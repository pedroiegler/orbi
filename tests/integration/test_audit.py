"""Auditoria: encadeamento de hash, append-only e LGPD."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from orbi.audit.logger import (
    ANONYMIZED_TEXT,
    AuditRecord,
    erase_user_data,
    hash_payload,
    record_feedback,
    verify_chain,
    write,
)
from orbi.db.session import admin_session, tenant_session

pytestmark = pytest.mark.integration


def _record(tenant: uuid.UUID, **overrides: object) -> AuditRecord:
    defaults: dict[str, object] = {
        "tenant_id": str(tenant),
        "trace_id": str(uuid.uuid4()),
        "channel": "whatsapp",
        "status": "ok",
        "policy_decision": "ALLOW",
        "policy_version_hash": "abc123",
        "tool_name": "check_stock",
        "tool_args": {"product_term": "cimento"},
        "resolved_entity": {"erp_entity_id": "5120", "name": "CIM CP-II 50KG"},
        "latencies_ms": {"llm": 400, "erp": 800},
        "erp_payload_hash": hash_payload({"physical": 420}),
        "key_fields": {"quantity": "360", "basis": "available"},
    }
    defaults.update(overrides)
    return AuditRecord(**defaults)  # type: ignore[arg-type]


def test_chain_is_valid_after_several_turns(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        for _ in range(5):
            write(session, _record(tenant_id))

    with tenant_session(tenant_id) as session:
        report = verify_chain(session, str(tenant_id))
    assert report.valid
    assert report.rows == 5


def test_each_row_points_to_the_previous_one(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        first = write(session, _record(tenant_id))
        write(session, _record(tenant_id))

    with tenant_session(tenant_id) as session:
        rows = session.execute(
            text(
                "SELECT prev_hash, row_hash FROM audit_logs "
                "WHERE tenant_id = :t ORDER BY occurred_at ASC"
            ),
            {"t": str(tenant_id)},
        ).all()
    assert rows[0].prev_hash is None
    assert rows[1].prev_hash == first


def test_the_database_refuses_to_change_an_audited_field(tenant_id: uuid.UUID) -> None:
    """Nem o papel administrativo altera campo coberto pela corrente."""
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id, tool_name="check_price"))

    with pytest.raises(Exception, match="append-only"), admin_session() as session:
        session.execute(
            text("UPDATE audit_logs SET tool_name = 'list_open_invoices' WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        )


def test_the_database_refuses_to_delete_an_audit_row(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id))

    with pytest.raises(Exception, match="append-only"), admin_session() as session:
        session.execute(
            text("DELETE FROM audit_logs WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )


def test_tampering_is_detected(tenant_id: uuid.UUID) -> None:
    """A corrente existe para o caso em que alguem tem acesso privilegiado.

    Aqui o gatilho e desligado de proposito — e exatamente o que um atacante com
    superusuario faria — e o scan precisa acusar.
    """
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id, tool_name="check_stock"))
        write(session, _record(tenant_id, tool_name="check_price"))

    with admin_session() as session:
        session.execute(text("ALTER TABLE audit_logs DISABLE TRIGGER audit_logs_append_only"))
        session.execute(
            text(
                "UPDATE audit_logs SET tool_name = 'list_open_invoices' "
                "WHERE tenant_id = :t AND tool_name = 'check_price'"
            ),
            {"t": str(tenant_id)},
        )
        session.execute(text("ALTER TABLE audit_logs ENABLE TRIGGER audit_logs_append_only"))

    with tenant_session(tenant_id) as session:
        report = verify_chain(session, str(tenant_id))
    assert not report.valid
    assert report.broken_at


def test_audit_never_stores_the_raw_erp_payload(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id))
        row = session.execute(
            text("SELECT erp_payload_hash, key_fields FROM audit_logs WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        ).one()
    assert len(row.erp_payload_hash) == 64
    assert set(row.key_fields) == {"quantity", "basis"}


def test_denied_turns_are_audited_too(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        write(
            session,
            _record(
                tenant_id,
                policy_decision="DENY",
                reason_code="TOOL_NOT_ALLOWED_FOR_ROLE",
                status="denied",
                erp_payload_hash=None,
                key_fields=None,
            ),
        )
        row = session.execute(
            text("SELECT policy_decision, reason_code FROM audit_logs WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        ).one()
    assert row.policy_decision == "DENY"
    assert row.reason_code == "TOOL_NOT_ALLOWED_FOR_ROLE"


def test_erase_user_data_keeps_metrics_and_the_chain(tenant_id: uuid.UUID) -> None:
    user = uuid.uuid4()
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id, user_id=str(user), message_text="quanto tem de cimento"))
        write(session, _record(tenant_id, user_id=str(user), message_text="e o preco dele"))

    with admin_session() as session:
        erased = erase_user_data(session, str(tenant_id), str(user))

    assert erased == 2
    with tenant_session(tenant_id) as session:
        rows = session.execute(
            text("SELECT message_text, tool_name FROM audit_logs WHERE tenant_id = :t"),
            {"t": str(tenant_id)},
        ).all()
        report = verify_chain(session, str(tenant_id))

    assert all(row.message_text == ANONYMIZED_TEXT for row in rows)
    assert all(row.tool_name for row in rows)  # metricas preservadas
    assert report.valid  # o direito ao esquecimento nao quebra a corrente


def test_feedback_is_recorded_against_the_trace(tenant_id: uuid.UUID) -> None:
    trace_id = str(uuid.uuid4())
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id, trace_id=trace_id))

    with admin_session() as session:
        updated = record_feedback(session, trace_id, "down")

    assert updated == 1
    with tenant_session(tenant_id) as session:
        stored = session.execute(
            text("SELECT feedback FROM audit_logs WHERE trace_id = :t"), {"t": trace_id}
        ).scalar_one()
    assert stored == "down"


def test_chain_is_per_tenant(tenant_id: uuid.UUID, other_tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        write(session, _record(tenant_id))
    with tenant_session(other_tenant_id) as session:
        write(session, _record(other_tenant_id))
        report = verify_chain(session, str(other_tenant_id))
    assert report.valid
    assert report.rows == 1
