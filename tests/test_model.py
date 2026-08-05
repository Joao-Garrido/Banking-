"""A camada canónica: das tabelas do documento para as vistas consolidadas.

O teste que mais interessa aqui é `test_mapeamento_errado_e_apanhado`: a camada
canónica pode reorganizar o dinheiro, nunca criá-lo nem perdê-lo.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.mapper import build_layout
from src.model import (
    Domain,
    build_datasets,
    classify_table,
    dataset_checks,
    portfolio_checks,
)
from src.pipeline import sweep_statement
from src.router import load_layout
from tests.make_synthetic_fixture import FIXTURE, build, build_lots

LAYOUT = Path("tests/fixtures/layout.synthetic.json")


class TestClassificacaoDeTabelas:
    @pytest.mark.parametrize(
        "title, domain",
        [
            ("COMMON STOCKS", Domain.POSITIONS),
            ("MUTUAL FUNDS", Domain.POSITIONS),
            ("CASH, BANK DEPOSIT PROGRAM AND MONEY MARKET FUNDS", Domain.POSITIONS),
            ("CASH FLOW ACTIVITY BY DATE", Domain.TRANSACTIONS),
            ("UNSETTLED PURCHASES/SALES ACTIVITY", Domain.TRANSACTIONS),
            ("LONG-TERM GAIN/(LOSS)", Domain.REALIZED),
            ("SECURITY TRANSFERS", Domain.TRANSFERS),
            ("CORPORATE ACTIONS", Domain.TRANSFERS),
            ("ALLOCATION OF ASSETS", Domain.ALLOCATION),
            ("CHANGE IN VALUE OF YOUR ACCOUNTS", Domain.OVERVIEW),
            ("QUALQUER OUTRA COISA", Domain.OTHER),
        ],
    )
    def test_titulo_decide_o_dominio(self, title, domain):
        assert classify_table(title) == domain


class TestVistasSobreOSintetico:
    @staticmethod
    @pytest.fixture(scope="class")
    def sweep():
        if not FIXTURE.exists():
            build()
        return sweep_statement(FIXTURE, load_layout(LAYOUT))

    @staticmethod
    @pytest.fixture(scope="class")
    def datasets(sweep):
        return build_datasets(sweep.tables, [])

    def test_posicoes_saem_da_tabela_de_holdings(self, datasets):
        positions = datasets[Domain.POSITIONS]
        assert len(positions.data_rows) == 6
        assert positions.total() == Decimal("1195590.55")

    def test_movimentos_saem_da_atividade(self, datasets):
        assert datasets[Domain.TRANSACTIONS].total() == Decimal("26754.60")

    def test_vistas_nao_incluem_totais_do_documento(self, datasets):
        for dataset in datasets.values():
            assert all(row.get("tipo_linha") == "data" for row in dataset.rows)

    def test_peso_por_posicao_soma_cem(self, datasets):
        pesos = [
            row["peso_pct"]
            for row in datasets[Domain.POSITIONS].data_rows
            if row.get("peso_pct") is not None
        ]
        assert len(pesos) == 6
        assert abs(sum(pesos) - Decimal("100")) <= Decimal("0.05")

    def test_conferencia_da_vista_contra_as_tabelas(self, datasets, sweep):
        checks = dataset_checks(datasets, sweep.tables)
        assert checks and all(c.passed for c in checks if c.name.startswith("vista"))


class TestRendimento:
    @staticmethod
    @pytest.fixture(scope="class")
    def datasets():
        if not FIXTURE.exists():
            build()
        sweep = sweep_statement(FIXTURE, load_layout(LAYOUT))
        return build_datasets(sweep.tables, [])

    def test_dividendos_e_juros_entram(self, datasets):
        tipos = {row["tipo"] for row in datasets[Domain.INCOME].rows}
        assert tipos == {"Dividend"}

    def test_uma_compra_nao_e_rendimento(self, datasets):
        descricoes = [row["descricao"] for row in datasets[Domain.INCOME].rows]
        assert not any("PURCHASE" in d.upper() for d in descricoes)

    def test_reinvestimento_nao_conta_como_rendimento(self):
        """Reinvestir um dividendo é aplicá-lo, não recebê-lo outra vez."""
        from src.model import Dataset, _income_from

        transactions = Dataset(domain=Domain.TRANSACTIONS)
        transactions.rows = [
            {"tipo": "Dividend", "valor": Decimal("100"), "tipo_linha": "data"},
            {"tipo": "Dividend Reinvestment", "valor": Decimal("-100"), "tipo_linha": "data"},
        ]
        income = _income_from(transactions)
        assert [row["valor"] for row in income.rows] == [Decimal("100")]


class TestConferenciasDaCamada:
    @staticmethod
    @pytest.fixture(scope="class")
    def lots(tmp_path_factory):
        pdf = build_lots(tmp_path_factory.mktemp("lotes") / "lotes.pdf")
        sweep = sweep_statement(pdf, build_layout(pdf))
        return sweep, build_datasets(sweep.tables, [])

    def test_vista_bate_com_as_tabelas_de_origem(self, lots):
        sweep, datasets = lots
        assert datasets[Domain.POSITIONS].total() == Decimal("380000.00")
        assert all(c.passed for c in dataset_checks(datasets, sweep.tables))

    def test_mapeamento_errado_e_apanhado(self, lots, monkeypatch):
        """Se o campo canónico apontar para a coluna errada, a conferência falha."""
        import src.model as model

        sweep, _ = lots
        monkeypatch.setitem(model.FIELD_SOURCES, "valor_mercado", [r"^quantity$"])
        datasets = build_datasets(sweep.tables, [])
        checks = dataset_checks(datasets, sweep.tables)
        assert any(not c.passed for c in checks)

    def test_prova_de_topo_por_conta(self):
        """Posições + operações por liquidar == total impresso da conta."""
        pdf = Path("tests/fixtures/real/ms_2016_12.pdf")
        if not pdf.exists():
            pytest.skip("statement real não está disponível (não é versionado)")

        cached = Path(".layouts/ms_2016_12.json")
        layout = load_layout(cached) if cached.exists() else build_layout(pdf)
        sweep = sweep_statement(pdf, layout)
        datasets = build_datasets(sweep.tables, layout["accounts"])
        checks = portfolio_checks(datasets, sweep.tables, layout["accounts"])
        assert checks and all(c.passed for c in checks)
