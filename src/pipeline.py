"""Cola: PDF + layout.json -> secções normalizadas.

Uma única leitura do PDF; as linhas são depois distribuídas pelas secções
segundo o índice de páginas do layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import pandas as pd

from .lines import Line, extract_lines, lines_in_band
from .reconcile import Check
from .precheck import TextLayerReport, assert_text_layer
from .router import (
    account_for_page,
    missing_sections,
    pages_for_section,
    section_names,
    section_spec,
)
from .sections import module_for
from .sections.base import ParseContext, SectionResult

__all__ = ["StatementResult", "parse_statement", "section_dataframe", "DISPLAY_DROP"]

# Colunas de diagnóstico: úteis no CSV, ruído no resumo.
DISPLAY_DROP = ("source_text",)


@dataclass
class StatementResult:
    pdf: str
    sections: dict[str, SectionResult] = field(default_factory=dict)
    absent: list[str] = field(default_factory=list)
    text_layer: TextLayerReport | None = None

    def __getitem__(self, name: str) -> SectionResult:
        return self.sections[name]


def parse_statement(
    pdf_path: str | Path,
    layout: dict,
    *,
    only: Sequence[str] | None = None,
    skip_precheck: bool = False,
) -> StatementResult:
    path = Path(pdf_path)
    report = None if skip_precheck else assert_text_layer(path)

    absent = missing_sections(layout)
    wanted = list(only) if only else section_names(layout)
    unknown = [name for name in wanted if name in absent]
    if unknown:
        raise ValueError(
            f"secções {unknown} estão marcadas como ausentes em layout.json para este statement"
        )

    body_band = layout.get("body_band")
    pages_by_section = {name: pages_for_section(layout, name) for name in wanted}
    all_pages = sorted({page for pages in pages_by_section.values() for page in pages})

    lines = extract_lines(
        path,
        body_band=body_band,
        pages=all_pages,
        y_tolerance=layout.get("y_tolerance", 2.5),
    )
    by_page: dict[int, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)

    context = ParseContext(
        statement_year=layout.get("statement_year"),
        page_accounts={page: account_for_page(layout, page) for page in all_pages
                       if account_for_page(layout, page)},
    )

    result = StatementResult(pdf=str(path), absent=absent, text_layer=report)
    for name in wanted:
        spec = section_spec(layout, name)
        section_lines = [line for page in pages_by_section[name] for line in by_page.get(page, [])]
        if not section_lines:
            raise ValueError(
                f"secção {name!r}: páginas {pages_by_section[name]} sem linhas dentro da "
                "banda do corpo. Revê body_band no layout.json."
            )
        result.sections[name] = module_for(name).parse(section_lines, spec, context)
    return result


def section_dataframe(result: SectionResult, *, drop_diagnostics: bool = False) -> pd.DataFrame:
    frame = pd.DataFrame(result.rows)
    if drop_diagnostics:
        frame = frame.drop(columns=[c for c in DISPLAY_DROP if c in frame.columns])
    return frame


@dataclass
class SweepResult:
    """Varredura automática: todas as tabelas do documento, com as conferências."""

    pdf: str
    tables: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    text_layer: TextLayerReport | None = None

    @property
    def failures(self) -> list:
        return [check for check in self.checks if check.fatal]

    @property
    def warnings(self) -> list:
        return [check for check in self.checks if not check.passed and not check.fatal]


def sweep_statement(
    pdf_path: str | Path,
    layout: dict,
    *,
    skip_precheck: bool = False,
    pages: dict[int, list[Line]] | None = None,
):
    """PDF + layout -> todas as tabelas extraídas e reconciliadas.

    `pages` são as linhas já lidas (sem corte de banda), para não reler o PDF.
    """
    from .tables import (
        cross_checks,
        detect_tables,
        extract_table,
        period_hint,
        refine_amount_column,
        table_checks,
    )

    path = Path(pdf_path)
    report = None if skip_precheck else assert_text_layer(path)

    if pages is None:
        lines = extract_lines(
            path,
            body_band=layout.get("body_band"),
            y_tolerance=layout.get("y_tolerance", 2.5),
        )
        by_page: dict[int, list[Line]] = {}
        for line in lines:
            by_page.setdefault(line.page, []).append(line)
    else:
        by_page = lines_in_band(pages, layout.get("body_band"))

    account_of_page = {page: account_for_page(layout, page) for page in by_page}
    notes: list[str] = []
    specs = detect_tables(
        by_page,
        account_of_page=account_of_page,
        notes=notes,
        period=period_hint(layout),
    )

    year = layout.get("statement_year")
    results = [extract_table(spec, statement_year=year) for spec in specs]

    for result in results:
        nota = refine_amount_column(result)
        if nota:
            notes.append(nota)

    checks = [check for result in results for check in table_checks(result)]
    checks.extend(cross_checks(results))
    checks.extend(
        Check(
            section="documento",
            name="bloco ignorado",
            passed=False,
            extracted=note,
            expected="—",
            detail="conteúdo que a varredura não conseguiu tratar como tabela",
            severity="aviso",
        )
        for note in notes
    )

    return SweepResult(pdf=str(path), tables=results, checks=checks, text_layer=report)
