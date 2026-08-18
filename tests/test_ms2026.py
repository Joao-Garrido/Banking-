"""O desenho de 2026, reconstruído a partir do guia do statement.

Não é o documento de ninguém — é a estrutura que o guia descreve. Cada teste
aqui corresponde a uma regra do guia, e foi cada um deles que apanhou um erro
real do parser antes de haver documento para testar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.mapper import build_layout
from src.model import Domain, build_datasets
from src.pipeline import sweep_statement
from tests.make_ms2026_fixture import CASH_TOTAL, TOTAL_CONTA, build


def _dec(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


@pytest.fixture(scope="module")
def sweep(tmp_path_factory):
    pdf = build(tmp_path_factory.mktemp("ms2026") / "ms2026.pdf")
    return pdf, sweep_statement(pdf, build_layout(pdf))


class TestEstrutura:
    def test_nenhuma_pagina_fica_por_ler(self, sweep):
        _pdf, resultado = sweep
        paginas = {p for t in resultado.tables for p in t.spec.pages}
        assert paginas == {1, 2, 3, 4, 5, 6}

    def test_caixa_com_travessoes_e_na_forma_coluna(self, sweep):
        """'—' e 'N/A' ocupam célula: sem isso a tabela de caixa desaparecia."""
        _pdf, resultado = sweep
        caixa = next(t for t in resultado.tables if t.spec.title.startswith("CASH,"))
        assert len(caixa.data_rows) == 2
        assert caixa.sum_of(caixa.spec.amount_column) == _dec(CASH_TOTAL)

    def test_seccao_e_subseccao_ficam_ambas(self, sweep):
        """'GOVERNMENT SECURITIES' com 'TREASURY SECURITIES' por baixo."""
        _pdf, resultado = sweep
        treasury = next(t for t in resultado.tables if "TREASURY" in t.spec.title)
        assert treasury.spec.section_path == ("GOVERNMENT SECURITIES", "TREASURY SECURITIES")

    def test_linha_de_fecho_da_seccao_nao_e_posicao(self, sweep):
        """A segunda aparição do nome da secção é o total dela."""
        _pdf, resultado = sweep
        etfs = next(t for t in resultado.tables if t.spec.title.startswith("EXCHANGE-TRADED"))
        fecho = [r for r in etfs.rows if r["label"].startswith("EXCHANGE-TRADED")]
        assert fecho and all(r["row_type"] == "total" for r in fecho)

    def test_categoria_impressa_por_cima_do_seu_total(self, sweep):
        """'CORPORATE FIXED INCOME' seguido de 'TOTAL CORPORATE FIXED INCOME'."""
        _pdf, resultado = sweep
        tabela = next(t for t in resultado.tables if "FIXED-RATE" in t.spec.title)
        rotulos = [r["label"] for r in tabela.rows if r["row_type"] == "data"]
        assert not any(r.startswith("CORPORATE FIXED INCOME") for r in rotulos)


class TestReconciliacao:
    def test_sem_contradicoes(self, sweep):
        _pdf, resultado = sweep
        assert resultado.failures == []

    def test_posicoes_somam_o_total_da_conta(self, sweep):
        """A prova de topo: o que extraímos é o que o statement diz que a conta vale."""
        pdf, resultado = sweep
        layout = build_layout(pdf)
        datasets = build_datasets(resultado.tables, layout.get("accounts", []))
        assert datasets[Domain.POSITIONS].total() == _dec(TOTAL_CONTA)

    def test_uma_linha_por_titulo(self, sweep):
        pdf, resultado = sweep
        layout = build_layout(pdf)
        posicoes = build_datasets(resultado.tables, layout.get("accounts", []))[Domain.POSITIONS]
        # 2 de caixa + 2 ETF + 2 bonds + 1 capital security + 1 treasury
        assert len(posicoes.data_rows) == 8

    def test_coluna_de_valor_e_a_que_o_documento_prova(self, sweep):
        """Com cabeçalhos ilegíveis, é a conciliação que escolhe a coluna."""
        _pdf, resultado = sweep
        for tabela in resultado.tables:
            if tabela.total_rows and tabela.spec.amount_column:
                soma = tabela.sum_of(tabela.spec.amount_column)
                assert soma != Decimal("0") or not tabela.data_rows
