"""Gateway do ERP: orcamento, breaker, bulkhead e a proibicao de cache."""

from __future__ import annotations

from decimal import Decimal

import pytest

from orbi.core.deadline import Deadline
from orbi.core.resilience import Bulkhead, CircuitBreaker, CircuitOpen
from orbi.erp.adapters.memory import MemoryAdapter
from orbi.erp.errors import ErpProtocolError, ErpTimeout, ErpUnavailable
from orbi.erp.gateway import ErpGateway
from orbi.erp.port import WRITE_METHOD_PREFIXES, ErpAdapter


def _gateway(adapter: object, **kwargs: object) -> ErpGateway:
    return ErpGateway(
        adapter,  # type: ignore[arg-type]
        "11111111-1111-4111-8111-111111111111",
        breaker=kwargs.pop("breaker", CircuitBreaker()),
        bulkhead=kwargs.pop("bulkhead", Bulkhead(limit=4)),
        **kwargs,  # type: ignore[arg-type]
    )


def test_no_stock_cache(tmp_path: object) -> None:
    """P3/D-009: cada pergunta bate no ERP. Estoque nunca vem de cache."""
    adapter = MemoryAdapter()
    gateway = _gateway(adapter)

    for _ in range(3):
        gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))

    assert [call[0] for call in adapter.calls] == ["get_stock"] * 3


def test_gateway_has_no_cache_attribute() -> None:
    """Um cache adicionado por descuido apareceria aqui."""
    suspicious = [name for name in dir(ErpGateway) if "cache" in name.lower()]
    assert suspicious == []


def test_stock_basis_is_preserved_through_the_gateway() -> None:
    gateway = _gateway(MemoryAdapter(supports_reservations=False))
    result = gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))
    assert result.basis == "physical"
    assert result.available is None


def test_erp_errors_are_recorded_and_reraised() -> None:
    breaker = CircuitBreaker()
    gateway = _gateway(MemoryAdapter(fail_with=ErpTimeout("caiu")), breaker=breaker)

    with pytest.raises(ErpTimeout):
        gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))

    key = CircuitBreaker.key_for("11111111-1111-4111-8111-111111111111", "memory", "get_stock")
    assert breaker.state(key) == "closed"  # uma falha ainda nao abre


def test_circuit_opens_after_repeated_failures_and_alerts_ops() -> None:
    breaker = CircuitBreaker()
    alerts: list[tuple[str, str, str]] = []
    gateway = _gateway(
        MemoryAdapter(fail_with=ErpUnavailable("fora do ar")),
        breaker=breaker,
        on_circuit_open=lambda tenant, adapter, operation: alerts.append(
            (tenant, adapter, operation)
        ),
    )

    for _ in range(5):
        with pytest.raises(ErpUnavailable):
            gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))

    with pytest.raises(CircuitOpen):
        gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))

    assert alerts, "circuito aberto precisa alertar o canal de ops"
    assert alerts[0][2] == "get_stock"


def test_circuit_is_per_operation() -> None:
    """D-008: estoque com problema nao derruba consulta de titulos."""
    breaker = CircuitBreaker()
    failing = _gateway(MemoryAdapter(fail_with=ErpUnavailable("fora")), breaker=breaker)
    for _ in range(5):
        with pytest.raises(ErpUnavailable):
            failing.get_stock("4471", deadline=Deadline(total_ms=5_000))

    healthy = _gateway(MemoryAdapter(), breaker=breaker)
    invoices = healthy.list_open_invoices("9001", deadline=Deadline(total_ms=5_000))
    assert isinstance(invoices, list)


def test_bulkhead_full_becomes_a_normalized_error() -> None:
    bulkhead = Bulkhead(limit=1)
    bulkhead.acquire("11111111-1111-4111-8111-111111111111")
    gateway = _gateway(MemoryAdapter(), bulkhead=bulkhead)

    with pytest.raises(ErpUnavailable):
        gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))


def test_unexpected_adapter_error_is_normalized() -> None:
    """Payload nativo nunca cruza a fronteira, nem como excecao."""

    class BrokenAdapter(MemoryAdapter):
        def get_stock(self, *args: object, **kwargs: object) -> object:
            raise KeyError("campo_inesperado_do_erp")

    gateway = _gateway(BrokenAdapter())
    with pytest.raises(ErpProtocolError) as exc:
        gateway.get_stock("4471", deadline=Deadline(total_ms=5_000))
    assert "campo_inesperado_do_erp" not in str(exc.value)


def test_gateway_records_erp_latency_in_the_deadline() -> None:
    deadline = Deadline(total_ms=5_000)
    _gateway(MemoryAdapter()).get_price("4471", quantity=Decimal("10"), deadline=deadline)
    assert "erp" in deadline.stage_latencies_ms


def test_protocol_has_no_write_methods() -> None:
    """P12: o contrato nao pode ganhar escrita enquanto o MVP for leitura."""
    for attribute in dir(ErpAdapter):
        assert not attribute.startswith(WRITE_METHOD_PREFIXES), attribute
