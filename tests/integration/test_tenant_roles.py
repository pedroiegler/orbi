"""Papeis proprios de cada cliente (D-040).

Cada empresa tem processo diferente: o "gerente" de um distribuidor de material
de construcao nao ve o mesmo que o "gerente" de uma farmacia. Estes testes
existem para que essa customizacao continue sendo **configuracao**, e para que
ela nao vire um caminho novo de vazamento.

O que precisa continuar verdadeiro, e por que:

- papel proprio de um cliente **nao existe** para outro — senao o nome do papel
  ja conta algo sobre a operacao do vizinho;
- permissao inventada e **recusada**, nunca concedida — erro de digitacao no
  cadastro nao pode virar acesso;
- papel desconhecido consulta **nada** — falha fechada, agora que a chave
  estrangeira global saiu de `users.role_code`;
- custo continua invisivel para quem nao tem `price:read_cost`, **qualquer que
  seja o nome do papel** — a Field Policy passou a ser indexada por permissao.
"""

from __future__ import annotations

import uuid

import pytest
from typer.testing import CliRunner

from orbi.cli.main import app
from orbi.db.models import Tenant
from orbi.db.session import admin_session, tenant_session
from orbi.policy import roles
from orbi.policy.field_policy import allowed_fields
from orbi.tools.registry import CAP_PRICE_READ_COST

pytestmark = pytest.mark.integration

runner = CliRunner()


def _slug() -> str:
    return f"r{uuid.uuid4().hex[:10]}"


def _phone() -> str:
    return f"+5543{uuid.uuid4().int % 10**9:09d}"


def _criar_tenant(slug: str) -> uuid.UUID:
    resultado = runner.invoke(
        app,
        [
            "tenant",
            "add",
            "--tenant",
            slug,
            "--name",
            f"Cliente {slug}",
            "--phone",
            _phone(),
            "--plan",
            "time",
        ],
    )
    assert resultado.exit_code == 0, resultado.output
    with admin_session() as session:
        tenant = session.query(Tenant).filter_by(slug=slug).one()
        return tenant.id


# --- o padrao continua valendo -------------------------------------------


def test_cliente_sem_customizacao_usa_os_tres_papeis_do_codigo() -> None:
    """A maioria dos clientes nao customiza nada, e nao paga por isso."""
    tenant_id = _criar_tenant(_slug())
    with tenant_session(tenant_id) as session:
        papeis = roles.papeis_efetivos(session, tenant_id)

    assert set(papeis) == {"sales_rep", "finance", "admin"}
    assert all(not papel.proprio for papel in papeis.values())


# --- o papel proprio ------------------------------------------------------


def test_papel_proprio_vale_para_quem_definiu() -> None:
    tenant_id = _criar_tenant(_slug())
    with tenant_session(tenant_id) as session:
        roles.definir_papel(
            session,
            tenant_id,
            "gerente",
            "Gerente Comercial",
            {"stock:read", "price:read", CAP_PRICE_READ_COST},
        )
        session.commit()

    with tenant_session(tenant_id) as session:
        caps = roles.capabilities_efetivas(session, tenant_id, "gerente")

    assert caps == frozenset({"stock:read", "price:read", CAP_PRICE_READ_COST})


def test_papel_proprio_de_um_cliente_nao_existe_no_outro() -> None:
    """Isolamento vale tambem para o **nome** do papel.

    Se 'gerente_regional' vazasse para o vizinho, o nome sozinho ja contaria
    como a operacao do outro cliente e organizada.
    """
    dono = _criar_tenant(_slug())
    vizinho = _criar_tenant(_slug())

    with tenant_session(dono) as session:
        roles.definir_papel(session, dono, "gerente_regional", "Gerente", {"stock:read"})
        session.commit()

    with tenant_session(vizinho) as session:
        papeis = roles.papeis_efetivos(session, vizinho)
        caps = roles.capabilities_efetivas(session, vizinho, "gerente_regional")

    assert "gerente_regional" not in papeis
    assert caps == frozenset()


def test_redefinir_substitui_a_lista_inteira_sem_heranca() -> None:
    """Heranca silenciosa e como uma permissao sobrevive a uma remocao."""
    tenant_id = _criar_tenant(_slug())
    with tenant_session(tenant_id) as session:
        roles.definir_papel(
            session, tenant_id, "sales_rep", "Vendedor", {"stock:read", "price:read"}
        )
        session.commit()
    with tenant_session(tenant_id) as session:
        roles.definir_papel(session, tenant_id, "sales_rep", "Vendedor", {"stock:read"})
        session.commit()

    with tenant_session(tenant_id) as session:
        assert roles.capabilities_efetivas(session, tenant_id, "sales_rep") == frozenset(
            {"stock:read"}
        )


def test_restaurar_padrao_devolve_o_papel_do_codigo() -> None:
    tenant_id = _criar_tenant(_slug())
    with tenant_session(tenant_id) as session:
        padrao = roles.capabilities_efetivas(session, tenant_id, "sales_rep")
        roles.definir_papel(session, tenant_id, "sales_rep", "Vendedor", {"stock:read"})
        session.commit()
    with tenant_session(tenant_id) as session:
        assert roles.restaurar_padrao(session, tenant_id, "sales_rep") is True
        session.commit()

    with tenant_session(tenant_id) as session:
        assert roles.capabilities_efetivas(session, tenant_id, "sales_rep") == padrao


# --- as falhas fecham, nao abrem -----------------------------------------


def test_permissao_inventada_e_recusada_no_cadastro() -> None:
    """Erro de digitacao precisa parar aqui, alto, e nao virar acesso."""
    tenant_id = _criar_tenant(_slug())
    with (
        tenant_session(tenant_id) as session,
        pytest.raises(ValueError, match="desconhecidas"),
    ):
        roles.definir_papel(session, tenant_id, "gerente", "Gerente", {"stock:read", "erp:write"})


def test_papel_desconhecido_nao_consulta_nada() -> None:
    """Sem a chave estrangeira global, esta e a protecao que restou — e basta.

    Um `role_code` invalido nao concede acesso: ele remove todo o acesso.
    """
    tenant_id = _criar_tenant(_slug())
    with tenant_session(tenant_id) as session:
        papel = roles.PapelEfetivo("fantasma", "Fantasma", frozenset(), proprio=False)
        assert (
            roles.capabilities_efetivas(session, tenant_id, "papel_que_ninguem_criou")
            == frozenset()
        )
        assert papel.tools() == ()


def test_papel_vazio_fica_sem_acesso_e_nao_com_acesso_total() -> None:
    tenant_id = _criar_tenant(_slug())
    with tenant_session(tenant_id) as session:
        papel = roles.definir_papel(session, tenant_id, "estagiario", "Estagiario", set())
        session.commit()

    assert papel.capabilities == frozenset()
    assert papel.tools() == ()


# --- a Field Policy nao depende do nome do papel -------------------------


def test_custo_depende_da_permissao_e_nao_do_nome_do_papel() -> None:
    """O papel pode se chamar `finance`, `diretoria` ou `bob`: o que libera
    custo e `price:read_cost`, e nada mais."""
    sem_custo = allowed_fields(frozenset({"price:read"}), "check_price")
    com_custo = allowed_fields(frozenset({"price:read", CAP_PRICE_READ_COST}), "check_price")

    assert "unit_cost" not in sem_custo and "margin_percent" not in sem_custo
    assert {"unit_cost", "margin_percent"} <= com_custo


def test_permissao_de_custo_nao_libera_campo_em_tool_que_nunca_teve_custo() -> None:
    """Extra de permissao e escopado por tool: tool nova comeca sem extra."""
    tudo = frozenset({"stock:read", "price:read", CAP_PRICE_READ_COST})
    assert allowed_fields(tudo, "check_stock") == allowed_fields(
        frozenset({"stock:read"}), "check_stock"
    )


# --- a CLI ----------------------------------------------------------------


def test_cli_cria_papel_e_aceita_usuario_nele() -> None:
    """O fluxo que o dono do produto vai rodar na implantacao do cliente."""
    slug = _slug()
    _criar_tenant(slug)

    criado = runner.invoke(
        app,
        [
            "role",
            "set",
            "--tenant",
            slug,
            "--role",
            "gerente",
            "--name",
            "Gerente Comercial",
            "--caps",
            "stock:read,price:read",
        ],
    )
    assert criado.exit_code == 0, criado.output

    usuario = runner.invoke(
        app,
        [
            "user",
            "add",
            "--tenant",
            slug,
            "--phone",
            _phone(),
            "--role",
            "gerente",
            "--name",
            "Ana",
        ],
    )
    assert usuario.exit_code == 0, usuario.output


def test_cli_recusa_usuario_em_papel_que_nao_existe() -> None:
    slug = _slug()
    _criar_tenant(slug)
    resultado = runner.invoke(
        app,
        [
            "user",
            "add",
            "--tenant",
            slug,
            "--phone",
            _phone(),
            "--role",
            "diretor",
            "--name",
            "Bob",
        ],
    )
    assert resultado.exit_code != 0
