"""CanonicalNameBuilder (ORBI.md secao 7).

Nao se vetoriza o nome cru:

```
"TB PVC ESG 100MM BR"  →  "tubo pvc esgoto 100 mm branco"
```

O ganho de qualidade vem daqui, nao de dobrar a dimensao do vetor. Catalogo de
PME brasileira e abreviado e inconsistente, e e exatamente isso que quebra a
busca semantica.

O dicionario de abreviacoes tem duas camadas: a base, que vale para qualquer
tenant, e a do tenant, alimentada pelo Discovery e por correcao manual.
"""

from __future__ import annotations

import re
import unicodedata

BASE_ABBREVIATIONS: dict[str, str] = {
    # hidraulica
    "tb": "tubo",
    "tub": "tubo",
    "esg": "esgoto",
    "sold": "soldavel",
    "rosc": "roscavel",
    "cnx": "conexao",
    "jlh": "joelho",
    "lv": "luva",
    "adapt": "adaptador",
    "reg": "registro",
    "torn": "torneira",
    "vlv": "valvula",
    "cx": "caixa",
    "hidr": "hidraulico",
    "sif": "sifao",
    # eletrica
    "cb": "cabo",
    "flex": "flexivel",
    "disj": "disjuntor",
    "elet": "eletrico",
    "eletrod": "eletroduto",
    "tom": "tomada",
    "interr": "interruptor",
    "lamp": "lampada",
    "refl": "refletor",
    "cond": "condutor",
    # civil
    "cim": "cimento",
    "arg": "argamassa",
    "vd": "vedacao",
    "gess": "gesso",
    "massa": "massa",
    "corr": "corrida",
    "tij": "tijolo",
    "blc": "bloco",
    "ver": "vergalhao",
    "tel": "tela",
    # tintas
    "tn": "tinta",
    "acr": "acrilica",
    "fosc": "fosca",
    "acet": "acetinada",
    "esm": "esmalte",
    "sint": "sintetico",
    "verniz": "verniz",
    "sel": "selador",
    # cores
    "br": "branco",
    "bc": "branco",
    "pt": "preto",
    "az": "azul",
    "vm": "vermelho",
    "amr": "amarelo",
    "mar": "marrom",
    "cz": "cinza",
    "bg": "bege",
    # embalagem e medida
    "un": "unidade",
    "und": "unidade",
    "pc": "peca",
    "pct": "pacote",
    "sc": "saco",
    "rl": "rolo",
    "gl": "galao",
    "jg": "jogo",
    "par": "par",
    "mt": "metro",
    "m2": "metro quadrado",
    "m3": "metro cubico",
    # qualificadores
    "int": "interno",
    "ext": "externo",
    "sup": "superior",
    "inf": "inferior",
    "res": "residencial",
    "ind": "industrial",
    "prof": "profissional",
    "pol": "polegada",
}

UNIT_ALIASES: dict[str, str] = {
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "kg": "kg",
    "g": "g",
    "l": "l",
    "lt": "l",
    "lts": "l",
    "ml": "ml",
    "w": "w",
    "v": "v",
    "a": "a",
    "cv": "cv",
    "pol": "polegada",
    '"': "polegada",
}

_NUMBER_UNIT_RE = re.compile(r"(?i)\b(\d+(?:[.,]\d+)?)\s*([a-z\"]{1,3})\b")
_WORD_SLASH_RE = re.compile(r"(?i)(?<=[a-z])\s*/\s*(?=[a-z])")
_PUNCTUATION_RE = re.compile(r"[^\w,./\"\s-]+")
_MULTISPACE_RE = re.compile(r"\s+")


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", value) if unicodedata.category(ch) != "Mn"
    )


def normalize(value: str) -> str:
    """Minusculas, sem acento, sem pontuacao de ruido, espaco unico."""
    plain = strip_accents(value.lower())
    # "INT/EXT" sao duas palavras; "1/2" e uma fracao e continua junta.
    plain = _WORD_SLASH_RE.sub(" ", plain)
    plain = _PUNCTUATION_RE.sub(" ", plain)
    return _MULTISPACE_RE.sub(" ", plain).strip()


class CanonicalNameBuilder:
    """Traduz nome cru de ERP em nome canonico para indexar e comparar."""

    def __init__(self, abbreviations: dict[str, str] | None = None) -> None:
        merged = dict(BASE_ABBREVIATIONS)
        if abbreviations:
            merged.update({normalize(k): normalize(v) for k, v in abbreviations.items()})
        self._abbreviations = merged

    def build(self, name: str, code: str | None = None) -> str:
        text = normalize(name)
        if code:
            # Codigo interno nao ajuda a entender a pergunta de ninguem.
            text = text.replace(normalize(code), " ")

        text = _NUMBER_UNIT_RE.sub(self._split_number_unit, text)

        tokens: list[str] = []
        for raw in text.split():
            token = raw.strip("-/.,")
            if not token:
                continue
            token = token.replace(",", ".") if _is_decimal(token) else token
            tokens.append(self._abbreviations.get(token, token))

        return _MULTISPACE_RE.sub(" ", " ".join(tokens)).strip()

    def _split_number_unit(self, match: re.Match[str]) -> str:
        number, unit = match.group(1), match.group(2).lower()
        canonical_unit = UNIT_ALIASES.get(unit)
        if canonical_unit is None:
            return match.group(0)
        return f"{number} {canonical_unit}"

    @property
    def abbreviations(self) -> dict[str, str]:
        return dict(self._abbreviations)


def _is_decimal(token: str) -> bool:
    return bool(re.fullmatch(r"\d+,\d+", token))


DEFAULT_BUILDER = CanonicalNameBuilder()
