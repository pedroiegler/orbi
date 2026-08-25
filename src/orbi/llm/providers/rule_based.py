"""Provedor deterministico por regras — desenvolvimento, demo e CI (D-020).

Implementa o mesmo `LLMPort` sem rede e sem chave de API. Serve para rodar o
turno inteiro no CI (evals L4), para demonstrar o produto e para depurar o
Runtime sem gastar token.

Nao e permitido em producao: `Settings.validate_for_production()` recusa.
"""

from __future__ import annotations

import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from orbi.llm.port import LLMRequest, ToolCallEnvelope

MANUFACTURER = "local"
PROVIDER_NAME = "rule_based"

OUT_OF_SCOPE = "FORA_DE_ESCOPO"
NEEDS_CLARIFICATION = "PRECISA_ESCLARECER"

# Ordem importa: o primeiro padrao que casar decide a tool. Financeiro vem antes
# de preco porque "quanto a Silva esta devendo" tem "quanto" e nao e estoque.
_TOOL_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "list_open_invoices",
        (
            "titulo",
            "titulos",
            "em aberto",
            "esta devendo",
            "ta devendo",
            "deve para",
            "debito",
            "boleto",
            "inadimplen",
            "fatura",
            "vencid",
        ),
    ),
    (
        "get_last_order",
        (
            "ultimo pedido",
            "ultima compra",
            "ultimo orcamento",
            "comprou",
            "ultima venda",
            "pedido mais recente",
        ),
    ),
    (
        "check_price",
        ("preco", "quanto custa", "quanto fica", "quanto sai", "valor do", "valor da", "cotacao"),
    ),
    (
        "check_stock",
        (
            "estoque",
            "saldo",
            "quanto tem",
            "quantos tem",
            "quanta",
            "tem de",
            "disponivel",
            "sobrou",
            "tem em",
            "chegou",
        ),
    ),
)

_STOCK_PREFIXES = (
    "quanto tem de",
    "quantos tem de",
    "quanto temos de",
    "quantas temos de",
    "quanto tem",
    "qual o saldo de",
    "qual o estoque de",
    "saldo de",
    "estoque de",
    "tem de",
    "tem",
)

_PRICE_PREFIXES = (
    "qual o preco de",
    "qual o preco do",
    "qual o preco da",
    "qual e o preco de",
    "quanto custa o",
    "quanto custa a",
    "quanto custa",
    "quanto fica",
    "quanto sai",
    "preco de",
    "preco do",
    "preco da",
    "valor do",
    "valor da",
)

_CUSTOMER_PREFIXES = (
    "qual o ultimo pedido da",
    "qual o ultimo pedido do",
    "qual foi a ultima compra da",
    "qual foi a ultima compra do",
    "ultimo pedido da",
    "ultimo pedido do",
    "ultima compra da",
    "ultima compra do",
    "quanto a",
    "quanto o",
    "a",
    "o",
)

_INVOICE_MARKERS = (
    "tem titulo em aberto",
    "tem titulos em aberto",
    "tem algum titulo",
    "esta devendo",
    "ta devendo",
    "esta com debito",
    "tem debito",
    "tem boleto",
    "esta vencido",
    "tem fatura",
    "titulos em aberto",
    "titulo em aberto",
    "em aberto",
)

_LOCATION_RE = re.compile(
    r"\b(?:na|no|em|da|do)\s+(filial|matriz|deposito|loja|cd|centro de distribuicao)\s*([\w\s]*)$"
)

_QUANTITY_RE = re.compile(
    r"\b(\d+(?:[.,]\d+)?)\s*(?:un|und|unidades|sacos|sc|pecas|pcs|rolos|rl)?\b"
)

_CUSTOMER_LINK_RE = re.compile(r"\b(?:para|pra|do cliente|para o cliente|da empresa)\s+(.+)$")

_NOISE_WORDS = {
    "por",
    "favor",
    "ai",
    "hoje",
    "agora",
    "ainda",
    "me",
    "diz",
    "fala",
    "ver",
    "consulta",
    "consultar",
    "verifica",
    "verificar",
    "olha",
    "sabe",
    "sabes",
    "qual",
    "quais",
    "e",
}

_LEADING_TOOL_WORDS = {"preco", "valor", "estoque", "saldo", "custo", "cotacao"}
"""Sobra do gatilho quando a pergunta usa pronome: "qual o preco dele" deixa
"preco dele", e o que interessa e o "dele" — o Runtime resolve pelo slot."""

_ANAPHORA = {"dele", "dela", "desse", "dessa", "deste", "desta", "disso", "mesmo", "mesma", "ele"}


class RuleBasedProvider:
    """Interpretador deterministico de portugues para o dominio do MVP."""

    manufacturer = MANUFACTURER

    def __init__(self, model: str = "rule_based-1") -> None:
        self.name = PROVIDER_NAME
        self.model = model

    def complete(self, request: LLMRequest) -> ToolCallEnvelope:
        started = time.monotonic()
        allowed = {tool["name"] for tool in request.tools}
        question = _last_user_message(request)
        normalized = _normalize(question)

        tool_name = _select_tool(normalized, allowed)
        if tool_name is None:
            return self._text(OUT_OF_SCOPE, started)

        args = _build_args(tool_name, normalized)
        if args is None:
            return self._text(f"{NEEDS_CLARIFICATION} qual produto ou cliente?", started)

        return ToolCallEnvelope(
            finish_reason="tool_call",
            tool_name=tool_name,
            tool_args=args,
            provider=self.name,
            model=self.model,
            tokens_in=len(question.split()),
            tokens_out=8,
            cost_usd=0.0,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def _text(self, text: str, started: float) -> ToolCallEnvelope:
        return ToolCallEnvelope(
            finish_reason="text",
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def _last_user_message(request: LLMRequest) -> str:
    for message in reversed(request.messages):
        if message.get("role") == "user":
            return message.get("content", "")
    return ""


def _normalize(text: str) -> str:
    lowered = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", stripped.replace("?", " ").replace("!", " ")).strip()


def _select_tool(normalized: str, allowed: set[str]) -> str | None:
    for name, markers in _TOOL_PATTERNS:
        if name not in allowed:
            continue
        if any(marker in normalized for marker in markers):
            return name
    return None


def _build_args(tool_name: str, normalized: str) -> dict[str, Any] | None:
    if tool_name == "check_stock":
        remainder, location = _split_location(normalized)
        product = _strip_prefixes(remainder, _STOCK_PREFIXES)
        product = _clean_term(product)
        if not product:
            return None
        args: dict[str, Any] = {"product_term": product}
        if location:
            args["location_term"] = location
        return args

    if tool_name == "check_price":
        remainder = normalized
        customer = None
        match = _CUSTOMER_LINK_RE.search(remainder)
        if match:
            customer = _clean_term(match.group(1))
            remainder = remainder[: match.start()].strip()

        quantity = _extract_quantity(remainder)
        product = _strip_prefixes(remainder, _PRICE_PREFIXES)
        if quantity is not None:
            product = re.sub(_QUANTITY_RE, " ", product, count=1)
        product = _clean_term(product)
        if not product:
            return None
        args = {"product_term": product}
        if customer:
            args["customer_term"] = customer
        if quantity is not None:
            args["quantity"] = float(quantity)
        return args

    # Tools de cliente: list_open_invoices e get_last_order.
    remainder = normalized
    for marker in _INVOICE_MARKERS:
        remainder = remainder.replace(marker, " ")
    customer = _strip_prefixes(remainder.strip(), _CUSTOMER_PREFIXES)
    customer = _clean_term(customer)
    if not customer:
        return None
    return {"customer_term": customer}


def _split_location(normalized: str) -> tuple[str, str | None]:
    match = _LOCATION_RE.search(normalized)
    if not match:
        return normalized, None
    label = f"{match.group(1)} {match.group(2)}".strip()
    return normalized[: match.start()].strip(), _clean_term(label)


def _strip_prefixes(text: str, prefixes: tuple[str, ...]) -> str:
    candidate = text.strip()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if candidate.startswith(f"{prefix} "):
            return candidate[len(prefix) :].strip()
        if candidate == prefix:
            return ""
    return candidate


def _extract_quantity(text: str) -> Decimal | None:
    match = _QUANTITY_RE.search(text)
    if not match:
        return None
    raw = match.group(1).replace(",", ".")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    # Medida colada ao nome ("100mm", "2,5") nao e quantidade pedida.
    tail = text[match.end() : match.end() + 3]
    if tail.strip().startswith(("mm", "cm", "m ", "kg", "l ", "v", "w")):
        return None
    return value if value > 0 else None


def _clean_term(text: str) -> str:
    tokens = [token for token in text.split() if token not in _NOISE_WORDS]
    leading = {"de", "do", "da", "o", "a", "os", "as", "em", "para", "no", "na"}
    while tokens and (tokens[0] in leading or tokens[0] in _LEADING_TOOL_WORDS):
        tokens.pop(0)
    while tokens and tokens[-1] in {"de", "do", "da", "o", "a", "em", "para", "no", "na", "que"}:
        tokens.pop()
    return " ".join(tokens).strip()


def is_anaphora(term: str) -> bool:
    """"e o preco dele?" — o Runtime resolve pelo slot, nao o modelo (D-012)."""
    return _normalize(term) in _ANAPHORA


def build(api_key: str = "", model: str = "", **kwargs: Any) -> RuleBasedProvider:
    return RuleBasedProvider(model=model or "rule_based-1")
