#!/usr/bin/env python3
"""Prepara o Odoo de teste do Orbi (ORBI.md secao 17).

Sobe a base com os dados de demonstracao, instala estoque, vendas e faturamento,
e ajusta alguns nomes de produto para ficarem abreviados como catalogo brasileiro
real (`TB PVC ESG 100MM BR`). Isso e o que torna o ambiente honesto: catalogo de
PME brasileira e abreviado e inconsistente, e e exatamente isso que quebra busca
semantica ingenua.

Somente API oficial: XML-RPC. Nada de escrever direto no banco do Odoo.

    python scripts/odoo_bootstrap.py --url http://localhost:8069
"""

from __future__ import annotations

import argparse
import sys
import time
import xmlrpc.client

DEFAULT_URL = "http://localhost:8069"
DEFAULT_DB = "orbi"
DEFAULT_LOGIN = "admin"
DEFAULT_PASSWORD = "admin"

MODULES = ["stock", "sale_management", "account", "purchase"]

# Produtos com nome abreviado, como catalogo real. O `template` casa com um
# produto da demo do Odoo; quando nao existe, o produto e criado.
CATALOG = [
    ("TB PVC ESG 100MM BR", "TBPVC100", "7891234500011", 38.90, 24.10),
    ("TB PVC ESG 150MM BR", "TBPVC150", "7891234500028", 72.50, 46.00),
    ("TB PVC SOLD 100MM MAR", "TBPVC100S", "7891234500035", 41.20, 26.30),
    ("CIM CP-II 50KG", "CIMCP2", "7891234501018", 34.00, 27.50),
    ("CB FLEX 2,5MM AZ 100M", "CABFLX25", "7891234502015", 289.90, 201.00),
    ("CB FLEX 4,0MM AZ 100M", "CABFLX40", "7891234502022", 446.00, 318.00),
    ("ARG AC-III INT/EXT 20KG", "ARGACIII", "7891234503012", 42.80, 31.90),
    ("TN ACR FOSC BR 18L", "TINTACR18", "7891234504019", 289.00, 196.00),
]

CUSTOMERS = [
    ("CONSTRUTORA SILVA LTDA", "CLI9001"),
    ("MARATEX MATERIAIS ME", "CLI9002"),
    ("JB MATERIAIS DE CONSTRUCAO", "CLI9003"),
]

STOCK = {
    "TBPVC100": 44,
    "TBPVC150": 8,
    "TBPVC100S": 18,
    "CIMCP2": 695,
    "CABFLX25": 22,
    "CABFLX40": 0,
    "ARGACIII": 260,
    "TINTACR18": 62,
}


class Odoo:
    def __init__(self, url: str, db: str, login: str, password: str) -> None:
        self.url, self.db, self.password = url.rstrip("/"), db, password
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)
        self.uid = self.common.authenticate(db, login, password, {})
        if not self.uid:
            raise SystemExit(f"credenciais recusadas para a base '{db}'")

    def call(self, model: str, method: str, *args: object, **kwargs: object) -> object:
        try:
            return self.models.execute_kw(
                self.db, self.uid, self.password, model, method, list(args), kwargs
            )
        except xmlrpc.client.Fault as fault:
            # Metodo de acao do Odoo que devolve None: o proprio XML-RPC do Odoo
            # nao consegue serializar o retorno, mas a acao ja foi aplicada.
            if "cannot marshal None" in str(fault.faultString):
                return None
            raise


def wait_for(url: str, timeout: int = 180) -> None:
    common = xmlrpc.client.ServerProxy(f"{url.rstrip('/')}/xmlrpc/2/common", allow_none=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            version = common.version()
            print(f"Odoo respondendo: {version.get('server_version')}")
            return
        except Exception:
            time.sleep(3)
    raise SystemExit("Odoo nao respondeu no prazo")


def ensure_database(url: str, db: str, password: str) -> bool:
    """Cria a base com dados de demonstracao, se ainda nao existir."""
    service = xmlrpc.client.ServerProxy(f"{url.rstrip('/')}/xmlrpc/2/db", allow_none=True)
    existing = service.list()
    if db in existing:
        print(f"base '{db}' ja existe")
        return False

    print(f"criando base '{db}' com dados de demonstracao (pode levar alguns minutos)...")
    service.create_database(password, db, True, "pt_BR", password, "admin", "br")
    print("base criada")
    return True


def install_modules(odoo: Odoo) -> None:
    for name in MODULES:
        ids = odoo.call("ir.module.module", "search", [["name", "=", name]])
        if not ids:
            print(f"modulo {name} indisponivel nesta instalacao")
            continue
        state = odoo.call("ir.module.module", "read", ids, ["state"])[0]["state"]
        if state == "installed":
            print(f"modulo {name}: ja instalado")
            continue
        print(f"instalando modulo {name}...")
        odoo.call("ir.module.module", "button_immediate_install", ids)
        print(f"modulo {name}: instalado")


def rename_catalog(odoo: Odoo) -> dict[str, int]:
    """Cria os produtos com nome abreviado, como um catalogo brasileiro real."""
    created: dict[str, int] = {}
    for name, code, barcode, price, cost in CATALOG:
        existing = odoo.call("product.product", "search", [["default_code", "=", code]])
        if existing:
            product_id = existing[0]
            odoo.call(
                "product.product",
                "write",
                [product_id],
                {"name": name, "barcode": barcode, "list_price": price, "standard_price": cost},
            )
        else:
            product_id = odoo.call(
                "product.product",
                "create",
                {
                    "name": name,
                    "default_code": code,
                    "barcode": barcode,
                    "list_price": price,
                    "standard_price": cost,
                    "type": "consu",
                    "is_storable": True,
                    "sale_ok": True,
                    "purchase_ok": True,
                },
            )
        created[code] = int(product_id)
        print(f"produto {code}: {name}")
    return created


def ensure_customers(odoo: Odoo) -> dict[str, int]:
    created: dict[str, int] = {}
    for name, ref in CUSTOMERS:
        existing = odoo.call("res.partner", "search", [["ref", "=", ref]])
        if existing:
            partner_id = existing[0]
        else:
            partner_id = odoo.call(
                "res.partner",
                "create",
                {"name": name, "ref": ref, "customer_rank": 1, "company_type": "company"},
            )
        created[ref] = int(partner_id)
        print(f"cliente {ref}: {name}")
    return created


def ensure_stock(odoo: Odoo, products: dict[str, int]) -> None:
    """Ajusta o estoque pela API oficial (`stock.quant`), nunca pelo banco."""
    warehouses = odoo.call(
        "stock.warehouse", "search_read", [], ["lot_stock_id", "name"], limit=3
    )
    if not warehouses:
        print("nenhum armazem encontrado: pulei o ajuste de estoque")
        return

    location_id = warehouses[0]["lot_stock_id"][0]
    for code, quantity in STOCK.items():
        product_id = products.get(code)
        if product_id is None:
            continue
        quants = odoo.call(
            "stock.quant",
            "search",
            [["product_id", "=", product_id], ["location_id", "=", location_id]],
        )
        if quants:
            odoo.call("stock.quant", "write", quants, {"inventory_quantity": quantity})
            odoo.call("stock.quant", "action_apply_inventory", quants)
        else:
            quant_id = odoo.call(
                "stock.quant",
                "create",
                {
                    "product_id": product_id,
                    "location_id": location_id,
                    "inventory_quantity": quantity,
                },
            )
            odoo.call("stock.quant", "action_apply_inventory", [quant_id])
        print(f"estoque {code}: {quantity}")


def ensure_sales(odoo: Odoo, products: dict[str, int], customers: dict[str, int]) -> None:
    """Cria um pedido confirmado e uma fatura em aberto para o cliente ancora."""
    partner_id = customers.get("CLI9001")
    if partner_id is None:
        return

    existing = odoo.call("sale.order", "search", [["partner_id", "=", partner_id]])
    if not existing:
        order_id = odoo.call(
            "sale.order",
            "create",
            {
                "partner_id": partner_id,
                "order_line": [
                    (0, 0, {"product_id": products["CIMCP2"], "product_uom_qty": 120}),
                    (0, 0, {"product_id": products["TBPVC100"], "product_uom_qty": 40}),
                ],
            },
        )
        odoo.call("sale.order", "action_confirm", [order_id])
        print(f"pedido de venda criado: {order_id}")

    invoices = odoo.call(
        "account.move",
        "search",
        [["partner_id", "=", partner_id], ["move_type", "=", "out_invoice"]],
    )
    if not invoices:
        move_id = odoo.call(
            "account.move",
            "create",
            {
                "partner_id": partner_id,
                "move_type": "out_invoice",
                "invoice_line_ids": [
                    (0, 0, {"product_id": products["CIMCP2"], "quantity": 120, "price_unit": 32.30})
                ],
            },
        )
        odoo.call("account.move", "action_post", [move_id])
        print(f"fatura em aberto criada: {move_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--login", default=DEFAULT_LOGIN)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--master-password", default="admin")
    args = parser.parse_args()

    wait_for(args.url)
    ensure_database(args.url, args.db, args.master_password)

    odoo = Odoo(args.url, args.db, args.login, args.password)
    install_modules(odoo)
    products = rename_catalog(odoo)
    customers = ensure_customers(odoo)
    ensure_stock(odoo, products)
    ensure_sales(odoo, products, customers)

    print("\nOdoo pronto. Conecte o Orbi com:")
    print(
        f"  orbi erp add --tenant <slug> --adapter odoo --credentials "
        f"'{{\"url\":\"{args.url}\",\"db\":\"{args.db}\","
        f"\"username\":\"{args.login}\",\"api_key\":\"{args.password}\"}}'"
    )
    print(f"  produto ancora: {products.get('CIMCP2')} · cliente ancora: {customers.get('CLI9001')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
