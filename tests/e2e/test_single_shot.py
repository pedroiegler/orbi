"""P8 — uma tool por turno, sem loop de agente (ORBI.md secao 6.3).

Esta e a proibicao mais facil de violar sem perceber e a mais cara quando
violada. Um laco no caminho da pergunta muda tres coisas de uma vez: o custo por
turno deixa de ser previsivel, a latencia deixa de caber no orcamento de 10 s, e
a auditoria deixa de ter uma linha por pergunta — passa a ter N, e ninguem sabe
qual delas respondeu.

O Orbi faz **no maximo duas** chamadas ao LLM: a pergunta e, quando o modelo nao
escolhe tool nenhuma, **uma** re-tentativa de esclarecimento. Nunca mais. E
executa **uma** operacao no ERP por turno.

O teste conta as chamadas de verdade, em vez de olhar o codigo atras de `while`:
um laco escrito com recursao passaria por uma leitura de codigo e nao passa aqui.
"""

from __future__ import annotations

from typing import Any

import pytest

from orbi.erp.port import ErpAdapter
from orbi.llm.port import LLMRequest, ToolCallEnvelope
from orbi.llm.providers.rule_based import RuleBasedProvider
from orbi.llm.router import LLMRouter

from .conftest import FINANCE, SALES, Harness

pytestmark = pytest.mark.e2e


class _ContandoLLM:
    """Envolve o provedor real e conta quantas vezes foi chamado."""

    def __init__(self) -> None:
        self._interno = RuleBasedProvider()
        self.chamadas: list[str] = []

    @property
    def name(self) -> str:
        return self._interno.name

    @property
    def manufacturer(self) -> str:
        return self._interno.manufacturer

    @property
    def model(self) -> str:
        return self._interno.model

    def complete(self, request: LLMRequest) -> ToolCallEnvelope:
        ultima = request.messages[-1] if request.messages else {}
        self.chamadas.append(str(ultima.get("content", "")))
        return self._interno.complete(request)


class _ContandoAdapter:
    """Envolve o adapter e registra cada operacao pedida ao ERP."""

    def __init__(self, interno: ErpAdapter) -> None:
        self._interno = interno
        self.operacoes: list[str] = []

    def __getattr__(self, nome: str) -> Any:
        atributo = getattr(self._interno, nome)
        if not callable(atributo) or nome.startswith("_"):
            return atributo

        def registrando(*args: Any, **kwargs: Any) -> Any:
            self.operacoes.append(nome)
            return atributo(*args, **kwargs)

        return registrando


def _instrumentar(harness: Harness) -> tuple[_ContandoLLM, list[_ContandoAdapter]]:
    contador = _ContandoLLM()
    harness.runtime._llm = LLMRouter(primary=contador)  # type: ignore[arg-type]

    adapters: list[_ContandoAdapter] = []
    from orbi.erp import connection as erp_connection

    original = erp_connection.build_for_tenant

    def envolvendo(*args: Any, **kwargs: Any) -> Any:
        tenant_erp = original(*args, **kwargs)
        espiao = _ContandoAdapter(tenant_erp.adapter)
        adapters.append(espiao)
        object.__setattr__(tenant_erp, "adapter", espiao)
        return tenant_erp

    erp_connection.build_for_tenant = envolvendo  # type: ignore[assignment]
    harness.runtime._restaurar = lambda: setattr(  # type: ignore[attr-defined]
        erp_connection, "build_for_tenant", original
    )
    return contador, adapters


@pytest.fixture
def instrumentado(harness: Harness):  # type: ignore[no-untyped-def]
    contador, adapters = _instrumentar(harness)
    yield harness, contador, adapters
    harness.runtime._restaurar()  # type: ignore[attr-defined]


def test_uma_pergunta_respondida_chama_o_llm_uma_vez(instrumentado) -> None:  # type: ignore[no-untyped-def]
    """Quando o modelo escolhe a tool de primeira, nao ha segunda chamada."""
    harness, llm, _ = instrumentado

    assert harness.ask("quanto tem de cimento?").status == "ok"

    assert len(llm.chamadas) == 1, llm.chamadas


def test_uma_pergunta_executa_no_maximo_uma_operacao_no_erp(instrumentado) -> None:  # type: ignore[no-untyped-def]
    """Duas operacoes por turno seriam um laco disfarcado de conveniencia."""
    harness, _, adapters = instrumentado

    assert harness.ask("quanto tem de cimento?").status == "ok"

    consultas = [
        operacao
        for espiao in adapters
        for operacao in espiao.operacoes
        if operacao.startswith(("get_", "list_", "check_"))
    ]
    assert len(consultas) <= 1, consultas


def test_pergunta_fora_de_escopo_para_na_segunda_chamada(instrumentado) -> None:  # type: ignore[no-untyped-def]
    """A re-tentativa de esclarecimento existe e e **uma**.

    Sem teto, "tentar de novo ate entender" e o formato mais natural de um laco
    infinito — e o que mais parece razoavel na hora de escrever.
    """
    harness, llm, adapters = instrumentado

    resposta = harness.ask("qual a capital da Franca?")

    assert resposta.status in {"out_of_scope", "not_found", "ambiguous"}
    assert len(llm.chamadas) <= 2, llm.chamadas
    assert [o for espiao in adapters for o in espiao.operacoes if o.startswith("get_")] == []


def test_o_teto_vale_para_todo_papel_e_toda_tool(instrumentado) -> None:  # type: ignore[no-untyped-def]
    harness, llm, _ = instrumentado

    for pergunta, quem in (
        ("quanto tem de cimento?", SALES),
        ("qual o preco do cimento?", SALES),
        ("a construtora silva tem titulos em aberto?", FINANCE),
    ):
        antes = len(llm.chamadas)
        harness.ask(pergunta, sender=quem)
        gastas = len(llm.chamadas) - antes
        assert gastas <= 2, f"{pergunta!r} gastou {gastas} chamadas"
