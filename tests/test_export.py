"""Layout de exportação para consolidador: posição, movimento e fundo exclusivo.

O teste que mais interessa é `test_campos_sem_origem_ficam_vazios`: escrever
zero num campo de imposto seria afirmar que o imposto foi zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.export import (
    MOVIMENTO_COLUMNS,
    POSICAO_COLUMNS,
    Explosion,
    build_export,
    explosion_checks,
)
from src.model import build_datasets
from src.pipeline import sweep_statement
from src.router import load_layout
from tests.make_synthetic_fixture import FIXTURE, build

LAYOUT = Path("tests/fixtures/layout.synthetic.json")

FUNDO = {
    "XYZ_CORP_SHORT_POSITION": {
        "nome": "Fundo Exclusivo XYZ",
        "carteira": [
            {"descricao": "TESOURO IPCA 2035", "classe": "Renda Fixa", "valor": "-8000.00"},
            {"descricao": "CDB BANCO A", "classe": "Renda Fixa", "valor": "-3000.00"},
            {"descricao": "AÇÕES BR", "classe": "Renda Variável", "valor": "-1500.00"},
        ],
    }
}


@pytest.fixture(scope="module")
def datasets():
    if not FIXTURE.exists():
        build()
    layout = load_layout(LAYOUT)
    sweep = sweep_statement(FIXTURE, layout)
    return build_datasets(sweep.tables, layout.get("accounts", [])), layout


class TestLayout:
    def test_posicoes_tem_as_colunas_do_layout(self, datasets):
        data, layout = datasets
        export = build_export(data, layout, modes=[Explosion.NONE])
        assert export.posicoes[Explosion.NONE]
        for linha in export.posicoes[Explosion.NONE]:
            assert list(linha) == POSICAO_COLUMNS

    def test_movimentos_tem_as_colunas_do_layout(self, datasets):
        data, layout = datasets
        export = build_export(data, layout)
        assert export.movimentos
        for linha in export.movimentos:
            assert list(linha) == MOVIMENTO_COLUMNS

    def test_idativo_e_estavel_e_determinista(self, datasets):
        data, layout = datasets
        primeiro = build_export(data, layout, modes=[Explosion.NONE])
        segundo = build_export(data, layout, modes=[Explosion.NONE])
        assert [p["IDATIVO"] for p in primeiro.posicoes[Explosion.NONE]] == [
            p["IDATIVO"] for p in segundo.posicoes[Explosion.NONE]
        ]
        assert all(p["IDATIVO"] for p in primeiro.posicoes[Explosion.NONE])

    def test_campos_sem_origem_ficam_vazios(self, datasets):
        """IR e IOF não existem neste documento: vazio, nunca zero."""
        data, layout = datasets
        export = build_export(data, layout, modes=[Explosion.NONE])
        for linha in export.posicoes[Explosion.NONE]:
            assert linha["VALOR_IR"] is None
            assert linha["VALOR_IOF"] is None
            assert linha["VALOR_LIQUIDO"] is None

        campos = {row["CAMPO"]: row for row in export.mapping}
        assert campos["VALOR_IR"]["ESTADO"] == "vazio"
        assert "IR" in campos["VALOR_IR"]["NOTA"]

    def test_data_de_posicao_vem_do_fim_do_periodo(self, datasets):
        data, layout = datasets
        layout = {**layout, "period_end": "2026-01-31"}
        export = build_export(data, layout, modes=[Explosion.NONE])
        assert {p["DT_POSICAO"] for p in export.posicoes[Explosion.NONE]} == {"2026-01-31"}

    def test_sem_periodo_a_data_fica_vazia_e_avisa(self, datasets):
        data, layout = datasets
        export = build_export(data, {**layout, "period_end": None}, modes=[Explosion.NONE])
        assert all(p["DT_POSICAO"] is None for p in export.posicoes[Explosion.NONE])
        assert any("DT_POSICAO" in nota for nota in export.notes)

    def test_cliente_vem_do_mapeamento(self, datasets):
        data, layout = datasets
        # A chave é o número da conta; "" cobre um statement de conta única.
        clients = {"": {"idcliente": "C-001", "nome": "Família Exemplo"}}
        export = build_export(data, layout, clients=clients, modes=[Explosion.NONE])
        linha = export.posicoes[Explosion.NONE][0]
        assert linha["IDCLIENTE"] == "C-001"
        assert linha["NOME_CLIENTE"] == "Família Exemplo"


class TestTipoDeMovimento:
    def test_traducao_dos_tipos(self, datasets):
        data, layout = datasets
        export = build_export(data, layout)
        tipos = {(m["TIPO_ORIGINAL"], m["TIPO_MOVIMENTO"]) for m in export.movimentos}
        assert ("Dividend", "DIV") in tipos

    def test_tipo_original_e_preservado(self, datasets):
        data, layout = datasets
        export = build_export(data, layout)
        assert all(m["TIPO_ORIGINAL"] for m in export.movimentos)

    def test_sem_correspondencia_sai_outros(self):
        from src.export import TIPO_MOVIMENTO, _movimento_row

        linha = _movimento_row(
            {"tipo": "Coisa Nunca Vista", "descricao": "x", "conta": "1"}, {}, "USD"
        )
        assert linha["TIPO_MOVIMENTO"] == "OUTROS"
        assert linha["TIPO_ORIGINAL"] == "Coisa Nunca Vista"
        assert "Coisa Nunca Vista" not in TIPO_MOVIMENTO


class TestFundoExclusivo:
    def test_sem_explosao_o_fundo_e_uma_linha(self, datasets):
        data, layout = datasets
        export = build_export(data, layout, exclusive_funds=FUNDO, modes=[Explosion.NONE])
        linhas = export.posicoes[Explosion.NONE]
        assert sum(1 for l in linhas if l["FUNDO_EXCLUSIVO"] == "sim") == 1

    def test_explosao_consolidada_agrega_por_classe(self, datasets):
        data, layout = datasets
        export = build_export(
            data, layout, exclusive_funds=FUNDO, modes=[Explosion.NONE, Explosion.CONSOLIDATED]
        )
        linhas = export.posicoes[Explosion.CONSOLIDATED]
        explodidas = [l for l in linhas if l["ORIGEM_EXPLOSAO"]]
        assert {l["DESCRICAO_ATIVO"] for l in explodidas} == {"Renda Fixa", "Renda Variável"}

    def test_explosao_detalhada_traz_cada_ativo(self, datasets):
        data, layout = datasets
        export = build_export(
            data, layout, exclusive_funds=FUNDO, modes=[Explosion.NONE, Explosion.DETAILED]
        )
        explodidas = [l for l in export.posicoes[Explosion.DETAILED] if l["ORIGEM_EXPLOSAO"]]
        assert {l["DESCRICAO_ATIVO"] for l in explodidas} == {
            "TESOURO IPCA 2035",
            "CDB BANCO A",
            "AÇÕES BR",
        }

    def test_explodir_nao_muda_o_total(self, datasets):
        """Explodir muda a composição da carteira, nunca o seu valor."""
        data, layout = datasets
        export = build_export(data, layout, exclusive_funds=FUNDO, modes=Explosion.ALL)
        checks = explosion_checks(export, FUNDO)
        assert checks and all(c.passed for c in checks)

    def test_carteira_que_nao_bate_e_apanhada(self, datasets):
        data, layout = datasets
        torcido = {
            "XYZ_CORP_SHORT_POSITION": {
                "carteira": [{"descricao": "X", "classe": "RF", "valor": "-9999.99"}]
            }
        }
        export = build_export(data, layout, exclusive_funds=torcido, modes=Explosion.ALL)
        checks = explosion_checks(export, torcido)
        assert any(not c.passed for c in checks)

    def test_sem_carteira_declarada_avisa(self, datasets):
        data, layout = datasets
        export = build_export(
            data, layout, exclusive_funds={}, modes=[Explosion.NONE, Explosion.DETAILED]
        )
        assert any("nenhuma posição corresponde" in nota for nota in export.notes)
