"""Varredura automática: PDF -> tabelas -> Excel, sem ninguém escrever secções.

É este o caminho de `python parse.py statement.pdf`.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

import parse as entrypoint
from src.excel import table_state
from src.pipeline import sweep_statement
from src.router import load_layout
from src.tables import classify
from tests.make_synthetic_fixture import FIXTURE, build

LAYOUT = Path("tests/fixtures/layout.synthetic.json")


@pytest.fixture(scope="module", autouse=True)
def fixture_pdf() -> Path:
    if not FIXTURE.exists():
        build()
    return FIXTURE


@pytest.fixture(scope="module")
def sweep():
    return sweep_statement(FIXTURE, load_layout(LAYOUT))


class TestDetecao:
    def test_encontra_uma_tabela_por_titulo(self, sweep):
        titles = [result.spec.title for result in sweep.tables]
        assert titles == [
            "ACCOUNT SUMMARY",
            "PORTFOLIO HOLDINGS",
            "ACCOUNT ACTIVITY",
            "INCOME",
            "FEES AND CHARGES",
        ]

    def test_tabela_que_atravessa_a_pagina_continua_a_ser_uma(self, sweep):
        holdings = next(t for t in sweep.tables if t.spec.title == "PORTFOLIO HOLDINGS")
        assert holdings.spec.pages == [2, 3]
        assert len(holdings.data_rows) == 6

    def test_colunas_saem_do_header_da_tabela(self, sweep):
        holdings = next(t for t in sweep.tables if t.spec.title == "PORTFOLIO HOLDINGS")
        names = [c["name"] for c in holdings.spec.columns]
        assert names[0] == "description"
        assert any("market_value" in name for name in names)

    def test_totais_nao_entram_nas_linhas_de_dados(self, sweep):
        holdings = next(t for t in sweep.tables if t.spec.title == "PORTFOLIO HOLDINGS")
        assert [row["description"] for row in holdings.total_rows] == ["Total Holdings"]
        assert all("Total" not in row["description"] for row in holdings.data_rows)

    def test_parenteses_continuam_a_ser_negativos(self, sweep):
        holdings = next(t for t in sweep.tables if t.spec.title == "PORTFOLIO HOLDINGS")
        column = holdings.spec.amount_column
        short = next(r for r in holdings.data_rows if r["description"].startswith("XYZ"))
        assert short[column] == Decimal("-12500.00")

    def test_descricao_partida_e_juntada(self, sweep):
        holdings = next(t for t in sweep.tables if t.spec.title == "PORTFOLIO HOLDINGS")
        assert any("DUE 11/15/2034" in row["description"] for row in holdings.data_rows)


class TestClassificacao:
    @pytest.mark.parametrize(
        "label, expected",
        [
            ("Total", "total"),
            ("Total Holdings", "total"),
            ("NET CREDITS/(DEBITS)", "total"),
            ("Purchases", "subtotal"),
            ("Total Purchases vs Market Value", "info"),
            ("Net Value Increase/(Decrease)", "info"),
            ("Asset Class: Equities", "info"),
            ("APPLE INC COMMON STOCK", "data"),
            ("", "data"),
        ],
    )
    def test_rotulos(self, label, expected):
        assert classify(label) == expected


class TestReconciliacao:
    def test_tudo_concilia_no_documento_consistente(self, sweep):
        assert sweep.failures == []
        assert len([c for c in sweep.checks if c.passed]) == 5

    def test_estado_por_tabela(self, sweep):
        for result in sweep.tables:
            assert table_state(result.spec, sweep.checks) == "conciliado"

    def test_total_torcido_no_documento_e_apanhado(self, tmp_path):
        """Se o PDF imprimir um total que não fecha, a varredura tem de dar FAIL."""
        corrupted = build(tmp_path / "corrupto.pdf", holdings_total="1,195,590.65")
        sweep = sweep_statement(corrupted, load_layout(LAYOUT))
        holdings = next(t for t in sweep.tables if t.spec.title == "PORTFOLIO HOLDINGS")
        assert holdings.sum_of(holdings.spec.amount_column) == Decimal("1195590.55")
        assert holdings.reference_total == Decimal("1195590.65")
        assert any(c.section == "PORTFOLIO HOLDINGS" and not c.passed for c in sweep.checks)


class TestExcel:
    def test_um_comando_produz_o_livro(self, tmp_path):
        out = tmp_path / "out"
        code = entrypoint.main([str(FIXTURE), "--layout", str(LAYOUT), "--out", str(out)])
        assert code == 0

        workbook = out / f"{FIXTURE.stem}.xlsx"
        assert workbook.exists()

        book = load_workbook(workbook)
        assert book.sheetnames[0] == "Capa"
        # Layout do consolidador primeiro, vistas analíticas a seguir.
        assert book.sheetnames[1:4] == ["POSICOES", "MOVIMENTOS", "De-para"]
        assert "Posições (análise)" in book.sheetnames
        assert "Reconciliação" in book.sheetnames
        assert "Tabelas do documento" in book.sheetnames

        indice = book["Tabelas do documento"]
        header = [cell.value for cell in indice[1]]
        assert "Soma extraída" in header and "Estado" in header
        estados = {
            row[header.index("Estado")].value
            for row in indice.iter_rows(min_row=2, max_row=6)
        }
        assert estados == {"conciliado"}

        totais = book["Totais impressos"]
        rotulos = [row[3].value for row in totais.iter_rows(min_row=2, values_only=False)]
        assert "Total Holdings" in rotulos

    def test_saida_marcada_parcial_quando_ha_falha(self, tmp_path):
        corrupted = build(tmp_path / "corrupto.pdf", holdings_total="1,195,590.65")
        out = tmp_path / "out"
        code = entrypoint.main([str(corrupted), "--layout", str(LAYOUT), "--out", str(out)])
        assert code == 1
        assert (out / "corrupto.PARTIAL.xlsx").exists()
        assert not (out / "corrupto.xlsx").exists()

    def test_strict_nao_escreve_nada(self, tmp_path):
        corrupted = build(tmp_path / "corrupto.pdf", holdings_total="1,195,590.65")
        out = tmp_path / "out"
        code = entrypoint.main(
            [str(corrupted), "--layout", str(LAYOUT), "--out", str(out), "--strict"]
        )
        assert code == 1
        assert not out.exists()


class TestEstruturaDeLotes:
    """A estrutura do statement real: lotes, subtotal, reinvestimentos, totais."""

    @staticmethod
    @pytest.fixture(scope="class")
    def lots(tmp_path_factory):
        from src.mapper import build_layout
        from tests.make_synthetic_fixture import build_lots

        pdf = build_lots(tmp_path_factory.mktemp("lotes") / "lotes.pdf")
        return sweep_statement(pdf, build_layout(pdf))

    def test_uma_tabela(self, lots):
        assert [t.spec.title for t in lots.tables] == ["MUTUAL FUNDS"]

    def test_lote_sem_descricao_herda_o_titulo_acima(self, lots):
        table = lots.tables[0]
        assert [row["group"] for row in table.data_rows[:2]] == [
            "FUND A GROWTH (AAAAX)",
            "FUND A GROWTH (AAAAX)",
        ]

    def test_reinvestimento_conta_mas_nao_abre_grupo(self, lots):
        table = lots.tables[0]
        reinvest = next(r for r in table.rows if r["label"].startswith("Short Term"))
        assert reinvest["row_type"] == "data"
        assert reinvest["group"] == "FUND A GROWTH (AAAAX)"

    def test_purchases_e_subtotal_e_fica_fora_da_soma(self, lots):
        table = lots.tables[0]
        assert all(r["row_type"] == "subtotal" for r in table.rows if r["label"] == "Purchases")
        assert table.sum_of("market_value") == Decimal("380000.00")

    def test_linha_com_o_nome_da_tabela_e_o_total_da_categoria(self, lots):
        table = lots.tables[0]
        category = next(r for r in table.rows if r["label"].startswith("MUTUAL FUNDS"))
        assert category["row_type"] == "total"
        assert table.reference_total == Decimal("380000.00")

    def test_cada_grupo_e_conciliado_contra_o_seu_total(self, lots):
        names = [c.name for c in lots.checks if c.passed]
        assert any("FUND A GROWTH" in name for name in names)
        assert any("FUND B INCOME" in name for name in names)
        assert lots.failures == []


class TestRegrasDeClassificacao:
    def test_datas_no_inicio_ou_no_fim_saem_da_descricao(self):
        from src.tables import _leading_or_trailing_date

        assert _leading_or_trailing_date("12/8 Funds Received") == ("Funds Received", ["12/8"])
        assert _leading_or_trailing_date("FUND A (AAAAX) 3/10/16") == (
            "FUND A (AAAAX)",
            ["3/10/16"],
        )
        assert _leading_or_trailing_date("FUND A (AAAAX)") == ("FUND A (AAAAX)", [])
        assert _leading_or_trailing_date("3/10/16") == ("", ["3/10/16"])
        # Mais-valias: data de compra e data de venda na mesma célula.
        assert _leading_or_trailing_date("SEC A 12/21/16 12/30/16") == (
            "SEC A",
            ["12/21/16", "12/30/16"],
        )

    @pytest.mark.parametrize(
        "label, title, expected",
        [
            ("Stocks", "COMMON STOCKS", True),
            ("MUTUAL FUNDS 71.21%", "MUTUAL FUNDS", True),
            ("Total", "COMMON STOCKS", False),
            ("AMERICAN FUNDS INC (AFXX)", "MUTUAL FUNDS", False),
            ("Cash, BDP, MMFs", "CASH, BANK DEPOSIT PROGRAM AND MONEY MARKET FUNDS", False),
        ],
    )
    def test_rotulo_identifica_a_tabela(self, label, title, expected):
        from src.tables import _label_matches_title

        assert _label_matches_title(label, title) is expected

    def test_total_de_categoria_so_e_promovido_se_dominar_as_parcelas(self):
        """'Change in Value' num roll-forward chama-se como a tabela mas é uma parcela."""
        from src.tables import TableResult, TableSpec, _promote_category_totals

        spec = TableSpec(
            id="t", title="CHANGE IN VALUE", account=None, pages=[1],
            columns=[{"name": "description", "x0": 0, "x1": 10},
                     {"name": "amount", "x0": 10, "x1": 20}],
            header="", amount_column="amount",
        )
        result = TableResult(spec=spec)
        result.rows = [
            {"label": "Credits", "row_type": "data", "amount": Decimal("1000")},
            {"label": "Change in Value", "row_type": "data", "amount": Decimal("10")},
        ]
        _promote_category_totals(result)
        assert [row["row_type"] for row in result.rows] == ["data", "data"]

        result.rows[1]["amount"] = Decimal("5000")
        _promote_category_totals(result)
        assert result.rows[1]["row_type"] == "total"


class TestLeituraUnica:
    def test_paginas_partilhadas_dao_o_mesmo_resultado(self):
        """Ler o PDF uma vez e cortar a banda em memória não pode mudar nada."""
        from src.lines import read_pages

        layout = load_layout(LAYOUT)
        pages, _heights = read_pages(FIXTURE)
        shared = sweep_statement(FIXTURE, layout, pages=pages)
        reread = sweep_statement(FIXTURE, layout)

        assert [t.spec.title for t in shared.tables] == [t.spec.title for t in reread.tables]
        assert [len(t.rows) for t in shared.tables] == [len(t.rows) for t in reread.tables]
        assert len(shared.checks) == len(reread.checks)
