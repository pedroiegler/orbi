"""Sincronizacao do catalogo (ORBI.md secao 7).

O Orbi nao copia o ERP: mantem um indice de resolucao. As quatro regras que este
modulo implementa e que costumam ser esquecidas:

- **Re-embedding so quando `name_hash` muda.** Sem isso, paga-se embedding do
  catalogo inteiro toda noite sem motivo.
- **Produto ausente vira `active=false`, nunca DELETE** — a auditoria referencia
  esses ids.
- **Sync que alteraria mais de 30% do catalogo para e alerta** em vez de aplicar:
  quase sempre significa credencial errada ou API devolvendo pagina vazia.
- **Job idempotente e retomavel**, com cursor em `catalog_sync_runs`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from orbi.catalog.firewall import inspect
from orbi.db.base import rows_affected
from orbi.db.models import CatalogAbbreviation, CatalogItem, CatalogSyncRun
from orbi.erp.port import CatalogItem as ErpCatalogItem
from orbi.resolution.canonical import CanonicalNameBuilder
from orbi.resolution.embeddings import EmbeddingPort

ABORT_CHANGE_RATIO = Decimal("0.30")
"""Acima disso o sync para e alerta em vez de aplicar."""

EMBEDDING_BATCH = 64


class SyncAborted(Exception):
    """A variacao passou do limite: quase sempre credencial ou API errada."""

    def __init__(self, change_ratio: Decimal, seen: int, total: int) -> None:
        super().__init__(
            f"sync abortado: mudaria {change_ratio:.0%} do catalogo "
            f"({seen} itens vistos, {total} no indice)"
        )
        self.change_ratio = change_ratio


@dataclass
class SyncReport:
    mode: str
    items_seen: int = 0
    items_created: int = 0
    items_updated: int = 0
    items_deactivated: int = 0
    embeddings_computed: int = 0
    flagged: int = 0
    change_ratio: Decimal = Decimal("0")
    aborted: bool = False
    error: str | None = None
    by_type: dict[str, int] = field(default_factory=dict)

    @property
    def items_upserted(self) -> int:
        return self.items_created + self.items_updated

    def summary(self) -> str:
        return (
            f"{self.items_seen} itens lidos · {self.items_created} novos · "
            f"{self.items_updated} atualizados · {self.items_deactivated} desativados · "
            f"{self.embeddings_computed} embeddings · {self.flagged} sinalizados"
        )


def name_hash(canonical_name: str) -> str:
    return hashlib.sha256(canonical_name.encode("utf-8")).hexdigest()[:32]


class CatalogSynchronizer:
    """Aplica um lote do ERP sobre o indice de resolucao do tenant."""

    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID | str,
        embedder: EmbeddingPort,
        *,
        builder: CanonicalNameBuilder | None = None,
        abort_ratio: Decimal = ABORT_CHANGE_RATIO,
    ) -> None:
        self._session = session
        self._tenant_id = uuid.UUID(str(tenant_id))
        self._embedder = embedder
        self._builder = builder or CanonicalNameBuilder(self._tenant_abbreviations())
        self._abort_ratio = abort_ratio

    def _tenant_abbreviations(self) -> dict[str, str]:
        rows = self._session.scalars(
            select(CatalogAbbreviation).where(CatalogAbbreviation.tenant_id == self._tenant_id)
        ).all()
        return {row.short: row.expanded for row in rows}

    def run(
        self,
        items: Iterable[ErpCatalogItem],
        *,
        mode: str = "full",
        deactivate_missing: bool = True,
        on_alert: Callable[[str], None] | None = None,
    ) -> SyncReport:
        """Executa o sync e registra a corrida em `catalog_sync_runs`."""
        run = CatalogSyncRun(tenant_id=self._tenant_id, mode=mode, status="running")
        self._session.add(run)
        self._session.flush()

        report = SyncReport(mode=mode)
        try:
            # SAVEPOINT: um sync abortado desfaz as alteracoes do catalogo mas
            # preserva o registro da corrida — sem ele, o abort seria invisivel.
            with self._session.begin_nested():
                self._apply(items, report, deactivate_missing=deactivate_missing, mode=mode)
        except SyncAborted as exc:
            report.aborted = True
            report.error = str(exc)
            report.change_ratio = exc.change_ratio
            run.status = "aborted"
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            run.change_ratio = exc.change_ratio
            self._session.flush()
            if on_alert is not None:
                on_alert(str(exc))
            return report
        except Exception as exc:  # falha de ERP no meio do sync
            run.status = "failed"
            run.error = str(exc)[:500]
            run.finished_at = datetime.now(UTC)
            self._session.flush()
            raise

        run.status = "completed"
        run.items_seen = report.items_seen
        run.items_upserted = report.items_upserted
        run.items_deactivated = report.items_deactivated
        run.embeddings_computed = report.embeddings_computed
        run.change_ratio = report.change_ratio
        run.finished_at = datetime.now(UTC)
        self._session.flush()
        return report

    # --- aplicacao -------------------------------------------------------

    def _apply(
        self,
        items: Iterable[ErpCatalogItem],
        report: SyncReport,
        *,
        deactivate_missing: bool,
        mode: str,
    ) -> None:
        existing = {
            (row.entity_type, row.erp_entity_id): row
            for row in self._session.scalars(
                select(CatalogItem).where(CatalogItem.tenant_id == self._tenant_id)
            ).all()
        }
        total_before = len(existing)
        seen_keys: set[tuple[str, str]] = set()
        pending_embeddings: list[CatalogItem] = []
        changed = 0

        for item in items:
            report.items_seen += 1
            report.by_type[item.entity_type] = report.by_type.get(item.entity_type, 0) + 1
            key = (item.entity_type, item.erp_entity_id)
            seen_keys.add(key)

            verdict = inspect(item.name)
            canonical = self._builder.build(item.name, item.code)
            digest = name_hash(canonical)
            row = existing.get(key)

            if row is None:
                row = CatalogItem(
                    tenant_id=self._tenant_id,
                    erp_entity_id=item.erp_entity_id,
                    entity_type=item.entity_type,
                    name=item.name,
                    code=item.code,
                    barcode=item.barcode,
                    canonical_name=canonical,
                    name_hash=digest,
                    active=item.active,
                    flagged=verdict.flagged,
                    flagged_reason=verdict.reason,
                    erp_updated_at=item.updated_at,
                    model_version=self._embedder.model_version,
                )
                self._session.add(row)
                pending_embeddings.append(row)
                report.items_created += 1
                changed += 1
            else:
                needs_embedding = (
                    row.name_hash != digest
                    or row.embedding is None
                    or row.model_version != self._embedder.model_version
                )
                mutated = (
                    row.name != item.name
                    or row.code != item.code
                    or row.barcode != item.barcode
                    or row.active != item.active
                    or needs_embedding
                )
                row.name = item.name
                row.code = item.code
                row.barcode = item.barcode
                row.canonical_name = canonical
                row.name_hash = digest
                row.active = item.active
                row.flagged = verdict.flagged
                row.flagged_reason = verdict.reason
                row.erp_updated_at = item.updated_at
                row.updated_at = datetime.now(UTC)
                if needs_embedding:
                    # Re-embedding so quando o nome muda: e o que evita pagar
                    # embedding do catalogo inteiro toda noite.
                    pending_embeddings.append(row)
                if mutated:
                    report.items_updated += 1
                    changed += 1

            if verdict.flagged:
                report.flagged += 1

        missing = [
            row
            for key, row in existing.items()
            if key not in seen_keys and row.active
        ]
        if deactivate_missing and mode == "full":
            changed += len(missing)

        report.change_ratio = (
            Decimal(changed) / Decimal(total_before) if total_before else Decimal("0")
        )
        if total_before > 0 and report.change_ratio > self._abort_ratio:
            raise SyncAborted(report.change_ratio, report.items_seen, total_before)

        if deactivate_missing and mode == "full":
            for row in missing:
                # Nunca DELETE: a auditoria referencia esses ids.
                row.active = False
                report.items_deactivated += 1

        self._embed(pending_embeddings, report)
        self._session.flush()

    def _embed(self, rows: list[CatalogItem], report: SyncReport) -> None:
        for start in range(0, len(rows), EMBEDDING_BATCH):
            batch = rows[start : start + EMBEDDING_BATCH]
            vectors = self._embedder.embed([row.canonical_name for row in batch])
            for row, vector in zip(batch, vectors, strict=True):
                row.embedding = vector
                row.model_version = self._embedder.model_version
            report.embeddings_computed += len(batch)


def deactivate_all(session: Session, tenant_id: uuid.UUID | str) -> int:
    """Usado ao desligar um tenant: o indice para de responder, o historico fica."""
    result = session.execute(
        update(CatalogItem)
        .where(CatalogItem.tenant_id == uuid.UUID(str(tenant_id)))
        .values(active=False)
    )
    return rows_affected(result)
