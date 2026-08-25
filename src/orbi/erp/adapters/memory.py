"""Adapter em memoria — desenvolvimento, demonstracao e testes E2E.

Nao e um stub: implementa o contrato inteiro do `ErpAdapter`, passa no
Conformance Kit e sustenta o Runtime de ponta a ponta sem rede. Serve para
demonstrar o produto antes de ter credencial do cliente e para rodar os evals
L4 no CI.

Nao e selecionavel em producao: `erp.registry` recusa (mesma regra do provedor
de LLM `rule_based`, D-020).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from orbi.erp.errors import ErpNotFound, ErpUnavailable
from orbi.erp.port import (
    Capabilities,
    CatalogItem,
    EntityType,
    Invoice,
    Order,
    OrderLine,
    PriceResult,
    StockLocation,
    StockResult,
)

ADAPTER_NAME = "memory"

DATASET_PATH = Path(__file__).with_name("memory_dataset.json")


class MemoryAdapter:
    """ERP em memoria, alimentado por um dataset JSON."""

    name = ADAPTER_NAME

    def __init__(
        self,
        dataset: dict[str, Any] | None = None,
        *,
        supports_reservations: bool = True,
        supports_multi_location: bool = True,
        fail_with: Exception | None = None,
    ) -> None:
        self._data = dataset if dataset is not None else load_default_dataset()
        self._supports_reservations = supports_reservations
        self._supports_multi_location = supports_multi_location
        self._fail_with = fail_with
        self._updated_at = datetime.now()
        """Carimbo unico do dataset: torna a leitura incremental testavel."""
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        """Registro de chamadas: usado para provar que nao existe cache (P3)."""

    # --- infraestrutura --------------------------------------------------

    def _record(self, operation: str, *args: Any) -> None:
        self.calls.append((operation, args))
        if self._fail_with is not None:
            raise self._fail_with

    def _product(self, product_id: str) -> dict[str, Any]:
        for item in self._data["products"]:
            if str(item["id"]) == str(product_id):
                return dict(item)
        raise ErpNotFound(f"produto {product_id} nao existe", adapter=self.name)

    def _customer(self, customer_id: str) -> dict[str, Any]:
        for item in self._data["customers"]:
            if str(item["id"]) == str(customer_id):
                return dict(item)
        raise ErpNotFound(f"cliente {customer_id} nao existe", adapter=self.name)

    # --- contrato --------------------------------------------------------

    def check_connection(self) -> bool:
        if self._fail_with is not None:
            raise ErpUnavailable("ERP de memoria configurado para falhar", adapter=self.name)
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities(
            adapter=self.name,
            integration_mode="official_api",
            supported_tools=frozenset(
                {"check_stock", "check_price", "list_open_invoices", "get_last_order"}
            ),
            supports_reservations=self._supports_reservations,
            supports_multi_location=self._supports_multi_location,
            supports_customer_pricing=True,
            supports_quantity_pricing=True,
            supports_incremental_catalog=True,
            currency="BRL",
            erp_version="memory-1",
        )

    def get_stock(
        self, product_id: str, location_id: str | None = None, *, timeout_ms: int | None = None
    ) -> StockResult:
        self._record("get_stock", product_id, location_id)
        product = self._product(product_id)

        raw_locations = [
            dict(entry)
            for entry in product.get("stock", [])
            if not location_id or str(entry["location_id"]) == str(location_id)
        ]

        physical = sum(
            (Decimal(str(entry["physical"])) for entry in raw_locations), Decimal("0")
        )
        reserved = sum(
            (Decimal(str(entry.get("reserved", 0))) for entry in raw_locations), Decimal("0")
        )

        locations: tuple[StockLocation, ...] = ()
        if self._supports_multi_location and not location_id:
            locations = tuple(
                StockLocation(
                    location_id=str(entry["location_id"]),
                    name=str(entry["location_name"]),
                    physical=Decimal(str(entry["physical"])),
                    reserved=Decimal(str(entry.get("reserved", 0)))
                    if self._supports_reservations
                    else None,
                    available=(
                        Decimal(str(entry["physical"])) - Decimal(str(entry.get("reserved", 0)))
                        if self._supports_reservations
                        else None
                    ),
                )
                for entry in sorted(
                    raw_locations, key=lambda e: Decimal(str(e["physical"])), reverse=True
                )
            )

        location_name = None
        if location_id:
            for entry in product.get("stock", []):
                if str(entry["location_id"]) == str(location_id):
                    location_name = str(entry["location_name"])
                    break

        return StockResult(
            erp_entity_id=str(product["id"]),
            name=str(product["name"]),
            uom=str(product.get("uom", "un")),
            physical=physical,
            reserved=reserved if self._supports_reservations else None,
            available=physical - reserved if self._supports_reservations else None,
            basis="available" if self._supports_reservations else "physical",
            locations=locations,
            location=location_name,
        )

    def get_price(
        self,
        product_id: str,
        customer_id: str | None = None,
        quantity: Decimal | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> PriceResult:
        self._record("get_price", product_id, customer_id, quantity)
        product = self._product(product_id)

        unit_price = Decimal(str(product["price"]))
        customer_name = None
        discount = None
        if customer_id:
            customer = self._customer(customer_id)
            customer_name = str(customer["name"])
            discount = Decimal(str(customer.get("discount_percent", 0)))
            if discount:
                unit_price = (unit_price * (Decimal("100") - discount) / Decimal("100")).quantize(
                    Decimal("0.01")
                )

        total = (unit_price * quantity).quantize(Decimal("0.01")) if quantity else None
        unit_cost = Decimal(str(product.get("cost", 0)))
        margin = (
            ((unit_price - unit_cost) / unit_price * 100).quantize(Decimal("0.01"))
            if unit_price > 0
            else None
        )

        return PriceResult(
            erp_entity_id=str(product["id"]),
            name=str(product["name"]),
            unit_price=unit_price,
            uom=str(product.get("uom", "un")),
            quantity=quantity,
            total=total,
            customer_id=str(customer_id) if customer_id else None,
            customer_name=customer_name,
            discount_percent=discount or None,
            unit_cost=unit_cost,
            margin_percent=margin,
        )

    def list_open_invoices(
        self, customer_id: str, *, timeout_ms: int | None = None
    ) -> list[Invoice]:
        self._record("list_open_invoices", customer_id)
        customer = self._customer(customer_id)
        today = date.today()
        invoices = []
        for raw in self._data.get("invoices", []):
            if str(raw["customer_id"]) != str(customer_id):
                continue
            due = today + timedelta(days=int(raw["due_in_days"]))
            overdue = max(0, (today - due).days)
            invoices.append(
                Invoice(
                    erp_entity_id=str(raw["id"]),
                    number=str(raw["number"]),
                    customer_id=str(customer["id"]),
                    customer_name=str(customer["name"]),
                    amount=Decimal(str(raw["amount"])),
                    open_amount=Decimal(str(raw.get("open_amount", raw["amount"]))),
                    due_date=due,
                    issue_date=due - timedelta(days=30),
                    days_overdue=overdue,
                    status="overdue" if overdue > 0 else "open",
                )
            )
        invoices.sort(key=lambda invoice: invoice.due_date)
        return invoices

    def get_last_order(self, customer_id: str, *, timeout_ms: int | None = None) -> Order | None:
        self._record("get_last_order", customer_id)
        customer = self._customer(customer_id)
        orders = [
            raw
            for raw in self._data.get("orders", [])
            if str(raw["customer_id"]) == str(customer_id)
        ]
        if not orders:
            return None
        raw = max(orders, key=lambda item: int(item["days_ago"]) * -1)
        ordered_at = datetime.now() - timedelta(days=int(raw["days_ago"]))
        lines = tuple(
            OrderLine(
                product_id=str(line["product_id"]),
                product_name=str(self._product(str(line["product_id"]))["name"]),
                quantity=Decimal(str(line["quantity"])),
                unit_price=Decimal(str(line["unit_price"])),
                total=(Decimal(str(line["quantity"])) * Decimal(str(line["unit_price"]))).quantize(
                    Decimal("0.01")
                ),
            )
            for line in raw.get("lines", [])
        )
        return Order(
            erp_entity_id=str(raw["id"]),
            number=str(raw["number"]),
            customer_id=str(customer["id"]),
            customer_name=str(customer["name"]),
            ordered_at=ordered_at,
            total=sum((line.total for line in lines), Decimal("0")).quantize(Decimal("0.01")),
            status=str(raw.get("status", "sale")),
            lines=lines,
        )

    def iter_catalog(
        self, since: datetime | None = None, *, entity_types: tuple[EntityType, ...] = ()
    ) -> Iterator[CatalogItem]:
        self._record("iter_catalog", since)
        wanted = entity_types or ("product", "customer", "location")
        if since is not None and since > self._updated_at:
            return
        if "product" in wanted:
            for product in self._data["products"]:
                yield CatalogItem(
                    erp_entity_id=str(product["id"]),
                    entity_type="product",
                    name=str(product["name"]),
                    code=product.get("code"),
                    barcode=product.get("barcode"),
                    active=bool(product.get("active", True)),
                    updated_at=self._updated_at,
                )
        if "customer" in wanted:
            for customer in self._data["customers"]:
                yield CatalogItem(
                    erp_entity_id=str(customer["id"]),
                    entity_type="customer",
                    name=str(customer["name"]),
                    code=customer.get("code"),
                    active=bool(customer.get("active", True)),
                    updated_at=self._updated_at,
                )
        if "location" in wanted:
            for location in self._data.get("locations", []):
                yield CatalogItem(
                    erp_entity_id=str(location["id"]),
                    entity_type="location",
                    name=str(location["name"]),
                    active=True,
                    updated_at=self._updated_at,
                )


def load_default_dataset() -> dict[str, Any]:
    """Catalogo de demonstracao com nomes abreviados como catalogo real."""
    with DATASET_PATH.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def build(credentials: dict[str, Any], config: dict[str, Any]) -> MemoryAdapter:
    dataset_path = config.get("dataset_path")
    dataset = None
    if dataset_path:
        with Path(dataset_path).open(encoding="utf-8") as handle:
            dataset = json.load(handle)
    return MemoryAdapter(
        dataset,
        supports_reservations=bool(config.get("supports_reservations", True)),
        supports_multi_location=bool(config.get("supports_multi_location", True)),
    )
