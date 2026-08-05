#!/usr/bin/env python3
"""Gera um statement SINTÉTICO para exercitar o arnês ponta a ponta.

Isto **não** é um statement Morgan Stanley e não substitui a Fase 0 do roadmap:
serve para provar que lines/router/sections/verify funcionam antes de existir um
documento real em mãos. Os números são inventados, mas o documento tem de propósito
tudo aquilo que costuma partir um parser:

  * valores negativos entre parênteses
  * descrição partida em duas linhas
  * uma secção (holdings) que atravessa a virada de página
  * header e footer repetidos em todas as páginas
  * totais impressos em cada secção

    python tests/make_synthetic_fixture.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import fitz

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_statement.pdf"

FONT = "helv"
BOLD = "hebo"
SIZE = 9.0
TITLE_SIZE = 13.0
WIDTH, HEIGHT = 612.0, 792.0

X_DESC = 40.0
X_QTY = 330.0     # margem direita
X_PRICE = 410.0
X_VALUE = 520.0

# ---------------------------------------------------------------- dados impressos
HOLDINGS = [
    ("APPLE INC COMMON STOCK", None, "1,200", "187.50", "225,000.00"),
    ("MICROSOFT CORP COMMON STOCK", None, "800", "412.25", "329,800.00"),
    ("US TREASURY NOTE 4.25%", "DUE 11/15/2034", "500,000", "98.75", "493,750.00"),
    ("VANGUARD TOTAL BOND MKT ETF", None, "2,000", "72.10", "144,200.00"),
    ("CASH AND CASH EQUIVALENTS", None, "15,340.55", "1.00", "15,340.55"),
    ("XYZ CORP SHORT POSITION", None, "(500)", "25.00", "(12,500.00)"),
]
HOLDINGS_TOTAL = "1,195,590.55"

ACTIVITY = [
    ("01/05/2026", "PURCHASE APPLE INC COMMON STOCK", "TRADE DATE 01/03/2026 SETTLED 01/07/2026", "(22,500.00)"),
    ("01/12/2026", "DIVIDEND MICROSOFT CORP", None, "1,150.00"),
    ("01/20/2026", "WIRE TRANSFER RECEIVED", None, "50,000.00"),
    ("01/28/2026", "ADVISORY FEE Q1", None, "(1,895.40)"),
]
ACTIVITY_TOTAL = "26,754.60"

INCOME = [
    ("01/12/2026", "MICROSOFT CORP DIVIDEND", None, "1,150.00"),
    ("01/15/2026", "US TREASURY NOTE INTEREST", None, "10,625.00"),
    ("01/31/2026", "VANGUARD TOTAL BOND MKT ETF DIVIDEND", None, "480.00"),
]
INCOME_TOTAL = "12,255.00"

FEES = [
    ("01/28/2026", "ADVISORY FEE Q1", None, "1,895.40"),
    ("01/31/2026", "CUSTODY FEE", None, "125.00"),
]
FEES_TOTAL = "2,020.40"

BEGINNING_VALUE = "1,140,000.00"
MARKET_CHANGE = "28,835.95"
ENDING_VALUE = "1,195,590.55"


def _decimal(text: str) -> Decimal:
    negative = text.startswith("(")
    value = Decimal(text.strip("()").replace(",", ""))
    return -value if negative else value


def _self_check() -> None:
    """O documento tem de ser aritmeticamente consistente consigo próprio."""
    holdings_sum = sum(_decimal(row[4]) for row in HOLDINGS)
    assert holdings_sum == _decimal(HOLDINGS_TOTAL), holdings_sum
    activity_sum = sum(_decimal(row[3]) for row in ACTIVITY)
    assert activity_sum == _decimal(ACTIVITY_TOTAL), activity_sum
    assert sum(_decimal(r[3]) for r in INCOME) == _decimal(INCOME_TOTAL)
    assert sum(_decimal(r[3]) for r in FEES) == _decimal(FEES_TOTAL)
    roll = _decimal(BEGINNING_VALUE) + activity_sum + _decimal(MARKET_CHANGE)
    assert roll == _decimal(ENDING_VALUE), roll


class Painter:
    def __init__(self, doc: fitz.Document, total_pages: int):
        self.doc = doc
        self.total_pages = total_pages
        self.page = None
        self.number = 0
        self.y = 0.0

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=WIDTH, height=HEIGHT)
        self.number += 1
        self._text(X_DESC, 40.0, "Morgan Stanley  ·  DOCUMENTO SINTÉTICO DE TESTE", size=8)
        self._text(430.0, 40.0, "Account 123-456789", size=8)
        self._text(X_DESC, 762.0, "Este documento não é um statement real.", size=7)
        self._text(470.0, 762.0, f"Page {self.number} of {self.total_pages}", size=7)
        self.y = 90.0

    def _text(self, x: float, y: float, text: str, *, size: float = SIZE, font: str = FONT) -> None:
        self.page.insert_text((x, y), text, fontname=font, fontsize=size)

    def _right(self, x_right: float, y: float, text: str, *, font: str = FONT) -> None:
        width = fitz.get_text_length(text, fontname=font, fontsize=SIZE)
        self.page.insert_text((x_right - width, y), text, fontname=font, fontsize=SIZE)

    def title(self, text: str) -> None:
        self._text(X_DESC, self.y, text, size=TITLE_SIZE, font=BOLD)
        self.y += 24.0

    def header_row(self, labels: list[tuple[float, str]]) -> None:
        self._text(X_DESC, self.y, labels[0][1], font=BOLD)
        for x_right, label in labels[1:]:
            width = fitz.get_text_length(label, fontname=BOLD, fontsize=SIZE)
            self._text(x_right - width, self.y, label, font=BOLD)
        self.y += 16.0

    def row(self, cells: list[tuple[float | None, str]], *, font: str = FONT) -> None:
        for x_right, text in cells:
            if x_right is None:
                self._text(X_DESC, self.y, text, font=font)
            else:
                self._right(x_right, self.y, text, font=font)
        self.y += 14.0

    def gap(self, amount: float = 10.0) -> None:
        self.y += amount


def build(path: Path = FIXTURE) -> Path:
    _self_check()
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open()
    painter = Painter(doc, total_pages=6)

    # ------------------------------------------------------------- p1: sumário
    painter.new_page()
    painter.title("Account Summary")
    painter.row([(None, "Beginning Value"), (X_VALUE, BEGINNING_VALUE)])
    painter.row([(None, "Net Flows"), (X_VALUE, ACTIVITY_TOTAL)])
    painter.row([(None, "Change in Market Value"), (X_VALUE, MARKET_CHANGE)])
    painter.gap()
    painter.row([(None, "Total Portfolio Value"), (X_VALUE, ENDING_VALUE)], font=BOLD)

    # -------------------------------------------------- p2-p3: holdings partido
    painter.new_page()
    painter.title("Portfolio Holdings")
    painter.header_row([(X_DESC, "Description"), (X_QTY, "Quantity"), (X_PRICE, "Price"),
                        (X_VALUE, "Market Value")])
    for description, continuation, quantity, price, value in HOLDINGS[:4]:
        painter.row([(None, description), (X_QTY, quantity), (X_PRICE, price), (X_VALUE, value)])
        if continuation:
            painter.row([(None, "   " + continuation)])

    painter.new_page()
    painter.header_row([(X_DESC, "Description"), (X_QTY, "Quantity"), (X_PRICE, "Price"),
                        (X_VALUE, "Market Value")])
    for description, continuation, quantity, price, value in HOLDINGS[4:]:
        painter.row([(None, description), (X_QTY, quantity), (X_PRICE, price), (X_VALUE, value)])
        if continuation:
            painter.row([(None, "   " + continuation)])
    painter.gap()
    painter.row([(None, "Total Holdings"), (X_VALUE, HOLDINGS_TOTAL)], font=BOLD)

    # ------------------------------------------------------------- p4: activity
    painter.new_page()
    painter.title("Account Activity")
    painter.header_row([(X_DESC, "Date  Description"), (X_VALUE, "Amount")])
    for date, description, continuation, amount in ACTIVITY:
        painter.row([(None, f"{date}  {description}"), (X_VALUE, amount)])
        if continuation:
            painter.row([(None, "   " + continuation)])
    painter.gap()
    painter.row([(None, "Total Activity"), (X_VALUE, ACTIVITY_TOTAL)], font=BOLD)

    # --------------------------------------------------------------- p5: income
    painter.new_page()
    painter.title("Income")
    painter.header_row([(X_DESC, "Date  Description"), (X_VALUE, "Amount")])
    for date, description, _, amount in INCOME:
        painter.row([(None, f"{date}  {description}"), (X_VALUE, amount)])
    painter.gap()
    painter.row([(None, "Total Income"), (X_VALUE, INCOME_TOTAL)], font=BOLD)

    # ----------------------------------------------------------------- p6: fees
    painter.new_page()
    painter.title("Fees and Charges")
    painter.header_row([(X_DESC, "Date  Description"), (X_VALUE, "Amount")])
    for date, description, _, amount in FEES:
        painter.row([(None, f"{date}  {description}"), (X_VALUE, amount)])
    painter.gap()
    painter.row([(None, "Total Fees"), (X_VALUE, FEES_TOTAL)], font=BOLD)

    doc.save(str(path))
    doc.close()
    return path


if __name__ == "__main__":
    print(f"escrito: {build()}")
