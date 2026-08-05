"""Ciclo completo sobre a fixture sintética: PDF -> secções -> reconciliação.

O teste que mais interessa aqui não é o PASS — é `test_gabarito_torcido_falha`.
Um arnês que não sabe falhar não é um arnês.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

import verify
from src.pipeline import parse_statement
from src.router import load_layout
from tests.make_synthetic_fixture import FIXTURE, build

LAYOUT = Path("tests/fixtures/layout.synthetic.json")
EXPECTED = Path("tests/expected.json")


@pytest.fixture(scope="module", autouse=True)
def fixture_pdf() -> Path:
    if not FIXTURE.exists():
        build()
    return FIXTURE


@pytest.fixture(scope="module")
def layout() -> dict:
    return load_layout(LAYOUT)


@pytest.fixture(scope="module")
def result(layout):
    return parse_statement(FIXTURE, layout)


class TestExtracao:
    def test_todas_as_seccoes_saem(self, result):
        assert sorted(result.sections) == ["activity", "fees", "holdings", "income"]

    def test_contagens(self, result):
        assert result["holdings"].row_count == 6
        assert result["activity"].row_count == 4
        assert result["income"].row_count == 3
        assert result["fees"].row_count == 2

    def test_holdings_atravessa_a_virada_de_pagina(self, result):
        assert result["holdings"].pages == [2, 3]
        assert {row["source_page"] for row in result["holdings"].rows} == {2, 3}

    def test_descricao_partida_em_duas_linhas_e_juntada(self, result):
        descriptions = [row["description"] for row in result["holdings"].rows]
        assert "US TREASURY NOTE 4.25% DUE 11/15/2034" in descriptions

    def test_continuacao_na_activity_nao_cria_registo_novo(self, result):
        rows = result["activity"].rows
        assert "TRADE DATE 01/03/2026" in rows[0]["description"]
        assert len(rows) == 4

    def test_valor_entre_parenteses_fica_negativo(self, result):
        short = next(
            row for row in result["holdings"].rows if row["description"].startswith("XYZ CORP")
        )
        assert short["market_value"] == Decimal("-12500.00")
        assert short["quantity"] == Decimal("-500")

    def test_totais_impressos_sao_lidos_e_nao_entram_nas_linhas(self, result):
        holdings = result["holdings"]
        assert [t.value for t in holdings.printed_totals] == [Decimal("1195590.55")]
        assert all("Total" not in row["description"] for row in holdings.rows)

    def test_soma_bate_com_o_total_impresso(self, result):
        for name, column in [
            ("holdings", "market_value"),
            ("activity", "amount"),
            ("income", "amount"),
            ("fees", "amount"),
        ]:
            section = result[name]
            assert section.sum_of(column) == section.printed_totals[0].value


class TestArnes:
    def test_verify_passa(self, capsys):
        assert verify.main(["--layout", str(LAYOUT)]) == 0
        assert "PASS" in capsys.readouterr().out

    def test_verify_por_seccao(self, capsys):
        assert verify.main(["--layout", str(LAYOUT), "--section", "holdings"]) == 0
        out = capsys.readouterr().out
        assert "holdings" in out and "activity ·" not in out

    def test_gabarito_torcido_falha(self, tmp_path, capsys):
        """Se mexermos num cêntimo do gabarito, o arnês tem de dar FAIL."""
        data = json.loads(EXPECTED.read_text(encoding="utf-8"))
        key = "tests/fixtures/synthetic_statement.pdf"
        data["statements"][key]["sections"]["holdings"]["subtotal"] = "1195590.56"
        tampered = tmp_path / "expected.json"
        tampered.write_text(json.dumps(data), encoding="utf-8")

        assert verify.main(["--layout", str(LAYOUT), "--expected", str(tampered)]) == 1
        out = capsys.readouterr().out
        assert "FAIL" in out and "holdings" in out

    def test_contagem_torcida_falha(self, tmp_path):
        data = json.loads(EXPECTED.read_text(encoding="utf-8"))
        key = "tests/fixtures/synthetic_statement.pdf"
        data["statements"][key]["sections"]["activity"]["row_count"] = 5
        tampered = tmp_path / "expected.json"
        tampered.write_text(json.dumps(data), encoding="utf-8")
        assert verify.main(["--layout", str(LAYOUT), "--expected", str(tampered)]) == 1

    def test_sem_layout_falha_de_forma_legivel(self, tmp_path, capsys):
        assert verify.main(["--layout", str(tmp_path / "nada.json")]) == 2
        out = capsys.readouterr().out
        assert "FAIL" in out and "mapper" in out

    def test_sem_gabarito_falha_de_forma_legivel(self, tmp_path, capsys):
        assert verify.main(["--expected", str(tmp_path / "nada.json")]) == 2
        out = capsys.readouterr().out
        assert "FAIL" in out and "Fase 0" in out


class TestEntrypoint:
    def test_escreve_csv_e_summary(self, tmp_path):
        import parse as entrypoint

        out = tmp_path / "out"
        code = entrypoint.main(
            [str(FIXTURE), "--layout", str(LAYOUT), "--expected", str(EXPECTED), "--out", str(out)]
        )
        assert code == 0
        for name in ("holdings", "activity", "income", "fees"):
            assert (out / f"{name}.csv").exists()

        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        assert summary["reconciled"] is True
        assert summary["sections"]["holdings"]["sum"] == "1195590.55"
        assert all(check["passed"] for check in summary["checks"])

    def test_reconciliacao_falhada_nao_escreve_csv(self, tmp_path):
        import parse as entrypoint

        data = json.loads(EXPECTED.read_text(encoding="utf-8"))
        key = "tests/fixtures/synthetic_statement.pdf"
        data["statements"][key]["sections"]["fees"]["subtotal"] = "9999.99"
        tampered = tmp_path / "expected.json"
        tampered.write_text(json.dumps(data), encoding="utf-8")

        out = tmp_path / "out"
        code = entrypoint.main(
            [str(FIXTURE), "--layout", str(LAYOUT), "--expected", str(tampered), "--out", str(out)]
        )
        assert code == 1
        assert not out.exists()

    def test_com_force_marca_a_saida_como_parcial(self, tmp_path):
        import parse as entrypoint

        data = json.loads(EXPECTED.read_text(encoding="utf-8"))
        key = "tests/fixtures/synthetic_statement.pdf"
        data["statements"][key]["sections"]["fees"]["subtotal"] = "9999.99"
        tampered = tmp_path / "expected.json"
        tampered.write_text(json.dumps(data), encoding="utf-8")

        out = tmp_path / "out"
        code = entrypoint.main(
            [str(FIXTURE), "--layout", str(LAYOUT), "--expected", str(tampered),
             "--out", str(out), "--force"]
        )
        assert code == 1
        assert (out / "fees.PARTIAL.csv").exists()
        assert not (out / "fees.csv").exists()


class TestMapperReproduzOLayout:
    def test_mapper_encontra_as_mesmas_seccoes(self, layout):
        from src.mapper import build_layout

        regenerated = build_layout(FIXTURE)
        assert regenerated["sections"].keys() == layout["sections"].keys()
        for name, spec in layout["sections"].items():
            assert regenerated["sections"][name]["pages"] == spec["pages"]
            assert [c["name"] for c in regenerated["sections"][name]["columns"]] == [
                c["name"] for c in spec["columns"]
            ]
