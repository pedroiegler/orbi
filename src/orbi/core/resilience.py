"""Resiliencia do caminho da requisicao (ORBI.md secao 6.13).

Tres mecanismos, todos em processo (sem Redis, decisao D-017):

- `CircuitBreaker` por `(tenant, adapter, operation)`. Nao por adapter inteiro:
  uma operacao lenta nao pode derrubar as outras tres.
- `Bulkhead` por tenant, para que um cliente nao consuma sozinho o rate limit do
  adapter e derrube os demais.
- `retry_call`, com 1 retentativa apenas em timeout/indisponibilidade, backoff
  com jitter limitado ao que sobrou do `Deadline`.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from orbi.core.deadline import Deadline

BreakerState = Literal["closed", "open", "half_open"]


class CircuitOpen(Exception):
    """O circuito daquela operacao esta aberto: nao adianta chamar o ERP."""

    def __init__(self, key: str, retry_after_ms: int) -> None:
        super().__init__(f"circuito aberto para {key}; tente em {retry_after_ms}ms")
        self.key = key
        self.retry_after_ms = retry_after_ms


class BulkheadFull(Exception):
    """O tenant ja esta usando todas as chamadas concorrentes permitidas."""

    def __init__(self, tenant_id: str, limit: int) -> None:
        super().__init__(f"tenant {tenant_id} atingiu o limite de {limit} chamadas concorrentes")
        self.tenant_id = tenant_id
        self.limit = limit


@dataclass
class BreakerConfig:
    failure_threshold: int = 5
    window_seconds: float = 30.0
    open_seconds: float = 60.0


@dataclass
class _BreakerEntry:
    state: BreakerState = "closed"
    failures: list[float] = field(default_factory=list)
    opened_at: float = 0.0
    probe_in_flight: bool = False


class CircuitBreaker:
    """5 falhas em 30s abrem por 60s; half-open libera uma sonda."""

    def __init__(
        self,
        config: BreakerConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or BreakerConfig()
        self._clock = clock
        self._entries: dict[str, _BreakerEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key_for(tenant_id: str, adapter: str, operation: str) -> str:
        return f"{tenant_id}:{adapter}:{operation}"

    def state(self, key: str) -> BreakerState:
        with self._lock:
            return self._transition(key).state

    def before_call(self, key: str) -> None:
        """Levanta `CircuitOpen` quando nao vale a pena tentar."""
        with self._lock:
            entry = self._transition(key)
            if entry.state == "open":
                elapsed = self._clock() - entry.opened_at
                remaining = max(0.0, self._config.open_seconds - elapsed)
                raise CircuitOpen(key, int(remaining * 1000))
            if entry.state == "half_open":
                if entry.probe_in_flight:
                    raise CircuitOpen(key, int(self._config.open_seconds * 1000))
                entry.probe_in_flight = True

    def record_success(self, key: str) -> None:
        with self._lock:
            entry = self._entries.setdefault(key, _BreakerEntry())
            entry.state = "closed"
            entry.failures.clear()
            entry.opened_at = 0.0
            entry.probe_in_flight = False

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            entry = self._entries.setdefault(key, _BreakerEntry())
            entry.probe_in_flight = False
            if entry.state == "half_open":
                entry.state = "open"
                entry.opened_at = now
                entry.failures.clear()
                return
            cutoff = now - self._config.window_seconds
            entry.failures = [ts for ts in entry.failures if ts >= cutoff]
            entry.failures.append(now)
            if len(entry.failures) >= self._config.failure_threshold:
                entry.state = "open"
                entry.opened_at = now
                entry.failures.clear()

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def _transition(self, key: str) -> _BreakerEntry:
        entry = self._entries.setdefault(key, _BreakerEntry())
        if entry.state == "open" and (self._clock() - entry.opened_at >= self._config.open_seconds):
            entry.state = "half_open"
            entry.probe_in_flight = False
        return entry


class Bulkhead:
    """Semaforo por tenant: limita chamadas concorrentes ao mesmo adapter."""

    def __init__(self, limit: int = 4) -> None:
        self._limit = limit
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, tenant_id: str) -> None:
        with self._lock:
            current = self._counts.get(tenant_id, 0)
            if current >= self._limit:
                raise BulkheadFull(tenant_id, self._limit)
            self._counts[tenant_id] = current + 1

    def release(self, tenant_id: str) -> None:
        with self._lock:
            current = self._counts.get(tenant_id, 0)
            if current <= 1:
                self._counts.pop(tenant_id, None)
            else:
                self._counts[tenant_id] = current - 1

    def in_flight(self, tenant_id: str) -> int:
        with self._lock:
            return self._counts.get(tenant_id, 0)


def retry_call[T](
    operation: Callable[[int], T],
    *,
    deadline: Deadline,
    stage: str,
    timeout_ms: int,
    retry_on: tuple[type[BaseException], ...],
    attempts: int = 2,
    base_backoff_ms: int = 120,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Executa `operation(timeout_ms)` com 1 retentativa dentro do orcamento.

    Leitura e idempotente, entao repetir e seguro. O backoff nunca ultrapassa o
    que sobrou do `Deadline`: preferimos falhar honestamente a estourar o turno.
    """
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        remaining = deadline.ensure(stage)
        budget = min(timeout_ms, remaining)
        try:
            return operation(budget)
        except retry_on as exc:
            last_error = exc
            if attempt == attempts:
                break
            backoff_ms = min(
                int(base_backoff_ms * (2 ** (attempt - 1)) * (0.5 + jitter())),
                max(0, deadline.remaining_ms() - 50),
            )
            if backoff_ms <= 0 or deadline.remaining_ms() <= 0:
                break
            sleep(backoff_ms / 1000)
    assert last_error is not None
    raise last_error
