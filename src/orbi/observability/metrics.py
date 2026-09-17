"""Metricas do turno (ORBI.md secao 14).

Latencia **por etapa**, nao so total; tokens, custo, taxa de `AMBIGUOUS` e taxa
de correcao. Em processo, sem dependencia nova: o resumo diario e o canal de ops
leem daqui e do banco.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from orbi.runtime.pipeline import TurnOutcome


@dataclass
class TurnMetrics:
    """Acumulador simples e thread-safe."""

    statuses: Counter[str] = field(default_factory=Counter)
    tools: Counter[str] = field(default_factory=Counter)
    stage_latencies: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    total_latencies: list[int] = field(default_factory=list)
    cost_usd: float = 0.0
    turns: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, outcome: TurnOutcome) -> None:
        with self._lock:
            self.turns += 1
            self.statuses[outcome.status] += 1
            if outcome.tool_name:
                self.tools[outcome.tool_name] += 1
            for stage, value in outcome.latencies_ms.items():
                self.stage_latencies[stage].append(value)
            self.total_latencies.append(sum(outcome.latencies_ms.values()))
            self.cost_usd += outcome.cost_usd

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "turns": self.turns,
                "statuses": dict(self.statuses),
                "tools": dict(self.tools),
                "ambiguity_rate": self._rate("ambiguous"),
                "not_found_rate": self._rate("not_found"),
                "error_rate": sum(
                    count
                    for status, count in self.statuses.items()
                    if status.startswith("erp_") or status in {"timeout", "misconfigured"}
                )
                / self.turns
                if self.turns
                else 0.0,
                "p50_ms": percentil(self.total_latencies, 50),
                "p95_ms": percentil(self.total_latencies, 95),
                "stages_p50_ms": {
                    stage: percentil(values, 50) for stage, values in self.stage_latencies.items()
                },
                "cost_usd": round(self.cost_usd, 6),
            }

    def reset(self) -> None:
        with self._lock:
            self.statuses.clear()
            self.tools.clear()
            self.stage_latencies.clear()
            self.total_latencies.clear()
            self.cost_usd = 0.0
            self.turns = 0

    def _rate(self, status: str) -> float:
        return self.statuses[status] / self.turns if self.turns else 0.0


def percentil(values: list[int], percentile: int) -> int:
    """Percentil compartilhado entre metricas de operacao e bake-off.

    Publico de proposito: "p95" precisa significar a mesma coisa nos dois lugares.
    """
    if not values:
        return 0
    if percentile == 50:
        # median devolve float quando a amostra e par.
        return round(median(values))
    ordered = sorted(values)
    rank = round((percentile / 100) * len(ordered) + 0.5) - 1
    index = min(len(ordered) - 1, max(0, rank))
    return ordered[index]
