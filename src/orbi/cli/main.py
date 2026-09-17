"""`orbi` — a interface de administracao do produto (ORBI.md secao 4).

Nao existe painel. A equipe opera por comando, e cada tela que alguem pedir
precisa antes justificar por que o comando nao basta (D-014).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from orbi.cli import catalog as catalog_commands
from orbi.cli import corrections as corrections_commands
from orbi.cli import erp as erp_commands
from orbi.cli import maintenance as maintenance_commands
from orbi.cli import role as role_commands
from orbi.cli import tenant as tenant_commands
from orbi.cli import trace as trace_commands
from orbi.cli import user as user_commands
from orbi.cli.common import console, fail, ok, resolve_tenant_id, table, warn
from orbi.core.crypto import CredentialCipher
from orbi.core.settings import get_settings
from orbi.db.base import app_engine, ping
from orbi.db.seed import sync_global_config
from orbi.db.session import admin_session, tenant_session

app = typer.Typer(
    help="Orbi — camada de linguagem natural sobre ERPs.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(tenant_commands.app, name="tenant")
app.add_typer(user_commands.app, name="user")
app.add_typer(role_commands.app, name="role")
app.add_typer(erp_commands.app, name="erp")
app.add_typer(catalog_commands.app, name="catalog")
app.add_typer(corrections_commands.app, name="corrections")
app.add_typer(maintenance_commands.app, name="maintenance")
app.add_typer(trace_commands.app, name="trace")


@app.command("doctor")
def doctor() -> None:
    """Verifica a configuracao antes de a operacao depender dela."""
    settings = get_settings()
    view = table("Diagnostico", ["item", "situacao"])

    view.add_row("ambiente", settings.env)
    try:
        view.add_row("banco", "ok" if ping(app_engine()) else "sem resposta")
    except Exception as exc:
        view.add_row("banco", f"falha: {type(exc).__name__}")

    try:
        CredentialCipher()
        view.add_row("ORBI_SECRET_KEY", "configurada")
    except Exception:
        view.add_row("ORBI_SECRET_KEY", "AUSENTE — credenciais de ERP nao funcionam")

    view.add_row(
        "LLM",
        f"{settings.llm_primary} → {settings.llm_fallback or 'sem fallback'}",
    )
    from orbi.llm.pricing import modelos_sem_preco, problemas_de_producao

    sem_preco = modelos_sem_preco(settings)
    view.add_row(
        "tabela de precos",
        "cobre os modelos em uso" if not sem_preco else "INCOMPLETA — custo sairia zero",
    )
    view.add_row("embeddings", settings.embedding_provider)
    view.add_row(
        "rendering",
        "template (correto)" if not settings.llm_rendering_enabled else "LLM (proibido)",
    )
    console.print(view)

    problems = problemas_de_producao(settings)
    if settings.is_production and problems:
        for problem in problems:
            warn(problem)
        fail("configuracao invalida para producao")
    if problems:
        console.print("\nPendencias para producao:")
        for problem in problems:
            warn(problem)
    else:
        ok("pronto para producao")


@app.command("init")
def init() -> None:
    """Sincroniza papeis, capabilities e tools a partir do codigo."""
    with admin_session() as session:
        report = sync_global_config(session)
    ok(f"configuracao global sincronizada: {report}")


@app.command("ask")
def ask(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    phone: Annotated[str, typer.Option("--phone", help="Numero de um usuario cadastrado.")],
    question: Annotated[str, typer.Argument(help="A pergunta, entre aspas.")],
) -> None:
    """Roda um turno real pela linha de comando.

    Mesmo caminho do WhatsApp — identidade, policy, resolucao, ERP, auditoria —
    apenas com outro transporte.
    """
    from orbi.llm.router import build_router
    from orbi.observability.ops_channel import build_notifier
    from orbi.runtime.pipeline import InboundMessage, OrbiRuntime

    tenant_id = resolve_tenant_id(tenant)
    with admin_session() as session:
        from orbi.db.models import Tenant

        row = session.get(Tenant, tenant_id)
        assert row is not None
        destination = row.channel_phone_number_id or row.channel_address or ""

    notifier = build_notifier()
    runtime = OrbiRuntime(build_router(), on_alert=notifier.as_callback())
    outcome = runtime.handle(
        InboundMessage(
            channel="whatsapp", from_address=phone, to_address=destination, text=question
        )
    )

    console.print()
    console.print(outcome.text or "[sem resposta]")
    console.print()
    console.print(
        f"[dim]status {outcome.status} · tool {outcome.tool_name or '-'} · "
        f"trace {outcome.trace_id} · "
        f"{sum(outcome.latencies_ms.values())} ms {outcome.latencies_ms}[/dim]"
    )


@app.command("onboard")
def onboard(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    users_csv: Annotated[
        Path | None, typer.Option("--users", help="CSV com name,phone,role,location")
    ] = None,
    acceptance: Annotated[
        Path | None,
        typer.Option("--acceptance", help="JSON com as 20 perguntas escritas pelo cliente."),
    ] = None,
    sample: Annotated[int, typer.Option("--sample", help="Produtos usados na calibracao.")] = 60,
) -> None:
    """Implantacao completa: um comando, nao um documento (ORBI.md secao 17).

    Valida credenciais → capabilities → sync do catalogo → usuarios →
    **calibra os limiares** → acceptance eval com as perguntas do cliente →
    relatorio de aprovacao.
    """
    from orbi.catalog.sync import CatalogSynchronizer
    from orbi.erp import connection as erp_connection
    from orbi.erp.errors import ErpError
    from orbi.evals.acceptance import run_acceptance
    from orbi.resolution import calibration
    from orbi.resolution.embeddings import build_embedder

    tenant_id = resolve_tenant_id(tenant)
    embedder = build_embedder()
    steps: list[tuple[str, str]] = []

    console.rule(f"[bold]Onboarding de {tenant}")

    # 1. credenciais e capabilities
    with tenant_session(tenant_id) as session:
        try:
            tenant_erp = erp_connection.build_for_tenant(
                session, tenant_id, refresh_capabilities=True
            )
            tenant_erp.adapter.check_connection()
        except ErpError as exc:
            fail(f"ERP recusou a conexao: {exc}")
        disabled = erp_connection.sync_tenant_tools(
            session, tenant_id, tenant_erp.capabilities
        )
    steps.append(("credenciais", f"ok — {tenant_erp.adapter_name}"))
    steps.append(
        (
            "capabilities",
            f"{len(tenant_erp.capabilities.supported_tools)} tools"
            + (f"; desligadas: {', '.join(disabled)}" if disabled else ""),
        )
    )

    # 2. catalogo
    with tenant_session(tenant_id) as session:
        report = CatalogSynchronizer(session, tenant_id, embedder).run(
            tenant_erp.adapter.iter_catalog(), mode="full"
        )
    if report.aborted:
        fail(f"sync abortado: {report.error}")
    steps.append(("catalogo", report.summary()))

    # 3. usuarios
    if users_csv is not None:
        user_commands.import_csv(tenant=tenant, path=users_csv)
        steps.append(("usuarios", f"importados de {users_csv.name}"))
    else:
        steps.append(("usuarios", "nenhum CSV informado — use `orbi user add`"))

    # 4. calibracao dos limiares com o catalogo real do cliente
    with tenant_session(tenant_id) as session:
        result = calibration.calibrate(session, tenant_id, embedder, sample=sample)
        if result is None:
            fail("catalogo vazio: nao ha como calibrar")
        calibration.apply(session, tenant_id, result, embedder)
    steps.append(("limiares", result.summary()))

    # 5. acceptance eval com as perguntas do proprio cliente
    if acceptance is not None:
        outcome = run_acceptance(tenant_id, acceptance)
        steps.append(("acceptance", outcome.summary()))
    else:
        outcome = None
        steps.append(("acceptance", "nenhum arquivo informado"))

    view = table(f"Relatorio de implantacao — {tenant}", ["etapa", "resultado"])
    for step, detail in steps:
        view.add_row(step, detail)
    console.print(view)

    if outcome is not None and not outcome.passed:
        fail("acceptance eval nao passou: reveja os casos antes do go-live", code=3)
    ok("cliente pronto para o go-live")


@app.command("evals")
def evals(
    layer: Annotated[str, typer.Option("--layer", help="L1..L5 ou 'all'.")] = "all",
    tenant: Annotated[
        str, typer.Option("--tenant", "-t", help="Necessario para L3 e L4.")
    ] = "",
    record: Annotated[
        bool, typer.Option("--record/--no-record", help="Grava em eval_runs.")
    ] = True,
) -> None:
    """Roda os evals em camadas. No CI, regressao bloqueia o merge."""
    from orbi.evals.runner import run_layers

    tenant_id = resolve_tenant_id(tenant) if tenant else None
    results = run_layers(layer, tenant_id=tenant_id, record=record)

    view = table("Evals", ["camada", "casos", "acertos", "score", "situacao"])
    failed = False
    for result in results:
        failed = failed or not result.passed
        view.add_row(
            result.layer,
            str(result.total),
            str(result.passed_cases),
            f"{result.score:.1%}",
            "ok" if result.passed else f"abaixo do minimo ({result.threshold:.0%})",
        )
    console.print(view)
    for result in results:
        for failure in result.failures[:5]:
            warn(f"{result.layer}: {failure}")
    if failed:
        raise typer.Exit(1)
    ok("todas as camadas passaram")


@app.command("bench")
def bench(
    provider: Annotated[
        str, typer.Option("--provider", help="Vazio usa o primario do .env.")
    ] = "",
    model: Annotated[str, typer.Option("--model", help="Vazio usa o do .env.")] = "",
    limite: Annotated[
        int, typer.Option("--limite", help="Casos a rodar. Camada gratuita tem cota.")
    ] = 0,
    cache: Annotated[
        float, typer.Option("--cache", help="Fracao da entrada servida por prompt caching.")
    ] = 0.0,
    record: Annotated[bool, typer.Option("--record/--no-record")] = True,
) -> None:
    """Bake-off: mede acerto, latencia e custo por acerto de um modelo.

    Os numeros decidem, nao a opiniao (ORBI.md secao 12). Rode uma vez por
    modelo candidato e compare a ultima coluna.
    """
    from orbi.evals.bench import rodar_bench
    from orbi.llm.pricing import CONFERIDO_EM, VALIDADE_DIAS, preco_de, tabela_velha
    from orbi.llm.router import LLMRouter, build_provider

    settings = get_settings()
    nome = provider or settings.llm_primary
    provedor = build_provider(nome, settings)
    if model:
        provedor.model = model  # type: ignore[misc]

    idade = tabela_velha()
    if idade > VALIDADE_DIAS:
        warn(
            f"tabela de precos conferida em {CONFERIDO_EM} ({idade} dias): "
            "reconfira antes de decidir por custo"
        )
    if preco_de(provedor.model).fabricante == "desconhecido":
        warn(f"modelo '{provedor.model}' fora da tabela de precos: o custo sai zerado")

    console.print(f"medindo [bold]{provedor.model}[/bold] ({nome})...")
    resultado = rodar_bench(
        LLMRouter(primary=provedor),
        limite=limite or None,
        cache_hit=cache,
        registrar=record,
    )

    entrada, saida = resultado.tokens_por_pergunta
    view = table(f"Bake-off — {resultado.modelo}", ["metrica", "valor"])
    view.add_row("casos", f"{resultado.total} ({resultado.falhas} falharam)")
    view.add_row("acerto de tool", f"{resultado.taxa_acerto:.0%}")
    view.add_row("acerto de argumentos", f"{resultado.taxa_args:.0%}")
    view.add_row("latencia p50", f"{resultado.p50_ms} ms")
    view.add_row("latencia p95", f"{resultado.p95_ms} ms")
    view.add_row("tokens por pergunta", f"{entrada} entrada · {saida} saida")
    view.add_row("US$ por 1.000 perguntas", f"{resultado.custo_mil_perguntas(cache):.4f}")
    view.add_row(
        "US$ por 1.000 ACERTOS",
        f"{resultado.custo_por_acerto(cache):.4f}  ← e esta que decide",
    )
    console.print(view)

    for divergencia in resultado.divergencias[:8]:
        warn(divergencia)
    if resultado.p95_ms > settings.deadline_total_ms:
        warn(
            f"p95 de {resultado.p95_ms} ms passa do orcamento do turno "
            f"({settings.deadline_total_ms} ms): este modelo perderia respostas"
        )


@app.command("discovery")
def discovery(
    tenant: Annotated[str, typer.Option("--tenant", "-t")],
    activate: Annotated[bool, typer.Option("--activate", help="Ativa a versao gerada.")] = False,
    show: Annotated[bool, typer.Option("--show", help="Mostra o modelo ativo.")] = False,
    export_evals: Annotated[
        Path | None,
        typer.Option("--export-evals", help="Grava as seed questions como dataset de eval."),
    ] = None,
) -> None:
    """Discovery offline: mapeia o ERP e gera o Knowledge Model versionado."""
    from orbi.discovery.run import (
        activate_version,
        describe_active,
        run_discovery,
        seed_questions_of,
    )

    tenant_id = resolve_tenant_id(tenant)
    if show:
        console.print(json.dumps(describe_active(tenant_id), indent=2, ensure_ascii=False))
        return

    if export_evals is not None:
        # As camadas L1 e L2 do eval nascem junto com o modelo (ORBI.md secao 9).
        questions = seed_questions_of(tenant_id)
        if not questions:
            fail("nenhum modelo ativo com seed questions: rode o discovery e ative")
        export_evals.write_text(
            json.dumps({"cases": questions}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ok(f"{len(questions)} seed questions exportadas para {export_evals}")
        return

    result = run_discovery(tenant_id)
    ok(f"Knowledge Model v{result.version} gerado ({result.summary()})")

    diff_view = table("Diff contra a versao ativa", ["mudanca"])
    for change in result.changes:
        diff_view.add_row(change)
    console.print(diff_view)

    for item in result.report.review_items:
        warn(f"revisar: {item}")
    if result.report.review_items:
        console.print(
            f"[dim]{result.report.review_ratio:.0%} dos campos precisam de olho humano[/dim]"
        )

    if not activate:
        console.print("Revise o diff acima e ative com `--activate`.")
        return

    if not result.report.approved:
        for blocker in result.report.blocking:
            warn(blocker)
        fail("o Validator reprovou esta versao: corrija antes de ativar", code=3)

    activate_version(tenant_id, result.version)
    ok(f"versao {result.version} ativa")


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host")] = "0.0.0.0",
    port: Annotated[int, typer.Option("--port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload/--no-reload")] = False,
) -> None:
    """Sobe o webhook do canal."""
    import uvicorn

    uvicorn.run(
        "orbi.api.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level=get_settings().log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    app()
