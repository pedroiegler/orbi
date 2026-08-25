"""Ambiente E2E: um tenant completo, com ERP, catalogo e usuarios.

Roda o turno inteiro — canal, identidade, prompt, LLM, policy, resolucao, ERP,
Field Policy, template e auditoria — sem rede, com o provedor `rule_based` e o
adapter `memory`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from orbi.core.crypto import CredentialCipher
from orbi.db.models import Tenant, TenantSettings, User, UserIdentity
from orbi.db.session import admin_session, tenant_session
from orbi.erp import connection as erp_connection
from orbi.erp.adapters.memory import MemoryAdapter
from orbi.llm.providers.rule_based import RuleBasedProvider
from orbi.llm.router import LLMRouter
from orbi.resolution.embeddings import HashingEmbedder
from orbi.runtime.pipeline import InboundMessage, OrbiRuntime, TurnOutcome

SALES = "sales"
FINANCE = "finance"
ADMIN = "admin"
STRANGER = "stranger"


@dataclass
class Harness:
    """Um tenant isolado por teste: alias, contexto e auditoria nao vazam."""

    tenant_id: uuid.UUID
    runtime: OrbiRuntime
    phones: dict[str, str]
    alerts: list[tuple[str, str]] = field(default_factory=list)

    def ask(self, text: str, sender: str = SALES) -> TurnOutcome:
        return self.runtime.handle(
            InboundMessage(
                channel="whatsapp",
                from_address=self.phones.get(sender, sender),
                to_address=self.phones["tenant"],
                text=text,
            )
        )


@pytest.fixture
def harness(database: None) -> Iterator[Harness]:
    tenant_id = uuid.uuid4()
    slug = f"e2e{tenant_id.hex[:8]}"
    base = tenant_id.int % 10**7
    phones = {
        "tenant": f"+5543300{base:07d}",
        SALES: f"+5543910{base:07d}",
        FINANCE: f"+5543920{base:07d}",
        ADMIN: f"+5543930{base:07d}",
        STRANGER: f"+5543980{base:07d}",
    }

    with admin_session() as session:
        session.add(
            Tenant(
                id=tenant_id,
                slug=slug,
                name="Distribuidora E2E",
                status="active",
                channel="whatsapp",
                channel_address=phones["tenant"],
                channel_phone_number_id=f"phone-{slug}",
                debug_mode=False,
            )
        )
        session.add(TenantSettings(tenant_id=tenant_id))
        session.flush()
        for name, role, phone in (
            ("Carlos Vendedor", "sales_rep", phones[SALES]),
            ("Ana Financeiro", "finance", phones[FINANCE]),
            ("Dono", "admin", phones[ADMIN]),
        ):
            user = User(tenant_id=tenant_id, name=name, role_code=role, active=True)
            session.add(user)
            session.flush()
            session.add(
                UserIdentity(
                    tenant_id=tenant_id,
                    user_id=user.id,
                    channel="whatsapp",
                    address=phone,
                    verified_at=__import__("datetime").datetime.now(
                        __import__("datetime").UTC
                    ),
                    active=True,
                )
            )

    cipher = CredentialCipher()
    with tenant_session(tenant_id) as session:
        erp_connection.store_credentials(
            session, tenant_id, "memory", {"token": "nao-usado"}, {}, cipher=cipher
        )
        erp_connection.build_for_tenant(session, tenant_id, cipher=cipher)

    # Catalogo indexado a partir do proprio ERP, como no `orbi onboard`.
    from orbi.catalog.sync import CatalogSynchronizer

    embedder = HashingEmbedder()
    with tenant_session(tenant_id) as session:
        CatalogSynchronizer(session, tenant_id, embedder).run(
            list(MemoryAdapter().iter_catalog())
        )

    alerts: list[tuple[str, str]] = []
    runtime = OrbiRuntime(
        LLMRouter(primary=RuleBasedProvider()),
        embedder=embedder,
        on_alert=lambda subject, detail: alerts.append((subject, detail)),
    )
    yield Harness(tenant_id=tenant_id, runtime=runtime, phones=phones, alerts=alerts)
