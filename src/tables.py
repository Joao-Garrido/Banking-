"""Varredura automática de tabelas: encontra todas as tabelas do documento e
extrai-as sem precisar que alguém escreva uma secção à mão.

Porque existe: um statement consolidado real não tem quatro tabelas — tem
dezenas de sub-tabelas (`CASH, BANK DEPOSIT PROGRAM AND MONEY MARKET FUNDS`,
`MUTUAL FUNDS`, `COMMON STOCKS`, `CASH FLOW ACTIVITY BY DATE`, …), cada uma com
o seu próprio conjunto de colunas. Este módulo detecta cada uma pelo que está
desenhado na página e reconcilia-a contra os totais que a própria tabela imprime.

O que não faz: inventar semântica. Onde não há total impresso, o resultado sai
marcado como *não verificável* — nunca como correto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

from .lines import Line, group_records
from .normalize import (
    NormalizeError,
    is_blank,
    looks_like_amount,
    looks_like_amount_loose,
    parse_amount_loose,
    parse_date,
)
from .reconcile import Check, compare

__all__ = [
    "TableSpec",
    "TableResult",
    "detect_tables",
    "extract_table",
    "table_checks",
    "cross_checks",
    "ROW_TYPES",
]

ROW_TYPES = ("data", "subtotal", "total", "info")

# Uma linha cujo rótulo casa com um destes é um total da tabela — é contra ele
# que a soma do que extraímos é conferida.
TOTAL_PATTERNS = [
    r"^total\b(?!\s+purchases\s+vs)",
    r"^net\s+(credits|debits|unsettled|value\s+of)",
    r"^grand\s+total\b",
]

# Somas intermédias: não entram na soma dos dados nem servem de prova.
SUBTOTAL_PATTERNS = [
    r"^purchases$",
    r"^sales$",
    r"^subtotal\b",
    r"^bank\s+deposits\b",
    r"^cash,\s*bdp",
]

# Linhas informativas com números que não pertencem a nenhuma soma.
INFO_PATTERNS = [
    r"^total\s+purchases\s+vs",
    r"^net\s+value\s+(increase|decrease)",
    r"^cumulative\s+cash\s+distributions",
    r"^(next\s+dividend|asset\s+class|enrolled\s+in)\b",
]

# Palavras demasiado comuns para provarem que um total pertence àquela tabela:
# 'TOTAL FOR ALL ACCOUNTS' não é o total de 'OVERVIEW OF YOUR ACCOUNTS'.
TITLE_STOPWORDS = {
    "account", "accounts", "value", "values", "total", "totals", "includes",
    "accrued", "interest", "your", "this", "period", "date", "detail", "summary",
}

MIN_NUMERIC_LINES = 2      # menos do que isto não é tabela
MAX_HEADER_DISTANCE = 60.0  # pt acima do primeiro registo onde procurar o header
MAX_HEADER_CHARS = 130
COLUMN_GAP = 8.0
COLUMN_PAD = 3.0
_NON_WORD = re.compile(r"[^a-z0-9]+")
# Título de tabela: MAIÚSCULAS, curto, com um parêntese descritivo opcional no
# fim — 'MUTUAL FUNDS', 'CHANGE IN VALUE OF YOUR ACCOUNTS (includes accrued
# interest)'. Aceitar títulos capitalizados fazia com que headers de coluna
# ('Date Activity Type Description') passassem por títulos e partissem tabelas
# ao meio; o statement escreve os seus títulos em maiúsculas, e é isso que vale.
_HEADING = re.compile(r"^[A-Z][A-Z0-9 &/,.'\-]{3,60}(?:\s*\([^)]{0,60}\))?$")
_CONTINUED = re.compile(r"\s*\(CONTINUED\)\s*$", re.IGNORECASE)
_PLAIN_TOTAL = re.compile(r"^totals?$", re.IGNORECASE)


def _slug(text: str) -> str:
    return _NON_WORD.sub("_", text.strip().lower()).strip("_") or "col"


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_TOTAL_RE = _compile(TOTAL_PATTERNS)
_SUBTOTAL_RE = _compile(SUBTOTAL_PATTERNS)
_INFO_RE = _compile(INFO_PATTERNS)


@dataclass
class TableSpec:
    id: str
    title: str
    account: str | None
    pages: list[int]
    columns: list[dict]
    header: str
    amount_column: str | None
    lines: list[Line] = field(default_factory=list, repr=False)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "account": self.account,
            "pages": self.pages,
            "header": self.header,
            "amount_column": self.amount_column,
            "columns": self.columns,
        }


@dataclass
class TableResult:
    spec: TableSpec
    rows: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Total impresso efetivamente usado na conferência (None se não houve).
    reference_total: Decimal | None = None

    @property
    def data_rows(self) -> list[dict]:
        return [row for row in self.rows if row["row_type"] == "data"]

    @property
    def total_rows(self) -> list[dict]:
        return [row for row in self.rows if row["row_type"] == "total"]

    def sum_of(self, column: str, row_type: str = "data") -> Decimal:
        total = Decimal("0")
        for row in self.rows:
            if row["row_type"] != row_type:
                continue
            value = row.get(column)
            if isinstance(value, Decimal):
                total += value
        return total


# --------------------------------------------------------------------- deteção


# Um número de tabela traz sempre pontuação: '1,200', '187.50', '(500)', '$25'.
# Um número solto em prosa ('by February 15') não traz. É esta distinção que
# impede as páginas de disclaimers de virarem tabelas.
_FORMATTED = re.compile(r"[.,()$]")


def _is_tabular_number(text: str) -> bool:
    return bool(_FORMATTED.search(text)) and looks_like_amount_loose(text)


def _is_numeric_line(line: Line) -> bool:
    return any(_is_tabular_number(word.text) for word in line.words)


def _is_prose(line: Line) -> bool:
    text = line.text.strip()
    return len(text) > MAX_HEADER_CHARS or text.endswith(".")


def _is_heading(line: Line, left_margin: float = 0.0, median_size: float = 0.0) -> bool:
    """Título de tabela: maiúsculas, na margem esquerda, e graficamente destacado.

    As três condições juntas são o que distingue 'MUTUAL FUNDS' de uma morada em
    maiúsculas ou de um 'DIV PAYMENT' indentado dentro de um registo.
    """
    text = line.text.strip()
    if not _HEADING.match(text) or _is_numeric_line(line):
        return False
    if line.x0 > left_margin + 12.0:
        return False
    return any(
        word.size > median_size + 0.4 or "bold" in word.fontname.lower() for word in line.words
    )


def _numeric_bands(lines: Sequence[Line], *, min_words: int = 2) -> list[tuple[float, float]]:
    """Faixas x das colunas numéricas.

    min_words=2 exige que a coluna se repita — evita que um número solto vire
    coluna. Blocos de uma só linha (um registo isolado entre notas) baixam para
    1: preferimos uma coluna a mais a perder o registo.
    """
    words = [w for line in lines for w in line.words if _is_tabular_number(w.text)]
    clusters: list[list] = []
    for word in sorted(words, key=lambda w: w.x1):
        if clusters and word.x1 - clusters[-1][-1].x1 <= COLUMN_GAP:
            clusters[-1].append(word)
        else:
            clusters.append([word])

    bands = [
        (
            round(min(w.x0 for w in cluster) - COLUMN_PAD, 2),
            round(max(w.x1 for w in cluster) + COLUMN_PAD, 2),
        )
        for cluster in clusters
        if len(cluster) >= min_words
    ]
    # Fronteiras a meio do espaço vazio: um valor mais largo do que os vistos
    # continua a cair na sua coluna em vez de desaparecer no intervalo.
    for position in range(1, len(bands)):
        left_x0, left_x1 = bands[position - 1]
        right_x0, right_x1 = bands[position]
        boundary = round((left_x1 + right_x0) / 2, 2)
        bands[position - 1] = (left_x0, boundary)
        bands[position] = (boundary, right_x1)
    return bands


def _keep_numeric_bands(
    bands: Sequence[tuple[float, float]], lines: Sequence[Line]
) -> list[tuple[float, float]]:
    """Descarta faixas cujas células são maioritariamente texto.

    Numa página de disclaimers há números soltos que formam faixas por acaso; o
    que as denuncia é o resto da coluna ser prosa.
    """
    kept: list[tuple[float, float]] = []
    for x0, x1 in bands:
        numeric = filled = 0
        for line in lines:
            cell = line.cell(x0, x1)
            if not cell:
                continue
            filled += 1
            if _is_tabular_number(cell):
                numeric += 1
        if filled and numeric / filled >= 0.6:
            kept.append((x0, x1))
    return kept


def _column_names(header_lines: Sequence[Line], bands: Sequence[tuple[float, float]]) -> list[str]:
    names: list[str] = []
    used: set[str] = set()
    for position, (x0, x1) in enumerate(bands, start=1):
        label = ""
        for line in reversed(header_lines):  # o header mais próximo manda
            cell = line.cell(x0 - 12, x1 + 12)
            if cell and len(cell) <= 28:
                label = f"{cell} {label}".strip() if label else cell
        name = _slug(label) if label else f"col_{position}"
        while name in used:
            name = f"{name}_{position}"
        used.add(name)
        names.append(name)
    return names


AMOUNT_PREFERENCE = (
    "market_value",
    "amount",
    "credits_debits",
    "value",
    "total_value",
    "market",
)


def _pick_amount_column(names: Sequence[str]) -> str | None:
    for preferred in AMOUNT_PREFERENCE:
        for name in names:
            if preferred in name:
                return name
    return names[-1] if names else None


def _blocks_of_page(
    lines: Sequence[Line], left_margin: float = 0.0, median_size: float = 0.0
) -> list[tuple[list[Line], list[Line], str | None]]:
    """Divide a página em (linhas do bloco, linhas de header, título)."""
    blocks: list[tuple[list[Line], list[Line], str | None]] = []
    current: list[Line] = []
    gap: list[Line] = []
    title: str | None = None
    pending_title: str | None = None

    def flush() -> None:
        """Fecha o bloco. Basta uma linha numérica: um registo isolado entre
        notas continua a ser um registo, e perdê-lo seria o pior erro possível."""
        nonlocal current, gap
        if any(_is_numeric_line(line) for line in current):
            header = _header_for(lines, current[0], left_margin, median_size)
            blocks.append((list(current), header, title))
        current, gap = [], []

    for line in lines:
        if _is_numeric_line(line):
            if pending_title is not None and not current:
                title = pending_title
                pending_title = None
            current.extend(gap)
            gap = []
            current.append(line)
            continue

        if _is_heading(line, left_margin, median_size):
            flush()
            pending_title = line.text.strip()
            continue

        if current:
            gap.append(line)
            if len(gap) >= 3 or _is_prose(line):
                flush()
        # linhas soltas antes de qualquer número: candidatas a header, ignoradas aqui

    flush()
    return blocks


def _header_for(
    lines: Sequence[Line], first: Line, left_margin: float = 0.0, median_size: float = 0.0
) -> list[Line]:
    header: list[Line] = []
    for line in reversed([line for line in lines if line.top < first.top]):
        if first.top - line.top > MAX_HEADER_DISTANCE:
            break
        if _is_numeric_line(line) or _is_prose(line) or _is_heading(line, left_margin, median_size):
            break
        header.insert(0, line)
        if len(header) >= 2:
            break
    return header


def detect_tables(
    lines_by_page: dict[int, list[Line]],
    *,
    account_of_page: dict[int, str | None] | None = None,
    notes: list[str] | None = None,
) -> list[TableSpec]:
    """Encontra as tabelas do documento e as suas colunas.

    Tabelas com o mesmo título e o mesmo número de colunas em páginas
    diferentes são a mesma tabela a continuar — é assim que uma tabela que
    atravessa dez páginas volta a ser uma só.
    """
    account_of_page = account_of_page or {}
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    # (título, página) por conta: um título só se herda para a página seguinte.
    # Sem isto, uma página de disclaimers a meio do documento fica com o título
    # da última tabela vista e o seu texto acaba somado numa coluna de valores.
    carry: dict[str | None, tuple[str, int]] = {}

    all_lines = [line for lines in lines_by_page.values() for line in lines]
    left_margin = min((line.x0 for line in all_lines), default=0.0)
    sizes = sorted(word.size for line in all_lines for word in line.words)
    median_size = sizes[len(sizes) // 2] if sizes else 0.0

    for page in sorted(lines_by_page):
        account = account_of_page.get(page)
        for block, header, title in _blocks_of_page(lines_by_page[page], left_margin, median_size):
            # '(CONTINUED)' é a mesma tabela a continuar noutra página.
            title = _CONTINUED.sub("", title).strip() if title else title
            if not title:
                previous = carry.get(account)
                title = previous[0] if previous and previous[1] >= page - 1 else None
            resolved = title or "SEM TÍTULO"
            carry[account] = (resolved, page)
            numeric_lines = sum(1 for line in block if _is_numeric_line(line))
            bands = _numeric_bands(block, min_words=1 if numeric_lines < 2 else 2)
            if not bands:
                if notes is not None:
                    notes.append(
                        f"p.{page}: bloco sem colunas numéricas reconhecíveis, ignorado: "
                        f"{block[0].text[:60]!r}"
                    )
                continue

            key = (account, resolved, len(bands))
            if key not in groups and numeric_lines < 3:
                # Registo isolado entre notas: junta-se à tabela do mesmo título
                # em vez de virar tabela própria — ou pior, de se perder.
                sibling = next(
                    (k for k in order if k[0] == account and k[1] == resolved),
                    None,
                )
                if sibling is not None:
                    key = sibling
            if key not in groups:
                groups[key] = {"lines": [], "headers": [], "pages": []}
                order.append(key)
            groups[key]["lines"].extend(block)
            groups[key]["headers"].extend(header)
            groups[key]["pages"].append(page)

    specs: list[TableSpec] = []
    seen_ids: set[str] = set()
    for key in order:
        account, title, _ = key
        bundle = groups[key]
        bands = _numeric_bands(bundle["lines"])
        bands = _keep_numeric_bands(bands, bundle["lines"])
        if not bands:
            if notes is not None:
                notes.append(
                    f"{title!r} (págs. {sorted(set(bundle['pages']))}): nenhuma coluna com "
                    "valores numéricos consistentes — não é uma tabela, ignorada"
                )
            continue

        names = _column_names(bundle["headers"], bands)
        body_left = min(line.x0 for line in bundle["lines"])
        columns = [
            {"name": "description", "x0": round(body_left - COLUMN_PAD, 2), "x1": bands[0][0],
             "type": "text"}
        ]
        for name, (x0, x1) in zip(names, bands):
            columns.append({"name": name, "x0": max(x0, columns[-1]["x1"]), "x1": x1,
                            "type": "amount"})

        base_id = _slug(f"{account or 'conta'}_{title}")
        table_id = base_id
        suffix = 2
        while table_id in seen_ids:
            table_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(table_id)

        specs.append(
            TableSpec(
                id=table_id,
                title=title,
                account=account,
                pages=sorted(set(bundle["pages"])),
                columns=columns,
                header=" | ".join(dict.fromkeys(line.text for line in bundle["headers"])),
                amount_column=_pick_amount_column(names),
                lines=sorted(bundle["lines"], key=lambda line: (line.page, line.top)),
            )
        )
    return specs


# ------------------------------------------------------------------- extração


def classify(label: str) -> str:
    text = label.strip()
    if not text:
        return "data"
    for pattern in _INFO_RE:
        if pattern.match(text):
            return "info"
    for pattern in _TOTAL_RE:
        if pattern.match(text):
            return "total"
    for pattern in _SUBTOTAL_RE:
        if pattern.match(text):
            return "subtotal"
    return "data"


def extract_table(spec: TableSpec, *, statement_year: int | None = None) -> TableResult:
    result = TableResult(spec=spec)
    columns = spec.columns
    records = group_records(spec.lines, _is_numeric_line, drop_orphan_head=True)
    group_label = ""

    for record in records:
        head, *continuation = record
        cells = head.cells(columns)
        row: dict[str, Any] = {"source_page": head.page, "account": spec.account}

        for column in columns:
            raw = cells[column["name"]]
            if column["name"] == "description":
                row["description"] = raw
                continue
            row[column["name"]] = _cell_value(
                raw, spec, head.page, column["name"], result, statement_year
            )

        extra = " ".join(line.text.strip() for line in continuation).strip()
        if extra:
            row["description"] = f"{row['description']} {extra}".strip()

        row["row_type"] = classify(row["description"])
        if row["row_type"] == "data" and row["description"]:
            group_label = row["description"]
        row["group"] = group_label
        row["source_text"] = " | ".join(line.text for line in record)
        result.rows.append(row)

    return result


def _cell_value(
    raw: str,
    spec: TableSpec,
    page: int,
    column: str,
    result: TableResult,
    statement_year: int | None,
) -> Any:
    """Valor da célula: número quando é número, texto quando não é.

    Se a célula da coluna de valor não for interpretável, isso fica registado
    em `problems` — nunca desaparece da soma sem deixar rasto.
    """
    if is_blank(raw):
        return None
    try:
        value, _marker = parse_amount_loose(raw)
        return value
    except NormalizeError:
        pass
    try:
        return parse_date(raw, year=statement_year)
    except NormalizeError:
        pass
    if column == spec.amount_column:
        result.problems.append(
            f"p.{page}: coluna de valor {column!r} com célula não interpretável: {raw!r}"
        )
    return raw


# -------------------------------------------------------------- reconciliação


def table_checks(result: TableResult) -> list[Check]:
    """Σ das linhas de dados contra o total que a tabela imprime."""
    spec = result.spec
    column = spec.amount_column
    if not column:
        return []

    checks: list[Check] = []
    totals = result.total_rows

    if not totals:
        checks.append(
            Check(
                section=spec.title,
                name="total impresso",
                passed=False,
                extracted=result.sum_of(column),
                expected="—",
                page=spec.pages[0],
                detail="a tabela não imprime total: soma extraída não é verificável aqui",
                severity="aviso",
            )
        )
        return checks

    # Se existe um total geral da tabela ('TOTAL MUTUAL FUNDS'), é esse que vale;
    # somar também os totais por grupo contaria tudo duas vezes.
    title_words = {
        word.lower()
        for word in re.findall(r"[A-Za-z]{4,}", spec.title or "")
    } - TITLE_STOPWORDS

    def names_the_table(label: str) -> bool:
        lowered = label.lower()
        return any(word in lowered for word in title_words)

    grand = [row for row in totals if names_the_table(row["description"])]
    reference = grand or totals

    expected_total = Decimal("0")
    printed_values = 0
    for row in reference:
        value = row.get(column)
        if isinstance(value, Decimal):
            expected_total += value
            printed_values += 1

    if not printed_values:
        checks.append(
            Check(
                section=spec.title,
                name="total impresso",
                passed=False,
                extracted=result.sum_of(column),
                expected="—",
                page=spec.pages[0],
                detail=(
                    f"a linha de total não imprime valor na coluna {column!r} "
                    "(costuma ser '—'): nada para conferir aqui"
                ),
                severity="aviso",
            )
        )
        return checks

    result.reference_total = expected_total
    extracted = result.sum_of(column)
    delta = abs(extracted - expected_total)

    # Só é erro quando sabemos que a tabela *devia* somar: o total é um 'Total'
    # simples ou traz o nome da tabela. Um 'TOTAL ENDING VALUE' num quadro de
    # roll-forward não é a soma das linhas acima — dizer que falhou seria tão
    # errado como dizer que passou.
    sum_shaped = all(
        _PLAIN_TOTAL.match(row["description"].strip()) or names_the_table(row["description"])
        for row in reference
    )
    # Cada total impresso pode ter sido arredondado ao cêntimo pelo próprio banco.
    rounding = delta <= Decimal("0.01") * len(reference)

    severity = "erro" if sum_shaped and not rounding else "aviso"
    detail = (
        f"{len(result.data_rows)} linhas de dados, {len(reference)} linha(s) de total: "
        + "; ".join(row["description"][:40] for row in reference[:3])
    )
    if not sum_shaped:
        detail += " — não é uma tabela de soma simples: diferença não prova erro nem acerto"
    elif rounding and delta:
        detail += f" — diferença de {delta} compatível com arredondamento do documento"

    checks.append(
        compare(
            spec.title,
            f"Σ {column} vs total impresso",
            extracted,
            expected_total,
            page=spec.pages[0],
            detail=detail,
            severity=severity,
        )
    )

    for problem in result.problems:
        checks.append(
            Check(
                section=spec.title,
                name="célula não interpretada",
                passed=False,
                extracted=problem,
                expected="valor legível",
                page=spec.pages[0],
                detail="a soma acima ignora esta célula — trata isto antes de confiar no total",
            )
        )
    return checks


def cross_checks(results: Sequence[TableResult]) -> list[Check]:
    """Tabela de resumo contra a tabela detalhada da mesma conta.

    O statement imprime, por conta, uma linha de resumo por categoria
    ('MUTUAL FUNDS 71.21% ... $1,978,840.85'). Essa linha existe noutra tabela e
    tem de bater com o detalhe — é uma prova cruzada de graça.
    """
    by_account: dict[str | None, list[TableResult]] = {}
    for result in results:
        by_account.setdefault(result.spec.account, []).append(result)

    checks: list[Check] = []
    for account, group in by_account.items():
        detail_by_title = {}
        for result in group:
            column = result.spec.amount_column
            if column and result.data_rows:
                detail_by_title.setdefault(result.spec.title.strip().lower(), result)

        for result in group:
            column = result.spec.amount_column
            if not column:
                continue
            for row in result.rows:
                label = (row.get("description") or "").strip().lower()
                target = detail_by_title.get(label)
                if target is None or target is result:
                    continue
                value = row.get(column)
                if not isinstance(value, Decimal):
                    continue
                checks.append(
                    compare(
                        target.spec.title,
                        "detalhe vs linha de resumo",
                        target.sum_of(target.spec.amount_column),
                        value,
                        page=row["source_page"],
                        detail=(
                            f"resumo em {result.spec.title!r} p.{row['source_page']} "
                            f"vs detalhe da conta {account}"
                        ),
                        severity="aviso",
                    )
                )
    return checks
