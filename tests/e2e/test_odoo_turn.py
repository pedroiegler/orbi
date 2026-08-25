"""Turno completo contra um Odoo real (ORBI.md secao 17).

Roda quando ha instancia viva:

    docker compose -f docker/docker-compose.odoo.yml up -d
    python scripts/odoo_bootstrap.py
    ORBI_ODOO_URL=http://localhost:8069 pytest tests/e2e/test_odoo_turn.py

E o teste que prova o principio 19 antes do primeiro cliente: se a mesma
abstracao atende o adapter em memoria e o XML-RPC do Odoo, ela atende quase
qualquer ERP.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from orbi.catalog.sync import CatalogSynchronizer
from orbi.core.crypto import CredentialCipher
from orbi.db.models import Tenant, TenantSettings, User, UserIdentity
from orbi.db.session import admin_session, tenant_session
from orbi.erp import connection as erp_connection
from orbi.llm.providers.rule_based import RuleBasedProvider
from orbi.llm.router import LLMRouter
from orbi.resolution import calibration
from orbi.resolution.embeddings import HashingEmbedder
from orbi.runtime.pipeline import InboundMessage, OrbiRuntime, TurnOutcome

pytestmark = [pytest.mark.e2e, pytest.mark.odoo, pytest.mark.integration]

ODOO_URL = os.environ.get("ORBI_ODOO_URL", "")


class OdooHarness:
    def __init__(self, tenant_id: uuid.UUID, runtime: OrbiRuntime, phones: dict[str, str]) -> None:
        self.tenant_id = tenant_id
        self.runtime = runtime
        self.phones = phones

    def ask(self, text: str, sender: str = "sales") -> TurnOutcome:
        return self.runtime.handle(
            InboundMessage(
                channel="whatsapp",
                from_address=self.phones[sender],
                to_address=self.phones["tenant"],
                text=text,
            )
        )


@pytest.fixture(scope="module")
def odoo(database: None) -> Iterator[OdooHarness]:
    if not ODOO_URL:
        pytest.skip("defina ORBI_ODOO_URL para rodar contra o Odoo")

    tenant_id = uuid.uuid4()
    slug = f"odoo{tenant_id.hex[:8]}"
    base = tenant_id.int % 10**7
    phones = {
        "tenant": f"+5543301{base:07d}",
        "sales": f"+5543911{base:07d}",
        "finance": f"+5543921{base:07d}",
    }

    with admin_session() as session:
        session.add(
            Tenant(
                id=tenant_id,
                slug=slug,
                name="Distribuidora Odoo",
                status="active",
                channel_address=phones["tenant"],
                channel_phone_number_id=f"phone-{slug}",
            )
        )
        session.add(TenantSettings(tenant_id=tenant_id))
        session.flush()
        for name, role, key in (
            ("Vendedor", "sales_rep", "sales"),
            ("Financeiro", "finance", "finance"),
        ):
            user = User(tenant_id=tenant_id, name=name, role_code=role)
            session.add(user)
            session.flush()
            session.add(
                UserIdentity(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    channel="whatsapp",
                    address=phones[key],
                    verified_at=datetime.now(UTC),
                )
            )

    credentials = {
        "url": ODOO_URL,
        "db": os.environ.get("ORBI_ODOO_DB", "orbi"),
        "username": os.environ.get("ORBI_ODOO_USER", "admin"),
        "api_key": os.environ.get("ORBI_ODOO_API_KEY", "admin"),
    }
    embedder = HashingEmbedder()
    with tenant_session(tenant_id) as session:
        erp_connection.store_credentials(
            session, tenant_id, "odoo", credentials, {"timeout_ms": 20_000}
        )
        tenant_erp = erp_connection.build_for_tenant(
            session, tenant_id, cipher=CredentialCipher(), refresh_capabilities=True
        )
        CatalogSynchronizer(session, tenant_id, embedder).run(
            tenant_erp.adapter.iter_catalog(), mode="full"
        )
    with tenant_session(tenant_id) as session:
        result = calibration.calibrate(session, tenant_id, embedder, sample=30)
        if result is not None:
            calibration.apply(session, tenant_id, result, embedder)

    runtime = OrbiRuntime(LLMRouter(primary=RuleBasedProvider()), embedder=embedder)
    yield OdooHarness(tenant_id, runtime, phones)


def test_capabilities_are_read_from_the_live_erp(odoo: OdooHarness) -> None:
    with tenant_session(odoo.tenant_id) as session:
        tenant_erp = erp_connection.build_for_tenant(session, odoo.tenant_id)
    capabilities = tenant_erp.capabilities

    assert capabilities.adapter == "odoo"
    assert capabilities.integration_mode == "official_api"
    assert capabilities.erp_version
    assert "check_stock" in capabilities.supported_tools


def test_stock_question_answers_from_odoo(odoo: OdooHarness) -> None:
    outcome = odoo.ask("quanto tem de cimento?")
    assert outcome.status == "ok"
    assert outcome.tool_name == "check_stock"
    assert "CIM CP-II 50KG" in outcome.text
    assert "[CIMCP2]" not in outcome.text  # o codigo aparece no recibo, nao no nome


def test_available_is_smaller_than_physical_when_there_are_reservations(
    odoo: OdooHarness,
) -> None:
    """O Odoo informa reservas: a resposta e o disponivel, e diz isso."""
    with tenant_session(odoo.tenant_id) as session:
        tenant_erp = erp_connection.build_for_tenant(session, odoo.tenant_id)

    from orbi.core.deadline import Deadline

    gateway = tenant_erp.gateway(odoo.tenant_id)
    product_id = os.environ.get("ORBI_ODOO_PRODUCT_ID")
    if not product_id:
        pytest.skip("defina ORBI_ODOO_PRODUCT_ID")

    stock = gateway.get_stock(product_id, deadline=Deadline(total_ms=20_000))
    assert stock.basis == "available"
    assert stock.available is not None and stock.reserved is not None
    assert stock.available == stock.physical - stock.reserved


def test_price_hides_cost_from_the_sales_rep_and_shows_it_to_finance(
    odoo: OdooHarness,
) -> None:
    sales = odoo.ask("qual o preco do cimento?", sender="sales")
    finance = odoo.ask("qual o preco do cimento?", sender="finance")

    assert sales.status == "ok"
    assert "Custo" not in sales.text
    assert finance.status == "ok"
    assert "Custo" in finance.text


def test_open_invoices_come_from_odoo(odoo: OdooHarness) -> None:
    outcome = odoo.ask("a construtora silva tem titulos em aberto?", sender="finance")
    assert outcome.status == "ok"
    assert outcome.tool_name == "list_open_invoices"
    assert "CONSTRUTORA SILVA" in outcome.text.upper()


def test_last_order_comes_from_odoo(odoo: OdooHarness) -> None:
    outcome = odoo.ask("qual o ultimo pedido da construtora silva?")
    assert outcome.status == "ok"
    assert outcome.tool_name == "get_last_order"
    assert "R$" in outcome.text


def test_ambiguous_product_asks_and_the_choice_answers(odoo: OdooHarness) -> None:
    first = odoo.ask("quanto tem de tubo pvc?")
    assert first.status == "ambiguous"
    assert len(first.options) >= 2

    second = odoo.ask("1")
    assert second.status == "ok"
    assert second.used_llm is False


def test_turn_stays_inside_the_latency_budget(odoo: OdooHarness) -> None:
    """Meta de 2 a 4 segundos, medida — nao prometida (secao 15)."""
    outcome = odoo.ask("quanto tem de cimento?")
    total = sum(outcome.latencies_ms.values())
    assert total < 4_000, f"turno levou {total} ms"
