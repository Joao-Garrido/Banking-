#!/usr/bin/env python3
"""Statement sintético no desenho de 2026, reconstruído a partir do guia.

Não é o documento de ninguém: é a **estrutura** descrita no guia do statement —
secções, cabeçalhos de coluna, linhas de lote, a linha que fecha cada secção,
`()` para negativo, `N/A` e `-` para ausente — com números inventados que fecham
entre si.

Serve para o que o documento real não pode: correr o parser contra o desenho de
2026 sem ter o documento à mão, ver o que parte e corrigir. O que este ficheiro
não descreve continua por verificar — e isso está dito no README.

    python tests/make_ms2026_fixture.py
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import fitz

FIXTURE = Path(__file__).parent / "fixtures" / "ms2026_statement.pdf"

FONT, BOLD = "helv", "hebo"
SIZE, TITLE_SIZE = 8.5, 11.0
WIDTH, HEIGHT = 792.0, 612.0          # o statement real é apaisado

X_DESC = 36.0
X_DATE = 260.0
X_QTY = 340.0
X_UNIT = 405.0
X_PRICE = 470.0
X_COST = 545.0
X_VALUE = 620.0
X_GAIN = 690.0
X_INCOME = 740.0
X_YIELD = 780.0

PERIODO = "6/1/26-6/30/26"
ANO = "1/1/26-6/30/26"
CONTAS = [
    ("316-014265-042", "Active Assets Account"),
    ("316-014306-042", "Select UMA Active Assets Account"),
]

# ------------------------------------------------------------------ os dados
# Cada título tem lotes e uma linha 'Total' — o guia manda ficar só com o Total.
ETFS = [
    ("INVESCO QQQ TRUST, SERIES 1 (QQQ)", [
        ("9/16/25", "120.000", "455.10", "520.40", "54,612.00", "62,448.00", "7,836.00"),
        ("12/4/25", "40.000", "480.25", "520.40", "19,210.00", "20,816.00", "1,606.00"),
        ("3/3/26", "25.000", "505.80", "520.40", "12,645.00", "13,010.00", "365.00"),
    ], "185.000", "86,467.00", "96,274.00", "9,807.00", "480.00", "0.50"),
    ("VANGUARD TOTAL BOND MARKET (BND)", [
        ("12/15/25", "1,500.000", "72.40", "71.10", "108,600.00", "106,650.00", "(1,950.00)"),
        ("2/17/26", "500.000", "71.90", "71.10", "35,950.00", "35,550.00", "(400.00)"),
    ], "2,000.000", "144,550.00", "142,200.00", "(2,350.00)", "4,260.00", "3.00"),
]
ETF_SECAO = ("14.55%", "231,017.00", "238,474.00", "7,457.00", "4,740.00", "1.99")

# Nos bonds o nome está na coluna da descrição em todas as linhas.
BONDS = [
    ("APPLE INC 3.85% DUE 5/4/2043", [
        ("5/4/23", "100,000.000", "98.20", "95.40", "98,200.00", "95,400.00", "(2,800.00)"),
    ], "100,000.000", "98,200.00", "95,400.00", "(2,800.00)", "3,850.00", "4.04"),
    ("MORGAN STANLEY 4.10% DUE 1/15/2030", [
        ("1/15/24", "50,000.000", "99.50", "101.20", "49,750.00", "50,600.00", "850.00"),
    ], "50,000.000", "49,750.00", "50,600.00", "850.00", "2,050.00", "4.05"),
]
BONDS_SECAO = ("8.91%", "147,950.00", "146,000.00", "(1,950.00)", "5,900.00", "4.04")

CAPITAL_SECURITIES = [
    ("JPMORGAN CAP TRUST 6.00% PERP", [
        ("3/2/24", "20,000.000", "100.00", "102.50", "20,000.00", "20,500.00", "500.00"),
    ], "20,000.000", "20,000.00", "20,500.00", "500.00", "1,200.00", "5.85"),
]
CAPITAL_SECAO = ("1.25%", "20,000.00", "20,500.00", "500.00", "1,200.00", "5.85")
# CORPORATE FIXED INCOME = bonds + fixed-rate capital securities
CORPORATE_FIXED_INCOME = ("10.16%", "167,950.00", "166,500.00", "(1,450.00)", "7,100.00", "4.26")

TREASURIES = [
    ("U.S. TREASURY NOTE 4.25% DUE 11/15/2034", [
        ("11/20/24", "200,000.000", "99.10", "97.80", "198,200.00", "195,600.00", "(2,600.00)"),
    ], "200,000.000", "198,200.00", "195,600.00", "(2,600.00)", "8,500.00", "4.35"),
]
TREASURIES_SECAO = ("11.93%", "198,200.00", "195,600.00", "(2,600.00)", "8,500.00", "4.35")

CASH = [
    ("MORGAN STANLEY BANK N.A.", "310,000.00", "—", "N/A", "0.010"),
    ("MORGAN STANLEY PRIVATE BANK NA", "128,426.00", "—", "31.00", "0.010"),
]
CASH_TOTAL = "438,426.00"

# TOTAL VALUE da conta = cash + etfs + corporate fixed income + treasuries
TOTAL_CONTA = "1,039,000.00"

MOVIMENTOS = [
    ("6/2", "Funds Received", "WIRE FROM CLIENT", "", "", "250,000.00"),
    ("6/5", "Bought", "INVESCO QQQ TRUST, SERIES 1", "25.000", "520.40", "(13,010.00)"),
    ("6/12", "Dividend", "VANGUARD TOTAL BOND MARKET", "", "", "355.00"),
    ("6/15", "Interest", "U.S. TREASURY NOTE 4.25%", "", "", "4,250.00"),
    ("6/20", "Sold", "APPLE INC 3.85% DUE 5/4/2043", "10,000.000", "95.40", "9,540.00"),
    ("6/26", "Service Charge", "ADVISORY FEE", "", "", "(1,875.00)"),
    ("6/30", "Automatic Investment", "BANK DEPOSIT PROGRAM", "", "", "(4,260.00)"),
]
MOVIMENTOS_TOTAL = "245,000.00"


def _dec(text: str) -> Decimal:
    negativo = text.startswith("(")
    return (-1 if negativo else 1) * Decimal(text.strip("()").replace(",", ""))


def _self_check() -> None:
    """O documento tem de fechar consigo próprio — senão não serve de fixture."""
    for grupo, secao in ((ETFS, ETF_SECAO), (BONDS, BONDS_SECAO),
                         (CAPITAL_SECURITIES, CAPITAL_SECAO), (TREASURIES, TREASURIES_SECAO)):
        for _, lotes, _, custo, valor, _, _, _ in grupo:
            assert sum(_dec(l[5]) for l in lotes) == _dec(valor), valor
            assert sum(_dec(l[4]) for l in lotes) == _dec(custo), custo
        assert sum(_dec(t[4]) for t in grupo) == _dec(secao[2]), secao
        assert sum(_dec(t[3]) for t in grupo) == _dec(secao[1]), secao

    assert _dec(BONDS_SECAO[2]) + _dec(CAPITAL_SECAO[2]) == _dec(CORPORATE_FIXED_INCOME[2])
    assert sum(_dec(c[1]) for c in CASH) == _dec(CASH_TOTAL)
    total = (
        _dec(CASH_TOTAL) + _dec(ETF_SECAO[2])
        + _dec(CORPORATE_FIXED_INCOME[2]) + _dec(TREASURIES_SECAO[2])
    )
    assert total == _dec(TOTAL_CONTA), total
    assert sum(_dec(m[5]) for m in MOVIMENTOS) == _dec(MOVIMENTOS_TOTAL)


class Painter:
    def __init__(self, doc: fitz.Document, total_pages: int, conta: str, titular: str):
        self.doc, self.total_pages = doc, total_pages
        self.conta, self.titular = conta, titular
        self.page = None
        self.number = 0
        self.y = 0.0

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=WIDTH, height=HEIGHT)
        self.number += 1
        self._text(X_DESC, 40.0,
                   f"CLIENT STATEMENT For the Period June 1-30, 2026 "
                   f"Page {self.number} of {self.total_pages}", size=7)
        self._text(X_DESC, 62.0, f"Account Detail {self.conta} {self.titular}", size=7)
        self.y = 105.0

    def _text(self, x, y, text, *, size=SIZE, font=FONT) -> None:
        self.page.insert_text((x, y), text, fontname=font, fontsize=size)

    def _right(self, x_right, y, text, *, font=FONT) -> None:
        largura = fitz.get_text_length(text, fontname=font, fontsize=SIZE)
        self.page.insert_text((x_right - largura, y), text, fontname=font, fontsize=SIZE)

    def title(self, text: str) -> None:
        self._text(X_DESC, self.y, text, size=TITLE_SIZE, font=BOLD)
        self.y += 20.0

    def header(self, labels: list[tuple[float | None, str]]) -> None:
        for x_right, label in labels:
            if x_right is None:
                self._text(X_DESC, self.y, label, font=BOLD)
            else:
                largura = fitz.get_text_length(label, fontname=BOLD, fontsize=SIZE)
                self._text(x_right - largura, self.y, label, font=BOLD)
        self.y += 15.0

    def row(self, cells: list[tuple[float | None, str]], *, font=FONT, indent=0.0) -> None:
        for x_right, text in cells:
            if not text:
                continue
            if x_right is None:
                self._text(X_DESC + indent, self.y, text, font=font)
            else:
                self._right(x_right, self.y, text, font=font)
        self.y += 12.0

    def gap(self, amount: float = 9.0) -> None:
        self.y += amount


# O cabeçalho real ocupa duas linhas quando o rótulo não cabe numa; é assim que
# o statement evita que os nomes das colunas se sobreponham.
HEADER_POSICOES_TOPO = [
    (X_GAIN, "Unrealized"), (X_INCOME, "Est Ann"), (X_YIELD, "Current"),
]
HEADER_POSICOES = [
    (None, "Security Description"), (X_DATE, "Trade Date"), (X_QTY, "Quantity"),
    (X_UNIT, "Unit Cost"), (X_PRICE, "Share Price"), (X_COST, "Total Cost"),
    (X_VALUE, "Market Value"), (X_GAIN, "Gain/(Loss)"),
    (X_INCOME, "Income"), (X_YIELD, "Yield %"),
]
HEADER_SECAO = [
    (None, ""), (X_DATE, "Percentage"), (X_COST, "Total Cost"),
    (X_VALUE, "Market Value"), (X_GAIN, "Unrealized Gain/(Loss)"),
    (X_INCOME, "Est Ann Income"), (X_YIELD, "Current Yield %"),
]


def _seccao_de_posicoes(painter: Painter, titulo: str, grupos, secao, *, subtitulo=None) -> None:
    painter.title(titulo)
    if subtitulo:
        painter.title(subtitulo)
    painter.header(HEADER_POSICOES_TOPO)
    painter.header(HEADER_POSICOES)

    for nome, lotes, qtd, custo, valor, ganho, rendimento, yld in grupos:
        primeiro = True
        for data, quantidade, unitario, preco, custo_lote, valor_lote, ganho_lote in lotes:
            painter.row([
                (None, nome if primeiro else ""),
                (X_DATE, data), (X_QTY, quantidade), (X_UNIT, unitario), (X_PRICE, preco),
                (X_COST, custo_lote), (X_VALUE, valor_lote), (X_GAIN, ganho_lote),
            ], indent=0.0 if primeiro else 0.0)
            primeiro = False
        painter.row([
            (None, "Total"), (X_QTY, qtd), (X_COST, custo), (X_VALUE, valor),
            (X_GAIN, ganho), (X_INCOME, rendimento), (X_YIELD, yld),
        ], font=BOLD)
        painter.gap(6.0)

    # A segunda aparição do nome da secção é o total da secção.
    painter.gap()
    painter.header(HEADER_SECAO)
    painter.row([
        (None, titulo), (X_DATE, secao[0]), (X_COST, secao[1]), (X_VALUE, secao[2]),
        (X_GAIN, secao[3]), (X_INCOME, secao[4]), (X_YIELD, secao[5]),
    ], font=BOLD)
    painter.gap()


def build(path: Path = FIXTURE) -> Path:
    _self_check()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    conta, titular = CONTAS[0]
    painter = Painter(doc, total_pages=6, conta=conta, titular=titular)

    # ---------------------------------------------------- p1: sumário da conta
    painter.new_page()
    painter.title("CHANGE IN VALUE OF YOUR ACCOUNT (includes accrued interest)")
    painter.header([(None, ""), (X_COST, f"This Period ({PERIODO})"), (X_VALUE, f"This Year ({ANO})")])
    for rubrica, periodo, ano in [
        ("TOTAL BEGINNING VALUE", "790,000.00", "700,000.00"),
        ("Credits", "250,000.00", "300,000.00"),
        ("Debits", "(5,000.00)", "(12,000.00)"),
        ("Security Transfers", "—", "—"),
        ("Net Credits/Debits/Transfers", "245,000.00", "288,000.00"),
        ("Change in Value", "4,000.00", "51,000.00"),
        ("TOTAL ENDING VALUE", TOTAL_CONTA, TOTAL_CONTA),
    ]:
        painter.row([(None, rubrica), (X_COST, periodo), (X_VALUE, ano)])

    painter.gap(18.0)
    painter.title("ASSET ALLOCATION (includes accrued interest)")
    painter.header([(None, ""), (X_COST, "Market Value"), (X_VALUE, "Percentage")])
    for rubrica, valor, pct in [
        ("Cash", CASH_TOTAL, "42.20%"),
        ("Equities", ETF_SECAO[2], "22.95%"),
        ("Fixed Income & Preferreds", "362,100.00", "34.85%"),
        ("Alternatives", "—", "—"),
        ("TOTAL VALUE", TOTAL_CONTA, "100.00%"),
    ]:
        painter.row([(None, rubrica), (X_COST, valor), (X_VALUE, pct)])

    # -------------------------------------------------------------- p2: caixa
    painter.new_page()
    painter.title("CASH, BANK DEPOSIT PROGRAM AND MONEY MARKET FUNDS")
    painter.header([
        (None, "Description"), (X_COST, "Market Value"), (X_VALUE, "Current Yield %"),
        (X_GAIN, "Est Ann Income"), (X_YIELD, "APY %"),
    ])
    for descricao, valor, yld, rendimento, apy in CASH:
        painter.row([(None, descricao), (X_COST, valor), (X_VALUE, yld),
                     (X_GAIN, rendimento), (X_YIELD, apy)])
    painter.row([(None, "CASH, BDP, AND MMFs"), (X_COST, CASH_TOTAL)], font=BOLD)

    # ---------------------------------------------------------------- p3: ETFs
    painter.new_page()
    _seccao_de_posicoes(painter, "EXCHANGE-TRADED & CLOSED-END FUNDS", ETFS, ETF_SECAO)

    # ------------------------------------------------- p4: rendimento fixo
    painter.new_page()
    _seccao_de_posicoes(painter, "CORPORATE BONDS", BONDS, BONDS_SECAO)
    _seccao_de_posicoes(
        painter, "FIXED-RATE CAPITAL SECURITIES", CAPITAL_SECURITIES, CAPITAL_SECAO
    )
    painter.row([
        (None, "CORPORATE FIXED INCOME"), (X_DATE, CORPORATE_FIXED_INCOME[0]),
        (X_COST, CORPORATE_FIXED_INCOME[1]), (X_VALUE, CORPORATE_FIXED_INCOME[2]),
        (X_GAIN, CORPORATE_FIXED_INCOME[3]), (X_INCOME, CORPORATE_FIXED_INCOME[4]),
        (X_YIELD, CORPORATE_FIXED_INCOME[5]),
    ], font=BOLD)
    painter.row([
        (None, "TOTAL CORPORATE FIXED INCOME"), (X_COST, CORPORATE_FIXED_INCOME[1]),
        (X_VALUE, CORPORATE_FIXED_INCOME[2]),
    ], font=BOLD)

    # --------------------------------------------------- p5: dívida pública
    painter.new_page()
    _seccao_de_posicoes(
        painter, "GOVERNMENT SECURITIES", TREASURIES, TREASURIES_SECAO,
        subtitulo="TREASURY SECURITIES",
    )
    painter.gap()
    painter.row([(None, "TOTAL VALUE (includes accrued interest)"),
                 (X_DATE, "100.00%"), (X_VALUE, TOTAL_CONTA)], font=BOLD)

    # ------------------------------------------------------------ p6: atividade
    painter.new_page()
    painter.title("CASH FLOW ACTIVITY BY DATE")
    painter.header([
        (None, "Date  Activity Type  Description"), (X_QTY, "Quantity"),
        (X_PRICE, "Price"), (X_VALUE, "Credits/(Debits)"),
    ])
    for data, tipo, descricao, quantidade, preco, valor in MOVIMENTOS:
        painter.row([(None, f"{data}  {tipo}  {descricao}"), (X_QTY, quantidade),
                     (X_PRICE, preco), (X_VALUE, valor)])
    painter.gap()
    painter.row([(None, "NET CREDITS/(DEBITS)"), (X_VALUE, MOVIMENTOS_TOTAL)], font=BOLD)

    doc.save(str(path))
    doc.close()
    return path


if __name__ == "__main__":
    print(f"escrito: {build()}")
