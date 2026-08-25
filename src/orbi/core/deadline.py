"""Orcamento de tempo do turno.

Um unico `Deadline` desce por todas as camadas. Cada estagio consulta
`remaining_ms()` antes de comecar, o que impede o classico "cada camada tem 5s
de timeout e o total vira 20s" (ORBI.md secao 6.13).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


class DeadlineExceeded(Exception):
    """O orcamento do turno acabou antes de o estagio comecar."""

    def __init__(self, stage: str, elapsed_ms: int, total_ms: int) -> None:
        super().__init__(
            f"orcamento esgotado antes de '{stage}': {elapsed_ms}ms de {total_ms}ms"
        )
        self.stage = stage
        self.elapsed_ms = elapsed_ms
        self.total_ms = total_ms


@dataclass
class Deadline:
    """Orcamento total do turno, com marcacao de latencia por etapa."""

    total_ms: int = 10_000
    started_at: float = field(default_factory=time.monotonic)
    stage_latencies_ms: dict[str, int] = field(default_factory=dict)

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    def remaining_ms(self) -> int:
        return max(0, self.total_ms - self.elapsed_ms())

    def expired(self) -> bool:
        return self.remaining_ms() <= 0

    def ensure(self, stage: str, minimum_ms: int = 1) -> int:
        """Garante folga para iniciar `stage`; devolve o que resta."""
        remaining = self.remaining_ms()
        if remaining < minimum_ms:
            raise DeadlineExceeded(stage, self.elapsed_ms(), self.total_ms)
        return remaining

    def budget_for(self, stage: str, desired_ms: int, minimum_ms: int = 1) -> int:
        """Timeout do estagio: o desejado, limitado ao que sobrou do turno."""
        remaining = self.ensure(stage, minimum_ms)
        return min(desired_ms, remaining)

    @contextmanager
    def stage(self, name: str, minimum_ms: int = 1) -> Iterator[int]:
        """Mede a etapa e registra a latencia para auditoria e metricas."""
        remaining = self.ensure(name, minimum_ms)
        started = time.monotonic()
        try:
            yield remaining
        finally:
            elapsed = int((time.monotonic() - started) * 1000)
            self.stage_latencies_ms[name] = self.stage_latencies_ms.get(name, 0) + elapsed
