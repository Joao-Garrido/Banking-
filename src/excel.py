"""Escrita do livro Excel.

O livro é organizado como um dossier de família, não como um decalque do PDF:

    Capa · Posições · Alocação · Movimentos · Rendimento · Mais-valias ·
    Transferências · Sumário por conta · Reconciliação · Totais impressos ·
    Tabelas (índice) · uma folha por tabela do documento

As primeiras folhas são as vistas consolidadas entre contas, com os nomes de
campo em português e uma linha de total sempre visível. As últimas são o
documento tal como foi lido, para auditoria: nenhuma linha da vista consolidada
existe sem que a linha original esteja no livro, com página e tabela de origem.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .model import (
    DATE_FIELDS,
    DOMAIN_ORDER,
    SUMMABLE_DOMAINS,
    account_summaries,
    FIELD_LABELS,
    FIELD_WIDTHS,
    MONEY_FIELDS,
    PERCENT_FIELDS,
    QUANTITY_FIELDS,
    Dataset,
    Domain,
)
from .export import (
    MOVIMENTO_COLUMNS,
    POSICAO_COLUMNS,
    TIPO_MOVIMENTO,
    Explosion,
    ExportSet,
)
from .reconcile import Check

__all__ = ["write_workbook", "table_state", "checks_for"]

MONEY_FORMAT = "#,##0.00"
QUANTITY_FORMAT = "#,##0.000"
PERCENT_FORMAT = '#,##0.00"%"'
DATE_FORMAT = "yyyy-mm-dd"
MAX_SHEET_NAME = 31
_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")

INK = "1F3864"
HEADER_FILL = PatternFill("solid", fgColor=INK)
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=16, color=INK)
LABEL_FONT = Font(bold=True)
TOTAL_FILL = PatternFill("solid", fgColor="D9E2F3")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(top=THIN, bottom=THIN)

STATE_FILLS = {
    "conciliado": PatternFill("solid", fgColor="C6EFCE"),
    "não conciliado": PatternFill("solid", fgColor="FFC7CE"),
    "parcial": PatternFill("solid", fgColor="FFEB9C"),
    "não verificável": PatternFill("solid", fgColor="E7E6E6"),
}
STATE_TAB_COLOR = {
    "conciliado": "70AD47",
    "não conciliado": "C00000",
    "parcial": "FFC000",
    "não verificável": "A6A6A6",
}
VIEW_TAB_COLOR = INK

LEGENDA = [
    ("conciliado", "a soma do que foi extraído bate com o total impresso"),
    ("parcial", "alguma conferência passou e outra ficou por provar"),
    ("não conciliado", "não bate, e sabemos que devia bater"),
    ("não verificável", "a tabela não imprime total: extraímos, mas não há prova"),
]

DIAGNOSTIC_COLUMNS = ("source_text",)


# ------------------------------------------------------------------- estados

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


# --------------------------------------------------------------- primitivas

def _sheet_name(used: set[str], base: str) -> str:
    """Nome único de folha. A comparação é sem maiúsculas porque é assim que o
    Excel decide: 'POSICOES' e 'Posições' seriam a mesma folha para ele."""
    name = _INVALID_SHEET.sub(" ", base).strip()[:MAX_SHEET_NAME]
    suffix = 2
    while name.casefold() in used:
        tail = f" ({suffix})"
        name = name[: MAX_SHEET_NAME - len(tail)] + tail
        suffix += 1
    used.add(name.casefold())
    return name


def _value(value: Any) -> Any:
    """Decimal -> float para o Excel; o resto passa como está.

    A reconciliação correu toda em Decimal: o que vai para a folha é uma
    representação, não a base de cálculo.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, int, float, str, bool)) or value is None:
        return value
    return str(value)


# Formato das colunas do layout de exportação, pelo nome.
_EXPORT_MONEY = re.compile(r"^(VLR_|VALOR|PU$|CUSTO_UNITARIO|RESULTADO_|RENDIMENTO_)")
_EXPORT_QUANTITY = re.compile(r"^QUANTIDADE$")
_EXPORT_PERCENT = re.compile(r"^(TAXA_YIELD|PESO_PCT)$")
_EXPORT_DATE = re.compile(r"^DT_")


def _export_format(column: str) -> str | None:
    if _EXPORT_MONEY.match(column):
        return MONEY_FORMAT
    if _EXPORT_QUANTITY.match(column):
        return QUANTITY_FORMAT
    if _EXPORT_PERCENT.match(column):
        return PERCENT_FORMAT
    if _EXPORT_DATE.match(column):
        return DATE_FORMAT
    return None


def _number_format(field: str, value: Any) -> str | None:
    export_format = _export_format(field)
    if export_format:
        return export_format
    if field in MONEY_FIELDS:
        return MONEY_FORMAT
    if field in QUANTITY_FIELDS:
        return QUANTITY_FORMAT
    if field in PERCENT_FIELDS:
        return PERCENT_FORMAT
    if field in DATE_FIELDS or isinstance(value, (date, datetime)):
        return DATE_FORMAT
    if isinstance(value, float):
        return MONEY_FORMAT
    return None


def _write_table(
    sheet,
    fields: Sequence[str],
    rows: Sequence[dict],
    *,
    labels: dict[str, str] | None = None,
    widths: dict[str, int] | None = None,
    total_fields: Sequence[str] = (),
    total_rows: Sequence[dict] | None = None,
    start_row: int = 1,
) -> int:
    labels = labels or FIELD_LABELS
    widths = widths or FIELD_WIDTHS

    for index, field in enumerate(fields, start=1):
        cell = sheet.cell(row=start_row, column=index, value=labels.get(field, field))
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=False)
        sheet.column_dimensions[get_column_letter(index)].width = widths.get(field, 16)

    body_start = start_row + 1
    if total_fields:
        # Linha de total logo abaixo do cabeçalho: fica sempre à vista, mesmo
        # com dez mil linhas por baixo.
        sheet.cell(row=body_start, column=1, value="TOTAL").font = LABEL_FONT
        for index, field in enumerate(fields, start=1):
            cell = sheet.cell(row=body_start, column=index)
            cell.fill = TOTAL_FILL
            cell.border = BOX
            if field in total_fields:
                total = sum(
                    (
                        row[field]
                        for row in (total_rows if total_rows is not None else rows)
                        if isinstance(row.get(field), Decimal)
                    ),
                    Decimal("0"),
                )
                cell.value = _value(total)
                cell.font = LABEL_FONT
                cell.number_format = _number_format(field, cell.value) or MONEY_FORMAT
        body_start += 1

    for offset, row in enumerate(rows):
        excel_row = body_start + offset
        for index, field in enumerate(fields, start=1):
            value = _value(row.get(field))
            cell = sheet.cell(row=excel_row, column=index, value=value)
            fmt = _number_format(field, value)
            if fmt:
                cell.number_format = fmt

    last_row = body_start + len(rows) - 1
    sheet.freeze_panes = sheet.cell(row=body_start, column=1).coordinate
    if rows:
        sheet.auto_filter.ref = (
            f"A{start_row}:{get_column_letter(len(fields))}{max(last_row, body_start)}"
        )
    return last_row


# ------------------------------------------------------------------- livro

def write_workbook(
    path: str | Path,
    *,
    pdf: str,
    layout: dict,
    tables: Sequence,
    checks: Sequence[Check],
    datasets: dict[str, Dataset],
    export: ExportSet | None = None,
    include_diagnostics: bool = True,
    include_raw: bool = True,
) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)
    used_names: set[str] = set()

    capa = workbook.create_sheet(_sheet_name(used_names, "Capa"))
    capa.sheet_properties.tabColor = VIEW_TAB_COLOR

    view_sheets: list[tuple[str, str, int, Decimal | None]] = []

    if export is not None:
        view_sheets.extend(_write_export(workbook, export, used_names))

    for domain in DOMAIN_ORDER:
        dataset = datasets.get(domain)
        if dataset is None or not dataset.rows or domain == Domain.OTHER:
            continue
        # As folhas do layout do consolidador ficam com o nome cru (POSICOES,
        # MOVIMENTOS); a vista analítica do mesmo tema leva o sufixo, para não
        # haver dúvida sobre qual é a que se carrega no sistema.
        colide = export is not None and domain in (Domain.POSITIONS, Domain.TRANSACTIONS)
        title = f"{dataset.title} (análise)" if colide else dataset.title
        name = _sheet_name(used_names, title)
        sheet = workbook.create_sheet(name)
        sheet.sheet_properties.tabColor = VIEW_TAB_COLOR
        fields = [f for f in dataset.fields if any(f in row for row in dataset.rows)]
        somavel = domain in SUMMABLE_DOMAINS
        total_field = dataset.total_field if somavel else None
        _write_table(
            sheet,
            fields,
            dataset.rows,
            total_fields=[total_field] if total_field in fields else [],
            total_rows=dataset.data_rows,
        )
        view_sheets.append(
            (dataset.title, name, len(dataset.data_rows), dataset.total() if somavel else None)
        )

    _write_checks(workbook, _sheet_name(used_names, "Reconciliação"), checks)
    _write_printed_totals(workbook, _sheet_name(used_names, "Totais impressos"), tables)

    # Os nomes das folhas em bruto são reservados antes do índice: o índice
    # precisa de lhes apontar, e são elas que vêm depois no livro.
    index_name = _sheet_name(used_names, "Tabelas do documento")
    raw_names: dict[int, str] = {}
    if include_raw:
        for result in tables:
            spec = result.spec
            prefix = spec.account.split("-")[1] if spec.account and "-" in spec.account else "geral"
            raw_names[id(spec)] = _sheet_name(used_names, f"{prefix} {spec.title}")

    _write_table_index(workbook, index_name, tables, checks, raw_names)

    for result in tables:
        if id(result.spec) in raw_names:
            _write_raw_table(
                workbook, result, checks, raw_names[id(result.spec)], include_diagnostics
            )

    _write_capa(capa, pdf, layout, tables, checks, datasets, view_sheets, index_name)

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(destination)
    return destination


def _write_capa(
    sheet,
    pdf: str,
    layout: dict,
    tables: Sequence,
    checks: Sequence[Check],
    datasets: dict[str, Dataset],
    view_sheets: Sequence[tuple[str, str, int, Decimal | None]],
    index_name: str,
) -> None:
    for column, width in zip("ABCDE", (26, 34, 20, 20, 46)):
        sheet.column_dimensions[column].width = width

    sheet["A1"] = "Statement reconciliado"
    sheet["A1"].font = TITLE_FONT

    falhas = [c for c in checks if c.fatal]
    avisos = [c for c in checks if not c.passed and not c.fatal]
    passou = [c for c in checks if c.passed]

    linhas = [
        ("Documento", Path(pdf).name),
        ("Período", layout.get("statement_year")),
        ("Páginas", layout.get("page_count")),
        ("Contas", len(layout.get("accounts", []))),
        ("Tabelas encontradas", len(tables)),
        ("Conferências conciliadas", f"{len(passou)} de {len(checks)}"),
        ("Por provar (avisos)", len(avisos)),
        ("Contradições", len(falhas)),
    ]
    row = 3
    for label, value in linhas:
        sheet.cell(row=row, column=1, value=label).font = LABEL_FONT
        sheet.cell(row=row, column=2, value=_value(value))
        row += 1

    veredicto = (
        "CONTRADIÇÕES — ver folha Reconciliação"
        if falhas
        else "Sem contradições entre o extraído e os totais impressos"
    )
    cell = sheet.cell(row=row + 1, column=1, value=veredicto)
    cell.font = LABEL_FONT
    cell.fill = STATE_FILLS["não conciliado" if falhas else "conciliado"]
    row += 3

    # --- contas
    sheet.cell(row=row, column=1, value="Contas").font = LABEL_FONT
    row += 1
    headers = [
        "Conta", "Designação", "Páginas", "Posições", "Valor das posições",
        "Por liquidar", "Total impresso da conta", "Fecha?",
    ]
    for index, label in enumerate(headers, start=1):
        cell = sheet.cell(row=row, column=index, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for column, width in zip("FGH", (18, 24, 12)):
        sheet.column_dimensions[column].width = width
    row += 1

    for summary in account_summaries(datasets, tables, layout.get("accounts", [])):
        sheet.cell(row=row, column=1, value=summary.id)
        sheet.cell(row=row, column=2, value=summary.label)
        sheet.cell(
            row=row, column=3,
            value=f"{summary.pages[0]}-{summary.pages[-1]}" if summary.pages else "",
        )
        sheet.cell(row=row, column=4, value=summary.positions)
        for column, value in (
            (5, summary.market_value),
            (6, summary.unsettled if summary.unsettled_rows else None),
            (7, summary.printed_total),
        ):
            cell = sheet.cell(row=row, column=column, value=_value(value))
            cell.number_format = MONEY_FORMAT
        veredicto = {True: "sim", False: "NÃO", None: "sem total impresso"}[summary.reconciles]
        cell = sheet.cell(row=row, column=8, value=veredicto)
        cell.fill = STATE_FILLS[
            {True: "conciliado", False: "não conciliado", None: "não verificável"}[
                summary.reconciles
            ]
        ]
        row += 1

    row += 2
    # --- índice das vistas
    sheet.cell(row=row, column=1, value="Vistas").font = LABEL_FONT
    row += 1
    for index, label in enumerate(["Folha", "Linhas", "Total"], start=1):
        cell = sheet.cell(row=row, column=index, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    row += 1

    for title, name, count, total in view_sheets:
        link = sheet.cell(row=row, column=1)
        link.value = f'=HYPERLINK("#\'{name}\'!A1","{title}")'
        link.style = "Hyperlink"
        sheet.cell(row=row, column=2, value=count)
        cell = sheet.cell(row=row, column=3, value=_value(total) if total is not None else None)
        cell.number_format = MONEY_FORMAT
        row += 1

    link = sheet.cell(row=row, column=1)
    link.value = f'=HYPERLINK("#\'{index_name}\'!A1","{index_name}")'
    link.style = "Hyperlink"
    sheet.cell(row=row, column=2, value=len(tables))
    row += 2

    sheet.cell(row=row, column=1, value="Como ler").font = LABEL_FONT
    notas = [
        "As vistas consolidam as duas contas; cada linha aponta para a tabela e página de origem.",
        "Só as linhas 'data' entram nos totais — subtotais e totais do documento ficam de fora.",
        "'Rendimento' é derivado dos movimentos por tipo (dividendos, juros, mais-valias "
        "distribuídas), sem reinvestimentos.",
        "'Peso %' é calculado sobre o total da conta; tudo o resto é lido do documento.",
        "O valor das posições excede o total da conta quando há compras por liquidar: o "
        "statement já conta os títulos, e o dinheiro só sai na liquidação.",
        "'Alocação' e 'Sumário por conta' não levam total — são rubricas heterogéneas.",
    ]
    for nota in notas:
        row += 1
        sheet.cell(row=row, column=1, value="·")
        sheet.cell(row=row, column=2, value=nota)


def _write_export(
    workbook: Workbook, export: ExportSet, used_names: set[str]
) -> list[tuple[str, str, int, Decimal | None]]:
    """Folhas no layout do consolidador: POSICOES, MOVIMENTOS e o De-para."""
    sheets: list[tuple[str, str, int, Decimal | None]] = []
    labels = {column: column for column in POSICAO_COLUMNS + MOVIMENTO_COLUMNS}
    widths = {
        "DESCRICAO_ATIVO": 46, "DESCRICAO": 52, "CLASSE_ATIVO": 34, "IDATIVO": 24,
        "NOME_CLIENTE": 24, "CONTA": 17, "TABELA_ORIGEM": 28, "TIPO_ORIGINAL": 24,
        "PAGINA": 7, "ORIGEM_EXPLOSAO": 18, "FUNDO_EXCLUSIVO": 16,
    }

    for mode, linhas in export.posicoes.items():
        title = "POSICOES" if mode == Explosion.NONE else f"POSICOES ({mode})"
        name = _sheet_name(used_names, title)
        sheet = workbook.create_sheet(name)
        sheet.sheet_properties.tabColor = VIEW_TAB_COLOR
        _write_table(
            sheet,
            POSICAO_COLUMNS,
            linhas,
            labels=labels,
            widths=widths,
            total_fields=["VLR_BRUTO_MOEDA_ORIGINAL"],
        )
        total = sum(
            (
                linha["VLR_BRUTO_MOEDA_ORIGINAL"]
                for linha in linhas
                if isinstance(linha.get("VLR_BRUTO_MOEDA_ORIGINAL"), Decimal)
            ),
            Decimal("0"),
        )
        sheets.append((title, name, len(linhas), total))

    name = _sheet_name(used_names, "MOVIMENTOS")
    sheet = workbook.create_sheet(name)
    sheet.sheet_properties.tabColor = VIEW_TAB_COLOR
    _write_table(
        sheet, MOVIMENTO_COLUMNS, export.movimentos, labels=labels, widths=widths,
        total_fields=["VALOR"],
    )
    total_mov = sum(
        (m["VALOR"] for m in export.movimentos if isinstance(m.get("VALOR"), Decimal)),
        Decimal("0"),
    )
    sheets.append(("MOVIMENTOS", name, len(export.movimentos), total_mov))

    _write_de_para(workbook, _sheet_name(used_names, "De-para"), export)
    return sheets


def _write_de_para(workbook: Workbook, name: str, export: ExportSet) -> None:
    """O que preenche cada campo do layout — e o que fica vazio, com o motivo."""
    sheet = workbook.create_sheet(name)
    sheet.sheet_properties.tabColor = VIEW_TAB_COLOR

    fields = ["CAMPO", "ESTADO", "ORIGEM", "NOTA"]
    labels = {"CAMPO": "Campo", "ESTADO": "Estado", "ORIGEM": "Origem", "NOTA": "Nota"}
    last = _write_table(
        sheet, fields, export.mapping, labels=labels,
        widths={"CAMPO": 34, "ESTADO": 14, "ORIGEM": 60, "NOTA": 70},
    )

    for offset, row in enumerate(export.mapping, start=2):
        if row["ESTADO"] != "preenchido":
            sheet.cell(row=offset, column=2).fill = STATE_FILLS["parcial"]

    row = last + 3
    sheet.cell(row=row, column=1, value="De-para do tipo de movimento").font = LABEL_FONT
    row += 1
    for column, label in enumerate(("Tipo impresso no statement", "Código"), start=1):
        cell = sheet.cell(row=row, column=column, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    for original, codigo in TIPO_MOVIMENTO.items():
        row += 1
        sheet.cell(row=row, column=1, value=original)
        sheet.cell(row=row, column=2, value=codigo)

    if export.notes:
        row += 2
        sheet.cell(row=row, column=1, value="Notas").font = LABEL_FONT
        for nota in export.notes:
            row += 1
            sheet.cell(row=row, column=1, value="·")
            sheet.cell(row=row, column=2, value=nota)


def _write_checks(workbook: Workbook, name: str, checks: Sequence[Check]) -> None:
    sheet = workbook.create_sheet(name)
    sheet.sheet_properties.tabColor = VIEW_TAB_COLOR
    fields = ["estado", "âmbito", "verificação", "extraído", "esperado", "delta", "pagina", "nota"]
    labels = {
        "estado": "Estado",
        "âmbito": "Âmbito",
        "verificação": "Verificação",
        "extraído": "Extraído",
        "esperado": "Esperado",
        "delta": "Delta",
        "pagina": "Pág.",
        "nota": "Nota",
    }
    widths = {"âmbito": 40, "verificação": 40, "nota": 95, "estado": 12, "pagina": 7}

    # Falhas primeiro, depois o que ficou por provar: quem abre esta folha quer
    # ver o que está mal, não percorrer trinta linhas verdes até lá chegar.
    ordered = sorted(
        checks, key=lambda c: (0 if c.fatal else (1 if not c.passed else 2), c.section)
    )
    rows = [
        {
            "estado": "PASS" if check.passed else ("FAIL" if check.fatal else "AVISO"),
            "âmbito": check.section,
            "verificação": check.name,
            "extraído": check.extracted,
            "esperado": check.expected,
            "delta": check.delta,
            "pagina": check.page,
            "nota": check.detail,
        }
        for check in ordered
    ]
    _write_table(sheet, fields, rows, labels=labels, widths=widths)

    for offset, row in enumerate(rows, start=2):
        fill = {
            "PASS": STATE_FILLS["conciliado"],
            "FAIL": STATE_FILLS["não conciliado"],
            "AVISO": STATE_FILLS["parcial"],
        }[row["estado"]]
        sheet.cell(row=offset, column=1).fill = fill


def _write_printed_totals(workbook: Workbook, name: str, tables: Sequence) -> None:
    sheet = workbook.create_sheet(name)
    sheet.sheet_properties.tabColor = VIEW_TAB_COLOR
    fields = ["conta", "tabela", "pagina", "rotulo", "valor"]
    labels = {
        "conta": "Conta",
        "tabela": "Tabela",
        "pagina": "Pág.",
        "rotulo": "Rótulo impresso",
        "valor": "Valor",
    }
    rows = []
    for result in tables:
        column = result.spec.amount_column
        for row in result.total_rows:
            rows.append(
                {
                    "conta": result.spec.account,
                    "tabela": result.spec.title,
                    "pagina": row.get("source_page"),
                    "rotulo": row.get("label") or row.get("description"),
                    "valor": row.get(column) if column else None,
                }
            )
    _write_table(
        sheet,
        fields,
        rows,
        labels=labels,
        widths={"tabela": 40, "rotulo": 56, "conta": 17, "pagina": 7},
    )


def _write_table_index(
    workbook: Workbook,
    name: str,
    tables: Sequence,
    checks: Sequence[Check],
    raw_names: dict[int, str],
) -> None:
    sheet = workbook.create_sheet(name)
    sheet.sheet_properties.tabColor = VIEW_TAB_COLOR

    fields = [
        "conta", "tabela", "folha", "paginas", "linhas", "coluna", "soma",
        "total_impresso", "conferencias", "estado",
    ]
    labels = {
        "conta": "Conta", "tabela": "Tabela do documento", "folha": "Folha",
        "paginas": "Páginas", "linhas": "Linhas de dados", "coluna": "Coluna de valor",
        "soma": "Soma extraída", "total_impresso": "Total impresso",
        "conferencias": "Conferências", "estado": "Estado",
    }
    widths = {"tabela": 46, "folha": 34, "conta": 17, "coluna": 26, "estado": 16}

    rows = []
    for result in tables:
        spec = result.spec
        mine = checks_for(spec, checks)
        rows.append(
            {
                "conta": spec.account,
                "tabela": spec.title,
                "folha": raw_names.get(id(spec), "—"),
                "paginas": f"{spec.pages[0]}-{spec.pages[-1]}" if spec.pages else "",
                "linhas": len(result.data_rows),
                "coluna": spec.amount_column,
                "soma": result.sum_of(spec.amount_column) if spec.amount_column else None,
                "total_impresso": result.reference_total,
                "conferencias": f"{sum(1 for c in mine if c.passed)}/{len(mine)}",
                "estado": table_state(spec, checks),
            }
        )
    _write_table(sheet, fields, rows, labels=labels, widths=widths)

    for offset, row in enumerate(rows, start=2):
        fill = STATE_FILLS.get(row["estado"])
        if fill:
            sheet.cell(row=offset, column=fields.index("estado") + 1).fill = fill
        if row["folha"] != "—":
            link = sheet.cell(row=offset, column=fields.index("folha") + 1)
            link.value = f'=HYPERLINK("#\'{row["folha"]}\'!A1","{row["folha"]}")'
            link.style = "Hyperlink"

    row = len(rows) + 4
    sheet.cell(row=row, column=1, value="Legenda").font = LABEL_FONT
    for estado, explicacao in LEGENDA:
        row += 1
        cell = sheet.cell(row=row, column=1, value=estado)
        cell.fill = STATE_FILLS[estado]
        sheet.cell(row=row, column=2, value=explicacao)


def _write_raw_table(
    workbook: Workbook,
    result,
    checks: Sequence[Check],
    name: str,
    include_diagnostics: bool,
) -> None:
    spec = result.spec
    sheet = workbook.create_sheet(name)
    sheet.sheet_properties.tabColor = STATE_TAB_COLOR.get(table_state(spec, checks))

    fields = ["row_type", "group", "description", "date"]
    fields += [c["name"] for c in spec.columns if c["name"] != "description"]
    fields += ["source_page", "account"]
    if include_diagnostics:
        fields += list(DIAGNOSTIC_COLUMNS)

    labels = {
        "row_type": "Tipo de linha",
        "group": "Grupo",
        "description": "Descrição",
        "date": "Data",
        "source_page": "Pág.",
        "account": "Conta",
        "source_text": "Texto original",
    }
    labels.update({name: name for name in fields if name not in labels})
    widths = {
        "description": 60, "group": 34, "row_type": 13, "source_text": 90,
        "account": 17, "source_page": 7, "date": 13,
    }
    _write_table(sheet, fields, result.rows, labels=labels, widths=widths)
