"""Vocabulário da reconciliação: uma verificação é um número extraído contra um
número impresso no documento.

Nenhum destes valores é estimativa. Todos existem impressos no statement — é
por isso que servem de prova.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

__all__ = ["Check", "TOLERANCE", "compare", "compare_count"]

# Zero. Os valores são Decimal — não há drift de vírgula flutuante para
# absorver, e um cêntimo de folga é exatamente o tipo de erro silencioso que
# este projeto existe para impedir. Quem precisar de folga passa-a explicitamente
# no `compare`, e fica escrito no código porquê.
TOLERANCE = Decimal("0")


@dataclass(frozen=True)
class Check:
    section: str
    name: str
    passed: bool
    extracted: Any
    expected: Any
    page: int | None = None
    detail: str = ""
    # 'erro' faz o comando falhar; 'aviso' é aquilo que não conseguimos provar.
    # A diferença importa: não-verificável não é o mesmo que errado, e chamar
    # certo àquilo que não foi provado era o erro que este projeto evita.
    severity: str = "erro"

    @property
    def fatal(self) -> bool:
        return not self.passed and self.severity == "erro"

    @property
    def delta(self):
        if isinstance(self.extracted, Decimal) and isinstance(self.expected, Decimal):
            return self.extracted - self.expected
        if isinstance(self.extracted, int) and isinstance(self.expected, int):
            return self.extracted - self.expected
        return None

    def format(self) -> str:
        status = "PASS" if self.passed else ("FAIL" if self.severity == "erro" else "AVISO")
        where = f" p.{self.page}" if self.page else ""
        line = f"  [{status}] {self.section}{where} · {self.name}: extraído={self.extracted} esperado={self.expected}"
        delta = self.delta
        if delta is not None and not self.passed:
            line += f" (delta={delta:+})"
        if self.detail:
            line += f"\n         {self.detail}"
        return line


def compare(
    section: str,
    name: str,
    extracted: Decimal | None,
    expected: Decimal | None,
    *,
    page: int | None = None,
    detail: str = "",
    tolerance: Decimal = TOLERANCE,
    severity: str = "erro",
) -> Check:
    if extracted is None or expected is None:
        return Check(
            section=section,
            name=name,
            passed=False,
            extracted=extracted,
            expected=expected,
            page=page,
            detail=detail or "valor em falta — nada para comparar",
            severity=severity,
        )
    passed = abs(extracted - expected) <= tolerance
    return Check(section, name, passed, extracted, expected, page, detail, severity)


def compare_count(
    section: str,
    name: str,
    extracted: int,
    expected: int | None,
    *,
    detail: str = "",
) -> Check:
    if expected is None:
        return Check(section, name, False, extracted, expected, None, detail or "contagem esperada não declarada")
    return Check(section, name, extracted == expected, extracted, expected, None, detail)
