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
    DATE_PATTERN,
    NormalizeError,
    is_blank,
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
    r"^net\s+(credits|debits|unsettled|activity|value\s+of)",
    r"^grand\s+total\b",
    # Mais-valias: o total do período está no topo da tabela, sem a palavra
    # 'total'. Sem o reconhecer, ele era somado como se fosse mais uma venda.
    r"^(long|short)[-\s]term\s+this\s+period\b",
]

# Somas intermédias: não entram na soma dos dados nem servem de prova.
SUBTOTAL_PATTERNS = [
    r"^purchases$",
    r"^sales$",
    r"^subtotal\b",
    r"^bank\s+deposits\b",
    r"^cash,\s*bdp",
]

# Rótulos que pertencem ao grupo em curso em vez de abrirem um grupo novo.
# 'Short Term Reinvestments' é uma linha de dados como as outras, mas quem manda
# no grupo continua a ser o título acima dela.
CONTINUATION_LABELS = [
    r"^(short|long)[\s-]term\s+reinvestments",
    r"^reinvestments?\b",
    r"^purchases\b",
    r"^sales\b",
    r"^total\b",
    r"^net\b",
]

# Linhas informativas com números que não pertencem a nenhuma soma.
INFO_PATTERNS = [
    # Acumulado do ano: outro âmbito temporal, não é a soma das linhas do período.
    r"^(long|short)[-\s]term\s+year\s+to\s+date\b",
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
_HEADING = re.compile(r"^[A-Z][A-Z0-9 &/,.'()\-]{3,70}(?:\s*\([^)]{0,60}\))?$")
_CONTINUED = re.compile(r"\s*\(CONTINUED\)\s*$", re.IGNORECASE)
_PLAIN_TOTAL = re.compile(r"^totals?$", re.IGNORECASE)
_PERCENT = re.compile(r"^\(?-?[\d,]+(\.\d+)?\)?\s*%$")


def _slug(text: str) -> str:
    return _NON_WORD.sub("_", text.strip().lower()).strip("_") or "col"


def _compile(patterns: Iterable[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_TOTAL_RE = _compile(TOTAL_PATTERNS)
_CONTINUATION_RE = _compile(CONTINUATION_LABELS)
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
    # Texto puro numa coluna de valores: não há número perdido, há uma coluna
    # que não é o que dizia ser.
    stray_text: list[str] = field(default_factory=list)
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


def _leading_or_trailing_date(text: str) -> tuple[str, list[str]]:
    """Separa as datas que o statement imprime no início ou no fim da descrição.

    As datas não formam coluna própria (não têm a pontuação que distingue um
    número de tabela), por isso caem na descrição. Tirá-las daqui dá colunas de
    data utilizáveis e — mais importante — impede que a data de um lote passe
    por nome da posição no agrupamento. Podem ser duas: as tabelas de
    mais-valias imprimem data de compra *e* data de venda.
    """
    words = text.split()
    leading: list[str] = []
    trailing: list[str] = []

    while words and DATE_PATTERN.match(words[0]):
        leading.append(words.pop(0))
    while len(words) > 1 and DATE_PATTERN.match(words[-1]):
        trailing.insert(0, words.pop())
    # Uma descrição feita só de datas ('12/21/16 12/30/16') fica vazia, e é isso
    # mesmo: o registo é do título acima, não de um título novo.
    if not words and len(trailing) == 0 and len(leading) > 1:
        pass
    return " ".join(words), leading + trailing


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
    """Nome de cada coluna a partir do header da tabela.

    Os headers repetem-se em cada página da tabela; sem os desduplicar, uma
    tabela de dez páginas ficava com colunas chamadas 'quantity_quantity_…'.
    """
    # O header repete-se em cada página da tabela, e nem sempre igual. Escolhe-se
    # o de uma página só — a que cobre mais colunas — em vez de juntar todos:
    # juntar dava colunas chamadas 'total_cost_total_cost'.
    by_page: dict[int, list[Line]] = {}
    for line in header_lines:
        by_page.setdefault(line.page, []).append(line)

    def coverage(lines: Sequence[Line]) -> int:
        return sum(
            1 for x0, x1 in bands if any(line.cell(x0 - 12, x1 + 12) for line in lines)
        )

    if by_page:
        best = max(by_page, key=lambda page: (coverage(by_page[page]), -page))
        seen: set[str] = set()
        header_lines = [
            line
            for line in sorted(by_page[best], key=lambda line: line.top)
            if not (line.text in seen or seen.add(line.text))
        ]

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


def _pick_amount_column(
    names: Sequence[str],
    period_hint: str | None = None,
    numeric_counts: Sequence[int] | None = None,
) -> str | None:
    """Escolhe a coluna que representa o valor da tabela.

    Os quadros de sumário trazem duas colunas — 'This Period' e 'This Year' — e
    os seus nomes acabam a ser as próprias datas. Sem preferir a do período, o
    resumo do mês sairia com o acumulado do ano, que é outro número.

    A preferência só vale para uma coluna que de facto tenha valores: numa
    página com dois quadros lado a lado, o nome do período pode calhar a uma
    banda de texto, e aí a heurística normal é mais fiável.
    """
    if period_hint:
        melhor = max(numeric_counts) if numeric_counts else 0
        do_periodo = [
            name
            for index, name in enumerate(names)
            if period_hint in name
            and (not numeric_counts or numeric_counts[index] >= melhor / 2)
        ]
        if do_periodo:
            return do_periodo[-1]
    for preferred in AMOUNT_PREFERENCE:
        for name in names:
            if preferred in name:
                return name
    return names[-1] if names else None


def period_hint(layout: dict) -> str | None:
    """Fragmento com que o statement escreve o início do período ('6_1_26')."""
    start = layout.get("period_start")
    if not start:
        return None
    year, month, day = start.split("-")
    return f"{int(month)}_{int(day)}_{year[2:]}"


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
    period: str | None = None,
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
        numeric_counts = [
            sum(1 for line in bundle["lines"] if _is_tabular_number(line.cell(x0, x1)))
            for x0, x1 in bands
        ]
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
                amount_column=_pick_amount_column(names, period, numeric_counts),
                lines=sorted(bundle["lines"], key=lambda line: (line.page, line.top)),
            )
        )
    return specs


# ------------------------------------------------------------------- extração


def _label_matches_title(label: str, title: str) -> bool:
    """'Stocks' na folha de balanço é a tabela 'COMMON STOCKS'.

    Exige-se que todas as palavras do rótulo existam no título — o contrário
    (título contido no rótulo) deixaria 'Total' casar com tudo.
    """
    label_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", label)}
    title_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", title)}
    if not label_words or not title_words:
        return False
    if not label_words <= title_words:
        return False
    return bool(label_words - TITLE_STOPWORDS)


def _matches(text: str, patterns: Sequence[re.Pattern]) -> bool:
    return any(pattern.match(text.strip()) for pattern in patterns)


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
                description, date_tokens = _leading_or_trailing_date(raw)
                row["description"] = description
                dates = [_as_date(token, statement_year) for token in date_tokens]
                row["date"] = dates[0] if dates else None
                row["dates"] = dates
                continue
            row[column["name"]] = _cell_value(
                raw, spec, head.page, column["name"], result, statement_year
            )

        # Classificar antes de juntar as continuações: o statement imprime a
        # nota do título ('Asset Class: Equities') na linha a seguir ao 'Total',
        # e juntá-la primeiro fazia o rótulo deixar de se parecer com um total.
        row["label"] = row["description"]
        row["row_type"] = classify(row["description"])

        extra = " ".join(line.text.strip() for line in continuation).strip()
        if extra:
            row["description"] = f"{row['description']} {extra}".strip()
        if row["row_type"] == "data" and row["label"] and not _matches(row["label"], _CONTINUATION_RE):
            group_label = row["label"]
        row["group"] = group_label
        row["source_text"] = " | ".join(line.text for line in record)
        result.rows.append(row)

    _promote_category_totals(result)
    return result


def _promote_category_totals(result: TableResult) -> None:
    """'STOCKS 19.42% … $1,384,278.01' é o total da categoria, não uma posição.

    Contá-lo como posição duplicava a soma da tabela. Duas condições, ambas
    necessárias: a linha chama-se como a tabela, e o seu valor é pelo menos tão
    grande como qualquer outra linha de dados — um total não é menor do que as
    parcelas. Sem a segunda condição, 'Change in Value' num quadro de
    roll-forward passaria por total e a tabela deixaria de fechar.
    """
    column = result.spec.amount_column
    if not column:
        return

    candidates = [
        row
        for row in result.rows
        if row["row_type"] == "data"
        and _label_matches_title(row["label"], result.spec.title)
        and isinstance(row.get(column), Decimal)
    ]
    if not candidates:
        return

    others = [
        abs(row[column])
        for row in result.rows
        if row["row_type"] == "data" and row not in candidates
        and isinstance(row.get(column), Decimal)
    ]
    largest = max(others, default=Decimal("0"))
    for row in candidates:
        if abs(row[column]) >= largest:
            row["row_type"] = "total"


def _as_date(token: str | None, statement_year: int | None):
    if not token:
        return None
    try:
        return parse_date(token, year=statement_year)
    except NormalizeError:
        return token


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
    if _PERCENT.match(raw.strip()):
        # Percentagem numa coluna de valores: não é dinheiro, fica como texto
        # para não entrar em soma nenhuma — e não é um problema de leitura.
        return raw.strip()
    # 'Activity $933,772.30': o rótulo de um quadro colado ao valor do quadro
    # ao lado. O valor é o último token e é legível — usá-lo é melhor do que
    # perdê-lo, mas o desalinhamento fica registado.
    partes = raw.split()
    if len(partes) > 1 and _is_tabular_number(partes[-1]):
        if not any(char.isdigit() for char in " ".join(partes[:-1])):
            try:
                valor, _marker = parse_amount_loose(partes[-1])
            except NormalizeError:
                valor = None
            if valor is not None:
                if column == spec.amount_column:
                    result.stray_text.append(
                        f"p.{page}: coluna de valor {column!r} com rótulo colado ao valor: {raw!r}"
                    )
                return valor

    if column == spec.amount_column:
        onde = f"p.{page}: coluna de valor {column!r}"
        if any(char.isdigit() for char in raw):
            # Há ali um número que não conseguimos ler — isso é um valor perdido.
            result.problems.append(f"{onde} com célula não interpretável: {raw!r}")
        else:
            # Texto sem dígitos: não se perdeu dinheiro, perdeu-se o alinhamento.
            result.stray_text.append(f"{onde} com texto: {raw!r}")
    return raw


# -------------------------------------------------------------- reconciliação


def _title_words(title: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z]{4,}", title or "")} - TITLE_STOPWORDS


def _rounding_slack(count: int) -> Decimal:
    """Um cêntimo por valor impresso que entra na soma.

    O statement arredonda cada componente ao cêntimo: em MUTUAL FUNDS, o 'Total'
    de uma posição difere três cêntimos da soma dos lotes que ele próprio imprime.
    Acima desta folga a diferença deixa de ser explicável por arredondamento e
    passa a erro. A diferença aparece sempre na folha 'Reconciliação', seja qual
    for a classificação.
    """
    return Decimal("0.01") * max(count, 1)


# Acima disto, o problema deixa de ser uma célula e passa a ser a tabela: nas
# páginas de sumário o statement imprime dois quadros lado a lado, e as colunas
# de um invadem o outro. Dizê-lo uma vez é mais útil do que repetir o mesmo
# erro por cada célula.
PROBLEMAS_QUE_SAO_DA_TABELA = 3


def _cell_problem_checks(result: TableResult) -> list[Check]:
    checks: list[Check] = []
    if result.stray_text:
        checks.append(
            Check(
                section=result.spec.title,
                name="texto na coluna de valores",
                passed=False,
                extracted=f"{len(result.stray_text)} célula(s)",
                expected="valores",
                page=result.spec.pages[0],
                detail=(
                    f"a coluna {result.spec.amount_column!r} apanha texto em algumas linhas "
                    "— não há valor perdido, mas as colunas desta tabela não estão bem "
                    f"alinhadas: {result.stray_text[0].split(': ', 1)[-1]}"
                ),
                severity="aviso",
            )
        )

    if len(result.problems) >= PROBLEMAS_QUE_SAO_DA_TABELA:
        return checks + [
            Check(
                section=result.spec.title,
                name="colunas desalinhadas",
                passed=False,
                extracted=f"{len(result.problems)} células ilegíveis",
                expected="colunas alinhadas",
                page=result.spec.pages[0],
                detail=(
                    f"a coluna {result.spec.amount_column!r} mistura texto e valores — "
                    "sinal de dois quadros impressos lado a lado nesta página. Os números "
                    "estão no livro, mas as colunas desta tabela não são de confiança: "
                    f"exemplos: {'; '.join(p.split(': ', 1)[-1] for p in result.problems[:2])}"
                ),
                severity="aviso",
            )
        ]
    return checks + [
        Check(
            section=result.spec.title,
            name="célula não interpretada",
            passed=False,
            extracted=problem,
            expected="valor legível",
            page=result.spec.pages[0],
            detail="as somas ignoram esta célula — trata isto antes de confiar nos totais",
        )
        for problem in result.problems
    ]


def _group_checks(result: TableResult, column: str) -> list[Check]:
    """Cada 'Total' de um título conferido contra as linhas desse título.

    O statement imprime 'Total' no fim de cada posição com vários lotes. Esse
    número é a soma daquele grupo, não da tabela: compará-lo com a tabela toda
    dava sempre errado, e ignorá-lo deitava fora a prova mais fina que existe
    no documento.
    """
    checks: list[Check] = []
    for row in result.rows:
        if row["row_type"] != "total" or not _PLAIN_TOTAL.match(row["label"].strip()):
            continue
        printed = row.get(column)
        if not isinstance(printed, Decimal):
            continue

        group = row.get("group") or ""
        members = [
            other
            for other in result.rows
            if other["row_type"] == "data" and (other.get("group") or "") == group
        ]
        extracted = sum(
            (other[column] for other in members if isinstance(other.get(column), Decimal)),
            Decimal("0"),
        )
        delta = abs(extracted - printed)
        rounding = delta <= _rounding_slack(len(members))
        detail = f"{len(members)} linha(s) sob {group[:44]!r}"
        if rounding and delta:
            detail += (
                f" — diferença de {delta} compatível com o arredondamento das "
                f"{len(members)} linhas somadas"
            )

        checks.append(
            compare(
                result.spec.title,
                f"Σ {group[:28]} vs 'Total' impresso" if group else "Σ grupo vs 'Total' impresso",
                extracted,
                printed,
                page=row["source_page"],
                detail=detail,
                severity="aviso" if rounding else "erro",
            )
        )
    return checks


def _table_total_checks(result: TableResult, column: str) -> list[Check]:
    """Totais da tabela inteira ('TOTAL SECURITY TRANSFERS', 'NET CREDITS/(DEBITS)')."""
    spec = result.spec
    totals = [
        row
        for row in result.total_rows
        if not _PLAIN_TOTAL.match(row["label"].strip())
        and isinstance(row.get(column), Decimal)
    ]
    if not totals:
        return []

    title_words = _title_words(spec.title)

    def names_the_table(label: str) -> bool:
        lowered = label.lower()
        return any(word in lowered for word in title_words)

    # Havendo um total que se identifica com a tabela, é esse que vale; somar
    # também os outros contaria o mesmo dinheiro duas vezes.
    grand = [row for row in totals if names_the_table(row["label"])]
    reference = grand or totals

    printed = sum((row[column] for row in reference), Decimal("0"))
    result.reference_total = printed
    extracted = result.sum_of(column)
    delta = abs(extracted - printed)

    # Só é erro quando sabemos que a tabela *devia* somar. Um 'TOTAL ENDING
    # VALUE' num quadro de roll-forward não é a soma das linhas acima — dizer
    # que falhou seria tão errado como dizer que passou.
    sum_shaped = all(names_the_table(row["label"]) for row in reference)
    rounding = delta <= _rounding_slack(len(reference))

    detail = (
        f"{len(result.data_rows)} linhas de dados, {len(reference)} linha(s) de total: "
        + "; ".join(row["label"][:40] for row in reference[:3])
    )
    if not sum_shaped:
        detail += " — não é uma tabela de soma simples: diferença não prova erro nem acerto"
    elif rounding and delta:
        detail += f" — diferença de {delta} compatível com arredondamento do documento"

    return [
        compare(
            spec.title,
            f"Σ {column} vs total impresso",
            extracted,
            printed,
            page=spec.pages[0],
            detail=detail,
            severity="erro" if sum_shaped and not rounding else "aviso",
        )
    ]


def table_checks(result: TableResult) -> list[Check]:
    """Conferências de uma tabela: por grupo, por tabela, e o que ficou por ler."""
    column = result.spec.amount_column
    if not column:
        return []

    legiveis = sum(1 for row in result.data_rows if isinstance(row.get(column), Decimal))
    if not legiveis:
        detail = (
            "tabela só com linhas de total: os valores estão no livro, mas não há nada "
            "para somar contra eles"
            if not result.data_rows
            else f"nenhuma linha de dados tem valor legível em {column!r} — as colunas desta "
            "tabela não estão alinhadas; os números estão na folha, por ler à mão"
        )
        return [
            Check(
                section=result.spec.title,
                name="soma verificável",
                passed=False,
                extracted=f"{len(result.data_rows)} linha(s) sem valor",
                expected="≥1 valor legível",
                page=result.spec.pages[0],
                detail=detail,
                severity="aviso",
            )
        ] + _cell_problem_checks(result)

    checks = _group_checks(result, column) + _table_total_checks(result, column)

    if not checks:
        checks.append(
            Check(
                section=result.spec.title,
                name="total impresso",
                passed=False,
                extracted=result.sum_of(column),
                expected="—",
                page=result.spec.pages[0],
                detail=(
                    "a tabela não imprime nenhum total legível nesta coluna: o que foi "
                    "extraído não é verificável aqui"
                ),
                severity="aviso",
            )
        )

    return checks + _cell_problem_checks(result)


def cross_checks(results: Sequence[TableResult]) -> list[Check]:
    """Tabela de resumo contra a tabela detalhada da mesma conta.

    O statement imprime, por conta, uma linha de resumo por categoria
    ('Mutual Funds — $1,978,840.85' na folha de balanço). Essa linha vive noutra
    tabela e tem de bater com o detalhe — é uma prova cruzada de graça, e é a
    única prova disponível para as tabelas que não imprimem total nenhum.
    """
    by_account: dict[str | None, list[TableResult]] = {}
    for result in results:
        by_account.setdefault(result.spec.account, []).append(result)

    checks: list[Check] = []
    for account, group in by_account.items():
        detailed = [
            result
            for result in group
            if result.spec.amount_column and result.data_rows
        ]

        for result in group:
            column = result.spec.amount_column
            if not column:
                continue
            for row in result.rows:
                label = (row.get("label") or row.get("description") or "").strip()
                targets = [
                    target
                    for target in detailed
                    if target is not result and _label_matches_title(label, target.spec.title)
                ]
                if len(targets) != 1:
                    continue  # ambíguo: preferimos não afirmar nada

                target = targets[0]
                # A comparação tem de ser da mesma grandeza: o valor de mercado
                # do resumo contra o valor de mercado do detalhe, nunca contra o
                # custo de aquisição que está na coluna ao lado.
                value = row.get(target.spec.amount_column)
                if not isinstance(value, Decimal):
                    continue
                extracted = target.sum_of(target.spec.amount_column)
                delta = abs(extracted - value)
                rows_summed = len(target.data_rows)
                detail = (
                    f"resumo {label!r} em {result.spec.title[:34]!r} p.{row['source_page']} "
                    f"vs {rows_summed} linhas do detalhe da conta {account}"
                )
                if delta and delta <= _rounding_slack(rows_summed):
                    detail += (
                        f" — diferença de {delta} compatível com o arredondamento das "
                        f"{rows_summed} linhas somadas"
                    )
                checks.append(
                    compare(
                        target.spec.title,
                        "detalhe vs linha de resumo",
                        extracted,
                        value,
                        page=row["source_page"],
                        detail=detail,
                        tolerance=_rounding_slack(rows_summed),
                        severity="aviso",
                    )
                )
    return checks
