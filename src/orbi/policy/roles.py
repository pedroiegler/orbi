"""Papeis efetivos de um cliente (D-040).

O ORBI.md sempre prometeu que atender o processo de um cliente seria
configuracao, nao deploy:

> Quando um tenant pedir "meu gerente ve tudo menos custo", isso e uma linha de
> configuracao — nao um deploy.

Este modulo cumpre a promessa. A regra de resolucao e deliberadamente simples:

- **Cliente sem papel proprio** usa os tres padroes do codigo. E o caso da
  maioria, e ele nao paga nada pela existencia da customizacao.
- **Cliente com papel proprio** usa a definicao dele **inteira** para aquele
  codigo de papel. Nao ha heranca parcial: heranca silenciosa e como uma
  permissao aparece onde ninguem esperava.

Duas garantias de seguranca que valem escrever:

1. **Permissao desconhecida e ignorada**, nunca concedida. Erro de digitacao em
   `orbi role set` nao pode virar acesso.
2. **Papel vazio nao consulta nada.** Quem esqueceu de preencher fica sem acesso,
   nao com acesso total.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from orbi.db.models import TenantRole, TenantRoleCapability
from orbi.tools.registry import ALL_CAPABILITIES, ROLE_CAPABILITIES, ToolSpec, all_tools


@dataclass(frozen=True)
class PapelEfetivo:
    """O papel como ele vale para aquele cliente, agora."""

    codigo: str
    nome: str
    capabilities: frozenset[str]
    proprio: bool
    """`True` quando o cliente sobrescreveu; `False` quando e o padrao do codigo."""

    def tools(self) -> tuple[ToolSpec, ...]:
        return tuple(
            spec for spec in all_tools() if spec.required_capabilities <= self.capabilities
        )

    def descricao(self) -> str:
        origem = "proprio" if self.proprio else "padrao"
        return f"{self.codigo} ({origem}): {', '.join(sorted(self.capabilities)) or 'sem acesso'}"


PAPEIS_PADRAO_NOMES: dict[str, str] = {
    "sales_rep": "Vendedor",
    "finance": "Financeiro",
    "admin": "Administrador",
}


def _sanear(capabilities: object) -> frozenset[str]:
    """Mantem so o que o registry conhece. Permissao inventada nao vira acesso."""
    if not isinstance(capabilities, (set, frozenset, list, tuple)):
        return frozenset()
    return frozenset(str(c) for c in capabilities if str(c) in ALL_CAPABILITIES)


def papeis_efetivos(session: Session, tenant_id: uuid.UUID) -> dict[str, PapelEfetivo]:
    """Todos os papeis validos para o cliente: padroes mais os proprios."""
    papeis: dict[str, PapelEfetivo] = {
        codigo: PapelEfetivo(
            codigo=codigo,
            nome=PAPEIS_PADRAO_NOMES.get(codigo, codigo),
            capabilities=_sanear(caps),
            proprio=False,
        )
        for codigo, caps in ROLE_CAPABILITIES.items()
    }

    proprios = session.scalars(select(TenantRole).where(TenantRole.tenant_id == tenant_id)).all()
    if not proprios:
        return papeis

    vinculos = session.scalars(
        select(TenantRoleCapability).where(TenantRoleCapability.tenant_id == tenant_id)
    ).all()
    por_papel: dict[str, set[str]] = {}
    for vinculo in vinculos:
        por_papel.setdefault(vinculo.role_code, set()).add(vinculo.capability_code)

    for papel in proprios:
        papeis[papel.code] = PapelEfetivo(
            codigo=papel.code,
            nome=papel.name,
            capabilities=_sanear(por_papel.get(papel.code, set())),
            proprio=True,
        )
    return papeis


def capabilities_efetivas(session: Session, tenant_id: uuid.UUID, role_code: str) -> frozenset[str]:
    """Permissoes de um papel naquele cliente.

    Papel inexistente devolve conjunto vazio — deny by default vale aqui
    tambem: um usuario com papel que ninguem definiu nao consulta nada.
    """
    papel = papeis_efetivos(session, tenant_id).get(role_code)
    return papel.capabilities if papel else frozenset()


def definir_papel(
    session: Session,
    tenant_id: uuid.UUID,
    codigo: str,
    nome: str,
    capabilities: set[str],
    descricao: str = "",
) -> PapelEfetivo:
    """Cria ou redefine um papel do cliente. A lista substitui a anterior."""
    validas = _sanear(capabilities)
    desconhecidas = {str(c) for c in capabilities} - validas
    if desconhecidas:
        raise ValueError(
            f"permissoes desconhecidas: {', '.join(sorted(desconhecidas))}. "
            f"Disponiveis: {', '.join(sorted(ALL_CAPABILITIES))}"
        )

    papel = session.get(TenantRole, (tenant_id, codigo))
    if papel is None:
        papel = TenantRole(tenant_id=tenant_id, code=codigo, name=nome, description=descricao)
        session.add(papel)
    else:
        papel.name = nome
        papel.description = descricao or papel.description

    for vinculo in session.scalars(
        select(TenantRoleCapability).where(
            TenantRoleCapability.tenant_id == tenant_id,
            TenantRoleCapability.role_code == codigo,
        )
    ).all():
        session.delete(vinculo)
    session.flush()

    for capability in sorted(validas):
        session.add(
            TenantRoleCapability(tenant_id=tenant_id, role_code=codigo, capability_code=capability)
        )
    session.flush()

    return PapelEfetivo(codigo=codigo, nome=nome, capabilities=validas, proprio=True)


def restaurar_padrao(session: Session, tenant_id: uuid.UUID, codigo: str) -> bool:
    """Remove a customizacao e devolve o papel ao padrao do codigo."""
    papel = session.get(TenantRole, (tenant_id, codigo))
    if papel is None:
        return False

    for vinculo in session.scalars(
        select(TenantRoleCapability).where(
            TenantRoleCapability.tenant_id == tenant_id,
            TenantRoleCapability.role_code == codigo,
        )
    ).all():
        session.delete(vinculo)
    session.delete(papel)
    session.flush()
    return True
