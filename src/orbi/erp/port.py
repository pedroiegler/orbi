"""Fronteira com o ERP: DTOs tipados e o Protocol do Adapter.

Esta e a Anti-Corruption Layer na pratica (ORBI.md secao 8). Nada do formato
nativo do ERP atravessa: o Adapter traduz para estes tipos, e o Core so conhece
estes tipos.

MVP e somente leitura — nao existe metodo de escrita neste Protocol, e e assim
que a proibicao P12 fica verificavel em teste.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

EntityType = Literal["product", "customer", "location"]
StockBasis = Literal["available", "physical"]


class ErpDTO(BaseModel):
    """Base dos DTOs de fronteira: imutavel e sem campo extra."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Capabilities(ErpDTO):
    """O que aquele ERP sabe fazer.

    `capabilities()` faz o Core desabilitar automaticamente as tools que o ERP
    nao atende — o que mata a tentacao de espalhar `if erp == "x"` pelo Runtime.
    """

    adapter: str
    integration_mode: Literal["official_api"] = "official_api"
    """Hoje aceita um unico valor. Existe para tornar o principio da secao 10
    verificavel em teste, em vez de boa intencao."""
    supported_tools: frozenset[str]
    supports_reservations: bool = False
    """Quando falso, o estoque respondido e fisico e a resposta diz isso."""
    supports_multi_location: bool = False
    supports_customer_pricing: bool = False
    supports_quantity_pricing: bool = False
    supports_incremental_catalog: bool = False
    currency: str = "BRL"
    erp_version: str | None = None


class StockLocation(ErpDTO):
    location_id: str
    name: str
    physical: Decimal
    reserved: Decimal | None = None
    available: Decimal | None = None


class StockResult(ErpDTO):
    """Conceito canonico de estoque (ORBI.md secao 6.12).

    O Orbi nunca mente sobre qual conceito esta entregando: `basis` diz se o
    numero e disponivel ou fisico, e o template usa isso no texto.
    """

    erp_entity_id: str
    name: str
    uom: str = "un"
    physical: Decimal
    reserved: Decimal | None = None
    available: Decimal | None = None
    basis: StockBasis
    locations: tuple[StockLocation, ...] = ()
    location: str | None = None
    """Preenchido quando a consulta foi restrita a um deposito."""

    @property
    def quantity(self) -> Decimal:
        """O numero que vai na resposta, coerente com `basis`."""
        if self.basis == "available" and self.available is not None:
            return self.available
        return self.physical


class PriceResult(ErpDTO):
    erp_entity_id: str
    name: str
    unit_price: Decimal
    currency: str = "BRL"
    uom: str = "un"
    quantity: Decimal | None = None
    total: Decimal | None = None
    customer_id: str | None = None
    customer_name: str | None = None
    price_list: str | None = None
    discount_percent: Decimal | None = None
    unit_cost: Decimal | None = None
    """Campo sensivel: a Field Policy remove para quem nao tem `price:read_cost`."""
    margin_percent: Decimal | None = None
    """Idem."""


class Invoice(ErpDTO):
    erp_entity_id: str
    number: str
    customer_id: str
    customer_name: str
    amount: Decimal
    open_amount: Decimal
    currency: str = "BRL"
    due_date: date
    issue_date: date | None = None
    days_overdue: int = 0
    status: Literal["open", "overdue", "partial"] = "open"


class OrderLine(ErpDTO):
    product_id: str
    product_name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    uom: str = "un"


class Order(ErpDTO):
    erp_entity_id: str
    number: str
    customer_id: str
    customer_name: str
    ordered_at: datetime
    total: Decimal
    currency: str = "BRL"
    status: str
    lines: tuple[OrderLine, ...] = ()
    delivery_date: date | None = None


class CatalogItem(ErpDTO):
    """Item do indice de resolucao. O Orbi nao copia o ERP (secao 7)."""

    erp_entity_id: str
    entity_type: EntityType
    name: str
    code: str | None = None
    barcode: str | None = None
    active: bool = True
    updated_at: datetime | None = None
    extra: dict[str, str] = Field(default_factory=dict)


@runtime_checkable
class ErpAdapter(Protocol):
    """Contrato que todo ERP precisa cumprir.

    Um adapter novo so entra depois de passar no Conformance Kit
    (`tests/conformance`).
    """

    name: str

    def capabilities(self) -> Capabilities: ...

    def check_connection(self) -> bool:
        """Valida credencial e disponibilidade. Usado por `orbi onboard`."""
        ...

    def get_stock(
        self, product_id: str, location_id: str | None = None, *, timeout_ms: int | None = None
    ) -> StockResult: ...

    def get_price(
        self,
        product_id: str,
        customer_id: str | None = None,
        quantity: Decimal | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> PriceResult: ...

    def list_open_invoices(
        self, customer_id: str, *, timeout_ms: int | None = None
    ) -> list[Invoice]: ...

    def get_last_order(
        self, customer_id: str, *, timeout_ms: int | None = None
    ) -> Order | None: ...

    def iter_catalog(
        self, since: datetime | None = None, *, entity_types: tuple[EntityType, ...] = ()
    ) -> Iterator[CatalogItem]: ...


WRITE_METHOD_PREFIXES: tuple[str, ...] = ("create_", "update_", "delete_", "post_", "confirm_")
"""P12: o Protocol nao pode ganhar metodo de escrita enquanto o MVP for leitura.
O teste `tests/unit/test_erp_port.py` usa esta lista."""
