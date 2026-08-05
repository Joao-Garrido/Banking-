"""Activity — movimentos do período.

Reconciliação: beginning value + net flows + market change == ending value.
Os quatro números estão impressos no sumário do statement e são digitados à mão
em expected.json; a soma dos movimentos extraídos é confrontada com net flows.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from ..lines import Line
from ..reconcile import Check, compare
from .base import ParseContext, SectionResult, as_decimal, extract_section, standard_checks

NAME = "activity"

LAYOUT_HINTS = {
    "amount_column": "amount",
    "description_column": "description",
    "row_start": {"kind": "date"},
    "total_label_patterns": [r"^Total\s+Activity", r"^Net\s+(Cash\s+)?Flows?"],
    "ignore_patterns": [r"^(Date|Settlement)\b.*\bAmount\b", r"continued"],
}


def parse(
    lines: Sequence[Line], spec: dict, context: ParseContext | None = None
) -> SectionResult:
    return extract_section(NAME, lines, spec, context=context)


def checks(result: SectionResult, spec: dict, expected: dict, statement: dict) -> list[Check]:
    out = standard_checks(result, spec, expected)
    out.extend(rollforward_checks(statement))

    roll = statement.get("activity_rollforward") or {}
    net_flows = as_decimal(roll.get("net_flows"))
    if net_flows is not None and expected.get("sum_is_net_flows", True):
        out.append(
            compare(
                NAME,
                "soma dos movimentos vs net flows impresso",
                result.sum_of(spec.get("amount_column", "amount")),
                net_flows,
                detail="se falhar, ou faltam linhas ou há linhas que não são fluxo de caixa",
            )
        )
    return out


def rollforward_checks(statement: dict) -> list[Check]:
    """beginning + net flows + market change == ending."""
    roll = statement.get("activity_rollforward")
    if not roll:
        return []

    parts = {
        key: as_decimal(roll.get(key))
        for key in ("beginning_value", "net_flows", "market_change", "ending_value")
    }
    missing = [k for k, v in parts.items() if v is None]
    if missing:
        return [
            Check(
                section=NAME,
                name="roll-forward",
                passed=False,
                extracted=None,
                expected=None,
                detail=f"activity_rollforward incompleto em expected.json, falta: {missing}",
            )
        ]

    computed: Decimal = parts["beginning_value"] + parts["net_flows"] + parts["market_change"]
    return [
        compare(
            NAME,
            "roll-forward (beginning + flows + market change)",
            computed,
            parts["ending_value"],
            detail="todos os quatro números estão impressos no statement",
        )
    ]
