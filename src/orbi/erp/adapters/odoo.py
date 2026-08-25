"""Adapter do Odoo — XML-RPC, interface oficial (ORBI.md secao 17).

O Odoo e o ERP de teste e o **adapter de referencia permanente** (D-015): e
contra ele que o Conformance Kit roda no CI. Como a API do Odoo e XML-RPC e a
dos ERPs em nuvem brasileiros e REST, sustentar os dois prova que a abstracao
aguenta quase qualquer coisa.

Somente leitura: nenhum metodo escreve no ERP (proibicao P12).
"""

from __future__ import annotations

import http.client
import re
import xmlrpc.client
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from orbi.erp.errors import (
    ErpAuthError,
    ErpNotFound,
    ErpProtocolError,
    ErpRateLimited,
    ErpTimeout,
    ErpUnavailable,
)
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

ADAPTER_NAME = "odoo"

CATALOG_PAGE_SIZE = 500

_AUTH_MARKERS = ("AccessDenied", "AccessError", "Invalid login", "expected singleton")


class _TimeoutTransport(xmlrpc.client.Transport):
    """Transport com timeout por chamada.

    Sem isso, uma chamada XML-RPC pendurada ignora o `Deadline` do turno.
    """

    def __init__(self, timeout: float, use_https: bool) -> None:
        super().__init__()
        self.timeout = timeout
        self._use_https = use_https

    def make_connection(self, host: Any) -> http.client.HTTPConnection:
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        chost, self._extra_headers, x509 = self.get_host_info(host)
        connection: http.client.HTTPConnection
        if self._use_https:
            connection = http.client.HTTPSConnection(
                chost, None, timeout=self.timeout, **(x509 or {})
            )
        else:
            connection = http.client.HTTPConnection(chost, timeout=self.timeout)
        self._connection = host, connection
        return connection


class OdooAdapter:
    """Fala com o Odoo por `/xmlrpc/2/common` e `/xmlrpc/2/object`."""

    name = ADAPTER_NAME

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        api_key: str,
        *,
        default_timeout_ms: int = 6_000,
        warehouse_ids: tuple[int, ...] = (),
    ) -> None:
        self._url = url.rstrip("/")
        self._db = db
        self._username = username
        self._api_key = api_key
        self._default_timeout_ms = default_timeout_ms
        self._warehouse_ids = warehouse_ids
        self._uid: int | None = None
        self._capabilities: Capabilities | None = None
        self._server_version: str | None = None

    # --- infraestrutura --------------------------------------------------

    def _proxy(self, endpoint: str, timeout_ms: int | None) -> xmlrpc.client.ServerProxy:
        timeout = (timeout_ms or self._default_timeout_ms) / 1000
        return xmlrpc.client.ServerProxy(
            f"{self._url}/xmlrpc/2/{endpoint}",
            transport=_TimeoutTransport(timeout, self._url.startswith("https")),
            allow_none=True,
        )

    def _uid_or_login(self, timeout_ms: int | None = None) -> int:
        if self._uid is not None:
            return self._uid
        common = self._proxy("common", timeout_ms)
        uid = self._guard(lambda: common.authenticate(self._db, self._username, self._api_key, {}))
        if not uid:
            raise ErpAuthError("credenciais do Odoo recusadas", adapter=self.name)
        self._uid = int(uid)
        return self._uid

    def _execute(
        self,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        uid = self._uid_or_login(timeout_ms)
        obj = self._proxy("object", timeout_ms)
        return self._guard(
            lambda: obj.execute_kw(self._db, uid, self._api_key, model, method, args, kwargs or {})
        )

    @staticmethod
    def _guard(call: Any) -> Any:
        """Traduz toda falha de transporte do XML-RPC em excecao normalizada."""
        try:
            return call()
        except TimeoutError as exc:
            raise ErpTimeout("Odoo nao respondeu no prazo", adapter=ADAPTER_NAME) from exc
        except xmlrpc.client.ProtocolError as exc:
            if exc.errcode in (401, 403):
                raise ErpAuthError("Odoo recusou a credencial", adapter=ADAPTER_NAME) from exc
            if exc.errcode == 429:
                raise ErpRateLimited("Odoo limitou as requisicoes", adapter=ADAPTER_NAME) from exc
            raise ErpUnavailable(
                f"Odoo devolveu HTTP {exc.errcode}", adapter=ADAPTER_NAME
            ) from exc
        except xmlrpc.client.Fault as exc:
            message = str(exc.faultString)
            if any(marker in message for marker in _AUTH_MARKERS):
                raise ErpAuthError("acesso negado pelo Odoo", adapter=ADAPTER_NAME) from exc
            raise ErpProtocolError("Odoo devolveu erro de aplicacao", adapter=ADAPTER_NAME) from exc
        except (ConnectionError, OSError) as exc:
            raise ErpUnavailable("Odoo inacessivel", adapter=ADAPTER_NAME) from exc

    # --- contrato --------------------------------------------------------

    def check_connection(self) -> bool:
        self._uid = None
        return self._uid_or_login() > 0

    def capabilities(self) -> Capabilities:
        if self._capabilities is not None:
            return self._capabilities

        common = self._proxy("common", None)
        version = self._guard(common.version)
        self._server_version = str(version.get("server_version", "desconhecida"))

        has_quants = bool(
            self._execute(
                "ir.model", "search_count", [[["model", "=", "stock.quant"]]]
            )
        )
        has_sale = bool(
            self._execute("ir.model", "search_count", [[["model", "=", "sale.order"]]])
        )
        has_account = bool(
            self._execute("ir.model", "search_count", [[["model", "=", "account.move"]]])
        )

        supported = {"check_stock", "check_price"}
        if has_sale:
            supported.add("get_last_order")
        if has_account:
            supported.add("list_open_invoices")

        self._capabilities = Capabilities(
            adapter=self.name,
            integration_mode="official_api",
            supported_tools=frozenset(supported),
            supports_reservations=has_quants,
            supports_multi_location=has_quants,
            supports_customer_pricing=True,
            supports_quantity_pricing=True,
            supports_incremental_catalog=True,
            currency="BRL",
            erp_version=self._server_version,
        )
        return self._capabilities

    def get_stock(
        self, product_id: str, location_id: str | None = None, *, timeout_ms: int | None = None
    ) -> StockResult:
        pid = _as_int(product_id)
        context: dict[str, Any] = {}
        if location_id:
            context["location"] = _as_int(location_id)

        products = self._execute(
            "product.product",
            "read",
            [[pid], ["name", "qty_available", "free_qty", "uom_id"]],
            {"context": context},
            timeout_ms=timeout_ms,
        )
        if not products:
            raise ErpNotFound(f"produto {product_id} nao existe no Odoo", adapter=self.name)
        product = products[0]

        physical = _decimal(product.get("qty_available"))
        free = product.get("free_qty")
        available = _decimal(free) if free is not None else None
        reserved = physical - available if available is not None else None
        basis = "available" if available is not None else "physical"

        locations: tuple[StockLocation, ...] = ()
        if self.capabilities().supports_multi_location and not location_id:
            locations = self._read_locations(pid, timeout_ms)

        return StockResult(
            erp_entity_id=str(pid),
            name=str(product.get("name") or ""),
            uom=_name_of(product.get("uom_id")) or "un",
            physical=physical,
            reserved=reserved,
            available=available,
            basis=basis,  # type: ignore[arg-type]
            locations=locations,
            location=self._location_name(location_id, timeout_ms) if location_id else None,
        )

    def _read_locations(self, product_id: int, timeout_ms: int | None) -> tuple[StockLocation, ...]:
        quants = self._execute(
            "stock.quant",
            "search_read",
            [
                [
                    ["product_id", "=", product_id],
                    ["location_id.usage", "=", "internal"],
                ],
                ["location_id", "quantity", "reserved_quantity"],
            ],
            {"limit": 200},
            timeout_ms=timeout_ms,
        )
        grouped: dict[int, dict[str, Any]] = {}
        for quant in quants:
            location = quant.get("location_id") or [0, "?"]
            key = int(location[0])
            entry = grouped.setdefault(
                key,
                {"name": str(location[1]), "physical": Decimal("0"), "reserved": Decimal("0")},
            )
            entry["physical"] += _decimal(quant.get("quantity"))
            entry["reserved"] += _decimal(quant.get("reserved_quantity"))

        result = []
        for key, entry in grouped.items():
            physical = entry["physical"]
            reserved = entry["reserved"]
            if physical == 0 and reserved == 0:
                continue
            result.append(
                StockLocation(
                    location_id=str(key),
                    name=entry["name"],
                    physical=physical,
                    reserved=reserved,
                    available=physical - reserved,
                )
            )
        result.sort(key=lambda item: item.available or item.physical, reverse=True)
        return tuple(result)

    def _location_name(self, location_id: str | None, timeout_ms: int | None) -> str | None:
        if not location_id:
            return None
        rows = self._execute(
            "stock.location",
            "read",
            [[_as_int(location_id)], ["complete_name"]],
            timeout_ms=timeout_ms,
        )
        return str(rows[0]["complete_name"]) if rows else None

    def get_price(
        self,
        product_id: str,
        customer_id: str | None = None,
        quantity: Decimal | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> PriceResult:
        pid = _as_int(product_id)
        context: dict[str, Any] = {}
        pricelist_id: int | None = None
        customer_name: str | None = None

        if customer_id:
            partners = self._execute(
                "res.partner",
                "read",
                [[_as_int(customer_id)], ["display_name", "property_product_pricelist"]],
                timeout_ms=timeout_ms,
            )
            if not partners:
                raise ErpNotFound(f"cliente {customer_id} nao existe no Odoo", adapter=self.name)
            customer_name = _clean_name(partners[0].get("display_name"))
            pricelist = partners[0].get("property_product_pricelist")
            if pricelist:
                pricelist_id = int(pricelist[0])
                context["pricelist"] = pricelist_id
                context["partner"] = _as_int(customer_id)
        if quantity is not None:
            context["quantity"] = float(quantity)

        fields = ["name", "list_price", "standard_price", "uom_id"]
        rows = self._execute(
            "product.product",
            "read",
            [[pid], fields],
            {"context": context},
            timeout_ms=timeout_ms,
        )
        if not rows:
            raise ErpNotFound(f"produto {product_id} nao existe no Odoo", adapter=self.name)
        product = rows[0]

        unit_price = _decimal(product.get("list_price"))
        if pricelist_id is not None:
            unit_price = self._price_from_pricelist(
                pid, pricelist_id, quantity, unit_price, timeout_ms
            )

        unit_cost = _decimal(product.get("standard_price"))
        total = unit_price * quantity if quantity is not None else None
        margin = None
        if unit_price > 0 and unit_cost >= 0:
            margin = ((unit_price - unit_cost) / unit_price * 100).quantize(Decimal("0.01"))

        return PriceResult(
            erp_entity_id=str(pid),
            name=str(product.get("name") or ""),
            unit_price=unit_price.quantize(Decimal("0.01")),
            uom=_name_of(product.get("uom_id")) or "un",
            quantity=quantity,
            total=total.quantize(Decimal("0.01")) if total is not None else None,
            customer_id=str(customer_id) if customer_id else None,
            customer_name=customer_name,
            price_list=str(pricelist_id) if pricelist_id else None,
            unit_cost=unit_cost.quantize(Decimal("0.01")),
            margin_percent=margin,
        )

    def _price_from_pricelist(
        self,
        product_id: int,
        pricelist_id: int,
        quantity: Decimal | None,
        fallback: Decimal,
        timeout_ms: int | None,
    ) -> Decimal:
        """Le a tabela de preco pelo metodo publico do Odoo.

        Versoes diferentes expoem metodos diferentes; quando nenhum estiver
        disponivel, o preco de lista e usado e a resposta continua honesta —
        e o Discovery registra a limitacao daquele ERP.
        """
        qty = float(quantity) if quantity is not None else 1.0
        try:
            prices = self._execute(
                "product.pricelist",
                "_get_products_price",
                [[pricelist_id], [product_id], qty],
                timeout_ms=timeout_ms,
            )
            if isinstance(prices, dict) and prices:
                return _decimal(next(iter(prices.values())))
        except ErpProtocolError:
            pass
        return fallback

    def list_open_invoices(
        self, customer_id: str, *, timeout_ms: int | None = None
    ) -> list[Invoice]:
        rows = self._execute(
            "account.move",
            "search_read",
            [
                [
                    ["partner_id", "=", _as_int(customer_id)],
                    ["move_type", "=", "out_invoice"],
                    ["state", "=", "posted"],
                    ["payment_state", "in", ["not_paid", "partial"]],
                ],
                [
                    "name",
                    "partner_id",
                    "amount_total",
                    "amount_residual",
                    "invoice_date",
                    "invoice_date_due",
                    "payment_state",
                ],
            ],
            {"limit": 50, "order": "invoice_date_due asc"},
            timeout_ms=timeout_ms,
        )

        today = date.today()
        invoices: list[Invoice] = []
        for row in rows:
            due = _date(row.get("invoice_date_due")) or _date(row.get("invoice_date")) or today
            overdue = max(0, (today - due).days)
            partner = row.get("partner_id") or [0, ""]
            invoices.append(
                Invoice(
                    erp_entity_id=str(row["id"]),
                    number=str(row.get("name") or row["id"]),
                    customer_id=str(int(partner[0])),
                    customer_name=_clean_name(partner[1]),
                    amount=_decimal(row.get("amount_total")).quantize(Decimal("0.01")),
                    open_amount=_decimal(row.get("amount_residual")).quantize(Decimal("0.01")),
                    due_date=due,
                    issue_date=_date(row.get("invoice_date")),
                    days_overdue=overdue,
                    status=(
                        "partial"
                        if row.get("payment_state") == "partial"
                        else ("overdue" if overdue > 0 else "open")
                    ),
                )
            )
        return invoices

    def get_last_order(self, customer_id: str, *, timeout_ms: int | None = None) -> Order | None:
        rows = self._execute(
            "sale.order",
            "search_read",
            [
                [
                    ["partner_id", "=", _as_int(customer_id)],
                    ["state", "in", ["sale", "done", "sent", "draft"]],
                ],
                ["name", "partner_id", "date_order", "amount_total", "state", "commitment_date"],
            ],
            {"limit": 1, "order": "date_order desc"},
            timeout_ms=timeout_ms,
        )
        if not rows:
            return None
        order = rows[0]

        lines = self._execute(
            "sale.order.line",
            "search_read",
            [
                [["order_id", "=", order["id"]], ["display_type", "=", False]],
                ["product_id", "name", "product_uom_qty", "price_unit", "price_subtotal"],
            ],
            {"limit": 50},
            timeout_ms=timeout_ms,
        )

        partner = order.get("partner_id") or [0, ""]
        return Order(
            erp_entity_id=str(order["id"]),
            number=str(order.get("name") or order["id"]),
            customer_id=str(int(partner[0])),
            customer_name=_clean_name(partner[1]),
            ordered_at=_datetime(order.get("date_order")) or datetime.now(),
            total=_decimal(order.get("amount_total")).quantize(Decimal("0.01")),
            status=str(order.get("state") or ""),
            delivery_date=_date(order.get("commitment_date")),
            lines=tuple(
                OrderLine(
                    product_id=str(int((line.get("product_id") or [0, ""])[0])),
                    product_name=_clean_name(
                        (line.get("product_id") or [0, line.get("name", "")])[1]
                    ),
                    quantity=_decimal(line.get("product_uom_qty")),
                    unit_price=_decimal(line.get("price_unit")).quantize(Decimal("0.01")),
                    total=_decimal(line.get("price_subtotal")).quantize(Decimal("0.01")),
                )
                for line in lines
            ),
        )

    def iter_catalog(
        self, since: datetime | None = None, *, entity_types: tuple[EntityType, ...] = ()
    ) -> Iterator[CatalogItem]:
        wanted = entity_types or ("product", "customer", "location")
        if "product" in wanted:
            yield from self._iter_products(since)
        if "customer" in wanted:
            yield from self._iter_customers(since)
        if "location" in wanted:
            yield from self._iter_locations(since)

    def _iter_products(self, since: datetime | None) -> Iterator[CatalogItem]:
        domain: list[Any] = [["sale_ok", "=", True]]
        if since is not None:
            domain.append(["write_date", ">=", _odoo_datetime(since)])
        offset = 0
        while True:
            rows = self._execute(
                "product.product",
                "search_read",
                [domain, ["name", "default_code", "barcode", "active", "write_date"]],
                {
                    "limit": CATALOG_PAGE_SIZE,
                    "offset": offset,
                    "order": "id asc",
                    "context": {"active_test": False},
                },
            )
            if not rows:
                return
            for row in rows:
                yield CatalogItem(
                    erp_entity_id=str(row["id"]),
                    entity_type="product",
                    name=str(row.get("name") or ""),
                    code=_optional_str(row.get("default_code")),
                    barcode=_optional_str(row.get("barcode")),
                    active=bool(row.get("active", True)),
                    updated_at=_datetime(row.get("write_date")),
                )
            offset += len(rows)

    def _iter_customers(self, since: datetime | None) -> Iterator[CatalogItem]:
        domain: list[Any] = [["customer_rank", ">", 0]]
        if since is not None:
            domain.append(["write_date", ">=", _odoo_datetime(since)])
        offset = 0
        while True:
            rows = self._execute(
                "res.partner",
                "search_read",
                [domain, ["display_name", "ref", "active", "write_date"]],
                {
                    "limit": CATALOG_PAGE_SIZE,
                    "offset": offset,
                    "order": "id asc",
                    "context": {"active_test": False},
                },
            )
            if not rows:
                return
            for row in rows:
                yield CatalogItem(
                    erp_entity_id=str(row["id"]),
                    entity_type="customer",
                    name=_clean_name(row.get("display_name")),
                    code=_optional_str(row.get("ref")),
                    active=bool(row.get("active", True)),
                    updated_at=_datetime(row.get("write_date")),
                )
            offset += len(rows)

    def _iter_locations(self, since: datetime | None) -> Iterator[CatalogItem]:
        domain: list[Any] = [["usage", "=", "internal"]]
        if since is not None:
            domain.append(["write_date", ">=", _odoo_datetime(since)])
        rows = self._execute(
            "stock.location",
            "search_read",
            [domain, ["complete_name", "active", "write_date"]],
            {"limit": CATALOG_PAGE_SIZE, "order": "id asc", "context": {"active_test": False}},
        )
        for row in rows:
            yield CatalogItem(
                erp_entity_id=str(row["id"]),
                entity_type="location",
                name=str(row.get("complete_name") or ""),
                active=bool(row.get("active", True)),
                updated_at=_datetime(row.get("write_date")),
            )


# --- conversores ----------------------------------------------------------


def _as_int(value: str | int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ErpNotFound(f"identificador invalido para o Odoo: {value!r}") from exc


def _decimal(value: Any) -> Decimal:
    if value in (None, False):
        return Decimal("0")
    return Decimal(str(value))


def _optional_str(value: Any) -> str | None:
    if value in (None, False, ""):
        return None
    return str(value)


_CODE_PREFIX_RE = re.compile(r"^\[[^\]]{1,32}\]\s*")


def _clean_name(value: Any) -> str:
    """O Odoo prefixa o nome com o codigo interno ("[CIMCP2] CIM ...").

    O codigo aparece no recibo da entidade por conta propria; repetir dentro do
    nome so polui a resposta.
    """
    return _CODE_PREFIX_RE.sub("", str(value or "")).strip()


def _name_of(value: Any) -> str | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[1])
    return None


def _date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def _odoo_datetime(value: datetime) -> str:
    # O Odoo guarda datas em UTC sem timezone; sobrepomos um minuto de folga
    # para nao perder registro escrito durante a leitura anterior.
    return (value - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")


def build(credentials: dict[str, Any], config: dict[str, Any]) -> OdooAdapter:
    """Factory usada pelo `erp.registry`."""
    missing = [key for key in ("url", "db", "username", "api_key") if not credentials.get(key)]
    if missing:
        raise ErpAuthError(f"credenciais do Odoo incompletas: faltam {', '.join(missing)}")
    return OdooAdapter(
        url=str(credentials["url"]),
        db=str(credentials["db"]),
        username=str(credentials["username"]),
        api_key=str(credentials["api_key"]),
        default_timeout_ms=int(config.get("timeout_ms", 6_000)),
        warehouse_ids=tuple(int(x) for x in config.get("warehouse_ids", [])),
    )
