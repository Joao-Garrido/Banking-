#!/usr/bin/env python3
"""Entrypoint.

    python parse.py statement.pdf

Faz tudo: verifica que o PDF tem texto, mapeia o layout (contas, banda do corpo,
ano do período), varre todas as tabelas do documento, reconcilia cada uma contra
os totais que ela própria imprime, e escreve um Excel em `out/`.

O layout é gerado automaticamente na primeira vez e guardado em `.layouts/`;
nas seguintes é reutilizado. `--relayout` força voltar a gerá-lo.

Se alguma reconciliação falhar, o ficheiro sai com o sufixo `.PARTIAL` e o
comando devolve código ≠ 0. Com `--strict`, não escreve nada nesse caso.

Modo alternativo `--sections`: usa as secções curadas do layout (holdings,
activity, income, fees) e escreve CSV + summary.json. É o caminho da Fase 3 do
roadmap, para quando uma secção já tem gabarito próprio em tests/expected.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.excel import table_state, write_workbook
from src.export import Explosion, build_export, explosion_checks
from src.model import build_datasets, dataset_checks, portfolio_checks
from src.lines import read_pages
from src.mapper import build_layout
from src.pipeline import parse_statement, section_dataframe, sweep_statement
from src.precheck import ScannedPdfError
from src.reconcile import Check, compare
from src.router import LayoutError, MissingSectionError, load_layout
from src.sections import module_for
from src.sections.base import SectionError, SectionResult

LAYOUT_CACHE = Path(".layouts")


# ------------------------------------------------------------------- layout

def resolve_layout(
    pdf: Path, explicit: str | None, *, relayout: bool
) -> tuple[dict, Path, dict | None]:
    """Layout explícito, em cache, ou gerado agora.

    Devolve também as páginas já lidas quando foi preciso ler o PDF para mapear,
    para que a varredura não o leia outra vez.
    """
    if explicit:
        return load_layout(explicit), Path(explicit), None

    cached = LAYOUT_CACHE / f"{pdf.stem}.json"
    if cached.exists() and not relayout:
        return load_layout(cached), cached, None

    print(f"a mapear o layout de {pdf.name} (só acontece uma vez por documento)…")
    pages = read_pages(pdf)
    layout = build_layout(pdf, pages=pages)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(layout, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"layout guardado em {cached} — revê-o se alguma tabela sair torta")
    for note in layout.get("_review", []):
        # As notas sobre as secções curadas só interessam ao modo --sections.
        if not any(note.startswith(f"{name}:") for name in ("holdings", "activity", "income", "fees")):
            print(f"  · {note}")
    return layout, cached, pages[0]


# --------------------------------------------------------- modo varredura

def run_sweep(pdf: Path, args) -> int:
    layout, _layout_path, pages = resolve_layout(pdf, args.layout, relayout=args.relayout)
    sweep = sweep_statement(pdf, layout, pages=pages)

    accounts = layout.get("accounts", [])
    datasets = build_datasets(sweep.tables, accounts, collapse_lots=not args.lotes)
    # As vistas consolidadas também são conferidas: não podem perder nem
    # duplicar dinheiro em relação às tabelas de onde vieram.
    sweep.checks.extend(dataset_checks(datasets, sweep.tables))
    sweep.checks.extend(portfolio_checks(datasets, sweep.tables, accounts))

    clients = _load_json(args.clientes, "clientes")
    exclusive = _load_json(args.fundos_exclusivos, "fundos exclusivos")
    # Sem fundos exclusivos declarados não há nada para explodir: gerar três
    # folhas iguais seria ruído.
    modes = args.explosao or (list(Explosion.ALL) if exclusive else [Explosion.NONE])
    export = build_export(
        datasets, layout, clients=clients, exclusive_funds=exclusive,
        moeda=args.moeda, modes=modes,
    )
    sweep.checks.extend(explosion_checks(export, exclusive))
    for note in export.notes:
        print(f"  · {note}")

    conciliadas = [c for c in sweep.checks if c.passed]
    print(f"\n{len(sweep.tables)} tabelas · {len(conciliadas)} conferências conciliadas · "
          f"{len(sweep.failures)} falhas · {len(sweep.warnings)} avisos")

    for check in sweep.failures:
        print(check.format())

    if sweep.failures and args.strict:
        print("\nFALHA: --strict e há reconciliações por bater. Nada foi escrito.", file=sys.stderr)
        return 1

    suffix = ".PARTIAL" if sweep.failures else ""
    destination = Path(args.out) / f"{pdf.stem}{suffix}.xlsx"
    write_workbook(
        destination,
        pdf=str(pdf),
        layout=layout,
        tables=sweep.tables,
        checks=sweep.checks,
        datasets=datasets,
        export=export,
        include_diagnostics=not args.no_diagnostics,
        include_raw=not args.no_raw,
    )
    print(f"\nescrito: {destination}")

    if args.csv:
        for result in sweep.tables:
            path = Path(args.out) / "csv" / f"{result.spec.id}.csv"
            _write_table_csv(path, result)
        print(f"escrito: {Path(args.out) / 'csv'}/ ({len(sweep.tables)} ficheiros)")

    from collections import Counter

    estados = Counter(table_state(result.spec, sweep.checks) for result in sweep.tables)
    print("tabelas: " + " · ".join(f"{estado} {contagem}" for estado, contagem in estados.most_common()))

    if sweep.failures:
        print(
            f"\nAVISO: saída marcada .PARTIAL — {len(sweep.failures)} reconciliações falharam. "
            "Vê a folha 'Reconciliação' antes de usar estes números.",
            file=sys.stderr,
        )
        return 1

    print("Sem contradições entre o extraído e os totais impressos.")
    return 0


def _load_json(path: str | None, what: str) -> dict:
    if not path:
        return {}
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"ficheiro de {what} não encontrado: {file}")
    return json.loads(file.read_text(encoding="utf-8"))


def _write_table_csv(path: Path, result) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["row_type", "group", "description"]
    columns += [c["name"] for c in result.spec.columns if c["name"] != "description"]
    columns += ["source_page", "account"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.rows)


# ------------------------------------------------------------- modo secções

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


def run_sections(pdf: Path, args) -> int:
    layout, _layout_path, _pages = resolve_layout(pdf, args.layout, relayout=args.relayout)
    result = parse_statement(pdf, layout, only=args.sections_only)

    checks = internal_checks(result, layout)
    expected_path = Path(args.expected)
    if expected_path.exists():
        checks.extend(gabarito_checks(result, layout, expected_path, pdf))

    failures = [c for c in checks if c.fatal]
    for check in checks:
        print(check.format())

    out_dir = Path(args.out)
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
            f"\nAVISO: saída marcada .PARTIAL — {len(failures)} reconciliações falharam.",
            file=sys.stderr,
        )
        return 1

    print(f"\nOK: {len(checks)} reconciliações conciliadas.")
    return 0


# ------------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Statement Morgan Stanley -> Excel reconciliado.",
    )
    parser.add_argument("pdf")
    parser.add_argument("--out", default="out", help="diretório de saída (default: out/)")
    parser.add_argument("--layout", help="layout.json explícito (default: gerado e cacheado)")
    parser.add_argument("--relayout", action="store_true", help="volta a mapear o layout")
    parser.add_argument("--csv", action="store_true", help="escreve também um CSV por tabela")
    parser.add_argument("--strict", action="store_true",
                        help="não escreve nada se alguma reconciliação falhar")
    parser.add_argument("--no-diagnostics", action="store_true",
                        help="omite a coluna com o texto original de cada linha")
    parser.add_argument("--lotes", action="store_true",
                        help="uma linha por lote em vez de uma por título (mantém a data de "
                             "compra e o custo de cada lote)")
    parser.add_argument("--clientes", help="JSON conta -> {idcliente, nome} para o IDCLIENTE")
    parser.add_argument("--fundos-exclusivos", dest="fundos_exclusivos",
                        help="JSON IDATIVO -> {nome, carteira[]} dos fundos exclusivos")
    parser.add_argument("--explosao", action="append", choices=list(Explosion.ALL),
                        help="visão de posições a gerar; repetível (default: todas se houver "
                             "fundos exclusivos declarados, senão só 'nao')")
    parser.add_argument("--moeda", default="USD", help="moeda do statement (default: USD)")
    parser.add_argument("--no-raw", action="store_true",
                        help="omite as folhas com as tabelas em bruto (só as vistas)")
    parser.add_argument("--sections", action="store_true",
                        help="modo secções curadas (CSV + summary.json) em vez da varredura")
    parser.add_argument("--section", action="append", dest="sections_only",
                        help="no modo --sections, limita a uma secção")
    parser.add_argument("--expected", default="tests/expected.json",
                        help="gabarito usado no modo --sections")
    parser.add_argument("--force", action="store_true",
                        help="no modo --sections, escreve mesmo com falhas (.PARTIAL)")
    args = parser.parse_args(argv)

    pdf = Path(args.pdf)
    try:
        if args.sections or args.sections_only:
            return run_sections(pdf, args)
        return run_sweep(pdf, args)
    except (LayoutError, ScannedPdfError, SectionError, MissingSectionError, ValueError,
            FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
