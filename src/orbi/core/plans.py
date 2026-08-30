"""Planos comerciais (ORBI.md secao 18).

O plano decide **duas coisas**, e as duas sao verificaveis:

- quantos usuarios o cliente pode cadastrar;
- quantas consultas ele pode fazer por mes.

O que o plano **nao** decide: qual modelo de IA o cliente usa. Todos usam o
mesmo, escolhido por acerto e latencia (D-036). Vender "IA melhor" por mais
dinheiro e prometer algo que o cliente nao consegue conferir.

O limite de usuarios e cobrado no **cadastro**, nao no turno: um cliente que
passou do limite por engano nao pode ficar sem resposta no meio do expediente.
A cobranca no lugar certo e a porta de entrada, nao a pergunta.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Plano:
    codigo: str
    nome: str
    max_usuarios: int
    teto_mensal: int
    """Consultas por mes. Protege a margem, nao so contra abuso."""
    mensal_brl: Decimal

    @property
    def consultas_por_usuario(self) -> int:
        return self.teto_mensal // self.max_usuarios

    def descricao(self) -> str:
        return (
            f"{self.nome}: ate {self.max_usuarios} usuarios · "
            f"{self.teto_mensal} consultas/mes · R$ {self.mensal_brl:.2f}/mes"
        )


# O teto e dimensionado com folga sobre o uso esperado: um vendedor ativo faz
# algo perto de 15 perguntas por dia util, ou ~330 por mes. O teto fica no dobro
# disso, para que so uso claramente anormal esbarre nele.
PLANOS: dict[str, Plano] = {
    "essencial": Plano("essencial", "Essencial", 5, 3_000, Decimal("490.00")),
    "time": Plano("time", "Time", 15, 10_000, Decimal("890.00")),
    "operacao": Plano("operacao", "Operacao", 30, 20_000, Decimal("1490.00")),
    # Preco de fundador: o primeiro cliente paga menos por ser referencia e dar
    # depoimento. Cliente que paga pouco continua sendo cliente; cliente que
    # paga zero e usuario, e usuario nao cancela — ele so some (ORBI.md secao 18).
    "fundador": Plano("fundador", "Fundador", 15, 10_000, Decimal("350.00")),
}

PADRAO = "essencial"


class PlanoDesconhecido(ValueError):
    """Plano fora da tabela. Falha alto: teto errado vira prejuizo silencioso."""


def plano_de(codigo: str) -> Plano:
    try:
        return PLANOS[codigo]
    except KeyError:
        disponiveis = ", ".join(sorted(PLANOS))
        raise PlanoDesconhecido(
            f"plano desconhecido: '{codigo}'. Disponiveis: {disponiveis}"
        ) from None


def codigos() -> tuple[str, ...]:
    return tuple(sorted(PLANOS))


def cabe_mais_um_usuario(codigo: str, usuarios_ativos: int) -> bool:
    return usuarios_ativos < plano_de(codigo).max_usuarios
