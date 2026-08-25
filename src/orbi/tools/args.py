"""Contrato de argumentos das tools (ORBI.md secao 6.6).

Regra estrutural, aplicada por tipo e nao por convencao:

    O LLM nunca emite identificadores. So escreve o que a pessoa falou, em palavras.

Modelo alucina ID com naturalidade, e um ID alucinado passa por qualquer
validacao de tipo comum. Aqui `product_term="4471"` e rejeitado pelo Pydantic
antes de qualquer execucao.

Codigo real de produto ainda entra no sistema — mas pelo caminho deterministico
da desambiguacao (`pending_resolutions`), construido pelo Runtime, nunca por um
argumento emitido pelo modelo.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import CoreSchema, core_schema

MAX_TERM_LENGTH = 120

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_INTERNAL_CODE_RE = re.compile(
    r"""(?ix)
    (?:^|\s)
    (?:
        \#\d{2,}                      # #4471
      | [a-z]{1,5}[-_./]\d{3,}        # PRD-4471, sku/1234
      | \d{3,}[-_/][a-z0-9]{1,6}      # 4471-A
      | (?:sku|cod|cod\.|ref|id)[-_.: ]*\d{2,}
      | \d{8,}                        # EAN / codigo longo
    )
    (?:\s|$)
    """
)
_UNIT_SUFFIX_RE = re.compile(
    r"(?i)^\d+(?:[.,]\d+)?\s*(mm|cm|m|km|kg|g|mg|l|ml|un|pc|pol|w|v|a|hp|cv|\")$"
)
_TOKEN_RE = re.compile(r"[^\s]+")


class InvalidEntityTerm(ValueError):
    """O termo parece um identificador e nao uma palavra dita por uma pessoa."""


def _strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn"
    )


def _looks_like_identifier(value: str) -> str | None:
    """Devolve o motivo da rejeicao, ou None quando o termo e aceitavel."""
    text = value.strip()
    if not text:
        return "termo vazio"
    if len(text) > MAX_TERM_LENGTH:
        return f"termo com mais de {MAX_TERM_LENGTH} caracteres"

    plain = _strip_accents(text)

    if _UUID_RE.search(plain):
        return "termo contem UUID"
    if _CPF_RE.search(plain):
        return "termo contem CPF"
    if _CNPJ_RE.search(plain):
        return "termo contem CNPJ"

    digits_only = re.sub(r"[\s.\-/]", "", plain)
    if digits_only.isdigit() and len(digits_only) >= 4:
        return "termo e um identificador numerico"

    if _INTERNAL_CODE_RE.search(f" {plain} "):
        return "termo contem padrao de codigo interno"

    for token in _TOKEN_RE.findall(plain):
        bare = token.strip(".,;:()[]{}")
        if bare.isdigit() and len(bare) >= 4:
            return "termo contem identificador numerico isolado"
        if _UNIT_SUFFIX_RE.match(bare):
            continue

    return None


class EntityTerm(str):
    """Termo em linguagem natural que aponta para uma entidade do ERP.

    Nunca um identificador: a resolucao para `erp_entity_id` e responsabilidade
    do Runtime, nao do modelo.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> EntityTerm:
        reason = _looks_like_identifier(value)
        if reason is not None:
            raise InvalidEntityTerm(f"{reason}: {value!r}")
        return super().__new__(cls, " ".join(value.split()))

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ) -> CoreSchema:
        def validate(value: str) -> EntityTerm:
            return cls(value)

        return core_schema.no_info_after_validator_function(
            validate,
            core_schema.str_schema(min_length=1, max_length=MAX_TERM_LENGTH, strict=False),
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> dict[str, Any]:
        json_schema: dict[str, Any] = handler(schema)
        json_schema.update(
            {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_TERM_LENGTH,
                "description": (
                    "Termo em linguagem natural, exatamente como a pessoa falou. "
                    "Nunca um codigo, ID, CPF, CNPJ ou numero de cadastro."
                ),
            }
        )
        return json_schema


class ToolArgs(BaseModel):
    """Base de todo argumento de tool: strict e sem campo extra."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class CheckStockArgs(ToolArgs):
    product_term: EntityTerm = Field(description="O produto, em palavras.")
    location_term: EntityTerm | None = Field(
        default=None, description="Deposito ou filial, em palavras, quando a pessoa citar."
    )


class CheckPriceArgs(ToolArgs):
    product_term: EntityTerm = Field(description="O produto, em palavras.")
    customer_term: EntityTerm | None = Field(
        default=None, description="O cliente, em palavras, quando a pessoa citar."
    )
    quantity: Decimal | None = Field(
        default=None, gt=0, description="Quantidade, quando a pessoa citar."
    )

    @field_validator("quantity", mode="before")
    @classmethod
    def _coerce_quantity(cls, value: Any) -> Any:
        # O provedor devolve numero JSON; Decimal em modo strict recusa int/float.
        if isinstance(value, (int, float, str)):
            return Decimal(str(value))
        return value


class ListOpenInvoicesArgs(ToolArgs):
    customer_term: EntityTerm = Field(description="O cliente, em palavras.")


class GetLastOrderArgs(ToolArgs):
    customer_term: EntityTerm = Field(description="O cliente, em palavras.")
