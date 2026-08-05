"""Fees — encargos do período.

Reconciliação: Σ taxas == total de fees impresso.
"""

from __future__ import annotations

from typing import Sequence

from ..lines import Line
from ..reconcile import Check
from .base import ParseContext, SectionResult, extract_section, standard_checks

NAME = "fees"

LAYOUT_HINTS = {
    "amount_column": "amount",
    "description_column": "description",
    "row_start": {"kind": "date"},
    "total_label_patterns": [r"^Total\s+(Fees|Charges|Expenses)"],
    "ignore_patterns": [r"^Date\b.*\bAmount\b", r"continued"],
}


def parse(
    lines: Sequence[Line], spec: dict, context: ParseContext | None = None
) -> SectionResult:
    return extract_section(NAME, lines, spec, context=context)


def checks(result: SectionResult, spec: dict, expected: dict, statement: dict) -> list[Check]:
    return standard_checks(result, spec, expected)
