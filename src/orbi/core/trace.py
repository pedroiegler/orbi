"""trace_id do turno.

Um id por turno, propagado por contextvars, compartilhado entre observabilidade
e auditoria (ORBI.md secao 14). O codigo curto e o que aparece no rodape da
mensagem em tenants de piloto.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_TRACE_ID: ContextVar[str | None] = ContextVar("orbi_trace_id", default=None)

_SHORT_ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTVWXYZ"  # Crockford: sem I, O, U


def new_trace_id() -> str:
    return str(uuid.uuid4())


def get_trace_id() -> str | None:
    return _TRACE_ID.get()


def short_code(trace_id: str) -> str:
    """Codigo curto e estavel derivado do trace_id, para o usuario citar."""
    value = uuid.UUID(trace_id).int
    digits = []
    for _ in range(6):
        digits.append(_SHORT_ALPHABET[value % len(_SHORT_ALPHABET)])
        value //= len(_SHORT_ALPHABET)
    return "".join(reversed(digits))


@contextmanager
def trace_context(trace_id: str | None = None) -> Iterator[str]:
    """Abre um turno com trace_id proprio."""
    resolved = trace_id or new_trace_id()
    token = _TRACE_ID.set(resolved)
    try:
        yield resolved
    finally:
        _TRACE_ID.reset(token)
