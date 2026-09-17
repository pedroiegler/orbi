"""`orbi role ...` — papeis e permissoes por cliente (D-040).

Cada empresa tem processo diferente. Aqui um cliente ganha papel proprio sem que
ninguem toque no codigo — que e o que o ORBI.md sempre prometeu.
"""

from __future__ import annotations

from typing import Annotated

import typer

from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.db.session import tenant_session
from orbi.policy import roles
from orbi.tools.registry import ALL_CAPABILITIES

app = typer.Typer(help="Papeis e permissoes de cada cliente.", no_args_is_help=True)

TenantOption = Annotated[str, typer.Option("--tenant", "-t")]

DESCRICOES: dict[str, str] = {
    "stock:read": "consultar saldo de estoque",
    "price:read": "consultar preco de venda",
    "price:read_cost": "ver custo e margem",
    "invoice:read": "consultar titulos em aberto",
    "customer:read": "consultar dados comerciais do cliente",
}


@app.command("capabilities")
def capabilities() -> None:
    """Lista as permissoes que podem compor um papel."""
    view = table("Permissoes disponiveis", ["permissao", "o que libera"])
    for codigo in sorted(ALL_CAPABILITIES):
        view.add_row(codigo, DESCRICOES.get(codigo, ""))
    console.print(view)
    console.print(
        "\n[dim]Combine-as em papeis com "
        "`orbi role set --tenant x --role gerente --caps stock:read,price:read`[/dim]"
    )


@app.command("list")
def list_roles(tenant: TenantOption) -> None:
    """Mostra os papeis validos para o cliente e de onde cada um vem."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        papeis = roles.papeis_efetivos(session, tenant_id)

    view = table(f"Papeis de {tenant}", ["codigo", "nome", "origem", "permissoes", "consulta"])
    for papel in sorted(papeis.values(), key=lambda p: p.codigo):
        tools = ", ".join(spec.name for spec in papel.tools()) or "(nada)"
        view.add_row(
            papel.codigo,
            papel.nome,
            "proprio" if papel.proprio else "padrao",
            ", ".join(sorted(papel.capabilities)) or "(nenhuma)",
            tools,
        )
    console.print(view)


@app.command("set")
def set_role(
    tenant: TenantOption,
    role: Annotated[str, typer.Option("--role", help="Codigo do papel, ex: gerente")],
    caps: Annotated[
        str, typer.Option("--caps", help="Permissoes separadas por virgula. Vazio = sem acesso.")
    ],
    name: Annotated[str, typer.Option("--name", help="Nome que a equipe do cliente usa.")] = "",
) -> None:
    """Cria ou redefine um papel do cliente.

    A lista de permissoes **substitui** a anterior: nao ha heranca do padrao.
    Heranca silenciosa e como uma permissao aparece onde ninguem esperava.
    """
    tenant_id = resolve_tenant_id(tenant)
    desejadas = {c.strip() for c in caps.split(",") if c.strip()}

    with tenant_session(tenant_id) as session:
        try:
            papel = roles.definir_papel(
                session, tenant_id, role, name or role.replace("_", " ").title(), desejadas
            )
        except ValueError as exc:
            fail(str(exc))

    ok(f"papel '{papel.codigo}' definido para '{tenant}': {papel.descricao()}")
    if not papel.capabilities:
        warn("papel sem nenhuma permissao: quem tiver esse papel nao consulta nada")
    console.print("  consulta: " + (", ".join(spec.name for spec in papel.tools()) or "(nada)"))


@app.command("reset")
def reset_role(
    tenant: TenantOption,
    role: Annotated[str, typer.Option("--role")],
) -> None:
    """Remove a customizacao e devolve o papel ao padrao do codigo."""
    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        removido = roles.restaurar_padrao(session, tenant_id, role)
    if not removido:
        warn(f"'{role}' nao tinha customizacao em '{tenant}': nada a fazer")
        return
    ok(f"papel '{role}' voltou ao padrao em '{tenant}'")


@app.command("show")
def show_role(
    tenant: TenantOption,
    role: Annotated[str, typer.Option("--role")],
) -> None:
    """Detalha um papel: o que ele consulta e o que ele nao ve."""
    from orbi.policy.field_policy import TOOL_FIELDS, allowed_fields

    tenant_id = resolve_tenant_id(tenant)
    with tenant_session(tenant_id) as session:
        papeis = roles.papeis_efetivos(session, tenant_id)

    papel = papeis.get(role)
    if papel is None:
        fail(f"papel '{role}' nao existe em '{tenant}'. Veja `orbi role list`.")

    view = table(f"{papel.nome} ({papel.codigo}) em {tenant}", ["tool", "acesso", "campos ocultos"])
    for tool in sorted(TOOL_FIELDS):
        from orbi.tools.registry import get_tool

        spec = get_tool(tool)
        pode = spec.required_capabilities <= papel.capabilities
        if not pode:
            view.add_row(tool, "NAO", "—")
            continue
        # O oculto e a diferenca entre o que a tool pode mostrar a alguem com
        # todas as permissoes e o que este papel ve. Assim `check_stock` nao
        # aparece "escondendo" custo, que ela nunca teve.
        visiveis = allowed_fields(papel.capabilities, tool)
        tudo = allowed_fields(frozenset(ALL_CAPABILITIES), tool)
        ocultos = sorted(tudo - visiveis)
        view.add_row(tool, "sim", ", ".join(ocultos) or "nenhum")
    console.print(view)
