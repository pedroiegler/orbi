"""Policy Layer — deterministica, ordenada, deny by default (ORBI.md secao 6.8).

```
TenantActive → UserActive → ToolEnabledForTenant → ToolSupportedByErp
→ ToolAllowedForRole → ArgsValid → RateLimit
```

Permissao efetiva = `tenant_tools ∩ role_tools ∩ capabilities()` do ERP.

Roda **antes** de qualquer acesso ao ERP e nunca depende do LLM: a saida do
modelo e apenas uma sugestao de tool que ainda precisa ser autorizada.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from orbi.policy.decision import PolicyDecision, ReasonCode, allow, deny
from orbi.policy.field_policy import FIELD_POLICY
from orbi.tools.registry import (
    ROLE_CAPABILITIES,
    TOOL_REGISTRY,
    capabilities_for_role,
    get_tool,
)


@dataclass
class PolicySubject:
    """Tudo que a decisao precisa, ja carregado do banco pelo Runtime."""

    tenant_id: str
    tenant_status: str
    user_id: str
    user_active: bool
    role: str
    needs_reverification: bool = False
    enabled_tools: frozenset[str] = field(default_factory=frozenset)
    """`tenant_tools` habilitadas."""
    erp_supported_tools: frozenset[str] = field(default_factory=frozenset)
    """`capabilities().supported_tools` do adapter daquele tenant."""
    rate_limit_per_minute: int = 12
    rate_limit_per_day: int = 300
    monthly_query_cap: int = 5_000
    """Teto do plano: protege a margem, nao so contra abuso."""


RateLimitCheck = Callable[[PolicySubject], ReasonCode | None]


def no_rate_limit(_subject: PolicySubject) -> ReasonCode | None:
    """Usado apenas em teste de configuracao e no Discovery.

    Explicito de proposito: pular rate limit precisa ser uma decisao visivel no
    codigo, nao um parametro omitido por engano.
    """
    return None


def evaluate(
    subject: PolicySubject,
    tool_name: str,
    raw_args: dict[str, Any],
    rate_limit_check: RateLimitCheck,
) -> PolicyDecision:
    """Avalia as regras em ordem e devolve a primeira negacao."""
    version = policy_version_hash(subject)
    checks: list[str] = []

    def track(name: str) -> tuple[str, ...]:
        checks.append(name)
        return tuple(checks)

    if subject.tenant_status != "active":
        return deny(ReasonCode.TENANT_INACTIVE, version, track("TenantActive"))
    track("TenantActive")

    if not subject.user_active:
        return deny(ReasonCode.USER_INACTIVE, version, track("UserActive"))
    if subject.needs_reverification:
        return deny(ReasonCode.USER_NEEDS_REVERIFICATION, version, track("UserActive"))
    track("UserActive")

    if tool_name not in TOOL_REGISTRY:
        return deny(
            ReasonCode.TOOL_UNKNOWN, version, track("ToolExists"), detail=f"tool {tool_name}"
        )
    track("ToolExists")
    spec = get_tool(tool_name)

    if tool_name not in subject.enabled_tools:
        return deny(ReasonCode.TOOL_DISABLED_FOR_TENANT, version, track("ToolEnabledForTenant"))
    track("ToolEnabledForTenant")

    if subject.erp_supported_tools and tool_name not in subject.erp_supported_tools:
        return deny(ReasonCode.TOOL_NOT_SUPPORTED_BY_ERP, version, track("ToolSupportedByErp"))
    track("ToolSupportedByErp")

    role_capabilities = capabilities_for_role(subject.role)
    if not spec.required_capabilities <= role_capabilities:
        return deny(ReasonCode.TOOL_NOT_ALLOWED_FOR_ROLE, version, track("ToolAllowedForRole"))
    if (subject.role, tool_name) not in FIELD_POLICY:
        # Sem whitelist de campos nao ha resposta possivel: nega por omissao.
        return deny(ReasonCode.TOOL_NOT_ALLOWED_FOR_ROLE, version, track("ToolAllowedForRole"))
    track("ToolAllowedForRole")

    try:
        validated = spec.validate_args(raw_args)
    except ValidationError as exc:
        reason = (
            ReasonCode.ARGS_CONTAIN_IDENTIFIER
            if _mentions_identifier(exc)
            else ReasonCode.ARGS_INVALID
        )
        return deny(reason, version, track("ArgsValid"), detail=_first_error(exc))
    track("ArgsValid")

    limited = rate_limit_check(subject)
    if limited is not None:
        return deny(limited, version, track("RateLimit"))
    track("RateLimit")

    return allow(validated, version, tuple(checks))


def _mentions_identifier(exc: ValidationError) -> bool:
    return any("identificador" in str(error.get("msg", "")).lower() for error in exc.errors())


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "argumentos invalidos"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    return f"{location}: {first.get('msg', 'invalido')}"


def policy_version_hash(subject: PolicySubject) -> str:
    """Assinatura da configuracao que autorizou a operacao.

    Cobre o registry, os presets de papel, a Field Policy e a configuracao do
    tenant. Meses depois da consulta, e possivel provar o que estava valendo.
    """
    snapshot = {
        "tools": {
            name: sorted(spec.required_capabilities) for name, spec in sorted(TOOL_REGISTRY.items())
        },
        "roles": {role: sorted(caps) for role, caps in sorted(ROLE_CAPABILITIES.items())},
        "field_policy": {
            f"{role}:{tool}": sorted(fields)
            for (role, tool), fields in sorted(FIELD_POLICY.items())
        },
        "tenant": {
            "status": subject.tenant_status,
            "enabled_tools": sorted(subject.enabled_tools),
            "erp_supported_tools": sorted(subject.erp_supported_tools),
        },
        "user_role": subject.role,
    }
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
