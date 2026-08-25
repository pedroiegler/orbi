"""Vocabulario aprendido do tenant (ORBI.md secao 6.10).

"Cano 100", "aquele tubo grosso de esgoto". Cada correcao vira alias permanente
daquele cliente — e esse acumulo e a defesa competitiva mais duravel do produto.

O alias **nao** nasce confirmado: entra com `confidence=low` e so e promovido
apos dois usos bem-sucedidos sem correcao (D-013). Isso evita que um toque
errado envenene a resolucao daquele tenant para sempre.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from orbi.db.models import EntityAlias
from orbi.resolution.canonical import normalize

PROMOTION_HITS = 2
MAX_ALIAS_LENGTH = 160


@dataclass(frozen=True)
class AliasOutcome:
    alias: str
    confidence: str
    hits: int
    created: bool


def _find(
    session: Session, tenant_id: uuid.UUID | str, entity_type: str, alias: str
) -> EntityAlias | None:
    return session.scalars(
        select(EntityAlias).where(
            EntityAlias.tenant_id == uuid.UUID(str(tenant_id)),
            EntityAlias.entity_type == entity_type,
            EntityAlias.alias == alias,
        )
    ).first()


def record_choice(
    session: Session,
    tenant_id: uuid.UUID | str,
    entity_type: str,
    term: str,
    erp_entity_id: str,
    user_id: uuid.UUID | str | None = None,
) -> AliasOutcome | None:
    """Grava a escolha do usuario na desambiguacao.

    Devolve `None` quando o termo nao vale como alias (vazio ou longo demais).
    """
    alias = normalize(term)
    if not alias or len(alias) > MAX_ALIAS_LENGTH:
        return None

    existing = _find(session, tenant_id, entity_type, alias)
    if existing is None:
        session.add(
            EntityAlias(
                tenant_id=uuid.UUID(str(tenant_id)),
                entity_type=entity_type,
                alias=alias,
                erp_entity_id=str(erp_entity_id),
                confidence="low",
                hits=1,
                created_by_user_id=uuid.UUID(str(user_id)) if user_id else None,
                last_used_at=datetime.now(UTC),
            )
        )
        session.flush()
        return AliasOutcome(alias=alias, confidence="low", hits=1, created=True)

    if existing.erp_entity_id != str(erp_entity_id):
        # A pessoa apontou outra entidade: o alias anterior estava errado.
        existing.erp_entity_id = str(erp_entity_id)
        existing.confidence = "low"
        existing.hits = 1
        existing.corrections += 1
    else:
        existing.hits += 1
        if existing.hits >= PROMOTION_HITS and existing.corrections == 0:
            existing.confidence = "confirmed"
    existing.last_used_at = datetime.now(UTC)
    session.flush()
    return AliasOutcome(
        alias=alias, confidence=existing.confidence, hits=existing.hits, created=False
    )


def record_successful_use(
    session: Session, tenant_id: uuid.UUID | str, entity_type: str, term: str
) -> AliasOutcome | None:
    """Uso limpo de um alias existente: aproxima da promocao."""
    alias = normalize(term)
    existing = _find(session, tenant_id, entity_type, alias)
    if existing is None:
        return None

    existing.hits += 1
    existing.last_used_at = datetime.now(UTC)
    if existing.hits >= PROMOTION_HITS and existing.corrections == 0:
        existing.confidence = "confirmed"
    session.flush()
    return AliasOutcome(
        alias=alias, confidence=existing.confidence, hits=existing.hits, created=False
    )


def record_correction(
    session: Session, tenant_id: uuid.UUID | str, entity_type: str, term: str
) -> None:
    """O usuario disse "nao e esse": o alias perde a confianca ou some.

    Um alias que ja errou nao volta a `confirmed` sozinho — precisa de escolha
    explicita de novo.
    """
    alias = normalize(term)
    existing = _find(session, tenant_id, entity_type, alias)
    if existing is None:
        return

    existing.corrections += 1
    existing.confidence = "low"
    existing.hits = 0
    if existing.corrections >= 2:
        session.delete(existing)
    session.flush()
