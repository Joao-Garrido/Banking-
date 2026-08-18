"""Relatório de diagnóstico: o que a varredura viu, sem dizer quanto.

Existe para quem não pode partilhar o documento. O relatório traz a *estrutura*
— que tabelas foram encontradas, em que páginas, com que colunas, o que
conciliou e o que não — e nenhum valor, nome de titular ou nome de título. Os
dígitos são mascarados com `#`, por isso até um número de conta que escape sai
ilegível.

É com este ficheiro que se descobre porque é que faltam posições: quase sempre
há uma página sem tabela nenhuma, e é lá que está a secção cujo título não foi
reconhecido.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Sequence

from .excel import table_state
from .reconcile import Check

__all__ = ["build_report"]

_DIGITS = re.compile(r"\d")

# O que fazer com cada sintoma. É a parte do relatório que interessa a quem o lê.
RECEITAS = {
    "pagina_sem_tabela": (
        "páginas sem tabela nenhuma: se alguma delas tem posições, o título da secção "
        "não foi reconhecido. Um título tem de estar EM MAIÚSCULAS, na margem esquerda "
        "e a negrito ou maior que o corpo. Vê `_HEADING` em src/tables.py"
    ),
    "sem_titulo": (
        "tabelas 'SEM TÍTULO': o título existe mas não foi lido como título — mesma "
        "regra do ponto acima"
    ),
    "sem_total": (
        "tabelas sem total impresso reconhecido: se o documento imprime lá um total, "
        "acrescenta o rótulo a `TOTAL_PATTERNS` em src/tables.py"
    ),
    "colunas": (
        "tabelas sem nenhuma coluna de valores: as faixas x não foram detetadas. "
        "Costuma ser tabela com dois quadros lado a lado na mesma página, ou uma "
        "tabela cujos números não têm pontuação (ver `_is_tabular_number` em "
        "src/tables.py)"
    ),
    "falhas": (
        "conferências FAIL: a soma extraída não bate com um total impresso que devia "
        "bater. A folha 'Reconciliação' do Excel diz a página; compara essa página do "
        "PDF com a folha em bruto correspondente"
    ),
}


def _mask(text: str | None) -> str:
    """Texto sem dígitos — o suficiente para reconhecer a tabela, não o cliente."""
    return _DIGITS.sub("#", str(text or ""))


def build_report(
    pdf: str | Path,
    layout: dict,
    tables: Sequence,
    checks: Sequence[Check],
    *,
    anonimo: bool = True,
) -> str:
    """Relatório de estrutura. anonimo=False mantém os títulos tal como estão."""
    def texto(value) -> str:
        return _mask(value) if anonimo else str(value or "")

    linhas: list[str] = []
    add = linhas.append

    add("DIAGNÓSTICO DA VARREDURA")
    add("=" * 78)
    add(f"ficheiro : {Path(pdf).name if not anonimo else _mask(Path(pdf).name)}")
    add(f"páginas  : {layout.get('page_count')}")
    add(f"período  : {layout.get('period_start')} a {layout.get('period_end')}")
    add(f"contas   : {len(layout.get('accounts', []))}")
    if anonimo:
        add("(anónimo: dígitos mascarados com #, sem valores nem nomes de títulos)")
    add("")

    # --- tabelas
    add(f"TABELAS ENCONTRADAS ({len(tables)})")
    add(f"{'págs':<9} {'linhas':>6} {'cols':>5}  {'estado':<16} {'coluna de valor':<28} título")
    add("-" * 78)
    paginas_com_tabela: set[int] = set()
    sem_titulo = sem_total = poucas_colunas = 0

    for result in sorted(tables, key=lambda r: r.spec.pages[0] if r.spec.pages else 0):
        spec = result.spec
        paginas_com_tabela.update(spec.pages)
        estado = table_state(spec, checks)
        paginas = f"{spec.pages[0]}-{spec.pages[-1]}" if spec.pages else "?"
        numericas = len(spec.columns) - 1
        add(
            f"{paginas:<9} {len(result.data_rows):>6} {numericas:>5}  {estado:<16} "
            f"{texto(spec.amount_column)[:28]:<28} {texto(spec.title)[:40]}"
        )
        if spec.title == "SEM TÍTULO":
            sem_titulo += 1
        if not result.total_rows:
            sem_total += 1
        if numericas == 0:
            poucas_colunas += 1
    add("")

    # --- páginas em branco para a varredura
    total_paginas = layout.get("page_count") or 0
    orfas = [p for p in range(1, total_paginas + 1) if p not in paginas_com_tabela]
    add(f"PÁGINAS SEM TABELA NENHUMA ({len(orfas)})")
    add(", ".join(str(p) for p in orfas) or "(nenhuma)")
    add("")

    # --- conferências
    falhas = [c for c in checks if c.fatal]
    avisos = [c for c in checks if not c.passed and not c.fatal]
    add(f"CONFERÊNCIAS: {len(checks) - len(falhas) - len(avisos)} PASS · "
        f"{len(falhas)} FAIL · {len(avisos)} AVISO")
    add("")

    if falhas:
        add(f"FALHAS ({len(falhas)}) — sem valores, só o que falhou e onde")
        add("-" * 78)
        for check in falhas:
            pagina = f"p.{check.page}" if check.page else "—"
            add(f"  {pagina:<7} {texto(check.section)[:36]:<36} {texto(check.name)[:32]}")
        add("")

    # --- o que mexer
    add("O QUE MEXER")
    add("-" * 78)
    sintomas = []
    if orfas:
        sintomas.append(("pagina_sem_tabela", f"{len(orfas)} página(s)"))
    if sem_titulo:
        sintomas.append(("sem_titulo", f"{sem_titulo} tabela(s)"))
    if sem_total:
        sintomas.append(("sem_total", f"{sem_total} tabela(s)"))
    if poucas_colunas:
        sintomas.append(("colunas", f"{poucas_colunas} tabela(s)"))
    if falhas:
        sintomas.append(("falhas", f"{len(falhas)} conferência(s)"))

    if not sintomas:
        add("  nada a assinalar: todas as páginas deram tabela e nada falhou.")
    for chave, quantos in sintomas:
        add(f"  · [{quantos}] {RECEITAS[chave]}")
    add("")

    # --- notas do mapeamento
    # As notas sobre as secções curadas (holdings/activity/…) pertencem ao outro
    # modo e só distraem quem está a diagnosticar a varredura.
    curadas = ("holdings:", "activity:", "income:", "fees:")
    revisao = [
        nota for nota in (layout.get("_review") or []) if not nota.startswith(curadas)
    ]
    if revisao:
        add("NOTAS DO MAPEAMENTO DO LAYOUT")
        add("-" * 78)
        for nota in revisao:
            add(f"  · {texto(nota)}")

    return "\n".join(linhas)
