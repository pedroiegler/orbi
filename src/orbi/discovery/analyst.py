"""Analyst do Discovery (ORBI.md secao 9).

Duas passadas: mapa raso do que o ERP oferece e modelo profundo dos tres
dominios do MVP. Alem disso, duas entregas que valem por si:

- **seed questions**: 30 a 50 perguntas realistas geradas a partir do catalogo
  real, com tool e argumentos esperados — as camadas L1 e L2 do eval nascem
  junto com o modelo;
- **candidatos a abreviacao**: tokens curtos e frequentes que o dicionario base
  nao cobre, que alimentam o `CanonicalNameBuilder`.

O CrewAI e opcional (D-016): quando instalado, enriquece a semantica dos campos;
quando ausente, o Analyst continua entregando o que da para deduzir do contrato
do adapter e do catalogo do cliente.
"""

from __future__ import annotations

import random
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from orbi.db.models import CatalogItem
from orbi.discovery.models import (
    AbbreviationCandidate,
    DeepModel,
    DiscoveryOutput,
    EntityNote,
    FieldNote,
    SeedQuestion,
    SurfaceMap,
)
from orbi.erp.port import Capabilities
from orbi.resolution.canonical import BASE_ABBREVIATIONS, UNIT_ALIASES
from orbi.tools.registry import tool_names

MIN_ABBREVIATION_OCCURRENCES = 3
MAX_SEED_QUESTIONS = 50
SEED_SAMPLE = 12

_STOCK_TEMPLATES = (
    "quanto tem de {term}?",
    "tem {term} no estoque?",
    "qual o saldo de {term}?",
)
_PRICE_TEMPLATES = (
    "qual o preco do {term}?",
    "quanto custa {term}?",
)
_INVOICE_TEMPLATES = (
    "{term} tem titulos em aberto?",
    "quanto {term} esta devendo?",
)
_ORDER_TEMPLATES = (
    "qual o ultimo pedido da {term}?",
    "quando {term} comprou pela ultima vez?",
)


def analyze(
    session: Session,
    tenant_id: uuid.UUID,
    capabilities: Capabilities,
    *,
    seed: int = 20260824,
) -> DiscoveryOutput:
    """Roda as duas passadas contra o adapter e o catalogo ja sincronizado."""
    surface = _surface_map(capabilities)
    deep = _deep_model(capabilities)

    products = _sample(session, tenant_id, "product", seed)
    customers = _sample(session, tenant_id, "customer", seed)

    return DiscoveryOutput(
        surface_map=surface,
        deep_model=deep,
        field_confidence=_field_confidence(surface),
        seed_questions=_seed_questions(products, customers, capabilities),
        abbreviation_candidates=_abbreviation_candidates(session, tenant_id),
    )


def _surface_map(capabilities: Capabilities) -> SurfaceMap:
    supported = set(capabilities.supported_tools)
    entities: list[EntityNote] = []

    if "check_stock" in supported:
        entities.append(
            EntityNote(
                name="produto",
                erp_model="product",
                domain="stock",
                supports=["get_stock", "iter_catalog"],
                fields=[
                    FieldNote(
                        name="physical",
                        meaning="quantidade fisica em maos",
                        unit="unidade do produto",
                        confidence="high",
                        evidence="contrato do ErpAdapter",
                    ),
                    FieldNote(
                        name="reserved",
                        meaning="quantidade comprometida com pedidos",
                        confidence="high" if capabilities.supports_reservations else "low",
                        evidence=(
                            "capabilities().supports_reservations"
                            if capabilities.supports_reservations
                            else "ERP nao declara reservas: a resposta usa estoque fisico"
                        ),
                    ),
                    FieldNote(
                        name="locations",
                        meaning="quebra por deposito",
                        confidence="high" if capabilities.supports_multi_location else "low",
                        evidence="capabilities().supports_multi_location",
                    ),
                ],
            )
        )

    if "check_price" in supported:
        entities.append(
            EntityNote(
                name="preco",
                erp_model="price",
                domain="commercial",
                supports=["get_price"],
                fields=[
                    FieldNote(
                        name="unit_price",
                        meaning="preco de venda unitario",
                        confidence="high",
                        evidence="contrato do ErpAdapter",
                    ),
                    FieldNote(
                        name="unit_cost",
                        meaning="custo — filtrado pela Field Policy",
                        confidence="medium",
                        evidence="custo pode ser medio ou de reposicao conforme o ERP",
                    ),
                    FieldNote(
                        name="price_list",
                        meaning="tabela de preco do cliente",
                        confidence="high" if capabilities.supports_customer_pricing else "low",
                        evidence="capabilities().supports_customer_pricing",
                    ),
                ],
            )
        )

    if "list_open_invoices" in supported:
        entities.append(
            EntityNote(
                name="titulo",
                erp_model="invoice",
                domain="finance",
                supports=["list_open_invoices"],
                fields=[
                    FieldNote(
                        name="open_amount",
                        meaning="saldo em aberto do titulo",
                        confidence="high",
                        evidence="contrato do ErpAdapter",
                    ),
                    FieldNote(
                        name="due_date",
                        meaning="vencimento",
                        confidence="high",
                        evidence="contrato do ErpAdapter",
                    ),
                ],
            )
        )

    if "get_last_order" in supported:
        entities.append(
            EntityNote(
                name="pedido",
                erp_model="order",
                domain="commercial",
                supports=["get_last_order"],
                fields=[
                    FieldNote(
                        name="status",
                        meaning="situacao do pedido no ERP",
                        confidence="low",
                        evidence="cada ERP nomeia os estados de um jeito: revisar com o cliente",
                    ),
                ],
            )
        )

    return SurfaceMap(
        adapter=capabilities.adapter,
        erp_version=capabilities.erp_version,
        entities=entities,
        operations=sorted(supported),
        unsupported_tools=sorted(set(tool_names()) - supported),
    )


def _deep_model(capabilities: Capabilities) -> DeepModel:
    notes: list[str] = []
    if not capabilities.supports_reservations:
        notes.append(
            "ERP nao informa reservas: toda resposta de estoque declara que o numero e fisico"
        )
    if not capabilities.supports_multi_location:
        notes.append("ERP sem multi-deposito: a resposta traz apenas o total")
    if not capabilities.supports_customer_pricing:
        notes.append("sem tabela por cliente: o preco respondido e o de lista")

    return DeepModel(
        stock_basis="available" if capabilities.supports_reservations else "physical",
        multi_location=capabilities.supports_multi_location,
        customer_pricing=capabilities.supports_customer_pricing,
        quantity_pricing=capabilities.supports_quantity_pricing,
        incremental_catalog=capabilities.supports_incremental_catalog,
        currency=capabilities.currency,
        notes=notes,
    )


def _field_confidence(surface: SurfaceMap) -> dict[str, dict[str, str]]:
    """Confianca campo a campo — o Validator nao da nota geral."""
    return {
        f"{entity.erp_model}.{note.name}": {
            "confidence": note.confidence,
            "meaning": note.meaning,
            "evidence": note.evidence,
        }
        for entity in surface.entities
        for note in entity.fields
    }


def _sample(
    session: Session, tenant_id: uuid.UUID, entity_type: str, seed: int
) -> list[CatalogItem]:
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
    if len(rows) <= SEED_SAMPLE:
        return list(rows)
    return random.Random(seed).sample(rows, SEED_SAMPLE)


def _seed_questions(
    products: list[CatalogItem], customers: list[CatalogItem], capabilities: Capabilities
) -> list[SeedQuestion]:
    """Perguntas realistas com a resposta certa conhecida por construcao."""
    questions: list[SeedQuestion] = []
    supported = set(capabilities.supported_tools)

    for item in products:
        term = _short_term(item.canonical_name)
        if "check_stock" in supported:
            for template in _STOCK_TEMPLATES[:2]:
                questions.append(
                    SeedQuestion(
                        question=template.format(term=term),
                        expected_tool="check_stock",
                        expected_args={"product_term": term},
                    )
                )
        if "check_price" in supported:
            questions.append(
                SeedQuestion(
                    question=_PRICE_TEMPLATES[0].format(term=term),
                    expected_tool="check_price",
                    expected_args={"product_term": term},
                )
            )

    for item in customers:
        term = _short_term(item.canonical_name, words=3)
        if "list_open_invoices" in supported:
            questions.append(
                SeedQuestion(
                    question=_INVOICE_TEMPLATES[0].format(term=term),
                    expected_tool="list_open_invoices",
                    expected_args={"customer_term": term},
                    role="finance",
                )
            )
        if "get_last_order" in supported:
            questions.append(
                SeedQuestion(
                    question=_ORDER_TEMPLATES[0].format(term=term),
                    expected_tool="get_last_order",
                    expected_args={"customer_term": term},
                )
            )

    return questions[:MAX_SEED_QUESTIONS]


def _short_term(canonical_name: str, words: int = 4) -> str:
    return " ".join(canonical_name.split()[:words])


def _abbreviation_candidates(session: Session, tenant_id: uuid.UUID) -> list[AbbreviationCandidate]:
    """Tokens curtos e frequentes que o dicionario base nao cobre.

    Nao se inventa a expansao: o candidato entra com confianca baixa e vai para a
    revisao humana, que e onde essa decisao pertence.
    """
    rows = session.scalars(
        select(CatalogItem).where(
            CatalogItem.tenant_id == tenant_id,
            CatalogItem.entity_type == "product",
            CatalogItem.active.is_(True),
        )
    ).all()

    counter: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    known = set(BASE_ABBREVIATIONS) | set(BASE_ABBREVIATIONS.values()) | set(UNIT_ALIASES)

    for row in rows:
        for token in row.canonical_name.split():
            if len(token) > 4 or token.isdigit() or token in known:
                continue
            counter[token] += 1
            examples.setdefault(token, [])
            if len(examples[token]) < 3:
                examples[token].append(row.name)

    return [
        AbbreviationCandidate(short=token, occurrences=count, examples=examples[token])
        for token, count in counter.most_common(20)
        if count >= MIN_ABBREVIATION_OCCURRENCES
    ]
