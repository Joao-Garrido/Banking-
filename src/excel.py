"""Escrita do livro Excel.

Uma folha por tabela encontrada, mais três folhas de leitura: `Resumo`,
`Reconciliação` e `Totais impressos`. O estado de cada tabela é sempre
explícito — conciliado, não conciliado ou não verificável. Nunca implícito.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .reconcile import Check

__all__ = ["write_workbook", "table_state"]

MONEY_FORMAT = "#,##0.00"
DATE_FORMAT = "yyyy-mm-dd"
MAX_SHEET_NAME = 31
_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STATE_FILLS = {
    "conciliado": PatternFill("solid", fgColor="C6EFCE"),
    "não conciliado": PatternFill("solid", fgColor="FFC7CE"),
    "parcial": PatternFill("solid", fgColor="FFEB9C"),
    "não verificável": PatternFill("solid", fgColor="E7E6E6"),
}
# Cor do separador de cada folha: o estado vê-se sem abrir a tabela.
STATE_TAB_COLOR = {
    "conciliado": "70AD47",
    "não conciliado": "C00000",
    "parcial": "FFC000",
    "não verificável": "A6A6A6",
}

LEGENDA = [
    ("conciliado", "a soma do que foi extraído bate com o total impresso"),
    ("parcial", "alguma conferência passou e outra ficou por provar"),
    ("não conciliado", "não bate, e sabemos que devia bater"),
    ("não verificável", "a tabela não imprime total: extraímos, mas não há prova"),
]

DIAGNOSTIC_COLUMNS = ("source_text",)


def checks_for(spec, checks: Sequence[Check]) -> list[Check]:
    """Conferências que dizem respeito a esta tabela."""
    pages = set(spec.pages)
    return [
        check
        for check in checks
        if check.section == spec.title and (check.page is None or check.page in pages)
    ]


def table_state(spec, checks: Sequence[Check]) -> str:
    """Estado de uma tabela, do mais grave para o mais brando.

    'parcial' é o caso honesto do meio: alguma coisa ficou provada e outra não.
    Chamar-lhe conciliado seria dizer mais do que sabemos.
    """
    mine = checks_for(spec, checks)
    if any(check.fatal for check in mine):
        return "não conciliado"
    passed = [check for check in mine if check.passed]
    if not passed:
        return "não verificável"
    if len(passed) == len(mine):
        return "conciliado"
    return "parcial"


def _checks_label(spec, checks: Sequence[Check]) -> str:
    mine = checks_for(spec, checks)
    return f"{sum(1 for c in mine if c.passed)}/{len(mine)}" if mine else "0/0"


def _sheet_name(used: set[str], account: str | None, title: str) -> str:
    prefix = account.split("-")[1] if account and "-" in account else (account or "geral")
    base = _INVALID_SHEET.sub(" ", f"{prefix} {title}").strip()
    name = base[:MAX_SHEET_NAME]
    suffix = 2
    while name in used:
        tail = f" ({suffix})"
        name = base[: MAX_SHEET_NAME - len(tail)] + tail
        suffix += 1
    used.add(name)
    return name


def _cell_value(value: Any) -> Any:
    """Decimal -> float para o Excel; o resto passa como está.

    A reconciliação já correu toda em Decimal: o que vai para a folha é uma
    representação, não a base de cálculo.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)


def _style_header(sheet, columns: Sequence[str], widths: dict[str, int] | None = None) -> None:
    for index, name in enumerate(columns, start=1):
        cell = sheet.cell(row=1, column=index, value=name)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = (widths or {}).get(name, 18)
    sheet.freeze_panes = "A2"


def _write_rows(sheet, columns: Sequence[str], rows: Sequence[dict]) -> None:
    for row_index, row in enumerate(rows, start=2):
        for column_index, name in enumerate(columns, start=1):
            value = _cell_value(row.get(name))
            cell = sheet.cell(row=row_index, column=column_index, value=value)
            if isinstance(value, float):
                cell.number_format = MONEY_FORMAT
            elif isinstance(value, (date, datetime)):
                cell.number_format = DATE_FORMAT
    if rows:
        sheet.auto_filter.ref = (
            f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
        )


def write_workbook(
    path: str | Path,
    *,
    pdf: str,
    layout: dict,
    tables: Sequence,
    checks: Sequence[Check],
    include_diagnostics: bool = True,
) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)

    accounts = {a["id"]: (a.get("label") or "") for a in layout.get("accounts", [])}
    used_names: set[str] = set()

    summary_rows: list[dict] = []
    printed_totals: list[dict] = []

    for result in tables:
        spec = result.spec
        name = _sheet_name(used_names, spec.account, spec.title)
        state = table_state(spec, checks)
        amount_column = spec.amount_column

        summary_rows.append(
            {
                "conta": spec.account,
                "designação da conta": accounts.get(spec.account, ""),
                "tabela": spec.title,
                "folha": name,
                "páginas": f"{spec.pages[0]}-{spec.pages[-1]}" if spec.pages else "",
                "linhas de dados": len(result.data_rows),
                "coluna de valor": amount_column,
                "soma extraída": result.sum_of(amount_column) if amount_column else None,
                # O mesmo número contra o qual a conferência foi feita — mostrar
                # aqui outra soma faria a folha contradizer-se a si própria.
                "total impresso": result.reference_total,
                "conferências": _checks_label(spec, checks),
                "estado": state,
            }
        )

        for row in result.total_rows:
            printed_totals.append(
                {
                    "conta": spec.account,
                    "tabela": spec.title,
                    "página": row.get("source_page"),
                    "rótulo": row.get("description"),
                    "valor": row.get(amount_column) if amount_column else None,
                }
            )

        columns = ["row_type", "group", "description"]
        columns += [c["name"] for c in spec.columns if c["name"] != "description"]
        columns += ["source_page", "account"]
        if include_diagnostics:
            columns += [c for c in DIAGNOSTIC_COLUMNS]

        sheet = workbook.create_sheet(name)
        sheet.sheet_properties.tabColor = STATE_TAB_COLOR.get(state)
        _style_header(
            sheet,
            columns,
            widths={"description": 60, "group": 34, "row_type": 10, "source_text": 80},
        )
        _write_rows(sheet, columns, result.rows)

    _write_summary(workbook, pdf, layout, summary_rows)
    _write_checks(workbook, checks)
    _write_printed_totals(workbook, printed_totals)

    # As folhas de leitura ficam à frente das tabelas.
    order = ["Resumo", "Reconciliação", "Totais impressos"]
    workbook._sheets.sort(key=lambda s: (order.index(s.title) if s.title in order else len(order)))

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination


def _write_summary(workbook: Workbook, pdf: str, layout: dict, rows: Sequence[dict]) -> None:
    sheet = workbook.create_sheet("Resumo")
    columns = [
        "conta",
        "designação da conta",
        "tabela",
        "folha",
        "páginas",
        "linhas de dados",
        "coluna de valor",
        "soma extraída",
        "total impresso",
        "conferências",
        "estado",
    ]
    _style_header(sheet, columns, widths={"tabela": 46, "designação da conta": 30, "folha": 32})
    _write_rows(sheet, columns, rows)

    for index, row in enumerate(rows, start=2):
        cell = sheet.cell(row=index, column=columns.index("estado") + 1)
        fill = STATE_FILLS.get(row["estado"])
        if fill:
            cell.fill = fill

        link = sheet.cell(row=index, column=columns.index("folha") + 1)
        link.value = f'=HYPERLINK("#\'{row["folha"]}\'!A1","{row["folha"]}")'
        link.style = "Hyperlink"

    footer = len(rows) + 3
    sheet.cell(row=footer, column=1, value="documento").font = Font(bold=True)
    sheet.cell(row=footer, column=2, value=Path(pdf).name)
    sheet.cell(row=footer + 1, column=1, value="período").font = Font(bold=True)
    sheet.cell(row=footer + 1, column=2, value=layout.get("statement_year"))
    sheet.cell(row=footer + 2, column=1, value="páginas").font = Font(bold=True)
    sheet.cell(row=footer + 2, column=2, value=layout.get("page_count"))
    sheet.cell(row=footer + 3, column=1, value="aviso").font = Font(bold=True)
    sheet.cell(
        row=footer + 3,
        column=2,
        value=(
            "Linhas marcadas 'subtotal', 'total' e 'info' não entram na soma extraída — "
            "somá-las de novo conta o mesmo dinheiro duas vezes."
        ),
    )

    sheet.cell(row=footer + 5, column=1, value="legenda").font = Font(bold=True)
    for offset, (estado, explicacao) in enumerate(LEGENDA):
        line = footer + 5 + offset
        cell = sheet.cell(row=line, column=2, value=estado)
        fill = STATE_FILLS.get(estado)
        if fill:
            cell.fill = fill
        sheet.cell(row=line, column=3, value=explicacao)


def _write_checks(workbook: Workbook, checks: Sequence[Check]) -> None:
    sheet = workbook.create_sheet("Reconciliação")
    columns = ["estado", "tabela", "verificação", "extraído", "esperado", "delta", "página", "nota"]
    _style_header(
        sheet,
        columns,
        widths={"tabela": 40, "verificação": 34, "nota": 90, "estado": 14},
    )

    # Falhas primeiro, depois o que ficou por provar: quem abre esta folha quer
    # ver o que está mal, não percorrer trinta linhas verdes até lá chegar.
    ordered = sorted(
        checks, key=lambda c: (0 if c.fatal else (1 if not c.passed else 2), c.section)
    )

    rows = []
    for check in ordered:
        rows.append(
            {
                "estado": "PASS" if check.passed else ("FAIL" if check.fatal else "AVISO"),
                "tabela": check.section,
                "verificação": check.name,
                "extraído": check.extracted,
                "esperado": check.expected,
                "delta": check.delta,
                "página": check.page,
                "nota": check.detail,
            }
        )
    _write_rows(sheet, columns, rows)

    for index, row in enumerate(rows, start=2):
        cell = sheet.cell(row=index, column=1)
        fill = {
            "PASS": STATE_FILLS["conciliado"],
            "FAIL": STATE_FILLS["não conciliado"],
            "AVISO": STATE_FILLS["não verificável"],
        }[row["estado"]]
        cell.fill = fill


def _write_printed_totals(workbook: Workbook, rows: Sequence[dict]) -> None:
    sheet = workbook.create_sheet("Totais impressos")
    columns = ["conta", "tabela", "página", "rótulo", "valor"]
    _style_header(sheet, columns, widths={"tabela": 40, "rótulo": 60})
    _write_rows(sheet, columns, rows)
