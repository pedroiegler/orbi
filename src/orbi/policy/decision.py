"""Decisao tipada da Policy Layer.

`ALLOW` ou `DENY(reason_code)` — nunca um booleano solto, porque o motivo da
negacao vai para a auditoria e para a mensagem que o usuario recebe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - apenas para tipagem
    from orbi.tools.args import ToolArgs


class ReasonCode(StrEnum):
    """Motivos de negacao. Cada um tem uma mensagem propria no renderer."""

    TENANT_INACTIVE = "TENANT_INACTIVE"
    USER_INACTIVE = "USER_INACTIVE"
    USER_NEEDS_REVERIFICATION = "USER_NEEDS_REVERIFICATION"
    TOOL_UNKNOWN = "TOOL_UNKNOWN"
    TOOL_DISABLED_FOR_TENANT = "TOOL_DISABLED_FOR_TENANT"
    TOOL_NOT_SUPPORTED_BY_ERP = "TOOL_NOT_SUPPORTED_BY_ERP"
    TOOL_NOT_ALLOWED_FOR_ROLE = "TOOL_NOT_ALLOWED_FOR_ROLE"
    ARGS_INVALID = "ARGS_INVALID"
    ARGS_CONTAIN_IDENTIFIER = "ARGS_CONTAIN_IDENTIFIER"
    RATE_LIMIT_MINUTE = "RATE_LIMIT_MINUTE"
    RATE_LIMIT_DAY = "RATE_LIMIT_DAY"
    PLAN_QUOTA_EXCEEDED = "PLAN_QUOTA_EXCEEDED"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason_code: ReasonCode | None = None
    detail: str | None = None
    validated_args: ToolArgs | None = None
    """Argumentos ja validados pelo modelo Pydantic da tool."""
    policy_version_hash: str = ""
    checks: tuple[str, ...] = field(default=())
    """Regras avaliadas, na ordem. Vai para a auditoria."""

    @property
    def denied(self) -> bool:
        return not self.allowed


def allow(
    validated_args: ToolArgs, policy_version_hash: str, checks: tuple[str, ...]
) -> PolicyDecision:
    return PolicyDecision(
        allowed=True,
        validated_args=validated_args,
        policy_version_hash=policy_version_hash,
        checks=checks,
    )


def deny(
    reason_code: ReasonCode,
    policy_version_hash: str,
    checks: tuple[str, ...],
    detail: str | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        allowed=False,
        reason_code=reason_code,
        detail=detail,
        policy_version_hash=policy_version_hash,
        checks=checks,
    )
