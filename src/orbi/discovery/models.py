"""Estruturas do Knowledge Model (ORBI.md secao 9).

A saida do Discovery e **dado versionado, nao codigo**. O Runtime consome o
modelo ativo e nao sabe que o CrewAI existe.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]

REVIEW_THRESHOLD: Confidence = "medium"
"""A revisao humana olha apenas o que esta abaixo do limiar — minutos, nao horas."""


class DiscoveryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldNote(DiscoveryDTO):
    name: str
    meaning: str
    unit: str | None = None
    confidence: Confidence = "medium"
    evidence: str = ""

    @property
    def needs_review(self) -> bool:
        return self.confidence == "low"


class EntityNote(DiscoveryDTO):
    """Uma entidade do ERP, com o que se sabe sobre ela."""

    name: str
    erp_model: str
    domain: Literal["stock", "commercial", "finance", "other"]
    supports: list[str] = Field(default_factory=list)
    fields: list[FieldNote] = Field(default_factory=list)


class SurfaceMap(DiscoveryDTO):
    """Primeira passada: o que existe, sem profundidade semantica.

    Barato de rodar e vale por tres coisas: vira o roadmap do que da para lancar
    em seguida, vira argumento de venda e evita comecar do zero na proxima
    rodada.
    """

    adapter: str
    erp_version: str | None = None
    entities: list[EntityNote] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    unsupported_tools: list[str] = Field(default_factory=list)


class DeepModel(DiscoveryDTO):
    """Segunda passada: so estoque, comercial e financeiro."""

    stock_basis: Literal["available", "physical", "unknown"] = "unknown"
    multi_location: bool = False
    customer_pricing: bool = False
    quantity_pricing: bool = False
    incremental_catalog: bool = False
    currency: str = "BRL"
    notes: list[str] = Field(default_factory=list)


class SeedQuestion(DiscoveryDTO):
    """Pergunta realista com tool e argumentos esperados.

    As camadas L1 e L2 do eval nascem junto com o modelo.
    """

    question: str
    expected_tool: str
    expected_args: dict[str, Any] = Field(default_factory=dict)
    role: str = "sales_rep"


class AbbreviationCandidate(DiscoveryDTO):
    """Abreviacao vista no catalogo do cliente que o dicionario base nao cobre."""

    short: str
    occurrences: int
    examples: list[str] = Field(default_factory=list)
    suggestion: str | None = None
    confidence: Confidence = "low"


class DiscoveryOutput(DiscoveryDTO):
    surface_map: SurfaceMap
    deep_model: DeepModel
    field_confidence: dict[str, Any] = Field(default_factory=dict)
    seed_questions: list[SeedQuestion] = Field(default_factory=list)
    abbreviation_candidates: list[AbbreviationCandidate] = Field(default_factory=list)
    tokens_used: int = 0

    def low_confidence_fields(self) -> list[str]:
        return [
            f"{entity.erp_model}.{note.name}"
            for entity in self.surface_map.entities
            for note in entity.fields
            if note.needs_review
        ]
