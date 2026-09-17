"""Camada de LLM: PII, prompt, provedores e failover."""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from orbi.core.deadline import Deadline
from orbi.core.errors import ConfigurationError, LLMError, LLMTimeout, LLMUnavailable
from orbi.llm.pii import PIIRedactor
from orbi.llm.port import LLMRequest, ToolCallEnvelope
from orbi.llm.prompt import PromptBuilder, PromptContext, prompt_version
from orbi.llm.providers.rule_based import RuleBasedProvider, is_anaphora
from orbi.llm.router import LLMRouter
from orbi.tools.registry import all_tools, tools_for_role

# --- PIIRedactor ---------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("meu cpf e 123.456.789-00", "meu cpf e [cpf]"),
        ("cnpj 12.345.678/0001-90 da silva", "cnpj [cnpj] da silva"),
        ("manda no 43 99123-4567", "manda no [telefone]"),
        ("email: joao.silva@empresa.com.br", "email: [email]"),
    ],
)
def test_redactor_masks_personal_data(original: str, expected: str) -> None:
    assert PIIRedactor().redact(original) == expected


def test_redactor_keeps_the_question_intact() -> None:
    """Mascarar nao custa capacidade: a pergunta continua inteira."""
    question = "quanto tem de tubo pvc 100 na filial cambe?"
    assert PIIRedactor().redact(question) == question


def test_redactor_reports_what_it_masked() -> None:
    report = PIIRedactor().analyze("cpf 123.456.789-00 e fone 43 99123-4567")
    assert report.counts["cpf"] == 1
    assert report.counts["phone"] == 1
    assert report.redacted_anything


# --- PromptBuilder -------------------------------------------------------


def _context(role: str = "sales_rep") -> PromptContext:
    return PromptContext(role=role, now=datetime(2026, 8, 24, 10, 30))


def test_user_text_never_enters_the_system_prompt() -> None:
    request = PromptBuilder().build(
        tenant_name="Distribuidora Teste",
        question="quanto tem de tubo pvc 100?",
        tools=all_tools(),
        context=_context(),
    )
    assert "tubo pvc" not in request.system_prefix
    assert "tubo pvc" not in request.system_suffix
    assert request.messages[-1]["content"] == "quanto tem de tubo pvc 100?"


def test_static_prefix_is_identical_between_requests() -> None:
    """E o que faz o prompt caching valer alguma coisa."""
    builder = PromptBuilder()
    first = builder.build(
        tenant_name="Distribuidora Teste",
        question="primeira",
        tools=all_tools(),
        context=PromptContext(role="sales_rep", now=datetime(2026, 8, 24, 10, 0)),
    )
    second = builder.build(
        tenant_name="Distribuidora Teste",
        question="segunda",
        tools=all_tools(),
        context=PromptContext(role="sales_rep", now=datetime(2026, 8, 24, 18, 0)),
    )
    assert first.system_prefix == second.system_prefix
    assert first.system_suffix != second.system_suffix


def test_pii_is_masked_before_the_request_is_built() -> None:
    request = PromptBuilder().build(
        tenant_name="T",
        question="o cliente de cpf 123.456.789-00 tem titulo em aberto?",
        tools=all_tools(),
        context=_context("finance"),
    )
    assert "123.456.789-00" not in request.messages[-1]["content"]
    assert "[cpf]" in request.messages[-1]["content"]


def test_tools_are_filtered_by_role_in_the_prompt() -> None:
    request = PromptBuilder().build(
        tenant_name="T",
        question="qualquer",
        tools=tools_for_role("sales_rep"),
        context=_context(),
    )
    names = {tool["name"] for tool in request.tools}
    assert "list_open_invoices" not in names


def test_only_the_last_five_turns_are_sent() -> None:
    context = _context()
    context.recent_turns = [{"role": "user", "text": f"pergunta {i}"} for i in range(10)]
    request = PromptBuilder().build(
        tenant_name="T", question="atual", tools=all_tools(), context=context
    )
    assert len(request.messages) == 6  # 5 turnos + a pergunta atual


def test_slots_go_to_the_dynamic_suffix() -> None:
    context = _context()
    context.slots = {"ultimo_produto": "Tubo PVC 100"}
    request = PromptBuilder().build(
        tenant_name="T", question="e o preco dele?", tools=all_tools(), context=context
    )
    assert "Tubo PVC 100" in request.system_suffix


def test_prompt_version_changes_with_the_tool_schemas() -> None:
    full = prompt_version(all_tools())
    partial = prompt_version(tools_for_role("sales_rep"))
    assert full != partial
    assert full.startswith("2026-")


# --- Provedor por regras -------------------------------------------------


def _ask(question: str, role: str = "admin") -> ToolCallEnvelope:
    request = PromptBuilder().build(
        tenant_name="T",
        question=question,
        tools=tools_for_role(role),
        context=_context(role),
    )
    return RuleBasedProvider().complete(request)


@pytest.mark.parametrize(
    ("question", "tool"),
    [
        ("quanto tem de tubo pvc 100?", "check_stock"),
        ("tem cimento no estoque?", "check_stock"),
        ("qual o preco do tubo pvc 100?", "check_price"),
        ("quanto custa o cimento?", "check_price"),
        ("a construtora silva tem titulos em aberto?", "list_open_invoices"),
        ("quanto a maratex esta devendo?", "list_open_invoices"),
        ("qual o ultimo pedido da construtora silva?", "get_last_order"),
    ],
)
def test_rule_based_selects_the_right_tool(question: str, tool: str) -> None:
    envelope = _ask(question)
    assert envelope.has_tool_call
    assert envelope.tool_name == tool


def test_rule_based_extracts_the_product_term() -> None:
    envelope = _ask("quanto tem de tubo pvc 100?")
    assert envelope.tool_args["product_term"] == "tubo pvc 100"


def test_rule_based_extracts_location() -> None:
    envelope = _ask("quanto tem de cimento na filial cambe?")
    assert envelope.tool_args["product_term"] == "cimento"
    assert envelope.tool_args["location_term"] == "filial cambe"


def test_rule_based_extracts_customer_and_quantity_for_price() -> None:
    envelope = _ask("quanto fica 50 sacos de cimento para a construtora silva?")
    assert envelope.tool_name == "check_price"
    assert envelope.tool_args["quantity"] == 50
    assert "cimento" in envelope.tool_args["product_term"]
    assert "construtora silva" in envelope.tool_args["customer_term"]


def test_rule_based_never_offers_a_tool_the_role_cannot_use() -> None:
    envelope = _ask("a construtora silva tem titulos em aberto?", role="sales_rep")
    assert not envelope.has_tool_call


def test_rule_based_answers_out_of_scope_for_unrelated_questions() -> None:
    envelope = _ask("qual a previsao do tempo amanha?")
    assert envelope.finish_reason == "text"
    assert envelope.text == "FORA_DE_ESCOPO"


def test_anaphora_is_detected_for_slot_filling() -> None:
    assert is_anaphora("dele")
    assert is_anaphora("Desse")
    assert not is_anaphora("cimento")


# --- Router --------------------------------------------------------------


class _FakeProvider:
    def __init__(self, name: str, manufacturer: str, error: Exception | None = None) -> None:
        self.name = name
        self.manufacturer = manufacturer
        self.model = f"{name}-1"
        self._error = error
        self.calls = 0

    def complete(self, request: LLMRequest) -> ToolCallEnvelope:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ToolCallEnvelope(
            finish_reason="tool_call",
            tool_name="check_stock",
            tool_args={"product_term": "cimento"},
            provider=self.name,
            model=self.model,
        )


def _request() -> LLMRequest:
    return PromptBuilder().build(
        tenant_name="T", question="quanto tem de cimento?", tools=all_tools(), context=_context()
    )


def test_router_uses_the_primary_when_it_works() -> None:
    primary = _FakeProvider("anthropic", "anthropic")
    fallback = _FakeProvider("openai", "openai")
    envelope = LLMRouter(primary, fallback).complete(_request(), Deadline(total_ms=5_000))
    assert envelope.provider == "anthropic"
    assert fallback.calls == 0


def test_router_fails_over_to_a_different_manufacturer() -> None:
    primary = _FakeProvider("anthropic", "anthropic", error=LLMTimeout("caiu"))
    fallback = _FakeProvider("openai", "openai")
    envelope = LLMRouter(primary, fallback).complete(_request(), Deadline(total_ms=5_000))
    assert envelope.provider == "openai"
    assert fallback.calls == 1


def test_router_refuses_two_providers_from_the_same_manufacturer() -> None:
    with pytest.raises(ConfigurationError):
        LLMRouter(_FakeProvider("a", "anthropic"), _FakeProvider("b", "anthropic"))


def test_router_does_not_try_the_fallback_without_budget() -> None:
    """O primario queimou o orcamento e falhou: nao ha tempo para um segundo."""
    deadline = Deadline(total_ms=5_000)

    class SlowFailingProvider(_FakeProvider):
        def complete(self, request: LLMRequest) -> ToolCallEnvelope:
            deadline.started_at -= 10  # consumiu o turno inteiro antes de falhar
            raise LLMTimeout("caiu depois de demorar")

    primary = SlowFailingProvider("anthropic", "anthropic")
    fallback = _FakeProvider("openai", "openai")

    with pytest.raises(LLMUnavailable):
        LLMRouter(primary, fallback).complete(_request(), deadline)
    assert fallback.calls == 0


def test_router_reports_both_failures() -> None:
    primary = _FakeProvider("anthropic", "anthropic", error=LLMError("um"))
    fallback = _FakeProvider("openai", "openai", error=LLMError("dois"))
    with pytest.raises(LLMUnavailable) as exc:
        LLMRouter(primary, fallback).complete(_request(), Deadline(total_ms=5_000))
    assert "um" in str(exc.value) and "dois" in str(exc.value)


def test_router_records_llm_latency() -> None:
    deadline = Deadline(total_ms=5_000)
    LLMRouter(_FakeProvider("anthropic", "anthropic")).complete(_request(), deadline)
    assert "llm" in deadline.stage_latencies_ms


# --- Provedor Gemini -----------------------------------------------------


class _FakeGeminiResponse:
    """Resposta do SDK do Google, no formato que o provedor consome."""

    def __init__(self, calls: list[object], text: str | None = None, finish: str = "STOP") -> None:
        self.function_calls = calls
        self._text = text
        self.usage_metadata = type(
            "Usage",
            (),
            {
                "prompt_token_count": 120,
                "candidates_token_count": 18,
                "cached_content_token_count": 0,
            },
        )()
        self.candidates = [type("Candidate", (), {"finish_reason": finish})()]

    @property
    def text(self) -> str | None:
        if self._text is None:
            raise ValueError("resposta sem texto: so function call")
        return self._text


class _FakeGeminiClient:
    def __init__(self, response: _FakeGeminiResponse) -> None:
        self._response = response
        self.calls: list[dict] = []
        self.models = self

    def generate_content(self, **kwargs: object) -> _FakeGeminiResponse:
        self.calls.append(kwargs)
        return self._response


def _gemini_request() -> LLMRequest:
    return PromptBuilder().build(
        tenant_name="T",
        question="quanto tem de cimento?",
        tools=tools_for_role("sales_rep"),
        context=_context(),
    )


def test_gemini_returns_a_normalized_envelope() -> None:
    from orbi.llm.providers.gemini_provider import GeminiProvider

    call = type("Call", (), {"name": "check_stock", "args": {"product_term": "cimento"}})()
    client = _FakeGeminiClient(_FakeGeminiResponse([call]))

    envelope = GeminiProvider("chave", "gemini-3.7-flash", client=client).complete(
        _gemini_request()
    )

    assert envelope.has_tool_call
    assert envelope.tool_name == "check_stock"
    assert envelope.tool_args == {"product_term": "cimento"}
    assert envelope.provider == "gemini"
    assert envelope.tokens_in == 120
    assert envelope.cost_usd > 0
    assert envelope.text is None  # com tool escolhida, nao se olha o texto


def test_gemini_never_executes_the_function_itself() -> None:
    """O SDK do Google sabe executar a funcao; aqui quem executa e a Policy Layer."""
    from orbi.llm.providers.gemini_provider import GeminiProvider

    call = type("Call", (), {"name": "check_stock", "args": {"product_term": "cimento"}})()
    client = _FakeGeminiClient(_FakeGeminiResponse([call]))
    GeminiProvider("chave", client=client).complete(_gemini_request())

    config = client.calls[0]["config"]
    assert config.automatic_function_calling.disable is True


def test_gemini_only_offers_the_tools_of_the_role() -> None:
    from orbi.llm.providers.gemini_provider import GeminiProvider

    call = type("Call", (), {"name": "check_stock", "args": {"product_term": "cimento"}})()
    client = _FakeGeminiClient(_FakeGeminiResponse([call]))
    GeminiProvider("chave", client=client).complete(_gemini_request())

    config = client.calls[0]["config"]
    declared = {fn.name for fn in config.tools[0].function_declarations}

    assert declared == {"check_stock", "check_price", "get_last_order"}
    assert "list_open_invoices" not in declared
    # `AUTO`, nao `ANY`: o modelo precisa poder responder FORA_DE_ESCOPO. E a
    # API recusa `allowed_function_names` fora do modo `ANY`.
    assert config.tool_config.function_calling_config.mode.value == "AUTO"
    assert config.tool_config.function_calling_config.allowed_function_names is None


def test_gemini_text_answer_is_not_a_tool_call() -> None:
    from orbi.llm.providers.gemini_provider import GeminiProvider

    client = _FakeGeminiClient(_FakeGeminiResponse([], text="FORA_DE_ESCOPO"))
    envelope = GeminiProvider("chave", client=client).complete(_gemini_request())

    assert not envelope.has_tool_call
    assert envelope.text == "FORA_DE_ESCOPO"


def test_gemini_safety_block_becomes_a_refusal() -> None:
    from orbi.llm.providers.gemini_provider import GeminiProvider

    client = _FakeGeminiClient(_FakeGeminiResponse([], finish="SAFETY"))
    envelope = GeminiProvider("chave", client=client).complete(_gemini_request())

    assert envelope.finish_reason == "refusal"


def test_gemini_errors_are_normalized() -> None:
    from orbi.llm.providers.gemini_provider import GeminiProvider

    class BrokenClient:
        def __init__(self) -> None:
            self.models = self

        def generate_content(self, **kwargs: object) -> None:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota da camada gratuita")

    with pytest.raises(LLMError):
        GeminiProvider("chave", client=BrokenClient()).complete(_gemini_request())


def test_gemini_and_anthropic_are_different_manufacturers() -> None:
    """O failover so vale entre fabricantes distintos (D-011)."""
    from orbi.llm.providers.anthropic_provider import AnthropicProvider
    from orbi.llm.providers.gemini_provider import GeminiProvider

    router = LLMRouter(GeminiProvider("a"), AnthropicProvider("b"))
    assert router.primary.manufacturer != router.fallback.manufacturer  # type: ignore[union-attr]


def test_gemini_asks_the_api_for_a_deadline_it_accepts() -> None:
    """A API recusa prazo abaixo de 10s; o orcamento real e cobrado por nos."""
    from orbi.llm.providers.gemini_provider import MIN_API_DEADLINE_MS, GeminiProvider

    call = type("Call", (), {"name": "check_stock", "args": {"product_term": "cimento"}})()
    client = _FakeGeminiClient(_FakeGeminiResponse([call]))
    request = _gemini_request().model_copy(update={"timeout_ms": 4_000})

    GeminiProvider("chave", client=client).complete(request)

    enviado = client.calls[0]["config"].http_options.timeout
    assert enviado == MIN_API_DEADLINE_MS
    assert enviado > request.timeout_ms


def test_gemini_gives_up_at_our_budget_not_at_the_api_one() -> None:
    """Sem isso, o `Deadline` teria um buraco neste provedor."""
    import time as _time

    from orbi.llm.providers.gemini_provider import GeminiProvider

    class SlowClient:
        def __init__(self) -> None:
            self.models = self

        def generate_content(self, **kwargs: object) -> None:
            _time.sleep(2)

    request = _gemini_request().model_copy(update={"timeout_ms": 200})
    with pytest.raises(LLMTimeout):
        GeminiProvider("chave", client=SlowClient()).complete(request)


def test_lite_models_can_omit_the_thinking_field() -> None:
    """Os modelos `lite` recusam `thinking_config`: -1 nao envia o campo."""
    from orbi.llm.providers.gemini_provider import GeminiProvider

    call = type("Call", (), {"name": "check_stock", "args": {"product_term": "cimento"}})()
    client = _FakeGeminiClient(_FakeGeminiResponse([call]))

    GeminiProvider("chave", thinking_budget=-1, client=client).complete(_gemini_request())

    assert client.calls[0]["config"].thinking_config is None


# --- chave por cliente (D-041) -------------------------------------------


def test_router_do_cliente_usa_a_chave_propria_e_guarda_a_global_de_reserva() -> None:
    """O que o cliente com projeto proprio compra: o turno dele sai da conta
    dele, e a nossa so entra se a dele parar."""
    from orbi.core.crypto import CredentialCipher
    from orbi.llm import tenant_keys
    from orbi.runtime.pipeline import OrbiRuntime

    global_provider = _FakeProvider("gemini", "google")
    do_cliente = _FakeProvider("openai", "openai")
    runtime = OrbiRuntime(LLMRouter(primary=global_provider))

    blob = CredentialCipher().encrypt(
        {"provider": "openai", "api_key": "sk-teste", "model": "gpt-5-mini"}
    )
    with mock.patch.object(tenant_keys, "provider_do_tenant", return_value=do_cliente):
        router = runtime._router_for(blob)

    assert router.primary is do_cliente
    assert router.fallback is global_provider


def test_sem_chave_propria_o_router_e_o_global_sem_copia() -> None:
    from orbi.runtime.pipeline import OrbiRuntime

    global_router = LLMRouter(primary=_FakeProvider("gemini", "google"))
    runtime = OrbiRuntime(global_router)

    assert runtime._router_for(None) is global_router


def test_duas_contas_do_mesmo_fabricante_sao_um_fallback_legitimo() -> None:
    """A regra dos dois fabricantes existe contra queda correlacionada. Duas
    contas da mesma empresa nao caem juntas por teto de gasto ou revogacao —
    esses sao eventos de conta, nao de fabricante."""
    do_cliente = _FakeProvider("openai", "openai")
    nosso = _FakeProvider("openai", "openai")

    with pytest.raises(ConfigurationError):
        LLMRouter(primary=do_cliente, fallback=nosso)

    router = LLMRouter(primary=do_cliente, fallback=nosso, contas_distintas=True)
    assert router.fallback is nosso
