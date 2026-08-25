"""Content firewall do sync (ORBI.md secao 11).

Terceira barreira contra prompt injection: nome de produto com padrao de
instrucao entra **sinalizado** e fica fora do indice ate revisao humana.

As duas primeiras barreiras (saida restrita a um conjunto fechado de tools e
rendering deterministico) ja resolvem o caso; esta existe porque o nome do
produto e o unico texto de terceiro que circula pelo sistema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instrucao",
        re.compile(
            r"(?i)\b(ignore|ignora|desconsidere|esqueca)\b.{0,40}\b(regra|instru|acima|anterior)"
        ),
    ),
    ("papel", re.compile(r"(?i)\b(system|assistant|user)\s*:")),
    ("delimitador", re.compile(r"(?i)(</?\s*(system|instruction|prompt)\s*>|```|\[\[|\]\])")),
    (
        "comando",
        re.compile(r"(?i)\b(voce deve|a partir de agora|responda apenas|nova instrucao)\b"),
    ),
    ("exfiltracao", re.compile(r"(?i)\b(api[_ ]?key|senha|token|credencial)\b")),
)

MAX_NAME_LENGTH = 300


@dataclass(frozen=True)
class FirewallVerdict:
    flagged: bool
    reason: str | None = None


def inspect(name: str) -> FirewallVerdict:
    """Analisa o nome vindo do ERP antes de entrar no indice."""
    if len(name) > MAX_NAME_LENGTH:
        return FirewallVerdict(True, f"nome com mais de {MAX_NAME_LENGTH} caracteres")
    if "\n" in name or "\r" in name:
        return FirewallVerdict(True, "nome com quebra de linha")
    for label, pattern in _PATTERNS:
        if pattern.search(name):
            return FirewallVerdict(True, f"padrao de {label}")
    return FirewallVerdict(False)
