"""Planos comerciais: o que foi vendido e o que o codigo cumpre.

Vender "ate 5 usuarios" com o sistema aceitando 50 e promessa que o codigo nao
cumpre. Estes testes existem para que a oferta e o comportamento nao se separem.
"""

from __future__ import annotations

import uuid

import pytest
from typer.testing import CliRunner

from orbi.cli.main import app
from orbi.core import plans
from orbi.db.models import Tenant
from orbi.db.session import admin_session

pytestmark = pytest.mark.integration

runner = CliRunner()


def _slug() -> str:
    return f"p{uuid.uuid4().hex[:10]}"


def _phone(n: int) -> str:
    return f"+5543{uuid.uuid4().int % 10**9:09d}{n}"


def _criar(slug: str, plano: str = "essencial") -> None:
    resultado = runner.invoke(
        app,
        ["tenant", "add", "--tenant", slug, "--name", f"Cliente {slug}",
         "--phone", _phone(0), "--plan", plano],
    )
    assert resultado.exit_code == 0, resultado.output


# --- a tabela de planos ---------------------------------------------------


def test_todo_plano_tem_teto_coerente_com_os_usuarios() -> None:
    """Teto que nao cabe no numero de usuarios e teto que nunca sera atingido."""
    for plano in plans.PLANOS.values():
        assert plano.consultas_por_usuario >= 500, plano.codigo
        assert plano.max_usuarios > 0


def test_plano_maior_nunca_e_mais_barato() -> None:
    escala = ["essencial", "time", "operacao"]
    precos = [plans.plano_de(c).mensal_brl for c in escala]
    assert precos == sorted(precos)
    tetos = [plans.plano_de(c).teto_mensal for c in escala]
    assert tetos == sorted(tetos)


def test_plano_desconhecido_falha_alto() -> None:
    """Teto errado vira prejuizo silencioso: melhor recusar."""
    with pytest.raises(plans.PlanoDesconhecido):
        plans.plano_de("banana")


# --- o plano define o teto ------------------------------------------------


def test_criar_cliente_aplica_o_teto_do_plano(database: None) -> None:
    slug = _slug()
    _criar(slug, "time")

    with admin_session() as session:
        tenant = session.query(Tenant).filter(Tenant.slug == slug).one()
    assert tenant.plan == "time"
    assert tenant.monthly_query_cap == plans.plano_de("time").teto_mensal


def test_plano_invalido_e_recusado_no_cadastro(database: None) -> None:
    resultado = runner.invoke(
        app,
        ["tenant", "add", "--tenant", _slug(), "--name", "X",
         "--phone", _phone(1), "--plan", "ilimitado"],
    )
    assert resultado.exit_code != 0
    assert "desconhecido" in resultado.output


# --- o limite de usuarios e cobrado --------------------------------------


def test_o_limite_de_usuarios_do_plano_e_cumprido(database: None) -> None:
    slug = _slug()
    _criar(slug, "essencial")  # ate 5
    limite = plans.plano_de("essencial").max_usuarios

    for i in range(limite):
        resultado = runner.invoke(
            app,
            ["user", "add", "--tenant", slug, "--phone", _phone(i + 10),
             "--role", "sales_rep", "--name", f"Vendedor {i}"],
        )
        assert resultado.exit_code == 0, resultado.output

    excedente = runner.invoke(
        app,
        ["user", "add", "--tenant", slug, "--phone", _phone(99),
         "--role", "sales_rep", "--name", "Um a mais"],
    )
    assert excedente.exit_code != 0
    assert "plano" in excedente.output.lower()


def test_subir_de_plano_libera_mais_usuarios(database: None) -> None:
    slug = _slug()
    _criar(slug, "essencial")
    for i in range(plans.plano_de("essencial").max_usuarios):
        runner.invoke(
            app,
            ["user", "add", "--tenant", slug, "--phone", _phone(i + 20),
             "--role", "sales_rep", "--name", f"V{i}"],
        )

    assert runner.invoke(app, ["tenant", "set-plan", "--tenant", slug, "--plan", "time"]).exit_code == 0

    depois = runner.invoke(
        app,
        ["user", "add", "--tenant", slug, "--phone", _phone(98),
         "--role", "sales_rep", "--name", "Sexto"],
    )
    assert depois.exit_code == 0, depois.output


def test_rebaixar_plano_com_usuarios_demais_e_recusado(database: None) -> None:
    """Rebaixar nao pode deixar cliente com mais gente do que o plano permite."""
    slug = _slug()
    _criar(slug, "time")
    for i in range(6):
        runner.invoke(
            app,
            ["user", "add", "--tenant", slug, "--phone", _phone(i + 30),
             "--role", "sales_rep", "--name", f"V{i}"],
        )

    resultado = runner.invoke(
        app, ["tenant", "set-plan", "--tenant", slug, "--plan", "essencial"]
    )
    assert resultado.exit_code != 0
    assert "desative" in resultado.output.lower()
