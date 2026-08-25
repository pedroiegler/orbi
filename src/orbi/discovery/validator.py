"""Validator do Discovery (ORBI.md secao 9).

O Validator **nao da nota geral**: ele marca a confianca campo a campo e produz
a lista do que precisa de olho humano. A revisao olha apenas o que ficou abaixo
do limiar — minutos, nao horas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orbi.discovery.models import DiscoveryOutput


@dataclass
class ValidationReport:
    total_fields: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    review_items: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)

    @property
    def review_ratio(self) -> float:
        return len(self.review_items) / self.total_fields if self.total_fields else 0.0

    @property
    def approved(self) -> bool:
        return not self.blocking

    def summary(self) -> str:
        return (
            f"{self.total_fields} campos · alta {self.high} · media {self.medium} · "
            f"baixa {self.low} · {len(self.review_items)} para revisar"
        )


def validate(output: DiscoveryOutput) -> ValidationReport:
    report = ValidationReport()

    for entity in output.surface_map.entities:
        for note in entity.fields:
            report.total_fields += 1
            if note.confidence == "high":
                report.high += 1
            elif note.confidence == "medium":
                report.medium += 1
            else:
                report.low += 1
                report.review_items.append(
                    f"{entity.erp_model}.{note.name}: {note.meaning} — {note.evidence}"
                )

    # Bloqueios: coisas que impedem responder com honestidade.
    if output.deep_model.stock_basis == "unknown" and "check_stock" in (
        output.surface_map.operations
    ):
        report.blocking.append(
            "estoque habilitado sem base declarada: a resposta nao poderia dizer "
            "se o numero e disponivel ou fisico"
        )
    if not output.surface_map.operations:
        report.blocking.append("nenhuma operacao suportada: nao ha o que ativar")
    if not output.seed_questions:
        report.blocking.append(
            "nenhuma seed question gerada: sincronize o catalogo antes do Discovery"
        )

    return report


def diff(previous: dict[str, Any] | None, current: DiscoveryOutput) -> list[str]:
    """Diff da versao candidata contra a ativa.

    O humano aprova a **mudanca**, nao o documento inteiro.
    """
    if previous is None:
        return ["primeira versao: nada a comparar"]

    changes: list[str] = []
    previous_ops = set((previous.get("surface_map") or {}).get("operations", []))
    current_ops = set(current.surface_map.operations)
    for added in sorted(current_ops - previous_ops):
        changes.append(f"+ operacao {added}")
    for removed in sorted(previous_ops - current_ops):
        changes.append(f"- operacao {removed}")

    previous_deep = previous.get("deep_model") or {}
    for key, value in current.deep_model.model_dump().items():
        if key == "notes":
            continue
        if previous_deep.get(key) != value:
            changes.append(f"~ {key}: {previous_deep.get(key)} → {value}")

    previous_fields = previous.get("field_confidence") or {}
    for name, data in current.field_confidence.items():
        before = previous_fields.get(name)
        if before is None:
            changes.append(f"+ campo {name} ({data['confidence']})")
        elif before.get("confidence") != data["confidence"]:
            changes.append(f"~ campo {name}: {before.get('confidence')} → {data['confidence']}")
    for name in previous_fields:
        if name not in current.field_confidence:
            changes.append(f"- campo {name}")

    return changes or ["sem mudancas relevantes"]
