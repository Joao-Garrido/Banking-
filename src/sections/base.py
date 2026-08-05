"""Extração genérica de uma secção tabular, guiada pelo layout.json.

As colunas nunca são relidas do header da página — vêm sempre do layout. É essa
a mitigação do risco 'cabeçalho não repetido na virada de página'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Sequence

from ..lines import Line, group_records, starts_with_date
from ..reconcile import Check, compare, compare_count
from ..normalize import (
    NormalizeError,
    detect_currency,
    is_blank,
    looks_like_amount,
    parse_amount,
    parse_date,
    parse_percent,
    parse_quantity,
)

__all__ = [
    "ParseContext",
    "SectionError",
    "PrintedTotal",
    "SectionResult",
    "extract_section",
    "standard_checks",
    "as_decimal",
]


@dataclass(frozen=True)
class ParseContext:
    """O que uma secção precisa de saber para lá das suas próprias colunas.

    statement_year: ano do período, para as datas que o documento imprime sem ano.
    page_accounts: página -> conta, num statement consolidado com várias contas.
    """

    statement_year: int | None = None
    page_accounts: dict[int, str] = field(default_factory=dict)

    def account_of(self, page: int) -> str | None:
        return self.page_accounts.get(page)


class SectionError(ValueError):
    """Falha ao extrair uma secção. Mensagem inclui sempre secção e página."""

    def __init__(self, section: str, page: int | None, message: str):
        self.section = section
        self.page = page
        where = f"secção {section}" + (f", página {page}" if page else "")
        super().__init__(f"[{where}] {message}")


@dataclass(frozen=True)
class PrintedTotal:
    label: str
    value: Decimal
    page: int


@dataclass
class SectionResult:
    name: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    printed_totals: list[PrintedTotal] = field(default_factory=list)
    currencies: set[str] = field(default_factory=set)
    pages: list[int] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def accounts(self) -> list[str]:
        return sorted({row["account"] for row in self.rows if row.get("account")})

    def rows_of(self, account: str | None = None) -> list[dict[str, Any]]:
        if account is None:
            return self.rows
        return [row for row in self.rows if row.get("account") == account]

    def sum_of(self, column: str, account: str | None = None) -> Decimal:
        total = Decimal("0")
        for row in self.rows_of(account):
            value = row.get(column)
            if value is None:
                continue
            total += value
        return total


_PARSERS: dict[str, Callable[..., Any]] = {
    "amount": parse_amount,
    "quantity": parse_quantity,
    "percent": parse_percent,
    "date": parse_date,
}

NUMERIC_TYPES = {"amount", "quantity", "percent"}


def _compile(patterns: Sequence[str] | None) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in (patterns or [])]


def _matches_any(text: str, patterns: Sequence[re.Pattern]) -> re.Pattern | None:
    for pattern in patterns:
        if pattern.search(text):
            return pattern
    return None


def _record_start_predicate(spec: dict) -> Callable[[Line], bool]:
    rule = spec.get("row_start", {"kind": "date"})
    kind = rule.get("kind", "date")

    if kind == "date":
        return starts_with_date

    if kind == "regex":
        pattern = re.compile(rule["pattern"])
        return lambda line: bool(line.words and pattern.match(line.text))

    if kind == "numeric_in":
        column = _column_by_name(spec, rule["column"])

        def predicate(line: Line) -> bool:
            if not line.words:
                return False
            if line.x0 > column["x0"]:  # linha que começa dentro da zona numérica
                return False
            return looks_like_amount(line.cell(column["x0"], column["x1"]))

        return predicate

    raise ValueError(f"row_start.kind desconhecido: {kind!r}")


def _column_by_name(spec: dict, name: str) -> dict:
    for column in spec["columns"]:
        if column["name"] == name:
            return column
    raise KeyError(f"coluna {name!r} não existe no layout desta secção")


def _parse_cell(
    section: str,
    page: int,
    column: dict,
    text: str,
    context: "ParseContext",
) -> Any:
    ctype = column.get("type", "text")
    optional = bool(column.get("optional", False))

    if ctype == "text":
        return text
    if ctype == "currency":
        return detect_currency(text)

    parser = _PARSERS.get(ctype)
    if parser is None:
        raise SectionError(section, page, f"tipo de coluna desconhecido: {ctype!r}")

    try:
        if ctype == "date":
            return parse_date(text, allow_blank=optional, year=context.statement_year)
        return parser(text, allow_blank=optional)
    except NormalizeError as exc:
        raise SectionError(
            section,
            page,
            f"coluna {column['name']!r}: {exc}. Linha inteira precisa de revisão do layout.",
        ) from exc


def extract_section(
    name: str,
    lines: Sequence[Line],
    spec: dict,
    *,
    description_column: str | None = None,
    context: ParseContext | None = None,
) -> SectionResult:
    """Transforma as linhas das páginas da secção em registos normalizados."""
    columns = sorted(spec["columns"], key=lambda c: c["x0"])
    anchor = spec.get("anchor", "center")
    ignore = _compile(spec.get("ignore_patterns"))
    total_patterns = _compile(spec.get("total_label_patterns"))
    description_column = description_column or spec.get(
        "description_column", columns[0]["name"]
    )
    amount_column = spec.get("amount_column")

    context = context or ParseContext()
    result = SectionResult(name=name, pages=sorted({line.page for line in lines}))

    body: list[Line] = []
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if _matches_any(text, ignore):
            continue
        total_match = _matches_any(text, total_patterns)
        if total_match:
            result.printed_totals.append(_printed_total(name, line, spec, columns, anchor))
            continue
        body.append(line)

    records = group_records(body, _record_start_predicate(spec))

    numeric_names = [c["name"] for c in columns if c.get("type") in NUMERIC_TYPES]

    for record in records:
        head, *continuation = record
        raw_cells = head.cells(columns, anchor=anchor)
        row: dict[str, Any] = {}
        for column in columns:
            row[column["name"]] = _parse_cell(
                name, head.page, column, raw_cells[column["name"]], context
            )

        extra_text: list[str] = []
        for cont in continuation:
            cont_cells = cont.cells(columns, anchor=anchor)
            stray = [
                col
                for col in numeric_names
                if not is_blank(cont_cells[col]) and looks_like_amount(cont_cells[col])
            ]
            if stray:
                raise SectionError(
                    name,
                    cont.page,
                    f"linha tratada como continuação mas com valores em {stray}: "
                    f"{cont.text!r}. row_start do layout provavelmente não a reconhece.",
                )
            extra_text.append(cont.text.strip())

        if extra_text:
            joined = " ".join(t for t in extra_text if t)
            base = str(row.get(description_column) or "").strip()
            row[description_column] = f"{base} {joined}".strip()

        row["source_page"] = head.page
        row["account"] = context.account_of(head.page)
        row["source_text"] = " | ".join(line.text for line in record)

        currency = _row_currency(row, columns)
        if currency:
            result.currencies.add(currency)
        row.setdefault("currency", currency)

        if amount_column and row.get(amount_column) is None:
            raise SectionError(
                name,
                head.page,
                f"registo sem valor em {amount_column!r}: {head.text!r}",
            )

        result.rows.append(row)

    return result


def _row_currency(row: dict, columns: Sequence[dict]) -> str | None:
    for column in columns:
        if column.get("type") == "currency":
            value = row.get(column["name"])
            if value:
                return str(value)
    return None


def _printed_total(
    name: str,
    line: Line,
    spec: dict,
    columns: Sequence[dict],
    anchor: str,
) -> PrintedTotal:
    """Lê o total impresso na linha — a prova contra a qual reconciliamos."""
    amount_column = spec.get("amount_column")
    candidates: list[str] = []

    if amount_column:
        column = _column_by_name(spec, amount_column)
        candidates.append(line.cell(column["x0"], column["x1"], anchor=anchor))

    # Fallback: último token numérico da linha.
    candidates.extend(word.text for word in reversed(line.words))

    for candidate in candidates:
        if looks_like_amount(candidate):
            return PrintedTotal(label=line.text, value=parse_amount(candidate), page=line.page)

    raise SectionError(
        name,
        line.page,
        f"linha de total sem valor legível: {line.text!r}",
    )


def as_decimal(value: Any) -> Decimal | None:
    """expected.json guarda números como string para não perder precisão."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def standard_checks(result: SectionResult, spec: dict, expected: dict | None) -> list[Check]:
    """As três conferências que toda a secção tem: soma, contagem, moeda.

    'soma' é comparada contra dois números independentes quando ambos existem:
    o total que foi digitado à mão em expected.json e o total impresso que o
    parser leu do próprio PDF. Se um deles falhar, sabemos qual.
    """
    expected = expected or {}
    amount_column = spec.get("amount_column")
    checks: list[Check] = []

    if amount_column:
        extracted_sum = result.sum_of(amount_column)
        checks.append(
            compare(
                result.name,
                f"soma de {amount_column} vs expected.json",
                extracted_sum,
                as_decimal(expected.get("subtotal")),
                detail=f"{result.row_count} registos nas páginas {result.pages}",
            )
        )
        for total in result.printed_totals:
            checks.append(
                compare(
                    result.name,
                    "soma vs total impresso",
                    extracted_sum,
                    total.value,
                    page=total.page,
                    detail=f"linha impressa: {total.label!r}",
                )
            )

    checks.append(
        compare_count(
            result.name,
            "contagem de registos",
            result.row_count,
            expected.get("row_count"),
        )
    )

    if len(result.currencies) > 1:
        checks.append(
            Check(
                section=result.name,
                name="moeda única",
                passed=False,
                extracted=sorted(result.currencies),
                expected=["<uma só>"],
                detail="secção com mais de uma moeda — somar valores assim dá um total sem significado",
            )
        )

    return checks
