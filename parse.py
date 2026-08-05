#!/usr/bin/env python3
"""Entrypoint.

    python parse.py statement.pdf --out out/

Escreve um CSV por secção e um summary.json com os totais reconciliados.
Se alguma reconciliação falhar, sai com código != 0 e não escreve nada — a não
ser que se passe --force, e nesse caso os ficheiros levam o sufixo .PARTIAL e o
aviso vai para stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from src.pipeline import parse_statement, section_dataframe
from src.precheck import ScannedPdfError
from src.reconcile import Check, compare
from src.router import LayoutError, MissingSectionError, load_layout
from src.sections import module_for
from src.sections.base import SectionError, SectionResult


def internal_checks(result, layout: dict) -> list[Check]:
    """Conferências que não precisam de gabarito: extraído vs impresso no PDF."""
    checks: list[Check] = []
    for name, section in result.sections.items():
        spec = layout["sections"][name]
        amount_column = spec.get("amount_column")
        if not amount_column:
            continue
        extracted = section.sum_of(amount_column)
        if not section.printed_totals:
            checks.append(
                Check(
                    section=name,
                    name="total impresso encontrado",
                    passed=False,
                    extracted="nenhum",
                    expected="≥1",
                    detail=(
                        "nenhuma linha de total reconhecida nesta secção — sem número impresso "
                        "não há prova; revê total_label_patterns no layout.json"
                    ),
                )
            )
            continue
        for total in section.printed_totals:
            checks.append(
                compare(
                    name,
                    "soma vs total impresso",
                    extracted,
                    total.value,
                    page=total.page,
                    detail=f"linha impressa: {total.label!r}",
                )
            )
    return checks


def gabarito_checks(result, layout: dict, expected_path: Path, pdf: Path) -> list[Check]:
    data = json.loads(expected_path.read_text(encoding="utf-8"))
    statements = data.get("statements", {})
    key = next((k for k in statements if Path(k).name == pdf.name), None)
    if key is None:
        return []
    statement_expected = statements[key]
    checks: list[Check] = []
    for name, section in result.sections.items():
        expected = statement_expected.get("sections", {}).get(name)
        if expected is None:
            continue
        checks.extend(
            module_for(name).checks(section, layout["sections"][name], expected, statement_expected)
        )
    return checks


def _summary(result, layout: dict, checks: list[Check]) -> dict:
    def section_summary(name: str, section: SectionResult) -> dict:
        amount_column = layout["sections"][name].get("amount_column")
        return {
            "pages": section.pages,
            "row_count": section.row_count,
            "amount_column": amount_column,
            "sum": str(section.sum_of(amount_column)) if amount_column else None,
            "printed_totals": [
                {"label": t.label, "value": str(t.value), "page": t.page}
                for t in section.printed_totals
            ],
            "currencies": sorted(section.currencies),
        }

    return {
        "pdf": result.pdf,
        "layout_version": layout.get("version"),
        "layout_generated_from": layout.get("generated_from"),
        "sections": {name: section_summary(name, s) for name, s in result.sections.items()},
        "absent_sections": result.absent,
        "checks": [
            {
                "section": c.section,
                "name": c.name,
                "passed": c.passed,
                "extracted": str(c.extracted),
                "expected": str(c.expected),
                "page": c.page,
            }
            for c in checks
        ],
        "reconciled": all(c.passed for c in checks) and bool(checks),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parser de statements Morgan Stanley.")
    parser.add_argument("pdf")
    parser.add_argument("--out", default="out", help="diretório de saída (default: out/)")
    parser.add_argument("--layout", default="layout.json")
    parser.add_argument("--expected", default="tests/expected.json",
                        help="gabarito; se o statement lá estiver, a reconciliação completa corre")
    parser.add_argument("--section", action="append", dest="sections")
    parser.add_argument("--force", action="store_true",
                        help="escreve mesmo com reconciliação falhada (ficheiros .PARTIAL)")
    args = parser.parse_args(argv)

    pdf = Path(args.pdf)
    out_dir = Path(args.out)

    try:
        layout = load_layout(args.layout)
        result = parse_statement(pdf, layout, only=args.sections)
    except (LayoutError, ScannedPdfError, SectionError, MissingSectionError, ValueError,
            FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    checks = internal_checks(result, layout)
    expected_path = Path(args.expected)
    if expected_path.exists():
        try:
            checks.extend(gabarito_checks(result, layout, expected_path, pdf))
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"ERRO ao ler {expected_path}: {exc}", file=sys.stderr)
            return 2

    failures = [c for c in checks if not c.passed]
    for check in checks:
        print(check.format())

    if failures and not args.force:
        print(
            f"\nFALHA: {len(failures)} de {len(checks)} reconciliações não bateram. "
            "Nada foi escrito.",
            file=sys.stderr,
        )
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".PARTIAL" if failures else ""
    written: list[Path] = []
    for name, section in result.sections.items():
        path = out_dir / f"{name}{suffix}.csv"
        section_dataframe(section).to_csv(path, index=False)
        written.append(path)

    summary_path = out_dir / f"summary{suffix}.json"
    summary_path.write_text(
        json.dumps(_summary(result, layout, checks), indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    written.append(summary_path)

    for path in written:
        print(f"escrito: {path}")

    if failures:
        print(
            f"\nAVISO: saída marcada .PARTIAL — {len(failures)} reconciliações falharam. "
            "Não uses estes CSV como se estivessem certos.",
            file=sys.stderr,
        )
        return 1

    print(f"\nOK: {len(checks)} reconciliações conciliadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
