"""Desambiguacao pendente (ORBI.md secao 6.10).

`AMBIGUOUS` devolve tres opcoes numeradas e grava uma pendencia com TTL de 10
minutos. O usuario responde "2" e o Runtime resolve **sem nova chamada ao LLM** —
que e o que mantem a desambiguacao barata e rapida.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orbi.db.models import PendingResolution
from orbi.resolution.resolver import Candidate

DEFAULT_TTL_SECONDS = 600

_CHOICE_RE = re.compile(r"^\s*(?:opcao\s*|op\s*|n[ºo]?\s*)?([1-9])\s*[.)\-]?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class PendingChoice:
    pending_id: uuid.UUID
    tool_name: str
    tool_args: dict[str, Any]
    entity_type: str
    term: str
    erp_entity_id: str
    name: str


def parse_choice(message: str) -> int | None:
    """"2", "opcao 2", "2)" — tudo vira 2. Qualquer outra coisa e None."""
    match = _CHOICE_RE.match(message.strip())
    if match is None:
        return None
    return int(match.group(1))


def create(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    trace_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    entity_type: str,
    term: str,
    options: tuple[Candidate, ...],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: datetime | None = None,
) -> PendingResolution:
    """Substitui qualquer pendencia anterior daquele usuario naquele canal."""
    moment = now or datetime.now(UTC)
    session.execute(
        delete(PendingResolution).where(
            PendingResolution.tenant_id == tenant_id,
            PendingResolution.user_id == user_id,
            PendingResolution.channel == channel,
            PendingResolution.resolved_at.is_(None),
        )
    )

    pending = PendingResolution(
        tenant_id=tenant_id,
        user_id=user_id,
        channel=channel,
        trace_id=trace_id,
        tool_name=tool_name,
        tool_args=tool_args,
        entity_type=entity_type,
        term=term,
        options=[
            {
                "erp_entity_id": option.erp_entity_id,
                "name": option.name,
                "code": option.code,
                "score": round(option.score, 4),
            }
            for option in options
        ],
        expires_at=moment + timedelta(seconds=ttl_seconds),
    )
    session.add(pending)
    session.flush()
    return pending


def resolve_choice(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    message: str,
    now: datetime | None = None,
) -> PendingChoice | None:
    """Le a resposta do usuario contra a pendencia viva. Sem LLM."""
    moment = now or datetime.now(UTC)
    pending = session.scalars(
        select(PendingResolution)
        .where(
            PendingResolution.tenant_id == tenant_id,
            PendingResolution.user_id == user_id,
            PendingResolution.channel == channel,
            PendingResolution.resolved_at.is_(None),
            PendingResolution.expires_at > moment,
        )
        .order_by(PendingResolution.created_at.desc())
    ).first()
    if pending is None:
        return None

    index = parse_choice(message)
    if index is None or index > len(pending.options):
        return None

    option = pending.options[index - 1]
    pending.resolved_at = moment
    pending.chosen_erp_entity_id = str(option["erp_entity_id"])
    session.flush()

    return PendingChoice(
        pending_id=pending.id,
        tool_name=pending.tool_name,
        tool_args=dict(pending.tool_args),
        entity_type=pending.entity_type,
        term=pending.term,
        erp_entity_id=str(option["erp_entity_id"]),
        name=str(option["name"]),
    )


def has_open(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    channel: str,
    now: datetime | None = None,
) -> bool:
    moment = now or datetime.now(UTC)
    return (
        session.scalars(
            select(PendingResolution.id).where(
                PendingResolution.tenant_id == tenant_id,
                PendingResolution.user_id == user_id,
                PendingResolution.channel == channel,
                PendingResolution.resolved_at.is_(None),
                PendingResolution.expires_at > moment,
            )
        ).first()
        is not None
    )


def purge_expired(session: Session, *, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    result = session.execute(
        delete(PendingResolution).where(PendingResolution.expires_at < moment)
    )
    return int(result.rowcount or 0)
