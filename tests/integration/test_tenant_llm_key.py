"""Chave de LLM por cliente (D-041).

Um cliente com projeto proprio no provedor ganha teto de gasto proprio e para
de dividir cota com os outros. Estes testes existem porque a customizacao mexe
com **segredo**, e segredo mal guardado e a falha mais cara que este produto
tem.

O que precisa continuar verdadeiro:

- a chave nunca fica em texto claro no banco;
- a chave de um cliente nao aparece para outro;
- a CLI nunca imprime a chave inteira;
- credencial invalida e recusada no **cadastro**, nao descoberta no turno;
- cliente sem chave propria continua na global, sem pagar nada pela existencia
  da customizacao.
"""

from __future__ import annotations

import uuid

import pytest
from typer.testing import CliRunner

from orbi.cli.main import app
from orbi.core.crypto import CredentialCipher
from orbi.core.errors import ConfigurationError
from orbi.db.models import TenantSettings
from orbi.db.session import admin_session
from orbi.llm import tenant_keys

pytestmark = pytest.mark.integration

runner = CliRunner()

CHAVE = "sk-proj-teste-nao-real-abcd1234"


def _slug() -> str:
    return f"k{uuid.uuid4().hex[:10]}"


def _phone() -> str:
    return f"+5543{uuid.uuid4().int % 10**9:09d}"


def _criar(slug: str) -> uuid.UUID:
    resultado = runner.invoke(
        app,
        ["tenant", "add", "--tenant", slug, "--name", f"Cliente {slug}",
         "--phone", _phone(), "--plan", "time"],
    )
    assert resultado.exit_code == 0, resultado.output
    from orbi.db.models import Tenant

    with admin_session() as session:
        return session.query(Tenant).filter_by(slug=slug).one().id


def _blob(tenant_id: uuid.UUID) -> bytes | None:
    with admin_session() as session:
        row = session.get(TenantSettings, tenant_id)
        assert row is not None
        return row.llm_credentials_encrypted


# --- o segredo ------------------------------------------------------------


def test_a_chave_nunca_fica_em_texto_claro_no_banco() -> None:
    slug = _slug()
    tenant_id = _criar(slug)
    runner.invoke(
        app,
        ["tenant", "set-llm", "--tenant", slug, "--provider", "openai",
         "--api-key", CHAVE, "--model", "gpt-5-mini"],
    )

    guardado = _blob(tenant_id)
    assert guardado is not None
    assert CHAVE.encode() not in guardado
    assert CredentialCipher().decrypt(guardado)["api_key"] == CHAVE


def test_a_cli_nunca_imprime_a_chave_inteira() -> None:
    """Chave num terminal vai para o historico do shell e para o scrollback."""
    slug = _slug()
    _criar(slug)
    runner.invoke(
        app,
        ["tenant", "set-llm", "--tenant", slug, "--provider", "openai",
         "--api-key", CHAVE, "--model", "gpt-5-mini"],
    )
    saida = runner.invoke(app, ["tenant", "show", "--tenant", slug]).output

    assert CHAVE not in saida
    assert "...1234" in saida


def test_a_chave_de_um_cliente_nao_aparece_no_outro() -> None:
    dono = _slug()
    vizinho = _slug()
    _criar(dono)
    vizinho_id = _criar(vizinho)
    runner.invoke(
        app,
        ["tenant", "set-llm", "--tenant", dono, "--provider", "openai",
         "--api-key", CHAVE, "--model", "gpt-5-mini"],
    )

    assert _blob(vizinho_id) is None
    assert CHAVE not in runner.invoke(app, ["tenant", "show", "--tenant", vizinho]).output


# --- as falhas param no cadastro -----------------------------------------


def test_provedor_sem_suporte_e_recusado_na_porta() -> None:
    slug = _slug()
    _criar(slug)
    resultado = runner.invoke(
        app,
        ["tenant", "set-llm", "--tenant", slug, "--provider", "azure",
         "--api-key", CHAVE, "--model", "gpt-5-mini"],
    )
    assert resultado.exit_code != 0
    assert "azure" in resultado.output


def test_modelo_fora_da_tabela_de_precos_avisa() -> None:
    """Grava, porque o modelo pode ser novo — mas nao em silencio: o custo
    daquele cliente sairia zero no resumo diario."""
    slug = _slug()
    _criar(slug)
    resultado = runner.invoke(
        app,
        ["tenant", "set-llm", "--tenant", slug, "--provider", "openai",
         "--api-key", CHAVE, "--model", "gpt-que-nao-existe"],
    )
    assert resultado.exit_code == 0
    assert "fora da tabela de precos" in resultado.output


def test_chave_vazia_e_modelo_vazio_sao_recusados() -> None:
    for provider, chave, modelo in (
        ("openai", "   ", "gpt-5-mini"),
        ("openai", CHAVE, "  "),
    ):
        with pytest.raises(ConfigurationError):
            tenant_keys.montar_credencial(provider, chave, modelo)


def test_credencial_ilegivel_nao_derruba_a_cli() -> None:
    """`ORBI_SECRET_KEY` trocada precisa aparecer como diagnostico, nao como
    stack trace no meio de uma implantacao."""
    assert "ILEGIVEL" in tenant_keys.descricao(b"nao-e-um-token-fernet")


# --- o padrao continua barato --------------------------------------------


def test_cliente_sem_chave_propria_usa_a_global() -> None:
    slug = _slug()
    tenant_id = _criar(slug)
    assert _blob(tenant_id) is None
    assert "global" in runner.invoke(app, ["tenant", "show", "--tenant", slug]).output


def test_clear_llm_devolve_o_cliente_a_global() -> None:
    slug = _slug()
    tenant_id = _criar(slug)
    runner.invoke(
        app,
        ["tenant", "set-llm", "--tenant", slug, "--provider", "openai",
         "--api-key", CHAVE, "--model", "gpt-5-mini"],
    )
    assert _blob(tenant_id) is not None

    assert runner.invoke(app, ["tenant", "clear-llm", "--tenant", slug]).exit_code == 0
    assert _blob(tenant_id) is None
