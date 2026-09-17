"""Tabela de precos dos provedores de LLM.

Fonte unica: antes disso, cada provedor carregava a propria tabela, e comparar
dois modelos exigia abrir dois arquivos.

Os precos sao **por milhao de tokens, em USD**, e mudam. A data de conferencia
esta em `CONFERIDO_EM`; o `orbi models bench` avisa quando a tabela esta velha,
porque preco desatualizado leva a decisao errada com aparencia de numero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from orbi.core.settings import Settings

CONFERIDO_EM = date(2026, 8, 29)
"""Ultima conferencia nas paginas oficiais dos tres fabricantes."""

VALIDADE_DIAS = 90
"""Acima disso a tabela e considerada velha e o benchmark avisa."""


@dataclass(frozen=True)
class Preco:
    """Preco por milhao de tokens, em USD."""

    entrada: float
    saida: float
    fabricante: str
    camada_gratuita: bool = False

    def custo(self, tokens_entrada: int, tokens_saida: int, cache_hit: float = 0.0) -> float:
        """Custo em USD de uma chamada.

        `cache_hit` e a fracao da entrada servida por prompt caching, cobrada a
        cerca de 10% do preco cheio. O prefixo estatico do Orbi e desenhado para
        isso (ORBI.md secao 6.4).
        """
        entrada_cheia = tokens_entrada * (1 - cache_hit) * self.entrada
        entrada_cacheada = tokens_entrada * cache_hit * self.entrada * 0.10
        return (entrada_cheia + entrada_cacheada + tokens_saida * self.saida) / 1_000_000


# Precos conferidos em 2026-08-29 nas paginas oficiais.
# Google: ai.google.dev/gemini-api/docs/pricing
# OpenAI: developers.openai.com/api/docs/pricing
# Anthropic: docs.claude.com/en/docs/about-claude/pricing
TABELA: dict[str, Preco] = {
    # --- Google -----------------------------------------------------------
    # A familia 2.5 aparece na pagina de precos mas a API devolve 404: foi
    # descontinuada. Preco de modelo que nao existe convida a escolher errado.
    "gemini-3.1-flash-lite": Preco(0.25, 1.50, "Google", camada_gratuita=True),
    "gemini-3.5-flash-lite": Preco(0.30, 2.50, "Google", camada_gratuita=True),
    "gemini-flash-lite-latest": Preco(0.30, 2.50, "Google", camada_gratuita=True),
    "gemini-3.6-flash": Preco(0.75, 3.75, "Google", camada_gratuita=True),
    "gemini-3.7-flash": Preco(0.75, 3.75, "Google", camada_gratuita=True),
    "gemini-flash-latest": Preco(0.75, 3.75, "Google", camada_gratuita=True),
    "gemini-3.1-pro-preview": Preco(2.00, 12.00, "Google", camada_gratuita=True),
    # --- OpenAI -----------------------------------------------------------
    "gpt-5-nano": Preco(0.05, 0.40, "OpenAI"),
    "gpt-4.1-nano": Preco(0.10, 0.40, "OpenAI"),
    "gpt-5-mini": Preco(0.25, 2.00, "OpenAI"),
    "gpt-4.1-mini": Preco(0.40, 1.60, "OpenAI"),
    "gpt-5": Preco(1.25, 10.00, "OpenAI"),
    "gpt-4.1": Preco(2.00, 8.00, "OpenAI"),
    # --- Anthropic --------------------------------------------------------
    "claude-haiku-4-5": Preco(1.00, 5.00, "Anthropic"),
    "claude-sonnet-5": Preco(3.00, 15.00, "Anthropic"),
    "claude-opus-5": Preco(5.00, 25.00, "Anthropic"),
    # --- local ------------------------------------------------------------
    "rule_based-1": Preco(0.0, 0.0, "local", camada_gratuita=True),
}

DESCONHECIDO = Preco(0.0, 0.0, "desconhecido")


def preco_de(modelo: str) -> Preco:
    """Preco do modelo. Modelo novo devolve zero, nunca levanta erro.

    Custo e informacao de apoio: um modelo fora da tabela nao pode derrubar um
    turno em producao.
    """
    return TABELA.get(modelo, DESCONHECIDO)


def custo_usd(modelo: str, tokens_entrada: int, tokens_saida: int, cache_hit: float = 0.0) -> float:
    return preco_de(modelo).custo(tokens_entrada, tokens_saida, cache_hit)


def tabela_velha(hoje: date | None = None) -> int:
    """Dias desde a ultima conferencia; acima de `VALIDADE_DIAS`, avise."""
    return ((hoje or date.today()) - CONFERIDO_EM).days


def modelos_sem_preco(settings: Settings) -> list[str]:
    """Modelos em uso que a tabela nao conhece.

    Modelo fora da tabela contabiliza **zero, em silencio** — de proposito, para
    que preco nunca derrube um turno. Mas o resumo diario somaria zeros com cara
    de medicao, e a decisao de plano sairia de um numero que nao existe. Entao o
    silencio precisa ser quebrado em algum lugar, e o lugar e a porta de entrada.

    So o primario e o fallback contam: modelo configurado e nao usado nao e
    problema de producao.
    """
    configurados = {
        "gemini": settings.gemini_model,
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
    }
    em_uso = [p for p in (settings.llm_primary, settings.llm_fallback) if p in configurados]
    return [
        f"{provedor.upper()}_MODEL='{configurados[provedor]}' fora da tabela de precos: "
        "o custo por turno seria gravado como zero"
        for provedor in sorted(set(em_uso))
        if configurados[provedor] not in TABELA
    ]


def problemas_de_producao(settings: Settings) -> list[str]:
    """Tudo que impede producao: as regras do core mais as de custo.

    Existe para que `doctor` e o startup da API cheguem a mesma conclusao. Duas
    listas divergentes seriam uma configuracao aprovada na CLI e recusada no ar.
    """
    return settings.validate_for_production() + modelos_sem_preco(settings)
