"""router.py — o layout é um contrato; um contrato inválido tem de rebentar cedo."""

from __future__ import annotations

import json

import pytest

from src.router import (
    LayoutError,
    account_for_page,
    account_ids,
    MissingSectionError,
    load_layout,
    missing_sections,
    pages_for_section,
    section_for_page,
    section_names,
)

BASE = {
    "version": 2,
    "sections": {
        "holdings": {
            "pages": [2, 3],
            "columns": [
                {"name": "description", "x0": 37, "x1": 287, "type": "text"},
                {"name": "market_value", "x0": 438, "x1": 523, "type": "amount"},
            ],
        },
        "fees": {"present": False},
    },
}


def write(tmp_path, layout) -> str:
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(layout), encoding="utf-8")
    return str(path)


def test_carrega_layout_valido(tmp_path):
    layout = load_layout(write(tmp_path, BASE))
    assert section_names(layout) == ["holdings"]
    assert pages_for_section(layout, "holdings") == [2, 3]
    assert section_for_page(layout, 3) == "holdings"
    assert section_for_page(layout, 1) is None


def test_seccao_ausente_e_explicita(tmp_path):
    layout = load_layout(write(tmp_path, BASE))
    assert missing_sections(layout) == ["fees"]
    with pytest.raises(MissingSectionError):
        pages_for_section(layout, "fees")
    with pytest.raises(MissingSectionError):
        pages_for_section(layout, "income")


def test_ficheiro_inexistente_diz_o_que_fazer(tmp_path):
    with pytest.raises(LayoutError, match="mapper"):
        load_layout(tmp_path / "nao_existe.json")


def test_versao_errada(tmp_path):
    with pytest.raises(LayoutError, match="version"):
        load_layout(write(tmp_path, {**BASE, "version": 99}))


def test_seccao_presente_sem_paginas(tmp_path):
    layout = json.loads(json.dumps(BASE))
    layout["sections"]["holdings"]["pages"] = []
    with pytest.raises(LayoutError, match="present=false"):
        load_layout(write(tmp_path, layout))


def test_colunas_sobrepostas(tmp_path):
    layout = json.loads(json.dumps(BASE))
    layout["sections"]["holdings"]["columns"][1]["x0"] = 100
    with pytest.raises(LayoutError, match="sobrepostas"):
        load_layout(write(tmp_path, layout))


def test_contas_nao_podem_partilhar_pagina(tmp_path):
    """Duas contas na mesma página = registos atribuídos ao acaso."""
    layout = json.loads(json.dumps(BASE))
    layout["accounts"] = [
        {"id": "316-000001-042", "pages": [2, 3]},
        {"id": "316-000002-042", "pages": [3, 4]},
    ]
    with pytest.raises(LayoutError, match="duas contas"):
        load_layout(write(tmp_path, layout))


def test_conta_por_pagina(tmp_path):
    layout = json.loads(json.dumps(BASE))
    layout["accounts"] = [
        {"id": "316-000001-042", "label": "Active Assets", "pages": [2]},
        {"id": "316-000002-042", "label": "Select UMA", "pages": [3]},
    ]
    loaded = load_layout(write(tmp_path, layout))
    assert account_ids(loaded) == ["316-000001-042", "316-000002-042"]
    assert account_for_page(loaded, 2) == "316-000001-042"
    assert account_for_page(loaded, 3) == "316-000002-042"
    assert account_for_page(loaded, 9) is None


def test_conta_duplicada(tmp_path):
    layout = json.loads(json.dumps(BASE))
    layout["accounts"] = [
        {"id": "316-000001-042", "pages": [2]},
        {"id": "316-000001-042", "pages": [3]},
    ]
    with pytest.raises(LayoutError, match="duplicada"):
        load_layout(write(tmp_path, layout))


def test_coluna_invertida(tmp_path):
    layout = json.loads(json.dumps(BASE))
    layout["sections"]["holdings"]["columns"][1]["x1"] = 400
    with pytest.raises(LayoutError, match="x1 <= x0"):
        load_layout(write(tmp_path, layout))
