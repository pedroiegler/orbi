"""Proibicoes escritas como teste (docs/ORBI-CONVENCOES.md).

Documento nao segura arquitetura; teste segura. Cada teste aqui corresponde a
uma proibicao numerada, e falhar aqui significa que uma decisao do projeto foi
desfeita sem passar pela decisao.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from orbi.tools.registry import FORBIDDEN_TOOL_PATTERNS, all_tools, tool_names

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "orbi"

CORE_PACKAGES = {"core", "db", "tools"}
"""Camadas de baixo: nao podem importar camadas de cima."""

UPPER_PACKAGES = {"runtime", "api", "cli", "channel", "erp", "llm", "render", "policy"}


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


# --- P6: a sessao e a unica porta -----------------------------------------


def test_only_the_session_module_touches_the_engine() -> None:
    """P6/D-005: o engine so pode ser usado por quem emite `SET LOCAL`."""
    allowed = {"db/base.py", "db/session.py", "api/app.py", "cli/main.py"}
    offenders: list[str] = []

    for path in _modules():
        relative = str(path.relative_to(SRC))
        if relative in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if "app_engine(" in source or "create_engine(" in source:
            offenders.append(relative)

    assert offenders == [], (
        f"estes modulos usam o engine direto em vez de `tenant_session`: {offenders}"
    )


def test_admin_session_is_not_used_in_the_request_path() -> None:
    """A sessao administrativa e de operacao, nunca do turno."""
    request_path = ["runtime/pipeline.py", "identity/resolver.py", "policy/engine.py"]
    for relative in request_path:
        source = (SRC / relative).read_text(encoding="utf-8")
        assert "admin_session" not in source, f"{relative} usa sessao administrativa"


# --- P10: nada de `if erp == x` no Core -----------------------------------


def test_core_never_imports_a_concrete_adapter() -> None:
    """P10: diferenca de ERP vive no Adapter + `capabilities()`."""
    offenders: list[str] = []
    for path in _modules():
        relative = str(path.relative_to(SRC))
        if relative.startswith(("erp/adapters/", "erp/registry.py")):
            continue
        for module in _imports(path):
            if module.startswith("orbi.erp.adapters"):
                offenders.append(f"{relative} → {module}")
    assert offenders == [], f"Core importando adapter concreto: {offenders}"


def test_no_module_branches_on_the_erp_name() -> None:
    """Comentario que cita a regra nao conta: o teste olha o codigo."""
    pattern = re.compile(r"""(?i)\b(if|elif)\b[^\n]*\b(adapter|erp)\b\s*==\s*['"]""")
    offenders: list[str] = []
    for path in _modules():
        relative = str(path.relative_to(SRC))
        if relative.startswith(("erp/adapters/", "erp/registry.py")):
            continue
        for line_number, line in enumerate(_code_lines(path), 1):
            if pattern.search(line):
                offenders.append(f"{relative}:{line_number}")
    assert offenders == [], f"condicional por ERP fora do adapter: {offenders}"


def test_no_module_branches_on_the_tenant() -> None:
    """A regra que sustenta a resposta a "pode customizar para um cliente?".

    Diferenca entre clientes vive em **linha de tabela**, nunca em ramo de
    codigo: papel proprio, tool ligada ou desligada, alias, limiar calibrado,
    chave de LLM. Tudo isso e dado.

    Um `if tenant == "construtora-silva"` seria o primeiro de dois produtos. O
    segundo cliente pede o oposto, o terceiro pede uma variacao, e a partir dai
    cada correcao de bug precisa ser pensada N vezes — uma por cliente — porque
    ninguem mais sabe quem esta em qual ramo. O custo nao aparece no dia em que
    a linha e escrita; ele aparece um ano depois, em cada mudanca.
    """
    pattern = re.compile(
        r"""(?i)\b(if|elif)\b[^\n]*\b(tenant|tenant_id|slug|cliente)\b\s*(==|!=)\s*['"]"""
    )
    offenders: list[str] = []
    for path in _modules():
        relative = str(path.relative_to(SRC))
        for line_number, line in enumerate(_code_lines(path), 1):
            if pattern.search(line):
                offenders.append(f"{relative}:{line_number}")
    assert offenders == [], (
        f"condicional por tenant no codigo: {offenders}. "
        "Diferenca entre clientes e configuracao, nao ramo."
    )


def _code_lines(path: Path) -> list[str]:
    """Linhas de codigo, sem docstring nem comentario."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            first = node.body[0]
            docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                docstring_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    return [
        "" if number in docstring_lines or line.strip().startswith("#") else line
        for number, line in enumerate(source.splitlines(), 1)
    ]


# --- dependencia entre camadas -------------------------------------------


def test_lower_layers_do_not_import_upper_layers() -> None:
    offenders: list[str] = []
    for path in _modules():
        relative = path.relative_to(SRC)
        package = relative.parts[0] if len(relative.parts) > 1 else ""
        if package not in CORE_PACKAGES:
            continue
        for module in _imports(path):
            if not module.startswith("orbi."):
                continue
            target = module.split(".")[1]
            if target in UPPER_PACKAGES:
                offenders.append(f"{relative} → {module}")
    assert offenders == [], f"camada de baixo importando camada de cima: {offenders}"


# --- P1: sem tool de busca ------------------------------------------------


def test_no_entity_search_tool_can_be_registered() -> None:
    for name in tool_names():
        for pattern in FORBIDDEN_TOOL_PATTERNS:
            assert pattern not in name, f"tool de busca detectada: {name}"


# --- P2: o LLM nao redige -------------------------------------------------


def test_llm_rendering_is_off_by_default() -> None:
    from orbi.core.settings import Settings

    assert Settings().llm_rendering_enabled is False


def test_the_renderer_does_not_import_the_llm() -> None:
    """P2: a resposta e template. O renderer nem conhece o provedor."""
    for path in (SRC / "render").rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith("orbi.llm"), f"{path.name} importa o LLM"


def test_the_erp_result_never_goes_back_to_the_llm() -> None:
    """Segunda barreira contra injecao: o dado do ERP nao volta ao modelo."""
    pipeline = (SRC / "runtime" / "pipeline.py").read_text(encoding="utf-8")
    after_erp = pipeline.split("_call_erp", 1)[-1]
    assert "self._llm.complete" not in after_erp


# --- P3: sem cache de estoque ---------------------------------------------


def test_no_cache_in_the_erp_path() -> None:
    """P3/D-009: melhor nao responder do que responder errado sobre quantidade."""
    offenders: list[str] = []
    for relative in ("erp/gateway.py", "runtime/pipeline.py"):
        source = (SRC / relative).read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), 1):
            lowered = line.lower()
            if "lru_cache" in lowered or "@cache" in lowered:
                offenders.append(f"{relative}:{number}")
    assert offenders == [], f"cache no caminho do ERP: {offenders}"


# --- P12: somente leitura -------------------------------------------------


def test_no_adapter_exposes_a_write_method() -> None:
    from orbi.erp.port import WRITE_METHOD_PREFIXES

    for path in (SRC / "erp").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                assert not node.name.startswith(WRITE_METHOD_PREFIXES), (
                    f"{path.name}.{node.name} parece escrita no ERP"
                )


# --- P9: PII mascarada ----------------------------------------------------


def test_every_llm_request_is_built_by_the_prompt_builder() -> None:
    """P9: o `PromptBuilder` e o unico lugar que monta `LLMRequest`."""
    offenders: list[str] = []
    for path in _modules():
        relative = str(path.relative_to(SRC))
        if relative in {"llm/prompt.py", "llm/port.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"\bLLMRequest\(", source):
            offenders.append(relative)
    assert offenders == [], f"LLMRequest montado fora do PromptBuilder: {offenders}"


# --- Tool Registry: os cinco artefatos ------------------------------------


def test_tool_spec_artifacts_are_in_sync() -> None:
    """Um teste de CI falha se algum artefato do `ToolSpec` dessincronizar."""
    from orbi.evals.runner import load_dataset
    from orbi.policy.field_policy import TOOL_FIELDS
    from orbi.render.renderer import TEMPLATES_DIR

    for spec in all_tools():
        assert spec.json_schema()["input_schema"]["properties"], f"{spec.name} sem argumentos"
        assert spec.args_model is not None
        assert (TEMPLATES_DIR / spec.template).exists(), f"{spec.name} sem template"
        assert load_dataset(spec.eval_fixture), f"{spec.name} sem fixture de eval"
        assert spec.name in TOOL_FIELDS, f"{spec.name} sem whitelist de campos"


def test_registered_tools_match_the_database_seed() -> None:
    from orbi.db.seed import ROLE_LABELS
    from orbi.tools.registry import ROLE_CAPABILITIES

    assert set(ROLE_LABELS) == set(ROLE_CAPABILITIES)


# --- segredos -------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        r"(?i)api[_-]?key\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]",
        r"(?i)password\s*=\s*['\"](?!change-me|orbi-dev|)[A-Za-z0-9_\-]{12,}['\"]",
        r"sk-[A-Za-z0-9]{20,}",
    ],
)
def test_no_secret_looking_literal_in_the_source(pattern: str) -> None:
    compiled = re.compile(pattern)
    offenders = [
        str(path.relative_to(SRC))
        for path in _modules()
        if compiled.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"literal com cara de segredo: {offenders}"


# --- a suite nunca pode destruir um banco que nao seja de teste ------------


def test_the_suite_refuses_a_non_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """A suite recria o schema: apontar para o banco errado tem que abortar."""
    from tests.conftest import guard_target_database

    monkeypatch.setenv(
        "ORBI_DATABASE_ADMIN_URL", "postgresql+psycopg://postgres:x@localhost:5433/orbi"
    )
    with pytest.raises(pytest.UsageError, match="nao e de teste"):
        guard_target_database()


def test_the_suite_accepts_the_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.conftest import guard_target_database

    monkeypatch.setenv(
        "ORBI_DATABASE_ADMIN_URL", "postgresql+psycopg://postgres:x@localhost:5433/orbi_test"
    )
    monkeypatch.setenv(
        "ORBI_DATABASE_URL", "postgresql+psycopg://orbi_app:x@localhost:5433/orbi_test"
    )
    guard_target_database()


# --- configuracao ---------------------------------------------------------


def test_env_example_documents_every_setting() -> None:
    """Variavel que existe no codigo e nao no exemplo e variavel que ninguem usa."""
    from orbi.core.settings import Settings

    exemplo = (SRC.parents[1] / ".env.example").read_text(encoding="utf-8")
    faltando = [
        campo.alias or nome
        for nome, campo in Settings.model_fields.items()
        if (campo.alias or nome) not in exemplo
    ]
    assert faltando == [], f"ausentes no .env.example: {faltando}"


def test_env_example_carries_no_real_secret() -> None:
    """O exemplo mostra a forma, nunca o valor."""
    exemplo = (SRC.parents[1] / ".env.example").read_text(encoding="utf-8")
    for linha in exemplo.splitlines():
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        valor = valor.split("#")[0].strip()
        if any(marca in chave for marca in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
            assert valor in {"", "change-me"}, f"{chave} tem valor no exemplo"


# --- as rotinas de operacao ----------------------------------------------

CRONTAB = ROOT / "docker" / "crontab"


def _crontab_lines() -> list[str]:
    """Linhas de comando do cron, sem comentario — comentario explica, nao roda."""
    return [
        linha
        for linha in CRONTAB.read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    ]


def _crontab_commands() -> list[str]:
    """Os comandos `orbi ...` que o cron executa."""
    return [
        trecho.strip()
        for linha in _crontab_lines()
        for trecho in re.findall(r"orbi ((?:[a-z-]+ )*[a-z-]+(?: --[a-z-]+)*)", linha)
    ]


def test_every_cron_command_exists_in_the_cli() -> None:
    """Cron que chama comando inexistente falha as 3h e ninguem ve.

    A versao anterior deste arquivo chamava `orbi tenant list --quiet`, que
    nunca existiu, e extraia o slug de uma tabela desenhada com `awk` — a borda
    virava item vazio e nome quebrado em duas linhas virava `|`. Funcionava o
    suficiente para parecer certo e falhava uma vez por cliente, toda noite.
    """
    from typer.main import get_command

    from orbi.cli.main import app

    raiz = get_command(app)
    faltando: list[str] = []
    for comando in _crontab_commands():
        partes = comando.split()
        atual: Any = raiz
        for parte in partes:
            if parte.startswith("--"):
                # `secondary_opts` e o lado negativo de um par como
                # `--dry-run/--apply`: as duas metades sao a mesma flag.
                nomes = {
                    opcao
                    for parametro in getattr(atual, "params", [])
                    for atributo in ("opts", "secondary_opts")
                    for opcao in getattr(parametro, atributo, [])
                }
                if parte not in nomes:
                    faltando.append(f"{comando} (flag {parte})")
                break
            proximo = getattr(atual, "commands", {}).get(parte)
            if proximo is None:
                faltando.append(f"{comando} (subcomando {parte})")
                break
            atual = proximo

    assert faltando == [], f"o cron chama o que a CLI nao tem: {faltando}"


def test_the_cron_does_not_parse_a_drawn_table() -> None:
    """Saida para humano e para maquina sao coisas diferentes.

    `--slugs` existe exatamente para isso. Voltar ao `awk` sobre a tabela
    reintroduziria uma falha por cliente por noite.
    """
    comandos = "\n".join(_crontab_lines())
    assert "awk" not in comandos, "o cron voltou a parsear tabela; use `--slugs`"
    assert "--slugs" in comandos


DOCS_COM_PONTEIRO = (
    ROOT / "docs" / "ORBI-CONVENCOES.md",
    ROOT / "docs" / "ORBI-ARQUITETURA.md",
)


def test_every_prohibition_points_at_a_test_that_exists() -> None:
    """A tabela de proibicoes e a unica coisa que as torna auditaveis.

    Oito dos quinze ponteiros apontavam para arquivos que nunca existiram
    (`test_pii.py`, `test_field_policy.py`, `test_import_lint.py`...). Os testes
    existiam — em outros arquivos — mas quem fosse conferir "o Orbi nao envia PII
    ao LLM" abriria o caminho citado, nao acharia nada, e concluiria com razao
    que a garantia era ficcao.

    Um ponteiro quebrado aqui e pior que ponteiro nenhum: ele parece prova.
    """
    arquivos_de_teste = {caminho.name: caminho for caminho in (ROOT / "tests").rglob("test_*.py")}
    quebrados: list[str] = []
    total = 0

    for documento in DOCS_COM_PONTEIRO:
        texto = documento.read_text(encoding="utf-8")
        # ORBI-CONVENCOES cita com pasta (`unit/test_x.py::t`); ORBI-ARQUITETURA,
        # so o nome do arquivo. As duas formas valem — o que nao vale e apontar
        # para o que nao existe.
        for arquivo, teste in re.findall(r"(test_[a-z_]+\.py)::(test_[a-z_]+)", texto):
            total += 1
            caminho = arquivos_de_teste.get(arquivo)
            if caminho is None:
                quebrados.append(f"{documento.name}: {arquivo} (arquivo)")
            elif f"def {teste}(" not in caminho.read_text(encoding="utf-8"):
                quebrados.append(f"{documento.name}: {arquivo}::{teste}")

    assert total > 20, "os documentos perderam os ponteiros de teste"
    assert quebrados == [], f"proibicao sem guarda: {quebrados}"


def test_o_filtro_de_ramo_continua_nos_dois_documentos() -> None:
    """D-045 e citado como filtro de decisao comercial em dois lugares.

    Se as seis condicoes sairem do ORBI-COMERCIAL, a decisao vira uma opiniao
    lembrada por alguem em vez de um criterio que se aplica.
    """
    comercial = (ROOT / "docs" / "ORBI-COMERCIAL.md").read_text(encoding="utf-8")
    decisoes = (ROOT / "docs" / "ORBI-DECISOES.md").read_text(encoding="utf-8")

    assert "O filtro para qualquer ramo novo" in comercial
    assert comercial.count("| 6 |") >= 1, "as seis condicoes sumiram do filtro"
    assert "D-045" in comercial and "D-045" in decisoes


# --- documentos concordam entre si e com o codigo -------------------------

DOCS = [*sorted((ROOT / "docs").glob("ORBI-*.md")), ROOT / "docs" / "ORBI.md", ROOT / "README.md"]

NUMEROS_APOSENTADOS = (
    # custo por cliente sem derivacao, substituido pela conta em ORBI-COMERCIAL
    "250–470",
    "120–250/mês",
    "1.200–2.500",
    # margens calculadas a partir daqueles custos
    "47–72%",
    "67–84%",
    # tarifa da Meta: valor discrepante de uma unica fonte, mantido so como cenario pessimista
    "US$ 0,0098",
    # afirmacoes que decisoes posteriores mudaram
    "(role, tool)",
    "(papel, tool)",
    "14 tabelas",
    "tenant_id` em tudo",
    "saem até setembro",
    "Adiados por escolha: **preço",
)


def test_nenhum_documento_carrega_numero_aposentado() -> None:
    """Um numero corrigido num documento sobreviveu em outro tres vezes nesta
    auditoria — primeiro no RESUMO, depois na especificacao, depois de novo.

    A varredura que pegava isso vivia no historico do shell. Agora vive aqui:
    quando um valor e substituido, ele entra nesta lista e o proximo `pytest`
    aponta cada arquivo que ainda o repete. ORBI-DECISOES fica de fora porque
    registrar o que era antes e o papel dele.
    """
    sobreviventes: list[str] = []
    for documento in DOCS:
        for linha in documento.read_text(encoding="utf-8").splitlines():
            # O valor discrepante pode aparecer como cenario pessimista, desde
            # que a propria linha diga isso — e a unica forma de cita-lo.
            if "pessimista" in linha or "discrepante" in linha:
                continue
            for numero in NUMEROS_APOSENTADOS:
                if numero in linha:
                    sobreviventes.append(f"{documento.name}: {numero!r}")
    assert sobreviventes == [], f"numero aposentado ainda em uso: {sobreviventes}"


def test_a_tabela_de_planos_dos_documentos_e_a_do_codigo() -> None:
    """Vender 'ate 5 usuarios' com o codigo aceitando 15 e promessa que o codigo
    nao cumpre; o inverso e deixar dinheiro na mesa. Os dois documentos que
    mostram a tabela ao cliente precisam repetir `core/plans.py` exatamente."""
    from orbi.core.plans import PLANOS

    nomes = {"Operacao": "Operação"}
    for documento in ("ORBI-COMERCIAL.md", "ORBI-TENANT.md"):
        texto = (ROOT / "docs" / documento).read_text(encoding="utf-8")
        for plano in PLANOS.values():
            if plano.codigo == "fundador" and documento == "ORBI-TENANT.md":
                continue  # o TENANT lista so os planos de tabela
            teto = f"{plano.teto_mensal:,}".replace(",", ".")
            preco = f"{int(plano.mensal_brl):,}".replace(",", ".")
            padrao = (
                r"\|\s*\*{0,2}"
                + nomes.get(plano.nome, plano.nome)
                + r"\*{0,2}\s*\|\s*até "
                + str(plano.max_usuarios)
                + r"\s*\|\s*"
                + re.escape(teto)
                + r"\s*\|\s*\*?R\$ "
                + re.escape(preco)
            )
            assert re.search(padrao, texto), (
                f"{documento}: plano {plano.nome} diverge de core/plans.py "
                f"(ate {plano.max_usuarios} / {teto} / R$ {preco})"
            )
