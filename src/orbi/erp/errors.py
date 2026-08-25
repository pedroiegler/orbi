"""Excecoes normalizadas do ERP (ORBI.md secao 6.11).

Payload nativo do ERP nunca cruza a fronteira. O Core so conhece estes cinco
tipos, e e sobre eles que o Runtime decide o que responder.
"""

from __future__ import annotations

from orbi.core.errors import OrbiError


class ErpError(OrbiError):
    """Raiz das falhas de ERP."""

    def __init__(self, message: str, *, adapter: str | None = None) -> None:
        super().__init__(message)
        self.adapter = adapter


class ErpTimeout(ErpError):
    """O ERP nao respondeu dentro do orcamento."""


class ErpUnavailable(ErpError):
    """O ERP esta fora do ar ou devolveu erro de servidor."""


class ErpAuthError(ErpError):
    """Credencial invalida, expirada ou sem permissao."""


class ErpRateLimited(ErpError):
    """O ERP recusou por excesso de requisicoes."""

    def __init__(
        self, message: str, *, adapter: str | None = None, retry_after_ms: int | None = None
    ) -> None:
        super().__init__(message, adapter=adapter)
        self.retry_after_ms = retry_after_ms


class ErpNotFound(ErpError):
    """A entidade nao existe mais no ERP."""


class ErpProtocolError(ErpError):
    """O ERP respondeu algo que o adapter nao sabe traduzir.

    Nao e retentavel: repetir devolveria a mesma resposta incompreensivel.
    """


RETRYABLE_ERRORS: tuple[type[ErpError], ...] = (ErpTimeout, ErpUnavailable, ErpRateLimited)
"""Leitura e idempotente, entao retentar timeout e 5xx e seguro (secao 6.13)."""
