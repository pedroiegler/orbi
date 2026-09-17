"""Conexao de ERP de um tenant: credencial cifrada, adapter e capabilities.

O Core nunca importa um adapter concreto: pede um a `erp.registry` a partir do
nome gravado na conexao. E isso que mantem `if erp == "x"` fora do Runtime (P10).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orbi.core.crypto import CredentialCipher
from orbi.core.errors import ConfigurationError
from orbi.db.models import ErpConnection, TenantTool
from orbi.erp.gateway import ErpGateway, OnCircuitOpen
from orbi.erp.port import Capabilities, ErpAdapter
from orbi.erp.registry import build_adapter, ensure_allowed_in_production
from orbi.tools.registry import tool_names

CAPABILITIES_TTL = timedelta(hours=12)


@dataclass
class TenantErp:
    """Adapter pronto para uso, com o que o Core precisa saber sobre ele."""

    connection_id: uuid.UUID
    adapter_name: str
    adapter: ErpAdapter
    capabilities: Capabilities

    def gateway(
        self, tenant_id: uuid.UUID | str, on_circuit_open: OnCircuitOpen | None = None
    ) -> ErpGateway:
        return ErpGateway(self.adapter, str(tenant_id), on_circuit_open=on_circuit_open)


def load_connection(session: Session, tenant_id: uuid.UUID) -> ErpConnection:
    connection = session.scalars(
        select(ErpConnection).where(
            ErpConnection.tenant_id == tenant_id, ErpConnection.active.is_(True)
        )
    ).first()
    if connection is None:
        raise ConfigurationError(
            "tenant sem conexao de ERP ativa: rode `orbi erp add --tenant <slug>`"
        )
    return connection


def build_for_tenant(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    cipher: CredentialCipher | None = None,
    refresh_capabilities: bool = False,
    now: datetime | None = None,
) -> TenantErp:
    """Monta o adapter do tenant e resolve as capabilities (com cache curto)."""
    moment = now or datetime.now(UTC)
    connection = load_connection(session, tenant_id)
    resolved_cipher = cipher or CredentialCipher()
    credentials = resolved_cipher.decrypt(connection.credentials_encrypted)
    adapter = build_adapter(connection.adapter, credentials, dict(connection.config or {}))

    cache = connection.capabilities_cache
    stale = (
        connection.capabilities_checked_at is None
        or moment - connection.capabilities_checked_at > CAPABILITIES_TTL
    )
    if refresh_capabilities or cache is None or stale:
        capabilities = adapter.capabilities()
        connection.capabilities_cache = _to_json(capabilities)
        connection.capabilities_checked_at = moment
        session.flush()
        sync_tenant_tools(session, tenant_id, capabilities)
    else:
        capabilities = _from_json(cache)

    return TenantErp(
        connection_id=connection.id,
        adapter_name=connection.adapter,
        adapter=adapter,
        capabilities=capabilities,
    )


def sync_tenant_tools(
    session: Session, tenant_id: uuid.UUID, capabilities: Capabilities
) -> list[str]:
    """Desliga automaticamente as tools que aquele ERP nao atende (secao 6.11).

    Devolve as tools desligadas. Nenhum `if erp == "x"` no Runtime: o adapter
    declara, o Core obedece.
    """
    rows = {
        row.tool_name: row
        for row in session.scalars(
            select(TenantTool).where(TenantTool.tenant_id == tenant_id)
        ).all()
    }
    disabled: list[str] = []

    for name in tool_names():
        supported = name in capabilities.supported_tools
        row = rows.get(name)
        if row is None:
            row = TenantTool(tenant_id=tenant_id, tool_name=name, enabled=supported)
            session.add(row)
        elif row.disabled_reason is not None or supported != row.enabled:
            # So mexe no que foi desligado pelo proprio ERP: desligamento
            # comercial feito na CLI nao pode ser revertido por um sync.
            if row.disabled_reason is not None or supported:
                row.enabled = supported
        if not supported:
            row.enabled = False
            row.disabled_reason = f"ERP {capabilities.adapter} nao atende esta operacao"
            disabled.append(name)
        elif row.disabled_reason is not None:
            row.disabled_reason = None
        row.updated_at = datetime.now(UTC)

    session.flush()
    return disabled


def enabled_tools(session: Session, tenant_id: uuid.UUID) -> frozenset[str]:
    rows = session.scalars(
        select(TenantTool).where(TenantTool.tenant_id == tenant_id, TenantTool.enabled.is_(True))
    ).all()
    return frozenset(row.tool_name for row in rows)


def store_credentials(
    session: Session,
    tenant_id: uuid.UUID,
    adapter: str,
    credentials: dict[str, Any],
    config: dict[str, Any] | None = None,
    *,
    label: str = "principal",
    cipher: CredentialCipher | None = None,
) -> ErpConnection:
    """Grava a credencial cifrada. O valor em claro nunca toca o banco."""
    resolved_cipher = cipher or CredentialCipher()
    ensure_allowed_in_production(adapter)

    existing = session.scalars(
        select(ErpConnection).where(
            ErpConnection.tenant_id == tenant_id, ErpConnection.label == label
        )
    ).first()

    encrypted = resolved_cipher.encrypt(credentials)
    if existing is None:
        connection = ErpConnection(
            tenant_id=tenant_id,
            adapter=adapter,
            label=label,
            credentials_encrypted=encrypted,
            config=config or {},
            active=True,
        )
        session.add(connection)
        session.flush()
        return connection

    existing.adapter = adapter
    existing.credentials_encrypted = encrypted
    existing.config = config or {}
    existing.active = True
    existing.capabilities_cache = None
    existing.capabilities_checked_at = None
    existing.updated_at = datetime.now(UTC)
    session.flush()
    return existing


def _to_json(capabilities: Capabilities) -> dict[str, Any]:
    data = capabilities.model_dump()
    data["supported_tools"] = sorted(capabilities.supported_tools)
    return data


def _from_json(payload: dict[str, Any]) -> Capabilities:
    data = dict(payload)
    data["supported_tools"] = frozenset(data.get("supported_tools", []))
    return Capabilities(**data)
