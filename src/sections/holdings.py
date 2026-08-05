"""Holdings — posições em carteira.

Reconciliação: Σ market value das posições == total do portfólio impresso.
"""

from __future__ import annotations

from typing import Sequence

from ..lines import Line
from ..reconcile import Check, compare
from .base import ParseContext, SectionResult, as_decimal, extract_section, standard_checks

NAME = "holdings"

# Sugestão para o mapper/revisão humana do layout.json.
LAYOUT_HINTS = {
    "amount_column": "market_value",
    "description_column": "description",
    "row_start": {"kind": "numeric_in", "column": "market_value"},
    "total_label_patterns": [r"^Total\s+(Holdings|Portfolio)", r"^Total\s+Market\s+Value"],
    "ignore_patterns": [r"^(Security|Description)\b.*\bMarket\s+Value\b", r"continued"],
}


def parse(
    lines: Sequence[Line], spec: dict, context: ParseContext | None = None
) -> SectionResult:
    return extract_section(NAME, lines, spec, context=context)


def checks(result: SectionResult, spec: dict, expected: dict, statement: dict) -> list[Check]:
    out = standard_checks(result, spec, expected)

    portfolio_total = as_decimal(statement.get("portfolio_total"))
    if portfolio_total is not None:
        amount_column = spec.get("amount_column", "market_value")
        out.append(
            compare(
                NAME,
                "soma das posições vs total do portfólio",
                result.sum_of(amount_column),
                portfolio_total,
                detail="o total do portfólio é o número impresso na capa/sumário do statement",
            )
        )
    return out
