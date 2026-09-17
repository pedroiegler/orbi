"""Porta de saida para o ERP.

Todo acesso ao ERP passa por aqui, e aqui ficam as tres garantias da secao 6.13:
orcamento de tempo, circuit breaker por operacao e bulkhead por tenant.

Uma regra vale mais que as tres: **nunca sirva estoque em cache** (D-009).
Melhor nao responder do que responder errado sobre quantidade. Por isso este
modulo nao tem cache algum — e o teste `test_no_stock_cache` garante que nao
ganhe um por descuido.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from orbi.core.deadline import Deadline
from orbi.core.resilience import (
    Bulkhead,
    BulkheadFull,
    CircuitBreaker,
    CircuitOpen,
    retry_call,
)
from orbi.core.settings import get_settings
from orbi.erp.errors import (
    RETRYABLE_ERRORS,
    ErpError,
    ErpProtocolError,
    ErpUnavailable,
)
from orbi.erp.port import (
    Capabilities,
    CatalogItem,
    EntityType,
    ErpAdapter,
    Invoice,
    Order,
    PriceResult,
    StockResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# Estado compartilhado pelo processo: o breaker so serve se lembrar das falhas
# entre turnos (D-017 — sem Redis enquanto um worker der conta).
_BREAKER = CircuitBreaker()
_BULKHEAD = Bulkhead(limit=4)

OnCircuitOpen = Callable[[str, str, str], None]
"""(tenant_id, adapter, operation) — dispara alerta no canal de ops."""


class ErpGateway:
    """Envolve um `ErpAdapter` com orcamento, breaker e bulkhead."""

    def __init__(
        self,
        adapter: ErpAdapter,
        tenant_id: str,
        *,
        breaker: CircuitBreaker | None = None,
        bulkhead: Bulkhead | None = None,
        timeout_ms: int | None = None,
        on_circuit_open: OnCircuitOpen | None = None,
    ) -> None:
        self._adapter = adapter
        self._tenant_id = str(tenant_id)
        self._breaker = breaker or _BREAKER
        self._bulkhead = bulkhead or _BULKHEAD
        self._timeout_ms = timeout_ms or get_settings().erp_timeout_ms
        self._on_circuit_open = on_circuit_open

    @property
    def adapter_name(self) -> str:
        return self._adapter.name

    def capabilities(self) -> Capabilities:
        return self._adapter.capabilities()

    def check_connection(self) -> bool:
        return self._adapter.check_connection()

    # --- operacoes do MVP ------------------------------------------------

    def get_stock(
        self, product_id: str, location_id: str | None = None, *, deadline: Deadline
    ) -> StockResult:
        return self._call(
            "get_stock",
            lambda timeout: self._adapter.get_stock(product_id, location_id, timeout_ms=timeout),
            deadline,
        )

    def get_price(
        self,
        product_id: str,
        customer_id: str | None = None,
        quantity: Decimal | None = None,
        *,
        deadline: Deadline,
    ) -> PriceResult:
        return self._call(
            "get_price",
            lambda timeout: self._adapter.get_price(
                product_id, customer_id, quantity, timeout_ms=timeout
            ),
            deadline,
        )

    def list_open_invoices(self, customer_id: str, *, deadline: Deadline) -> list[Invoice]:
        return self._call(
            "list_open_invoices",
            lambda timeout: self._adapter.list_open_invoices(customer_id, timeout_ms=timeout),
            deadline,
        )

    def get_last_order(self, customer_id: str, *, deadline: Deadline) -> Order | None:
        return self._call(
            "get_last_order",
            lambda timeout: self._adapter.get_last_order(customer_id, timeout_ms=timeout),
            deadline,
        )

    def iter_catalog(
        self, since: datetime | None = None, *, entity_types: tuple[EntityType, ...] = ()
    ) -> Iterator[CatalogItem]:
        """Sync roda offline, sem orcamento de turno: nao passa pelo breaker."""
        return self._adapter.iter_catalog(since, entity_types=entity_types)

    # --- execucao --------------------------------------------------------

    def _call[T](self, operation: str, fn: Callable[[int], T], deadline: Deadline) -> T:
        key = CircuitBreaker.key_for(self._tenant_id, self.adapter_name, operation)
        try:
            self._breaker.before_call(key)
        except CircuitOpen:
            self._notify_circuit_open(operation)
            raise

        try:
            self._bulkhead.acquire(self._tenant_id)
        except BulkheadFull as exc:
            raise ErpUnavailable(
                "limite de consultas simultaneas do cliente atingido",
                adapter=self.adapter_name,
            ) from exc

        try:
            with deadline.stage("erp", minimum_ms=50):
                result = retry_call(
                    fn,
                    deadline=deadline,
                    stage="erp",
                    timeout_ms=self._timeout_ms,
                    retry_on=RETRYABLE_ERRORS,
                )
        except ErpError:
            self._breaker.record_failure(key)
            if self._breaker.state(key) == "open":
                self._notify_circuit_open(operation)
            raise
        except Exception as exc:
            # Qualquer coisa que o adapter deixou escapar vira erro normalizado:
            # payload nativo nunca cruza a fronteira.
            self._breaker.record_failure(key)
            raise ErpProtocolError(
                f"resposta inesperada do ERP em {operation}", adapter=self.adapter_name
            ) from exc
        finally:
            self._bulkhead.release(self._tenant_id)

        self._breaker.record_success(key)
        return result

    def _notify_circuit_open(self, operation: str) -> None:
        if self._on_circuit_open is not None:
            self._on_circuit_open(self._tenant_id, self.adapter_name, operation)


def reset_resilience_state() -> None:
    """Usado por testes e pelo comando de operacao `orbi erp reset-breaker`."""
    _BREAKER.reset()
