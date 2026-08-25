"""PIIRedactor — mascara antes de qualquer envio ao LLM (ORBI.md secao 6.4).

Como o LLM nunca precisa de identificador para nada (secao 6.6), mascarar nao
custa capacidade alguma: "quanto tem de tubo pvc 100" continua inteira.

Isto e minimizacao de dado, nao criptografia: o objetivo e nao mandar CPF, CNPJ,
telefone e e-mail para fora do pais sem necessidade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CPF_MASK = "[cpf]"
CNPJ_MASK = "[cnpj]"
PHONE_MASK = "[telefone]"
EMAIL_MASK = "[email]"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.UNICODE)
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_PHONE_RE = re.compile(
    r"""(?x)
    (?<![\w-])
    (?:\+?55\s?)?
    (?:\(?\d{2}\)?[\s.-]?)?
    9?\d{4}[\s.-]?\d{4}
    (?![\w-])
    """
)


@dataclass(frozen=True)
class RedactionReport:
    text: str
    counts: dict[str, int]

    @property
    def redacted_anything(self) -> bool:
        return any(self.counts.values())


class PIIRedactor:
    """Mascara CPF, CNPJ, telefone e e-mail."""

    def redact(self, text: str) -> str:
        return self.analyze(text).text

    def analyze(self, text: str) -> RedactionReport:
        counts = {"cpf": 0, "cnpj": 0, "phone": 0, "email": 0}

        def replace(pattern: re.Pattern[str], mask: str, key: str, value: str) -> str:
            def _sub(_match: re.Match[str]) -> str:
                counts[key] += 1
                return mask

            return pattern.sub(_sub, value)

        # A ordem importa: CNPJ antes de CPF (o padrao de CPF casaria pedaco de
        # CNPJ), e ambos antes de telefone.
        result = replace(_EMAIL_RE, EMAIL_MASK, "email", text)
        result = replace(_CNPJ_RE, CNPJ_MASK, "cnpj", result)
        result = replace(_CPF_RE, CPF_MASK, "cpf", result)
        result = replace(_PHONE_RE, PHONE_MASK, "phone", result)
        return RedactionReport(text=result, counts=counts)


DEFAULT_REDACTOR = PIIRedactor()
