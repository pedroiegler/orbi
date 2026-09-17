"""Isolamento entre tenants — RLS na pratica (ORBI.md secao 11).

O que estes testes provam:
- a sessao sem `SET LOCAL app.tenant_id` nao enxerga nada;
- a sessao de um tenant nao enxerga linha de outro;
- `audit_logs` e append-only no banco, nao so por convencao;
- o tenant canario nunca aparece — garantido pela fixture global.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from orbi.db.base import app_engine
from orbi.db.models import AuditLog, CatalogItem, Tenant, User
from orbi.db.session import admin_session, current_tenant, tenant_session
from tests.conftest import CANARY_TENANT_ID, CanaryLeak

pytestmark = pytest.mark.integration


def _add_product(tenant: uuid.UUID, name: str) -> None:
    with tenant_session(tenant) as session:
        session.add(
            CatalogItem(
                tenant_id=tenant,
                erp_entity_id=f"p-{uuid.uuid4().hex[:8]}",
                entity_type="product",
                name=name,
                canonical_name=name.lower(),
                name_hash=uuid.uuid4().hex,
            )
        )


def test_session_sets_tenant_for_the_transaction(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        assert current_tenant(session) == str(tenant_id)


def test_session_without_tenant_sees_nothing(tenant_id: uuid.UUID) -> None:
    """Falha fechada: sem `SET LOCAL`, a politica nao casa e o resultado e vazio."""
    _add_product(tenant_id, "Tubo PVC 100")
    with Session(bind=app_engine()) as raw:
        rows = raw.execute(select(CatalogItem)).all()
        assert rows == []


def test_tenant_cannot_see_another_tenants_rows(
    tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    _add_product(tenant_id, "Tubo do tenant A")
    _add_product(other_tenant_id, "Tubo do tenant B")

    with tenant_session(tenant_id) as session:
        names = [item.name for item in session.scalars(select(CatalogItem)).all()]
    assert names == ["Tubo do tenant A"]

    with tenant_session(other_tenant_id) as session:
        names = [item.name for item in session.scalars(select(CatalogItem)).all()]
    assert names == ["Tubo do tenant B"]


def test_tenant_row_itself_is_isolated(tenant_id: uuid.UUID, other_tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        visible = session.scalars(select(Tenant.id)).all()
    assert visible == [tenant_id]


def test_insert_for_another_tenant_is_rejected(
    tenant_id: uuid.UUID, other_tenant_id: uuid.UUID
) -> None:
    """WITH CHECK impede gravar linha de outro tenant mesmo com o id na mao."""
    with pytest.raises(Exception), tenant_session(tenant_id) as session:  # noqa: B017
        session.add(User(tenant_id=other_tenant_id, name="Intruso", role_code="sales_rep"))
        session.flush()


def test_audit_logs_reject_update_and_delete(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                trace_id=str(uuid.uuid4()),
                channel="whatsapp",
                policy_decision="ALLOW",
                policy_version_hash="hash",
                status="ok",
                occurred_at=datetime.now(UTC),
                row_hash="a" * 64,
            )
        )

    with pytest.raises(ProgrammingError), tenant_session(tenant_id) as session:
        session.execute(text("UPDATE audit_logs SET status = 'tampered'"))

    with pytest.raises(ProgrammingError), tenant_session(tenant_id) as session:
        session.execute(text("DELETE FROM audit_logs"))


def test_canary_tenant_is_invisible_to_other_tenants(tenant_id: uuid.UUID) -> None:
    with tenant_session(tenant_id) as session:
        rows = session.execute(select(CatalogItem.name)).all()
    assert all("CANARIO" not in name for (name,) in rows)


def test_canary_guard_catches_a_real_leak() -> None:
    """Prova que a fixture global funciona: ler o canario explicitamente falha."""
    with pytest.raises(CanaryLeak), tenant_session(CANARY_TENANT_ID) as session:
        session.execute(select(CatalogItem)).all()


def test_toda_tabela_com_tenant_id_tem_rls_forcado() -> None:
    """A documentacao afirma isso. Um teste e a unica forma de continuar verdade.

    Tabela nova com `tenant_id` e sem RLS nasce legivel por qualquer cliente, e
    o erro so aparece quando ja houver dado de dois clientes la dentro. Aqui ele
    aparece no primeiro `pytest`.
    """
    with admin_session() as session:
        desprotegidas = session.execute(
            text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                  AND EXISTS (
                      SELECT 1 FROM information_schema.columns col
                      WHERE col.table_schema = 'public'
                        AND col.table_name = c.relname
                        AND col.column_name = 'tenant_id'
                  )
                  AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
                ORDER BY c.relname
                """
            )
        ).scalars().all()

    assert not desprotegidas, f"tabelas com tenant_id e sem RLS forcado: {desprotegidas}"


def test_tabelas_globais_sao_exatamente_as_esperadas() -> None:
    """Sem `tenant_id` so pode existir o que e global de proposito.

    Este teste falha quando alguem cria uma tabela de dado de cliente e esquece
    o `tenant_id` — o momento certo de perceber, porque depois de a tabela ter
    dado de dois clientes o conserto ja e migracao de dados.
    """
    esperadas = {
        # catalogo do produto, igual para todos os clientes
        "tenants", "roles", "capabilities", "tools", "role_tools", "role_capabilities",
        # operacao: escritas pela CLI e pelo Runtime, sem leitura por cliente
        "eval_runs", "rate_limit_counters",
        "alembic_version",
    }
    with admin_session() as session:
        globais = set(
            session.execute(
                text(
                    """
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'p')
                      AND c.relname NOT LIKE 'audit_logs_%'
                      AND NOT EXISTS (
                          SELECT 1 FROM information_schema.columns col
                          WHERE col.table_schema = 'public'
                            AND col.table_name = c.relname
                            AND col.column_name = 'tenant_id'
                      )
                    """
                )
            ).scalars().all()
        )

    novas = globais - esperadas
    assert not novas, (
        f"tabela sem tenant_id que ninguem declarou global: {sorted(novas)}. "
        "Se guarda dado de cliente, ela precisa de tenant_id e RLS."
    )
