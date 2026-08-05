"""lines.py — agrupamento visual e regra de continuação.

Sem PDF: as palavras são construídas à mão para isolar a geometria.
"""

from __future__ import annotations

import pytest

from src.lines import Line, Word, group_records, group_words_into_lines, starts_with_date


def word(text: str, x0: float, top: float, *, page: int = 1, width: float = 40.0) -> Word:
    return Word(text=text, x0=x0, x1=x0 + width, top=top, bottom=top + 9, page=page)


class TestAgrupamentoPorY:
    def test_palavras_na_mesma_altura_formam_uma_linha(self):
        lines = group_words_into_lines(
            [word("APPLE", 40, 100), word("INC", 80, 100.8), word("225,000.00", 460, 100)]
        )
        assert len(lines) == 1
        assert lines[0].text == "APPLE INC 225,000.00"

    def test_alturas_diferentes_formam_linhas_diferentes(self):
        lines = group_words_into_lines([word("A", 40, 100), word("B", 40, 120)])
        assert [line.text for line in lines] == ["A", "B"]

    def test_paginas_diferentes_nunca_se_misturam(self):
        lines = group_words_into_lines([word("A", 40, 100, page=1), word("B", 40, 100, page=2)])
        assert [(line.page, line.text) for line in lines] == [(1, "A"), (2, "B")]

    def test_palavras_saem_ordenadas_da_esquerda_para_a_direita(self):
        lines = group_words_into_lines([word("fim", 400, 100), word("inicio", 40, 100)])
        assert lines[0].text == "inicio fim"


class TestCelulas:
    @pytest.fixture
    def line(self) -> Line:
        return group_words_into_lines(
            [
                word("APPLE INC", 40, 100, width=120),
                word("1,200", 300, 100, width=30),
                word("225,000.00", 460, 100, width=55),
            ]
        )[0]

    def test_recorta_por_faixa_x(self, line):
        assert line.cell(37, 287) == "APPLE INC"
        assert line.cell(287, 356) == "1,200"
        assert line.cell(438, 523) == "225,000.00"

    def test_faixa_vazia_devolve_string_vazia(self, line):
        assert line.cell(524, 560) == ""

    def test_cells_devolve_dicionario_por_coluna(self, line):
        columns = [
            {"name": "description", "x0": 37, "x1": 287},
            {"name": "quantity", "x0": 287, "x1": 356},
            {"name": "market_value", "x0": 438, "x1": 523},
        ]
        assert line.cells(columns) == {
            "description": "APPLE INC",
            "quantity": "1,200",
            "market_value": "225,000.00",
        }


class TestRegraDeContinuacao:
    def _lines(self, spec: list[tuple[int, float, str]]) -> list[Line]:
        words = []
        for page, top, text in spec:
            for offset, token in enumerate(text.split()):
                words.append(word(token, 40 + offset * 45, top, page=page))
        return group_words_into_lines(words)

    def test_linha_sem_data_e_continuacao_da_anterior(self):
        lines = self._lines(
            [
                (1, 100, "01/05/2026 PURCHASE APPLE"),
                (1, 114, "TRADE DATE 01/03/2026"),
                (1, 128, "01/12/2026 DIVIDEND MSFT"),
            ]
        )
        records = group_records(lines, starts_with_date)
        assert len(records) == 2
        assert len(records[0]) == 2
        assert records[1][0].text.startswith("01/12/2026")

    def test_continuacao_atravessa_a_virada_de_pagina(self):
        """Mesmo mecanismo: a lista de linhas é contínua, a página não interessa."""
        lines = self._lines(
            [
                (1, 700, "01/05/2026 PURCHASE APPLE"),
                (2, 100, "SETTLED 01/07/2026"),
            ]
        )
        records = group_records(lines, starts_with_date)
        assert len(records) == 1
        assert [line.page for line in records[0]] == [1, 2]

    def test_linhas_antes_do_primeiro_registo_nao_se_colam_a_ele(self):
        lines = self._lines(
            [
                (1, 90, "Date Description Amount"),
                (1, 110, "01/05/2026 PURCHASE APPLE"),
            ]
        )
        records = group_records(lines, starts_with_date)
        assert len(records) == 1
        assert len(records[0]) == 1

        kept = group_records(lines, starts_with_date, drop_orphan_head=False)
        assert len(kept) == 2
        assert kept[0][0].text.startswith("Date")

    def test_predicado_de_data(self):
        lines = self._lines([(1, 100, "01/05/2026 X"), (1, 120, "TRADE X")])
        assert starts_with_date(lines[0])
        assert not starts_with_date(lines[1])
