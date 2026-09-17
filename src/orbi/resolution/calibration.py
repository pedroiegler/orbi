"""Calibracao dos limiares por tenant (ORBI.md secao 6.9).

Limiares nao sao fixos no codigo. No onboarding, o sistema gera um conjunto de
teste sintetico a partir do **catalogo real do cliente**: para cada produto
amostrado, cria variacoes realistas — nome truncado, abreviado, com erro de
digitacao, sem a medida. Como a resposta certa e conhecida por construcao, da
para varrer os pares de limiar sem dado rotulado a mao.

O criterio de otimizacao e assimetrico, e essa e a decisao central:

> Responder a entidade errada com confianca e um erro grave.
> Perguntar "qual desses?" e um custo pequeno.

Maximiza-se o acerto sujeito a **erro silencioso ≤ 1%**.
"""

from __future__ import annotations

import random
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orbi.db.models import CatalogItem, TenantSettings
from orbi.resolution.embeddings import EmbeddingPort
from orbi.resolution.resolver import EntityResolver, Thresholds

MAX_SILENT_ERROR_RATE = 0.01
DEFAULT_SAMPLE = 60
RECALIBRATION_CHANGE_RATIO = Decimal("0.20")
"""Catalogo que muda mais de 20% pede recalibracao automatica."""

TOP1_GRID = [round(0.50 + step * 0.02, 2) for step in range(25)]
GAP_GRID = [round(step * 0.01, 2) for step in range(21)]

_MEASURE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(mm|cm|m|kg|g|l|ml|w|v)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SyntheticCase:
    term: str
    expected_id: str
    variation: str


@dataclass
class CalibrationResult:
    top1: float
    gap: float
    accuracy: float
    silent_error_rate: float
    ambiguous_rate: float
    not_found_rate: float
    cases: int
    sample: int
    by_variation: dict[str, float] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"limiar top1 {self.top1:.2f} · gap {self.gap:.2f} · "
            f"acerto {self.accuracy:.1%} · erro silencioso {self.silent_error_rate:.2%} · "
            f"ambiguidade {self.ambiguous_rate:.1%} ({self.cases} casos)"
        )


def build_synthetic_set(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    entity_type: str = "product",
    sample: int = DEFAULT_SAMPLE,
    seed: int = 20260824,
) -> list[SyntheticCase]:
    """Gera variacoes realistas a partir do catalogo real do cliente."""
    rows = session.scalars(
        select(CatalogItem)
        .where(
            CatalogItem.tenant_id == tenant_id,
            CatalogItem.entity_type == entity_type,
            CatalogItem.active.is_(True),
            CatalogItem.flagged.is_(False),
        )
        .order_by(CatalogItem.erp_entity_id)
    ).all()
    if not rows:
        return []

    rng = random.Random(seed)
    chosen = rows if len(rows) <= sample else rng.sample(rows, sample)

    cases: list[SyntheticCase] = []
    for row in chosen:
        canonical = row.canonical_name
        for variation, term in _variations(canonical, rng).items():
            if term and term != canonical:
                cases.append(
                    SyntheticCase(term=term, expected_id=row.erp_entity_id, variation=variation)
                )
        cases.append(
            SyntheticCase(term=canonical, expected_id=row.erp_entity_id, variation="exato")
        )
    return cases


def _variations(canonical: str, rng: random.Random) -> dict[str, str]:
    tokens = canonical.split()
    return {
        "truncado": " ".join(tokens[: max(1, len(tokens) // 2)]),
        "sem_medida": _MEASURE_RE.sub("", canonical).strip(),
        "abreviado": " ".join(token[:4] for token in tokens[:3]),
        "com_erro": _typo(canonical, rng),
    }


def _typo(text: str, rng: random.Random) -> str:
    """Troca duas letras de lugar — o erro de digitacao mais comum."""
    letters = [index for index, ch in enumerate(text) if ch.isalpha()]
    if len(letters) < 4:
        return text
    position = rng.choice(letters[1:-1])
    chars = list(text)
    chars[position], chars[position - 1] = chars[position - 1], chars[position]
    return "".join(chars)


def calibrate(
    session: Session,
    tenant_id: uuid.UUID,
    embedder: EmbeddingPort,
    *,
    entity_type: str = "product",
    sample: int = DEFAULT_SAMPLE,
    max_silent_error: float = MAX_SILENT_ERROR_RATE,
) -> CalibrationResult | None:
    """Varre os pares de limiar e devolve o melhor sob o teto de erro silencioso."""
    cases = build_synthetic_set(session, tenant_id, entity_type=entity_type, sample=sample)
    if not cases:
        return None

    resolver = EntityResolver(session, str(tenant_id), embedder)
    scored: list[tuple[SyntheticCase, float, float, bool]] = []
    for case in cases:
        ranked = resolver.candidates(case.term, entity_type)
        if not ranked:
            scored.append((case, 0.0, 0.0, False))
            continue
        top = ranked[0]
        gap = top.score - (ranked[1].score if len(ranked) > 1 else 0.0)
        scored.append((case, top.score, gap, top.erp_entity_id == case.expected_id))

    best: CalibrationResult | None = None
    for top1 in TOP1_GRID:
        for gap_threshold in GAP_GRID:
            result = _evaluate(scored, top1, gap_threshold, len(cases), sample)
            if result.silent_error_rate > max_silent_error:
                continue
            if best is None or (result.accuracy, -result.ambiguous_rate) > (
                best.accuracy,
                -best.ambiguous_rate,
            ):
                best = result

    # Se nenhum par respeitou o teto, o mais conservador ainda e a resposta certa:
    # perguntar sempre e melhor que responder errado com confianca.
    return best or _evaluate(scored, TOP1_GRID[-1], GAP_GRID[-1], len(cases), sample)


def _evaluate(
    scored: list[tuple[SyntheticCase, float, float, bool]],
    top1: float,
    gap: float,
    cases: int,
    sample: int,
) -> CalibrationResult:
    correct = silent = ambiguous = not_found = 0
    per_variation: dict[str, list[int]] = {}

    for case, score, candidate_gap, is_right in scored:
        bucket = per_variation.setdefault(case.variation, [0, 0])
        bucket[1] += 1
        if score <= 0:
            not_found += 1
            continue
        if score >= top1 and candidate_gap >= gap:
            if is_right:
                correct += 1
                bucket[0] += 1
            else:
                silent += 1
        else:
            ambiguous += 1

    total = max(1, cases)
    return CalibrationResult(
        top1=top1,
        gap=gap,
        accuracy=correct / total,
        silent_error_rate=silent / total,
        ambiguous_rate=ambiguous / total,
        not_found_rate=not_found / total,
        cases=cases,
        sample=sample,
        by_variation={
            variation: (hits / max(1, count)) for variation, (hits, count) in per_variation.items()
        },
    )


def apply(
    session: Session, tenant_id: uuid.UUID, result: CalibrationResult, embedder: EmbeddingPort
) -> TenantSettings:
    """Grava os limiares calibrados em `tenant_settings`."""
    from datetime import UTC, datetime

    settings = session.get(TenantSettings, tenant_id)
    if settings is None:
        settings = TenantSettings(tenant_id=tenant_id)
        session.add(settings)

    catalog_size = session.scalar(
        select(func.count())
        .select_from(CatalogItem)
        .where(CatalogItem.tenant_id == tenant_id, CatalogItem.active.is_(True))
    )

    settings.resolution_top1_threshold = Decimal(str(result.top1))
    settings.resolution_gap_threshold = Decimal(str(result.gap))
    settings.calibrated_at = datetime.now(UTC)
    settings.calibration_catalog_size = int(catalog_size or 0)
    settings.calibration_silent_error_rate = Decimal(str(round(result.silent_error_rate, 4)))
    settings.embedding_model_version = embedder.model_version
    session.flush()
    return settings


def needs_recalibration(session: Session, tenant_id: uuid.UUID) -> bool:
    """Catalogo que mudou mais de 20% desde a calibracao pede uma nova."""
    settings = session.get(TenantSettings, tenant_id)
    if settings is None or settings.calibrated_at is None:
        return True
    if settings.calibration_catalog_size == 0:
        return True

    current = (
        session.scalar(
            select(func.count())
            .select_from(CatalogItem)
            .where(CatalogItem.tenant_id == tenant_id, CatalogItem.active.is_(True))
        )
        or 0
    )
    change = abs(current - settings.calibration_catalog_size) / settings.calibration_catalog_size
    return Decimal(str(change)) > RECALIBRATION_CHANGE_RATIO


def current_thresholds(session: Session, tenant_id: uuid.UUID) -> Thresholds:
    settings = session.get(TenantSettings, tenant_id)
    return Thresholds.from_settings_row(settings) if settings else Thresholds()
