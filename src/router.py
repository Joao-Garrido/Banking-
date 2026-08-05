"""página -> secção, a partir do layout.json.

Ausência de secção é tratada explicitamente: um período em que o statement não
tem, por exemplo, fees, não pode produzir KeyError nem uma secção vazia
silenciosa. Tem de aparecer no relatório como 'ausente' e ser decisão de quem
lê, não do parser.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

__all__ = [
    "LayoutError",
    "MissingSectionError",
    "load_layout",
    "section_names",
    "section_spec",
    "pages_for_section",
    "section_for_page",
    "missing_sections",
    "account_ids",
    "account_for_page",
]

log = logging.getLogger("ms-parser.router")

LAYOUT_VERSION = 2
REQUIRED_SECTION_KEYS = ("pages", "columns")


class LayoutError(ValueError):
    """layout.json inválido ou incompatível."""


class MissingSectionError(KeyError):
    """Secção pedida que não existe neste statement."""

    def __init__(self, name: str, available: list[str]):
        self.name = name
        self.available = available
        super().__init__(
            f"secção {name!r} não existe no layout. Disponíveis: {', '.join(available) or '(nenhuma)'}"
        )


def load_layout(path: str | Path = "layout.json") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise LayoutError(
            f"{p} não existe. Corre `python -m src.mapper <statement.pdf>` para o gerar "
            "(Fase 1) e revê-o à mão antes de continuar."
        )
    layout = json.loads(p.read_text(encoding="utf-8"))

    version = layout.get("version")
    if version != LAYOUT_VERSION:
        raise LayoutError(
            f"{p}: version={version!r}, esperado {LAYOUT_VERSION}. Re-corre o mapper."
        )
    if "sections" not in layout or not isinstance(layout["sections"], dict):
        raise LayoutError(f"{p}: falta o objeto 'sections'.")

    _validate_accounts(p, layout.get("accounts", []))

    for name, spec in layout["sections"].items():
        if not spec.get("present", True):
            continue
        for key in REQUIRED_SECTION_KEYS:
            if key not in spec:
                raise LayoutError(f"{p}: secção {name!r} sem '{key}'.")
        if not spec["pages"]:
            raise LayoutError(
                f"{p}: secção {name!r} marcada como presente mas sem páginas. "
                "Marca present=false se ela não existe neste período."
            )
        _validate_columns(p, name, spec["columns"])
    return layout


def _validate_accounts(path: Path, accounts: Any) -> None:
    """Um statement consolidado tem várias contas; cada página pertence a uma só.

    Se duas contas reclamassem a mesma página, os registos dessa página seriam
    atribuídos por acaso — e um total por conta errado não se vê a olho.
    """
    if not accounts:
        return
    if not isinstance(accounts, list):
        raise LayoutError(f"{path}: 'accounts' tem de ser uma lista.")

    owner: dict[int, str] = {}
    seen: set[str] = set()
    for account in accounts:
        for key in ("id", "pages"):
            if key not in account:
                raise LayoutError(f"{path}: conta sem '{key}': {account!r}")
        if account["id"] in seen:
            raise LayoutError(f"{path}: conta duplicada {account['id']!r}")
        seen.add(account["id"])
        for page in account["pages"]:
            if page in owner:
                raise LayoutError(
                    f"{path}: página {page} atribuída a duas contas "
                    f"({owner[page]!r} e {account['id']!r})"
                )
            owner[page] = account["id"]


def account_ids(layout: dict) -> list[str]:
    return [account["id"] for account in layout.get("accounts", [])]


def account_for_page(layout: dict, page: int) -> str | None:
    """Conta dona da página. None num statement de conta única."""
    for account in layout.get("accounts", []):
        if page in account["pages"]:
            return account["id"]
    return None


def _validate_columns(path: Path, section: str, columns: Any) -> None:
    if not isinstance(columns, list) or not columns:
        raise LayoutError(f"{path}: secção {section!r} sem colunas.")
    seen: set[str] = set()
    previous_x1: float | None = None
    for col in sorted(columns, key=lambda c: c.get("x0", 0)):
        for key in ("name", "x0", "x1"):
            if key not in col:
                raise LayoutError(f"{path}: coluna sem '{key}' em {section!r}: {col!r}")
        if col["name"] in seen:
            raise LayoutError(f"{path}: coluna duplicada {col['name']!r} em {section!r}")
        seen.add(col["name"])
        if col["x1"] <= col["x0"]:
            raise LayoutError(f"{path}: coluna {col['name']!r} de {section!r} com x1 <= x0")
        if previous_x1 is not None and col["x0"] < previous_x1 - 0.01:
            raise LayoutError(
                f"{path}: colunas sobrepostas em {section!r} junto a {col['name']!r} "
                f"(x0={col['x0']} < x1 anterior={previous_x1})"
            )
        previous_x1 = col["x1"]


def section_names(layout: dict, *, include_absent: bool = False) -> list[str]:
    return [
        name
        for name, spec in layout["sections"].items()
        if include_absent or spec.get("present", True)
    ]


def missing_sections(layout: dict) -> list[str]:
    absent = [
        name for name, spec in layout["sections"].items() if not spec.get("present", True)
    ]
    for name in absent:
        log.warning("secção %r declarada ausente neste statement — não será extraída", name)
    return absent


def section_spec(layout: dict, name: str) -> dict[str, Any]:
    sections = layout["sections"]
    if name not in sections:
        raise MissingSectionError(name, list(sections))
    spec = sections[name]
    if not spec.get("present", True):
        raise MissingSectionError(name, section_names(layout))
    return spec


def pages_for_section(layout: dict, name: str) -> list[int]:
    return list(section_spec(layout, name)["pages"])


def section_for_page(layout: dict, page: int) -> str | None:
    """Secção dona da página, ou None (capa, sumário, disclaimers)."""
    for name in section_names(layout):
        if page in layout["sections"][name]["pages"]:
            return name
    return None
