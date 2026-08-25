"""Execucao do Discovery — offline, versionada, com aprovacao humana.

```
Analyst → Validator → Knowledge Model v.N → diff review humano → activate
```

Linhas imutaveis + ponteiro `active_knowledge_model_id` na conexao de ERP:
rollback e um UPDATE no ponteiro.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from orbi.db.models import CatalogAbbreviation, ErpConnection, KnowledgeModel
from orbi.db.session import tenant_session
from orbi.discovery.analyst import analyze
from orbi.discovery.models import DiscoveryOutput
from orbi.discovery.validator import ValidationReport, diff, validate
from orbi.erp import connection as erp_connection


@dataclass
class DiscoveryResult:
    version: int
    output: DiscoveryOutput
    report: ValidationReport
    changes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.report.summary()} · "
            f"{len(self.output.seed_questions)} seed questions · "
            f"{len(self.output.abbreviation_candidates)} candidatos a abreviacao"
        )


def run_discovery(tenant_id: uuid.UUID, *, adapter_name: str | None = None) -> DiscoveryResult:
    """Gera uma versao candidata. Nada entra em producao sem ativacao."""
    with tenant_session(tenant_id) as session:
        tenant_erp = erp_connection.build_for_tenant(session, tenant_id)
        capabilities = tenant_erp.capabilities
        output = analyze(session, tenant_id, capabilities)
        report = validate(output)

        previous = session.scalars(
            select(KnowledgeModel)
            .where(
                KnowledgeModel.tenant_id == tenant_id,
                KnowledgeModel.adapter == (adapter_name or tenant_erp.adapter_name),
                KnowledgeModel.status == "active",
            )
            .order_by(KnowledgeModel.version.desc())
        ).first()

        previous_payload: dict[str, Any] | None = None
        if previous is not None:
            previous_payload = {
                "surface_map": previous.surface_map,
                "deep_model": previous.deep_model,
                "field_confidence": previous.field_confidence,
            }
        changes = diff(previous_payload, output)

        next_version = (
            session.scalar(
                select(func.coalesce(func.max(KnowledgeModel.version), 0)).where(
                    KnowledgeModel.tenant_id == tenant_id,
                    KnowledgeModel.adapter == (adapter_name or tenant_erp.adapter_name),
                )
            )
            or 0
        ) + 1

        session.add(
            KnowledgeModel(
                tenant_id=tenant_id,
                adapter=adapter_name or tenant_erp.adapter_name,
                version=next_version,
                status="candidate",
                surface_map=output.surface_map.model_dump(),
                deep_model=output.deep_model.model_dump(),
                field_confidence=output.field_confidence,
                seed_questions=[q.model_dump() for q in output.seed_questions],
                abbreviations={
                    candidate.short: candidate.suggestion or ""
                    for candidate in output.abbreviation_candidates
                },
                tokens_used=output.tokens_used,
            )
        )

    return DiscoveryResult(version=next_version, output=output, report=report, changes=changes)


def activate_version(tenant_id: uuid.UUID, version: int) -> None:
    """Ativa uma versao candidata. A anterior vira `archived`, nunca some."""
    with tenant_session(tenant_id) as session:
        candidate = session.scalars(
            select(KnowledgeModel).where(
                KnowledgeModel.tenant_id == tenant_id, KnowledgeModel.version == version
            )
        ).first()
        if candidate is None:
            raise ValueError(f"versao {version} nao encontrada")

        for row in session.scalars(
            select(KnowledgeModel).where(
                KnowledgeModel.tenant_id == tenant_id,
                KnowledgeModel.adapter == candidate.adapter,
                KnowledgeModel.status == "active",
            )
        ).all():
            row.status = "archived"

        candidate.status = "active"
        candidate.activated_at = datetime.now(UTC)

        connection = session.scalars(
            select(ErpConnection).where(
                ErpConnection.tenant_id == tenant_id, ErpConnection.active.is_(True)
            )
        ).first()
        if connection is not None:
            connection.active_knowledge_model_id = candidate.id

        # As abreviacoes aprovadas alimentam o CanonicalNameBuilder do tenant.
        for short, expanded in (candidate.abbreviations or {}).items():
            if not expanded:
                continue
            existing = session.scalars(
                select(CatalogAbbreviation).where(
                    CatalogAbbreviation.tenant_id == tenant_id,
                    CatalogAbbreviation.short == short,
                )
            ).first()
            if existing is None:
                session.add(
                    CatalogAbbreviation(
                        tenant_id=tenant_id,
                        short=short,
                        expanded=expanded,
                        source="discovery",
                    )
                )
            else:
                existing.expanded = expanded


def rollback(tenant_id: uuid.UUID, version: int) -> None:
    """Rollback e um UPDATE no ponteiro — nada e reescrito."""
    activate_version(tenant_id, version)


def describe_active(tenant_id: uuid.UUID) -> dict[str, Any]:
    with tenant_session(tenant_id) as session:
        active = session.scalars(
            select(KnowledgeModel)
            .where(KnowledgeModel.tenant_id == tenant_id, KnowledgeModel.status == "active")
            .order_by(KnowledgeModel.version.desc())
        ).first()
        if active is None:
            return {"active": None}
        return {
            "version": active.version,
            "adapter": active.adapter,
            "activated_at": active.activated_at.isoformat() if active.activated_at else None,
            "surface_map": active.surface_map,
            "deep_model": active.deep_model,
            "seed_questions": len(active.seed_questions or []),
        }


def seed_questions_of(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    """As seed questions do modelo ativo alimentam os evals L1 e L2."""
    with tenant_session(tenant_id) as session:
        active = session.scalars(
            select(KnowledgeModel)
            .where(KnowledgeModel.tenant_id == tenant_id, KnowledgeModel.status == "active")
            .order_by(KnowledgeModel.version.desc())
        ).first()
        return list(active.seed_questions or []) if active else []
