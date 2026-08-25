"""Hierarquia de erros do Orbi.

Erro tecnico nunca chega ao usuario final como stacktrace: vira mensagem
deterministica renderizada por template + alerta no canal de ops.
"""

from __future__ import annotations


class OrbiError(Exception):
    """Raiz de tudo que o Orbi levanta de proposito."""


class ConfigurationError(OrbiError):
    """Configuracao ausente ou incoerente. Falha na subida, nao no turno."""


class IdentityError(OrbiError):
    """Problema ao identificar tenant, usuario ou papel."""


class UnknownSenderError(IdentityError):
    """Numero sem cadastro previo.

    A resposta ao remetente e sempre generica: nunca revela se o numero existe.
    """


class InactiveUserError(IdentityError):
    """Usuario existe mas esta inativo ou precisa de re-verificacao."""


class PolicyDenied(OrbiError):
    """A Policy Layer negou a operacao."""

    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        super().__init__(detail or reason_code)
        self.reason_code = reason_code
        self.detail = detail


class LLMError(OrbiError):
    """Falha do provedor de LLM."""


class LLMTimeout(LLMError):
    """Provedor nao respondeu dentro do orcamento."""


class LLMUnavailable(LLMError):
    """Todos os provedores configurados falharam."""


class NoToolSelected(LLMError):
    """O modelo respondeu texto em vez de escolher uma tool."""

    def __init__(self, text: str | None = None) -> None:
        super().__init__("o modelo nao escolheu nenhuma tool")
        self.text = text


class RenderError(OrbiError):
    """Falha ao montar a resposta a partir do template."""
