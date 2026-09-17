"""Sincroniza a configuracao global a partir do codigo.

O Tool Registry e a fonte da verdade; a tabela `tools` e o espelho usado para
configuracao por tenant e para auditoria historica. Este modulo e o terceiro
artefato do `ToolSpec` (ORBI.md secao 6.7) e roda em toda subida e no
`orbi onboard`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from orbi.db.models import Capability, Role, RoleCapability, RoleTool, Tool
from orbi.tools.registry import (
    ALL_CAPABILITIES,
    ROLE_CAPABILITIES,
    all_tools,
    tools_for_role,
)

ROLE_LABELS: dict[str, tuple[str, str]] = {
    "sales_rep": ("Vendedor", "Consulta estoque, preco e ultimo pedido. Nunca ve custo."),
    "finance": ("Financeiro", "Consulta titulos em aberto, pedidos e preco com custo."),
    "admin": ("Administrador", "Acesso a todas as tools e campos do tenant."),
}

CAPABILITY_LABELS: dict[str, str] = {
    "stock:read": "Ler saldo de estoque",
    "price:read": "Ler preco de venda",
    "price:read_cost": "Ler custo e margem",
    "invoice:read": "Ler titulos em aberto",
    "customer:read": "Ler dados comerciais do cliente",
}


@dataclass
class SeedReport:
    roles: int = 0
    capabilities: int = 0
    tools: int = 0
    role_tools: int = 0

    def __str__(self) -> str:
        return (
            f"{self.roles} papeis, {self.capabilities} capabilities, "
            f"{self.tools} tools, {self.role_tools} vinculos papel-tool"
        )


def sync_global_config(session: Session) -> SeedReport:
    """Reaplica papeis, capabilities e tools. Idempotente."""
    report = SeedReport()

    for code, description in CAPABILITY_LABELS.items():
        capability = session.get(Capability, code)
        if capability is None:
            session.add(Capability(code=code, description=description))
        else:
            capability.description = description
        report.capabilities += 1

    for code, (name, description) in ROLE_LABELS.items():
        role = session.get(Role, code)
        if role is None:
            session.add(Role(code=code, name=name, description=description))
        else:
            role.name, role.description = name, description
        report.roles += 1

    session.flush()

    for spec in all_tools():
        tool = session.get(Tool, spec.name)
        if tool is None:
            session.add(
                Tool(
                    name=spec.name,
                    domain=spec.domain,
                    description=spec.description,
                    erp_operation=spec.erp_operation,
                    active=True,
                )
            )
        else:
            tool.domain = spec.domain
            tool.description = spec.description
            tool.erp_operation = spec.erp_operation
            tool.active = True
        report.tools += 1

    session.flush()

    # Vinculos sao derivados do codigo: reescrever e mais simples e mais seguro
    # do que reconciliar diferenca.
    session.execute(delete(RoleCapability))
    session.execute(delete(RoleTool))
    for role_code, capabilities in ROLE_CAPABILITIES.items():
        for capability_code in sorted(capabilities):
            session.add(RoleCapability(role_code=role_code, capability_code=capability_code))
        for spec in tools_for_role(role_code):
            session.add(RoleTool(role_code=role_code, tool_name=spec.name))
            report.role_tools += 1

    session.flush()

    # Tool que saiu do registry nao pode continuar ativa no banco.
    known = {spec.name for spec in all_tools()}
    for tool in session.scalars(select(Tool)).all():
        if tool.name not in known:
            tool.active = False

    unknown: set[str] = set(CAPABILITY_LABELS) - set(ALL_CAPABILITIES)
    if unknown:
        raise RuntimeError(f"capabilities documentadas sem declaracao no registry: {unknown}")

    return report
