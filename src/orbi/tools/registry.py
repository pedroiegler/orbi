"""Tool Registry (ORBI.md secao 6.7).

Cada tool e declarada **uma unica vez**, num `ToolSpec`, que gera cinco
artefatos:

1. JSON Schema enviado ao LLM
2. modelo Pydantic de validacao
3. registro na tabela `tools`
4. fixture do eval set
5. template de resposta

Um teste de CI falha se algum artefato estiver dessincronizado. A causa mais
comum de bug nesse tipo de sistema e o schema do LLM divergir da validacao do
backend depois de meses de mudancas.

Proibicao P1: **nao existe tool de busca de produto ou cliente**. A resolucao de
entidade e etapa interna do Runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from orbi.tools.args import (
    CheckPriceArgs,
    CheckStockArgs,
    GetLastOrderArgs,
    ListOpenInvoicesArgs,
    ToolArgs,
)

Domain = Literal["stock", "commercial", "finance"]
EntityKind = Literal["product", "customer", "location"]

# Capabilities internas. Os papeis visiveis sao presets sobre elas (D-018).
CAP_STOCK_READ = "stock:read"
CAP_PRICE_READ = "price:read"
CAP_PRICE_READ_COST = "price:read_cost"
CAP_INVOICE_READ = "invoice:read"
CAP_CUSTOMER_READ = "customer:read"

ALL_CAPABILITIES: tuple[str, ...] = (
    CAP_STOCK_READ,
    CAP_PRICE_READ,
    CAP_PRICE_READ_COST,
    CAP_INVOICE_READ,
    CAP_CUSTOMER_READ,
)

Role = Literal["sales_rep", "finance", "admin"]

ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "sales_rep": frozenset({CAP_STOCK_READ, CAP_PRICE_READ, CAP_CUSTOMER_READ}),
    "finance": frozenset(
        {CAP_INVOICE_READ, CAP_CUSTOMER_READ, CAP_PRICE_READ, CAP_PRICE_READ_COST}
    ),
    "admin": frozenset(ALL_CAPABILITIES),
}


@dataclass(frozen=True)
class ToolSpec:
    """Declaracao unica de uma tool."""

    name: str
    domain: Domain
    description: str
    args_model: type[ToolArgs]
    required_capabilities: frozenset[str]
    erp_operation: str
    """Operacao do `ErpAdapter` exigida. `capabilities()` do adapter desliga a
    tool automaticamente quando o ERP nao atende (ORBI.md secao 6.11)."""
    template: str
    """Nome do template Jinja, relativo a `render/templates`."""
    primary_entity: EntityKind | None
    """Entidade que a resolucao precisa descobrir antes de chamar o ERP."""
    eval_fixture: str
    """Arquivo de casos em `src/orbi/evals/datasets`."""
    examples: tuple[str, ...] = field(default=())
    """Perguntas de exemplo. Alimentam o prompt e o eval L1."""

    def json_schema(self) -> dict[str, Any]:
        """Schema enviado ao LLM, no formato neutro do `LLMPort`."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }

    def validate_args(self, raw: dict[str, Any]) -> ToolArgs:
        return self.args_model.model_validate(raw)


CHECK_STOCK = ToolSpec(
    name="check_stock",
    domain="stock",
    description=(
        "Consulta a quantidade em estoque de um produto. Use quando a pessoa perguntar "
        "quanto tem, se tem, saldo, disponibilidade ou estoque de algo."
    ),
    args_model=CheckStockArgs,
    required_capabilities=frozenset({CAP_STOCK_READ}),
    erp_operation="get_stock",
    template="check_stock.txt.j2",
    primary_entity="product",
    eval_fixture="check_stock.json",
    examples=(
        "quanto tem de tubo pvc 100?",
        "tem cimento na filial?",
        "qual o saldo de cabo flexivel 2,5?",
    ),
)

CHECK_PRICE = ToolSpec(
    name="check_price",
    domain="commercial",
    description=(
        "Consulta o preco de venda de um produto, opcionalmente para um cliente e uma "
        "quantidade. Use quando a pessoa perguntar preco, valor ou quanto custa."
    ),
    args_model=CheckPriceArgs,
    required_capabilities=frozenset({CAP_PRICE_READ}),
    erp_operation="get_price",
    template="check_price.txt.j2",
    primary_entity="product",
    eval_fixture="check_price.json",
    examples=(
        "qual o preco do tubo pvc 100?",
        "quanto fica 50 sacos de cimento para a construtora silva?",
    ),
)

LIST_OPEN_INVOICES = ToolSpec(
    name="list_open_invoices",
    domain="finance",
    description=(
        "Lista os titulos em aberto de um cliente. Use quando a pessoa perguntar sobre "
        "debito, titulo, boleto em aberto, pendencia financeira ou se o cliente esta devendo."
    ),
    args_model=ListOpenInvoicesArgs,
    required_capabilities=frozenset({CAP_INVOICE_READ}),
    erp_operation="list_open_invoices",
    template="list_open_invoices.txt.j2",
    primary_entity="customer",
    eval_fixture="list_open_invoices.json",
    examples=(
        "a construtora silva tem titulo em aberto?",
        "quanto a maratex esta devendo?",
    ),
)

GET_LAST_ORDER = ToolSpec(
    name="get_last_order",
    domain="commercial",
    description=(
        "Mostra o ultimo pedido de um cliente. Use quando a pessoa perguntar qual foi a "
        "ultima compra, o ultimo pedido ou quando o cliente comprou pela ultima vez."
    ),
    args_model=GetLastOrderArgs,
    required_capabilities=frozenset({CAP_CUSTOMER_READ}),
    erp_operation="get_last_order",
    template="get_last_order.txt.j2",
    primary_entity="customer",
    eval_fixture="get_last_order.json",
    examples=(
        "qual o ultimo pedido da construtora silva?",
        "quando a maratex comprou pela ultima vez?",
    ),
)

_SPECS: tuple[ToolSpec, ...] = (CHECK_STOCK, CHECK_PRICE, LIST_OPEN_INVOICES, GET_LAST_ORDER)

TOOL_REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in _SPECS}

FORBIDDEN_TOOL_PATTERNS: tuple[str, ...] = ("search", "find", "lookup", "query", "list_products")
"""P1: nomes que denunciam uma tool de busca de entidade. O teste de CI usa esta
lista para impedir que uma volte a ser criada."""


def all_tools() -> tuple[ToolSpec, ...]:
    return _SPECS


def get_tool(name: str) -> ToolSpec:
    try:
        return TOOL_REGISTRY[name]
    except KeyError:
        raise KeyError(f"tool desconhecida: {name}") from None


def tool_names() -> tuple[str, ...]:
    return tuple(TOOL_REGISTRY)


def capabilities_for_role(role: str) -> frozenset[str]:
    try:
        return ROLE_CAPABILITIES[role]
    except KeyError:
        raise KeyError(f"papel desconhecido: {role}") from None


def tools_for_role(role: str) -> tuple[ToolSpec, ...]:
    """Tools que o papel pode chamar.

    Usado na montagem do prompt: o modelo nunca ve uma tool que nao pode chamar
    (ORBI.md secao 6.4).
    """
    caps = capabilities_for_role(role)
    return tuple(spec for spec in _SPECS if spec.required_capabilities <= caps)
