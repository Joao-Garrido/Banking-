#!/usr/bin/env python3
"""Arnês de reconciliação — o gate do projeto.

Corre o parser sobre as fixtures, soma o que foi extraído e compara com os
totais que foram digitados à mão a partir do documento impresso.

Falha nomeando secção e página. Enquanto não houver parser, layout ou
expected.json, falha à mesma — de forma legível, nunca com traceback.

    python verify.py                          # todas as fixtures, todas as secções
    python verify.py --section holdings       # só uma secção
    python verify.py tests/fixtures/x.pdf     # um statement específico
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.precheck import ScannedPdfError
from src.reconcile import Check
from src.router import LayoutError, MissingSectionError, load_layout, section_names
from src.sections import KNOWN_SECTIONS, module_for
from src.sections.base import SectionError

EXPECTED_PATH = Path("tests/expected.json")
LAYOUT_PATH = Path("layout.json")

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def _colour(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}" if sys.stdout.isatty() else text


def load_expected(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} não existe. É a Fase 0 do roadmap: abre o PDF e digita à mão os "
            "totais impressos (total do portfólio, subtotal e contagem de linhas por secção). "
            "Sem este ficheiro não há como saber se o parser está certo."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    statements = data.get("statements")
    if not statements:
        raise ValueError(f"{path}: nenhum statement declarado em 'statements'.")
    return data


def verify_statement(
    pdf: str,
    statement_expected: dict,
    layout: dict,
    *,
    only: list[str] | None,
) -> tuple[list[Check], list[str]]:
    """Devolve (checks, erros). Um erro é uma falha que impede o check de correr."""
    from src.pipeline import parse_statement  # import tardio: pdfplumber é lento a carregar

    errors: list[str] = []
    checks: list[Check] = []

    expected_sections = statement_expected.get("sections", {})
    available = section_names(layout)
    wanted = only or [name for name in available if name in expected_sections] or available

    unknown = [name for name in wanted if name not in KNOWN_SECTIONS]
    if unknown:
        errors.append(f"secções sem módulo: {unknown}")
        wanted = [name for name in wanted if name in KNOWN_SECTIONS]

    not_in_layout = [name for name in wanted if name not in available]
    if not_in_layout:
        errors.append(
            f"secções pedidas mas ausentes do layout deste statement: {not_in_layout}"
        )
        wanted = [name for name in wanted if name in available]

    if not wanted:
        return checks, errors or ["nada a verificar"]

    try:
        result = parse_statement(pdf, layout, only=wanted)
    except (ScannedPdfError, SectionError, MissingSectionError, ValueError, FileNotFoundError) as exc:
        errors.append(str(exc))
        return checks, errors

    for name in wanted:
        section = result.sections[name]
        spec = layout["sections"][name]
        expected = expected_sections.get(name)
        if expected is None:
            errors.append(
                f"secção {name!r} extraída ({section.row_count} registos) mas sem entrada em "
                "expected.json — sem gabarito, o resultado não é verificável"
            )
            continue
        checks.extend(module_for(name).checks(section, spec, expected, statement_expected))

    if result.absent:
        print(f"  {_colour('nota', YELLOW)}: secções declaradas ausentes: {result.absent}")

    return checks, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcilia o parser contra os totais impressos.")
    parser.add_argument("pdf", nargs="?", help="statement específico (default: todas as fixtures)")
    parser.add_argument("--section", action="append", dest="sections", help="limita a uma secção")
    parser.add_argument("--layout", default=str(LAYOUT_PATH))
    parser.add_argument("--expected", default=str(EXPECTED_PATH))
    args = parser.parse_args(argv)

    try:
        expected_data = load_expected(Path(args.expected))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"{_colour('FAIL', RED)} — gabarito indisponível\n  {exc}")
        return 2

    try:
        layout = load_layout(args.layout)
    except (LayoutError, json.JSONDecodeError) as exc:
        print(f"{_colour('FAIL', RED)} — layout indisponível\n  {exc}")
        return 2

    statements = expected_data["statements"]
    if args.pdf:
        key = next((k for k in statements if Path(k).name == Path(args.pdf).name), None)
        if key is None:
            print(
                f"{_colour('FAIL', RED)} — {args.pdf} não tem gabarito em {args.expected}.\n"
                f"  Statements com gabarito: {list(statements)}"
            )
            return 2
        statements = {key: statements[key]}

    total_checks = 0
    failures: list[Check] = []
    errors: list[str] = []

    for pdf, statement_expected in statements.items():
        print(f"\n{pdf}")
        if not Path(pdf).exists():
            message = f"fixture não encontrada: {pdf}"
            print(f"  {_colour('FAIL', RED)} {message}")
            errors.append(message)
            continue

        checks, section_errors = verify_statement(
            pdf, statement_expected, layout, only=args.sections
        )
        for check in checks:
            total_checks += 1
            if not check.passed:
                failures.append(check)
            print(check.format())
        for message in section_errors:
            errors.append(f"{pdf}: {message}")
            print(f"  {_colour('FAIL', RED)} {message}")

    print("\n" + "-" * 72)
    if failures or errors or total_checks == 0:
        print(
            f"{_colour('FAIL', RED)} — {len(failures)} de {total_checks} verificações falharam, "
            f"{len(errors)} erro(s)."
        )
        for check in failures:
            print(check.format())
        for message in errors:
            print(f"  · {message}")
        if total_checks == 0 and not errors:
            print("  · nenhuma verificação correu — não confundas isto com sucesso.")
        return 1

    print(f"{_colour('PASS', GREEN)} — {total_checks} verificações, todas conciliadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
