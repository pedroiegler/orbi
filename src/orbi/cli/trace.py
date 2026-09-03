"""`orbi trace ...` — achar o turno que o cliente citou (D-042).

Com `orbi tenant debug --on`, a resposta leva um codigo curto de seis
caracteres. O cliente reclama "o Orbi errou o estoque" e manda `T6XLNJ`; este
comando transforma esse codigo na linha da auditoria — pergunta, tool escolhida,
produto resolvido, decisao da policy, latencia por etapa e custo.

Sem ele o codigo era metade de uma promessa: saia na resposta e nao voltava para
lugar nenhum, e a primeira ligacao de suporte terminaria em SQL escrito na mao.

O codigo **nao e reversivel** de proposito — ele e uma derivacao do `trace_id`,
nao uma cifra. Entao a busca calcula o codigo das linhas do periodo e compara.
Por isso a janela e limitada: a auditoria e particionada por mes e varrer um ano
para achar uma reclamacao de ontem seria caro sem servir a ninguem.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import typer
from sqlalchemy import select

from orbi.cli.common import console, fail, resolve_tenant_id, table
from orbi.core.trace import short_code
from orbi.db.models import AuditLog, User
from orbi.db.session import tenant_session

app = typer.Typer(help="Achar um turno pelo codigo que o cliente citou.", no_args_is_help=True)

TenantOption = Annotated[str, typer.Option("--tenant", "-t", help="Slug do cliente.")]


@app.command("show")
def show(
    codigo: Annotated[str, typer.Argument(help="Codigo do rodape (ex: T6XLNJ) ou o trace_id.")],
    tenant: TenantOption,
    dias: Annotated[int, typer.Option("--dias", help="Janela de busca para tras.")] = 30,
) -> None:
    """Mostra o turno inteiro: o que foi perguntado e o que o Orbi fez."""
    tenant_id = resolve_tenant_id(tenant)
    procurado = codigo.strip().upper()
    desde = datetime.now(UTC) - timedelta(days=dias)

    with tenant_session(tenant_id) as session:
        linhas = session.scalars(
            select(AuditLog)
            .where(AuditLog.occurred_at >= desde)
            .order_by(AuditLog.occurred_at.desc())
        ).all()
        achado = next(
            (
                linha
                for linha in linhas
                if linha.trace_id == codigo.strip() or short_code(linha.trace_id) == procurado
            ),
            None,
        )
        if achado is None:
            fail(
                f"nenhum turno com '{codigo}' em '{tenant}' nos ultimos {dias} dias. "
                "Se for mais antigo, use --dias."
            )
        usuario = session.get(User, achado.user_id) if achado.user_id else None
        nome = usuario.name if usuario else "(desconhecido)"
        papel = usuario.role_code if usuario else "-"
        dados = _linhas(achado, nome, papel)

    view = table(f"Turno {short_code(achado.trace_id)} de {tenant}", ["campo", "valor"])
    for campo, valor in dados:
        view.add_row(campo, valor)
    console.print(view)
    console.print(f"\n[dim]trace_id completo: {achado.trace_id}[/dim]")


def _linhas(linha: AuditLog, nome: str, papel: str) -> list[tuple[str, str]]:
    """O que um atendimento precisa ver, na ordem em que a duvida aparece."""
    dados: list[tuple[str, str]] = [
        ("quando", linha.occurred_at.astimezone().strftime("%d/%m/%Y %H:%M:%S")),
        ("quem", f"{nome} ({papel})"),
        ("perguntou", linha.message_text or "(nao guardado)"),
        ("status", linha.status),
        ("tool", linha.tool_name or "(nenhuma)"),
    ]
    if linha.tool_args:
        dados.append(("argumentos", _json(linha.tool_args)))
    if linha.resolved_entity:
        dados.append(("entidade usada", _json(linha.resolved_entity)))
    dados.append(
        (
            "policy",
            linha.policy_decision + (f" · {linha.reason_code}" if linha.reason_code else ""),
        )
    )
    if linha.llm_provider:
        custo = f" · US$ {linha.cost_usd:.6f}" if linha.cost_usd is not None else " · custo zerado"
        dados.append(
            (
                "LLM",
                f"{linha.llm_provider}/{linha.llm_model} · "
                f"{linha.tokens_in or 0}→{linha.tokens_out or 0} tokens{custo}",
            )
        )
    if linha.latencies_ms:
        total = sum(int(v) for v in linha.latencies_ms.values())
        etapas = " · ".join(f"{k} {v}ms" for k, v in sorted(linha.latencies_ms.items()))
        dados.append(("latencia", f"{total} ms  ({etapas})"))
    if linha.erp_payload_hash:
        # O payload do ERP nunca e guardado; o hash prova que a resposta veio
        # daquele dado sem manter dado de cliente na auditoria (secao 11).
        dados.append(("hash do payload do ERP", linha.erp_payload_hash[:16] + "..."))
    if linha.feedback:
        dados.append(("feedback", linha.feedback))
    return dados


def _json(valor: dict[str, Any]) -> str:
    return ", ".join(f"{chave}={item}" for chave, item in sorted(valor.items()))
