"""words -> linhas visuais -> registos.

Duas responsabilidades, ambas independentes de secção:

1. Agrupar palavras em linhas visuais (tolerância em y), cortando header/footer
   pela banda declarada no layout.
2. Agrupar linhas em registos: uma linha que não começa um registo novo é
   continuação da anterior. O mesmo mecanismo resolve descrição partida em duas
   linhas *e* transação que atravessa a virada de página — desde que a lista de
   linhas seja contínua ao longo do documento, que é como este módulo a produz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pdfplumber

from .normalize import DATE_PATTERN

__all__ = [
    "Word",
    "Line",
    "extract_lines",
    "read_pages",
    "lines_in_band",
    "group_records",
    "starts_with_date",
]

WORD_ATTRS = ["size", "fontname"]


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int
    size: float = 0.0
    fontname: str = ""

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class Line:
    page: int
    top: float
    bottom: float
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return min((w.x0 for w in self.words), default=0.0)

    @property
    def x1(self) -> float:
        return max((w.x1 for w in self.words), default=0.0)

    def cell(self, x0: float, x1: float, *, anchor: str = "center") -> str:
        """Texto das palavras cujo âncora cai em [x0, x1).

        anchor='center' é o default e é o que funciona quando as faixas vêm do
        mapper (derivadas dos dados reais). 'left'/'right' existem para colunas
        coladas em que só uma das margens é fiável.
        """
        picked = [w for w in self.words if x0 <= _anchor_of(w, anchor) < x1]
        picked.sort(key=lambda w: w.x0)
        return " ".join(w.text for w in picked).strip()

    def cells(self, columns: Sequence[dict], *, anchor: str = "center") -> dict[str, str]:
        return {c["name"]: self.cell(c["x0"], c["x1"], anchor=anchor) for c in columns}

    def __str__(self) -> str:  # pragma: no cover - debug
        return f"[p{self.page} y={self.top:.1f}] {self.text}"


def _anchor_of(word: Word, anchor: str) -> float:
    if anchor == "center":
        return word.center_x
    if anchor == "left":
        return word.x0
    if anchor == "right":
        return word.x1
    raise ValueError(f"anchor inválido: {anchor!r}")


def words_from_page(
    page,
    page_number: int,
    body_band: dict | None = None,
    *,
    keep_rotated: bool = False,
) -> list[Word]:
    """Palavras da página, já filtradas.

    Texto rodado (marca d'água vertical na margem do statement) é descartado por
    omissão: partilha o y com as linhas da tabela e, se entrasse, apareceria no
    meio dos registos.
    """
    raw = page.extract_words(extra_attrs=WORD_ATTRS, keep_blank_chars=False)
    top_limit = body_band.get("top") if body_band else None
    bottom_limit = body_band.get("bottom") if body_band else None

    words: list[Word] = []
    for w in raw:
        if not keep_rotated and w.get("upright") is False:
            continue
        if top_limit is not None and w["top"] < top_limit:
            continue
        if bottom_limit is not None and w["bottom"] > bottom_limit:
            continue
        words.append(
            Word(
                text=w["text"],
                x0=float(w["x0"]),
                x1=float(w["x1"]),
                top=float(w["top"]),
                bottom=float(w["bottom"]),
                page=page_number,
                size=float(w.get("size") or 0.0),
                fontname=str(w.get("fontname") or ""),
            )
        )
    return words


def group_words_into_lines(words: Iterable[Word], *, y_tolerance: float = 2.5) -> list[Line]:
    """Agrupa por proximidade em y, dentro da mesma página."""
    ordered = sorted(words, key=lambda w: (w.page, round(w.top, 2), w.x0))
    lines: list[Line] = []
    for word in ordered:
        current = lines[-1] if lines else None
        if (
            current is not None
            and current.page == word.page
            and abs(word.top - current.top) <= y_tolerance
        ):
            current.words.append(word)
            current.bottom = max(current.bottom, word.bottom)
            current.top = min(current.top, word.top)
        else:
            lines.append(Line(page=word.page, top=word.top, bottom=word.bottom, words=[word]))

    for line in lines:
        line.words.sort(key=lambda w: w.x0)
    return lines


def extract_lines(
    pdf_path: str | Path,
    *,
    body_band: dict | None = None,
    pages: Sequence[int] | None = None,
    y_tolerance: float = 2.5,
) -> list[Line]:
    """Lista contínua de linhas visuais do documento (páginas 1-indexed)."""
    wanted = set(pages) if pages is not None else None
    collected: list[Word] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            if wanted is not None and index not in wanted:
                continue
            collected.extend(words_from_page(page, index, body_band))

    return group_words_into_lines(collected, y_tolerance=y_tolerance)


def read_pages(
    pdf_path: str | Path, *, y_tolerance: float = 2.5
) -> tuple[dict[int, list[Line]], dict[int, float]]:
    """Todas as linhas do documento, por página, sem cortar banda nenhuma.

    Existe para que o PDF seja lido uma vez só: o mapper precisa da página
    inteira (o cabeçalho é o que lhe diz de que conta é a página) e a varredura
    precisa do corpo. Cortar a banda em memória é mais barato do que reler.
    """
    per_page: dict[int, list[Line]] = {}
    heights: dict[int, float] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            words = words_from_page(page, index)
            per_page[index] = group_words_into_lines(words, y_tolerance=y_tolerance)
            heights[index] = float(page.height)
    return per_page, heights


def lines_in_band(
    lines_by_page: dict[int, list[Line]], body_band: dict | None
) -> dict[int, list[Line]]:
    """Corta header/footer de linhas já lidas."""
    if not body_band:
        return {page: list(lines) for page, lines in lines_by_page.items()}

    top = body_band.get("top")
    bottom = body_band.get("bottom")
    return {
        page: [
            line
            for line in lines
            if (top is None or line.top >= top) and (bottom is None or line.bottom <= bottom)
        ]
        for page, lines in lines_by_page.items()
    }


def starts_with_date(line: Line) -> bool:
    """Predicado default de início de registo."""
    if not line.words:
        return False
    return bool(DATE_PATTERN.match(line.words[0].text))


def group_records(
    lines: Sequence[Line],
    is_record_start: Callable[[Line], bool] = starts_with_date,
    *,
    drop_orphan_head: bool = True,
) -> list[list[Line]]:
    """Agrupa linhas em registos.

    A primeira linha de cada grupo satisfaz is_record_start; as seguintes são
    continuações. Linhas antes do primeiro início de registo (cabeçalhos de
    tabela, por exemplo) são descartadas quando drop_orphan_head=True, e
    devolvidas como grupo inicial caso contrário — nunca silenciosamente
    coladas ao primeiro registo.
    """
    records: list[list[Line]] = []
    orphans: list[Line] = []

    for line in lines:
        if is_record_start(line):
            records.append([line])
        elif records:
            records[-1].append(line)
        else:
            orphans.append(line)

    if orphans and not drop_orphan_head:
        records.insert(0, orphans)
    return records
