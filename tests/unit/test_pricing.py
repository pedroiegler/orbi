"""Tabela de precos: fonte unica, conferida e sem modelo fantasma."""

from __future__ import annotations

from datetime import date

import pytest

from orbi.core.settings import Settings
from orbi.llm import pricing
from orbi.llm.pricing import (
    CONFERIDO_EM,
    DESCONHECIDO,
    TABELA,
    VALIDADE_DIAS,
    custo_usd,
    preco_de,
    tabela_velha,
)


def test_custo_usa_a_medicao_real_do_orbi() -> None:
    """889 tokens de entrada e 24 de saida foi o medido contra o Gemini real."""
    custo = custo_usd("gemini-3.5-flash-lite", 889, 24)
    assert 0.0002 < custo < 0.0004  # ~US$ 0,33 por mil perguntas


def test_prompt_caching_derruba_o_custo_da_entrada() -> None:
    """O prefixo estatico existe para isso (ORBI.md secao 6.4)."""
    sem_cache = custo_usd("gemini-3.5-flash-lite", 889, 24)
    com_cache = custo_usd("gemini-3.5-flash-lite", 889, 24, cache_hit=0.85)
    assert com_cache < sem_cache * 0.45


def test_modelo_fora_da_tabela_nao_derruba_o_turno() -> None:
    """Custo e informacao de apoio: modelo novo devolve zero, nunca erro."""
    assert preco_de("modelo-que-nao-existe") is DESCONHECIDO
    assert custo_usd("modelo-que-nao-existe", 1000, 100) == 0.0


def test_saida_sempre_custa_mais_que_entrada() -> None:
    """Invariante de todos os fabricantes; quebrar indica preco digitado errado."""
    for modelo, preco in TABELA.items():
        if preco.fabricante == "local":
            continue
        assert preco.saida > preco.entrada, modelo


def test_nenhum_modelo_descontinuado_na_tabela() -> None:
    """A familia 2.5 do Gemini devolve 404: manter o preco convida a escolher errado."""
    assert not [m for m in TABELA if m.startswith("gemini-2.5")]


def test_tabela_avisa_quando_envelhece() -> None:
    assert tabela_velha(CONFERIDO_EM) == 0
    assert tabela_velha(date(2027, 1, 1)) > VALIDADE_DIAS


@pytest.mark.parametrize("fabricante", ["Google", "OpenAI", "Anthropic"])
def test_os_tres_fabricantes_estao_representados(fabricante: str) -> None:
    """O failover exige fabricantes diferentes: a comparacao precisa dos tres."""
    assert any(p.fabricante == fabricante for p in TABELA.values())


def test_modelo_fora_da_tabela_e_pendencia_de_producao() -> None:
    """Custo zero em silencio e pior que custo ausente.

    O turno continua funcionando de proposito — preco nao pode derrubar
    producao. Mas o resumo diario somaria zeros com cara de medicao, e a decisao
    de plano sairia de um numero que nao existe. O aviso vai na porta.
    """
    settings = Settings(
        ORBI_ENV="production", ORBI_LLM_PRIMARY="openai", OPENAI_MODEL="gpt-inventado"
    )
    problemas = " ".join(pricing.problemas_de_producao(settings))
    assert "gpt-inventado" in problemas
    assert "zero" in problemas


def test_modelo_conhecido_nao_gera_pendencia() -> None:
    settings = Settings(
        ORBI_ENV="production", ORBI_LLM_PRIMARY="openai", OPENAI_MODEL="gpt-5-mini"
    )
    assert pricing.modelos_sem_preco(settings) == []


def test_modelo_de_provedor_que_nao_esta_em_uso_nao_e_cobrado() -> None:
    """So o primario e o fallback contam: modelo configurado e nao usado nao e
    problema de producao."""
    settings = Settings(
        ORBI_ENV="production",
        ORBI_LLM_PRIMARY="gemini",
        ANTHROPIC_MODEL="claude-que-nao-existe",
    )
    assert pricing.modelos_sem_preco(settings) == []


def test_problemas_de_producao_inclui_as_regras_do_core() -> None:
    """Doctor e o startup da API precisam chegar a mesma conclusao: duas listas
    divergentes seriam configuracao aprovada na CLI e recusada no ar."""
    settings = Settings(ORBI_ENV="production", ORBI_LLM_PRIMARY="rule_based")
    problemas = pricing.problemas_de_producao(settings)
    assert set(settings.validate_for_production()) <= set(problemas)
