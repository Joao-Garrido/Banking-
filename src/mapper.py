"""Gera o layout.json — corre UMA vez por desenho de statement, não por documento.

O que produz:
  * índice de secções: título -> intervalo de páginas
  * faixas x: limites de cada coluna, derivados dos dados reais (não do header)
  * banda y: onde começa e acaba o corpo, cortando header/footer repetidos

O ficheiro gerado é uma proposta. Tem de ser revisto a olho antes de valer como
contrato — o mapper marca em `_review` tudo aquilo de que não tem a certeza.

    python -m src.mapper statement.pdf --out layout.json
    python -m src.mapper statement.pdf --sample holdings   # amostra de extract_words
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Sequence

import pdfplumber

from .lines import Line, Word, read_pages, words_from_page
from .normalize import looks_like_amount
from .precheck import assert_text_layer
from .router import LAYOUT_VERSION
from .sections import REGISTRY

__all__ = ["build_layout", "SECTION_TITLE_PATTERNS"]

# Padrões de título por secção. Se o teu statement usa outros nomes, é aqui que
# se muda — e o layout.json resultante é que passa a mandar.
SECTION_TITLE_PATTERNS: dict[str, list[str]] = {
    "holdings": [
        r"^(Portfolio\s+)?Holdings\b",
        r"^Investment\s+Holdings\b",
        r"^Security\s+Holdings\b",
        r"^Asset\s+Detail\b",
    ],
    "activity": [
        r"^(Account\s+)?Activity\b",
        r"^Cash\s+Flow\s+Activity\b",
        r"^Transaction\s+(Detail|History)\b",
    ],
    "income": [
        r"^Income\b",
        r"^Dividends?\s+and\s+Interest\b",
        r"^Interest\s+and\s+Dividends?\b",
    ],
    "fees": [
        r"^Fees\b",
        r"^Fees\s+and\s+Charges\b",
        r"^Charges\s+and\s+Expenses\b",
        r"^Advisory\s+Fees\b",
    ],
}

# Número de conta no cabeçalho de cada página ('316-014265-042'). Se o teu
# statement usa outro formato, muda aqui — o resto do mapper não precisa saber.
ACCOUNT_NUMBER = re.compile(r"\b\d{3}-\d{6}-\d{3}\b")
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_CAPS_RUN = re.compile(r"\b[A-Z][A-Z&.'-]{2,}(?:\s+[A-Z][A-Z&.'-]{2,})+")

MAX_HEADER_CHARS = 90       # acima disto é prosa, não header de tabela
MAX_HEADER_CELL_CHARS = 22  # idem, por célula
COLUMN_GAP = 8.0           # pt de folga entre colunas numéricas distintas
COLUMN_PAD = 3.0           # margem acrescentada a cada faixa
HEADER_FOOTER_RATIO = 0.8  # header/footer repete-se em quase todas as páginas
HEADER_ZONE = 0.12         # fração do topo/fundo onde header/footer podem estar
_DIGITS = re.compile(r"\d")
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _fingerprint(text: str) -> str:
    """Texto com os dígitos mascarados — 'Page 3 of 12' e 'Page 4 of 12' iguais."""
    return _DIGITS.sub("#", text.strip().lower())


def _snake(text: str) -> str:
    return _NON_WORD.sub("_", text.strip().lower()).strip("_") or "col"


def _page_lines(pdf_path: Path) -> tuple[dict[int, list[Line]], dict[int, float]]:
    return read_pages(pdf_path)


def detect_body_band(
    per_page: dict[int, list[Line]],
    heights: dict[int, float],
    account_ids: Sequence[str] = (),
) -> tuple[dict, list[str]]:
    """Banda y do corpo: abaixo do header repetido, acima do footer repetido.

    O limiar é alto de propósito (80% das páginas). O header de *tabela* também
    se repete, mas só nas páginas de uma secção — se o limiar fosse baixo, o
    corpo começaria abaixo dele e perdíamos os nomes das colunas.
    """
    page_count = len(per_page)
    counts: Counter[str] = Counter()
    for lines in per_page.values():
        for fingerprint in {_fingerprint(line.text) for line in lines if line.text.strip()}:
            counts[fingerprint] += 1

    threshold = max(2, round(page_count * HEADER_FOOTER_RATIO))
    repeated = {fp for fp, count in counts.items() if count >= threshold}

    header_bottom = 0.0
    footer_top = min(heights.values()) if heights else 792.0
    classified: set[str] = set()

    for page, lines in per_page.items():
        height = heights[page]
        for line in lines:
            # A linha com o número da conta é cabeçalho de página mesmo quando
            # só se repete nas páginas daquela conta.
            is_account_header = any(account_id in line.text for account_id in account_ids)
            if is_account_header and line.top < height * 0.25:
                # Pode estar mais abaixo do que o cabeçalho comum, mas continua a
                # ser mobília da página: se entrar no corpo, o nome do titular
                # acaba colado aos nomes das colunas.
                header_bottom = max(header_bottom, line.bottom)
                classified.add("header: linha com o número da conta")
                continue
            if _fingerprint(line.text) not in repeated:
                continue
            if line.bottom < height * HEADER_ZONE:
                header_bottom = max(header_bottom, line.bottom)
                classified.add(f"header: {_fingerprint(line.text)!r}")
            elif line.top > height * (1 - HEADER_ZONE):
                footer_top = min(footer_top, line.top)
                classified.add(f"footer: {_fingerprint(line.text)!r}")

    band = {
        "top": round(header_bottom + 1.0, 2) if header_bottom else 0.0,
        "bottom": round(footer_top - 1.0, 2),
    }
    notes = [f"banda do corpo y=[{band['top']}, {band['bottom']}] — cortado como {item}"
             for item in sorted(classified)]
    if not classified:
        notes.append(
            "nenhum header/footer repetido detetado — body_band ficou aberta, confirma a olho"
        )
    return band, notes


def detect_accounts(
    per_page: dict[int, list[Line]], heights: dict[int, float]
) -> tuple[list[dict], list[str]]:
    """Statement consolidado: cada página traz no cabeçalho a conta a que pertence.

    Sem isto, as posições de duas contas somam-se num total que não existe em
    lado nenhum do documento — e nada no output denuncia a mistura.
    """
    pages_by_account: dict[str, list[int]] = defaultdict(list)
    labels: dict[str, str] = {}
    notes: list[str] = []

    for page in sorted(per_page):
        header_zone = heights.get(page, 792.0) * 0.25
        lines = [line for line in per_page[page] if line.top < header_zone]
        for position, line in enumerate(lines):
            match = ACCOUNT_NUMBER.search(line.text)
            if not match:
                continue
            account_id = match.group(0)
            pages_by_account[account_id].append(page)
            if account_id not in labels:
                labels[account_id] = _account_label(lines, position)
            break

    accounts = [
        {"id": account_id, "label": labels.get(account_id), "pages": pages}
        for account_id, pages in sorted(pages_by_account.items(), key=lambda kv: kv[1][0])
    ]

    if not accounts:
        notes.append(
            "nenhum número de conta encontrado no cabeçalho — tratado como statement de "
            "conta única; se for consolidado, ajusta ACCOUNT_NUMBER no mapper"
        )
    elif len(accounts) > 1:
        notes.append(
            f"statement consolidado com {len(accounts)} contas: "
            + ", ".join(f"{a['id']} ({a['label']}) págs. {a['pages'][0]}-{a['pages'][-1]}"
                        for a in accounts)
        )
    for account in accounts:
        if account["label"] is None:
            notes.append(f"conta {account['id']}: sem nome legível, preenche 'label' à mão")

    return accounts, notes


def _account_label(lines: Sequence[Line], position: int) -> str | None:
    """Nome do tipo de conta, sem o nome do titular (que vem em maiúsculas)."""
    for candidate in (lines[position - 1] if position else None, lines[position]):
        if candidate is None:
            continue
        text = ACCOUNT_NUMBER.sub("", candidate.text).strip()
        text = _CAPS_RUN.split(text)[0].strip(" ,-·")
        text = re.sub(r"^Account\s+(Detail|Summary)\s*", "", text).strip()
        if len(text) > 3:
            return text
    return None


# 'For the Period December 1-31, 2016' -> 2016-12-01 a 2016-12-31.
PERIOD = re.compile(
    r"(?P<mes>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(?P<inicio>\d{1,2})\s*[-–]\s*(?P<fim>\d{1,2}),\s*(?P<ano>\d{4})",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def detect_period(per_page: dict[int, list[Line]]) -> tuple[dict, list[str]]:
    """Período do statement: ano (para as datas sem ano) e data de posição.

    A data de posição é o fim do período — é ela que vai para `DT_POSICAO` na
    exportação. Sem ela, quem carrega o ficheiro não sabe a que dia se refere a
    carteira, e essa é a primeira coisa que um consolidador pergunta.
    """
    from datetime import date

    for page in sorted(per_page)[:2]:
        for line in per_page[page][:6]:
            match = PERIOD.search(line.text)
            if match:
                year = int(match.group("ano"))
                month = _MONTHS[match.group("mes").lower()]
                start = date(year, month, int(match.group("inicio")))
                end = date(year, month, int(match.group("fim")))
                return (
                    {
                        "statement_year": year,
                        "period_start": start.isoformat(),
                        "period_end": end.isoformat(),
                    },
                    [f"período {start.isoformat()} a {end.isoformat()} — confirma"],
                )

    for page in sorted(per_page)[:2]:
        for line in per_page[page][:6]:
            match = YEAR.search(line.text)
            if match:
                year = int(match.group(0))
                return (
                    {"statement_year": year, "period_start": None, "period_end": None},
                    [
                        f"statement_year={year} lido de {line.text.strip()!r}, mas as datas de "
                        "início e fim do período não foram reconhecidas: preenche "
                        "'period_end' à mão (é a data de posição)"
                    ],
                )

    return {"statement_year": None, "period_start": None, "period_end": None}, [
        "período não encontrado: datas sem ano ('12/8') vão levantar exceção e a data de "
        "posição fica por preencher"
    ]


def detect_sections(
    per_page: dict[int, list[Line]],
    patterns: dict[str, list[str]],
) -> tuple[dict[str, list[int]], list[str]]:
    """Índice título -> páginas. Uma secção vai do seu título ao título seguinte."""
    compiled = {name: [re.compile(p, re.IGNORECASE) for p in pats] for name, pats in patterns.items()}
    sizes = [w.size for lines in per_page.values() for line in lines for w in line.words]
    median_size = sorted(sizes)[len(sizes) // 2] if sizes else 0.0

    hits: list[tuple[int, str]] = []  # (página, secção)
    notes: list[str] = []
    for page in sorted(per_page):
        for line in per_page[page]:
            text = line.text.strip()
            if not text or len(text) > 80:
                continue
            for name, regexes in compiled.items():
                if any(regex.match(text) for regex in regexes):
                    prominent = any(
                        w.size > median_size + 0.5 or "bold" in w.fontname.lower()
                        for w in line.words
                    )
                    if prominent or line.words[0].x0 < 60:
                        hits.append((page, name))
                    break

    # Primeira ocorrência de cada secção define o início; secções repetidas em
    # páginas seguintes são continuação, não uma secção nova.
    ordered: list[tuple[int, str]] = []
    for page, name in hits:
        if ordered and ordered[-1][1] == name:
            continue
        ordered.append((page, name))

    seen: set[str] = set()
    starts: list[tuple[int, str]] = []
    for page, name in ordered:
        if name in seen:
            notes.append(
                f"secção {name!r} volta a aparecer na página {page} — o mapper ignorou a "
                "segunda ocorrência; confirma se não são duas tabelas distintas"
            )
            continue
        seen.add(name)
        starts.append((page, name))

    last_page = max(per_page) if per_page else 0
    index: dict[str, list[int]] = {}
    for position, (page, name) in enumerate(starts):
        end = starts[position + 1][0] - 1 if position + 1 < len(starts) else last_page
        index[name] = list(range(page, max(page, end) + 1))

    for name in patterns:
        if name not in index:
            notes.append(f"secção {name!r} não encontrada — marcada present=false")

    return index, notes


def _numeric_clusters(words: Sequence[Word]) -> list[tuple[float, float]]:
    """Agrupa palavras numéricas em colunas pela margem direita."""
    numeric = sorted((w for w in words if looks_like_amount(w.text)), key=lambda w: w.x1)
    clusters: list[list[Word]] = []
    for word in numeric:
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
        if len(cluster) >= 2  # uma ocorrência isolada não faz coluna
    ]

    # Faixas contíguas: a fronteira entre duas colunas fica a meio do espaço
    # vazio. Assim um valor mais largo do que os vistos aqui continua a cair
    # dentro da sua coluna em vez de desaparecer no intervalo.
    for position in range(1, len(bands)):
        left_x0, left_x1 = bands[position - 1]
        right_x0, right_x1 = bands[position]
        boundary = round((left_x1 + right_x0) / 2, 2)
        bands[position - 1] = (left_x0, boundary)
        bands[position] = (boundary, right_x1)
    return bands


def _header_names(lines: Sequence[Line], bands: Sequence[tuple[float, float]]) -> list[str | None]:
    """Tenta nomear as colunas pelo texto do header da tabela."""
    def covered(line: Line) -> list[str]:
        return [line.cell(x0 - 12, x1 + 12) for x0, x1 in bands]

    # Statements reais têm parágrafos de disclaimer por cima das tabelas. Sem
    # este filtro, o header das colunas passa a ser prosa jurídica.
    candidates = [
        line
        for line in lines
        if not any(looks_like_amount(w.text) for w in line.words)
        and len(line.text) <= MAX_HEADER_CHARS
        and all(len(cell) <= MAX_HEADER_CELL_CHARS for cell in covered(line))
    ]
    if not candidates:
        return [None] * len(bands)

    # O header da tabela é a linha não-numérica que mais colunas cobre. Escolher
    # pela contagem de palavras apanharia descrições longas de continuação.
    header = max(
        candidates,
        key=lambda line: (sum(1 for cell in covered(line) if cell), -line.page, -line.top),
    )
    return [_snake(text) if text else None for text in covered(header)]


def detect_columns(
    lines: Sequence[Line],
    body_left: float,
) -> tuple[list[dict], list[str]]:
    words = [w for line in lines for w in line.words]
    bands = _numeric_clusters(words)
    notes: list[str] = []

    if not bands:
        return [], ["nenhuma coluna numérica detetada — a secção pode não ser tabular"]

    names = _header_names(lines, bands)
    columns: list[dict] = [
        {
            "name": "description",
            "x0": round(body_left - COLUMN_PAD, 2),
            "x1": bands[0][0],
            "type": "text",
        }
    ]

    used: set[str] = {"description"}
    previous_x1 = bands[0][0]
    for position, ((x0, x1), name) in enumerate(zip(bands, names), start=1):
        column_name = name or f"col_{position}"
        while column_name in used:
            column_name = f"{column_name}_{position}"
        used.add(column_name)
        columns.append(
            {
                "name": column_name,
                "x0": max(round(x0, 2), previous_x1),
                "x1": round(x1, 2),
                "type": "amount",
            }
        )
        previous_x1 = round(x1, 2)
        if name is None:
            notes.append(f"coluna em x=[{x0}, {x1}] sem nome no header — chamada {column_name!r}")

    return columns, notes


def build_layout(
    pdf_path: str | Path,
    *,
    patterns: dict[str, list[str]] | None = None,
    pages: tuple[dict[int, list[Line]], dict[int, float]] | None = None,
) -> dict:
    """`pages` evita reler o PDF quando quem chama já o leu."""
    path = Path(pdf_path)
    assert_text_layer(path)

    per_page, heights = pages if pages is not None else _page_lines(path)
    accounts, notes = detect_accounts(per_page, heights)

    body_band, band_notes = detect_body_band(
        per_page, heights, [account["id"] for account in accounts]
    )
    notes = band_notes + notes

    period, period_notes = detect_period(per_page)
    notes.extend(period_notes)

    # Num statement consolidado, o mesmo título ('HOLDINGS') repete-se uma vez
    # por conta. O índice é construído dentro de cada conta e só depois unido.
    index: dict[str, list[int]] = {}
    pages_by_account: dict[str, dict[str, list[int]]] = defaultdict(dict)
    scopes = (
        [(account["id"], {p: per_page[p] for p in account["pages"]}) for account in accounts]
        if accounts
        else [(None, per_page)]
    )
    for account_id, scope in scopes:
        found, scope_notes = detect_sections(scope, patterns or SECTION_TITLE_PATTERNS)
        prefix = f"conta {account_id}: " if account_id else ""
        notes.extend(
            f"{prefix}{note}" for note in scope_notes if "não encontrada" not in note or not accounts
        )
        for name, pages in found.items():
            index.setdefault(name, []).extend(pages)
            if account_id:
                pages_by_account[name][account_id] = pages
    for name in index:
        index[name] = sorted(set(index[name]))

    def in_band(line: Line) -> bool:
        return line.top >= body_band["top"] and line.bottom <= body_band["bottom"]

    sections: dict[str, dict] = {}
    for name in (patterns or SECTION_TITLE_PATTERNS):
        pages = index.get(name)
        hints = dict(getattr(REGISTRY[name], "LAYOUT_HINTS", {})) if name in REGISTRY else {}

        if not pages:
            sections[name] = {"present": False, **hints}
            continue

        section_lines = [line for page in pages for line in per_page[page] if in_band(line)]
        body_left = min((line.x0 for line in section_lines), default=0.0)
        columns, column_notes = detect_columns(section_lines, body_left)
        notes.extend(f"{name}: {note}" for note in column_notes)

        spec = {
            "present": True,
            "pages": pages,
            "anchor": "center",
            "columns": columns,
            **hints,
        }
        if name in pages_by_account:
            spec["pages_by_account"] = dict(pages_by_account[name])

        amount_column = spec.get("amount_column")
        column_names = [c["name"] for c in columns]
        if amount_column not in column_names:
            fallback = column_names[-1] if column_names else None
            spec["amount_column"] = fallback
            notes.append(
                f"{name}: coluna de valor {amount_column!r} não existe; usei {fallback!r}. "
                "Confirma qual é a coluna que soma para o total."
            )
        if spec.get("description_column") not in column_names:
            spec["description_column"] = column_names[0] if column_names else None

        sections[name] = spec

    return {
        "version": LAYOUT_VERSION,
        "generated_from": path.name,
        "page_count": len(per_page),
        **period,
        "y_tolerance": 2.5,
        "body_band": body_band,
        "accounts": accounts,
        "sections": sections,
        "_review": notes
        or ["nenhum aviso automático — revê na mesma o índice de secções e as faixas x"],
    }


def sample_words(pdf_path: str | Path, page: int, limit: int = 60) -> list[dict]:
    """Amostra de extract_words para colar numa issue (contexto da Fase 3)."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        target = pdf.pages[page - 1]
        words = words_from_page(target, page)
    return [
        {
            "text": w.text,
            "x0": round(w.x0, 1),
            "x1": round(w.x1, 1),
            "top": round(w.top, 1),
            "size": round(w.size, 1),
            "fontname": w.fontname,
        }
        for w in words[:limit]
    ]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera layout.json a partir de um statement.")
    parser.add_argument("pdf")
    parser.add_argument("--out", default="layout.json")
    parser.add_argument("--sample", type=int, metavar="PAGE",
                        help="em vez de gerar o layout, imprime uma amostra de extract_words")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.sample:
        print(json.dumps(sample_words(args.pdf, args.sample), indent=2, ensure_ascii=False))
        return 0

    layout = build_layout(args.pdf)
    Path(args.out).write_text(
        json.dumps(layout, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"escrito: {args.out}")
    if layout["accounts"]:
        print("\nContas:")
        for account in layout["accounts"]:
            pages = account["pages"]
            print(f"  {account['id']}  {account['label'] or '(sem nome)'}  "
                  f"págs. {pages[0]}-{pages[-1]}")
    print("\nÍndice de secções:")
    for name, spec in layout["sections"].items():
        if spec.get("present", True):
            print(f"  {name:<10} páginas {spec['pages']}  colunas: "
                  f"{[c['name'] for c in spec['columns']]}")
        else:
            print(f"  {name:<10} AUSENTE")
    print("\nA rever:")
    for note in layout["_review"]:
        print(f"  · {note}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
