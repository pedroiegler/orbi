"""Contexto de conversa — slots resolvidos, nao so mensagens (D-012).

Guardar `last_product_id` e `last_customer_id` e o que faz "e o preco dele?"
funcionar de forma deterministica: o Runtime preenche o termo a partir do slot
em vez de deixar o LLM adivinhar.

TTL deslizante de 15 minutos, configuravel por tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orbi.db.base import rows_affected
from orbi.db.models import ConversationContext

MAX_TURNS = 5
DEFAULT_TTL_SECONDS = 900

ANAPHORA_TERMS = {
    "dele",
    "dela",
    "deles",
    "delas",
    "desse",
    "dessa",
    "deste",
    "desta",
    "disso",
    "esse",
    "essa",
    "ele",
    "ela",
    "mesmo",
    "mesma",
    "o mesmo",
    "a mesma",
    "esse produto",
    "esse item",
    "esse cliente",
    "o cliente",
}
"""Termos que apontam para o assunto anterior. Quando o modelo devolve um
desses, quem resolve e o slot — nao um chute do modelo."""


@dataclass
class TurnContext:
    """Estado da conversa daquele usuario naquele canal."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    last_product_id: str | None = None
    last_product_name: str | None = None
    last_customer_id: str | None = None
    last_customer_name: str | None = None
    last_location_id: str | None = None
    last_tool: str | None = None
    recent_turns: list[dict[str, str]] = field(default_factory=list)

    def slots(self) -> dict[str, str]:
        """Descricao dos slots para o sufixo dinamico do prompt."""
        values: dict[str, str] = {}
        if self.last_product_name:
            values["ultimo_produto"] = self.last_product_name
        if self.last_customer_name:
            values["ultimo_cliente"] = self.last_customer_name
        return values

    def slot_for(self, entity_type: str) -> tuple[str, str] | None:
        if entity_type == "product" and self.last_product_id and self.last_product_name:
            return self.last_product_id, self.last_product_name
        if entity_type == "customer" and self.last_customer_id and self.last_customer_name:
            return self.last_customer_id, self.last_customer_name
        return None


def is_anaphora(term: str) -> bool:
    return term.strip().lower() in ANAPHORA_TERMS


def load(
    session: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    *,
    now: datetime | None = None,
) -> TurnContext:
    moment = now or datetime.now(UTC)
    row = session.scalars(
        select(ConversationContext).where(
            ConversationContext.tenant_id == tenant_id,
            ConversationContext.user_id == user_id,
            ConversationContext.channel == channel,
        )
    ).first()

    if row is None or row.expires_at <= moment:
        return TurnContext(tenant_id=tenant_id, user_id=user_id, channel=channel)

    return TurnContext(
        tenant_id=tenant_id,
        user_id=user_id,
        channel=channel,
        last_product_id=row.last_product_id,
        last_product_name=row.last_product_name,
        last_customer_id=row.last_customer_id,
        last_customer_name=row.last_customer_name,
        last_location_id=row.last_location_id,
        last_tool=row.last_tool,
        recent_turns=list(row.recent_turns or []),
    )


def save(
    session: Session,
    context: TurnContext,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> None:
    """Grava com TTL deslizante."""
    moment = now or datetime.now(UTC)
    expires_at = moment + timedelta(seconds=ttl_seconds)

    row = session.scalars(
        select(ConversationContext).where(
            ConversationContext.tenant_id == context.tenant_id,
            ConversationContext.user_id == context.user_id,
            ConversationContext.channel == context.channel,
        )
    ).first()

    values: dict[str, Any] = {
        "last_product_id": context.last_product_id,
        "last_product_name": context.last_product_name,
        "last_customer_id": context.last_customer_id,
        "last_customer_name": context.last_customer_name,
        "last_location_id": context.last_location_id,
        "last_tool": context.last_tool,
        "recent_turns": context.recent_turns[-MAX_TURNS:],
        "expires_at": expires_at,
        "updated_at": moment,
    }

    if row is None:
        session.add(
            ConversationContext(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                channel=context.channel,
                **values,
            )
        )
    else:
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()


def remember_turn(context: TurnContext, role: str, text: str) -> None:
    context.recent_turns.append({"role": role, "text": text})
    del context.recent_turns[:-MAX_TURNS]


def purge_expired(session: Session, *, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    result = session.execute(
        delete(ConversationContext).where(ConversationContext.expires_at < moment)
    )
    return rows_affected(result)
