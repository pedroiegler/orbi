"""O papel proprio do cliente muda o turno inteiro (D-040).

Os testes de unidade provam que a tabela de permissoes esta certa. Estes provam
o que o cliente realmente compra: **mesma pessoa, mesma pergunta, resposta
diferente** — e a unica coisa que mudou foi configuracao, nao deploy.

E provam tambem o lado que ninguem lembra de testar: tirar permissao precisa
tirar acesso de verdade, no turno, e nao so na tela de administracao.
"""

from __future__ import annotations

import pytest

from orbi.db.session import tenant_session
from orbi.policy import roles
from orbi.tools.registry import CAP_PRICE_READ_COST

from .conftest import FINANCE, SALES, Harness

pytestmark = pytest.mark.e2e


def _definir(harness: Harness, codigo: str, capabilities: set[str]) -> None:
    with tenant_session(harness.tenant_id) as session:
        roles.definir_papel(session, harness.tenant_id, codigo, codigo.title(), capabilities)
        session.commit()


def test_cliente_pode_dar_custo_ao_proprio_vendedor(harness: Harness) -> None:
    """Ha cliente onde o vendedor negocia margem. Isso e decisao dele."""
    assert "Custo" not in harness.ask("qual o preco do cimento?").text

    _definir(harness, "sales_rep", {"stock:read", "price:read", CAP_PRICE_READ_COST})

    assert "Custo" in harness.ask("qual o preco do cimento?").text


def test_cliente_pode_tirar_permissao_e_o_turno_recusa(harness: Harness) -> None:
    """Tirar da tela e facil; o que importa e a consulta parar."""
    assert harness.ask("qual o preco do cimento?").status == "ok"

    _definir(harness, "sales_rep", {"stock:read"})

    recusado = harness.ask("qual o preco do cimento?")
    assert recusado.status in {"denied", "out_of_scope"}
    assert "R$" not in recusado.text


def test_papel_reduzido_nao_derruba_o_que_sobrou(harness: Harness) -> None:
    """Reduzir permissao nao pode virar cliente sem sistema."""
    _definir(harness, "sales_rep", {"stock:read"})

    estoque = harness.ask("quanto tem de cimento?")
    assert estoque.status == "ok"


def test_papel_vazio_nao_vira_acesso_total(harness: Harness) -> None:
    """A falha aqui e silenciosa e cara: conjunto vazio interpretado como
    'sem restricao' liberaria tudo para quem deveria ver nada."""
    _definir(harness, "sales_rep", set())

    for pergunta in ("quanto tem de cimento?", "qual o preco do cimento?"):
        recusado = harness.ask(pergunta)
        assert recusado.status in {"denied", "out_of_scope"}, pergunta


def test_papel_proprio_de_um_cliente_nao_altera_o_outro(harness: Harness) -> None:
    """O financeiro do proprio tenant continua intacto quando o vendedor muda."""
    _definir(harness, "sales_rep", set())

    assert harness.ask("qual o preco do cimento?", sender=FINANCE).status == "ok"


def test_restaurar_padrao_devolve_o_comportamento_original(harness: Harness) -> None:
    """Voltar atras precisa ser tao barato quanto customizar."""
    _definir(harness, "sales_rep", set())
    assert harness.ask("quanto tem de cimento?", sender=SALES).status != "ok"

    with tenant_session(harness.tenant_id) as session:
        roles.restaurar_padrao(session, harness.tenant_id, "sales_rep")
        session.commit()

    assert harness.ask("quanto tem de cimento?", sender=SALES).status == "ok"
