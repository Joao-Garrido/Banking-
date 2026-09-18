"""Montagem do Relatório BFO — secções, paginação dinâmica e índice.

    python relatorio/relatorio.py --exemplo
    python relatorio/relatorio.py /caminho/para/fonte --saida out/relatorio_bfo.pdf

Ordem das páginas, seguindo o guia:

    Índice · Performance consolidada (§10) · Evolução patrimonial (§7) ·
    Rentabilidade (§8) · Detalhamento dos ativos (§12) · Movimentações (§13) ·
    Vencimentos (§14) · Ativos em acompanhamento (§15) · Notas

Este módulo é só montagem: não escolhe cores, tamanhos nem espaçamentos — pede
tudo a `design.py`. Acrescentar uma secção é escrever aqui uma função
`secao_*` que devolve uma lista de flowables, e registá-la em `construir()`.

Paginação dinâmica (§2 e §6), que é o que separa este relatório de um PDF de
páginas fixas:

* nenhuma secção tem número de páginas fixado — as tabelas correm e o
  `LongTable` reparte-as sozinho, repetindo o cabeçalho;
* uma secção sem dados **não é criada**, e não entra no índice (§14 e §15);
* o índice é gerado na segunda passagem, quando as páginas já existem (§6);
* o total do rodapé (`Página X de Y`) só se escreve depois disso (§5).
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from reportlab.platypus import Paragraph, Spacer
from reportlab.platypus.tableofcontents import TableOfContents

try:  # corre como script (python relatorio/relatorio.py) ou como módulo
    from . import dados, design
except ImportError:  # pragma: no cover
    import dados
    import design

__all__ = ["construir"]

D = Decimal


def _larguras(*fracoes: float) -> list[float]:
    """Converte proporções de coluna em pontos, somando sempre a área útil."""
    total = sum(fracoes)
    return [design.LARGURA_UTIL * f / total for f in fracoes]


def _celula(texto: str) -> Paragraph:
    """Texto de tabela que pode precisar de quebrar linha (§11: nome longo
    quebra linha, nunca desaparece)."""
    return Paragraph(texto, design.ESTILOS["corpo"])


# ── Índice (§6) ───────────────────────────────────────────────────────────────
def secao_indice(rel: dados.Relatorio) -> list:
    indice = TableOfContents()
    indice.levelStyles = [design.ESTILOS["toc1"], design.ESTILOS["toc2"]]
    return [
        design.titulo_capa(f"{rel.familia} — relatório de consolidação"),
        design.nota(f"Competência {rel.competencia}. Valores em "
                    f"{rel.moeda_local} salvo indicação em contrário."),
        Spacer(1, 8),
        design.rotulo_capa("Índice"),
        indice,
    ]


# ── §10 Performance consolidada ───────────────────────────────────────────────
def secao_performance(rel: dados.Relatorio, arte: Path) -> list:
    """Página de performance, em blocos equilibrados (§10).

    A composição segue §2.9 e §10: nada de bloco minúsculo cercado de vazio.
    O resumo financeiro é uma tabela estreita, por isso vai lado a lado com a
    atribuição, em vez de esticar sozinho pela mancha toda.
    """
    if not rel.indicadores and not rel.resumo:
        return []
    ind, res = rel.indicadores, rel.resumo
    meia = design.LARGURA_UTIL / 2 - 6
    historia: list = [*design.abre_secao("Performance consolidada", 0.9)]

    if ind:  # bloco 1 — indicadores superiores
        historia += [
            design.subtitulo("Indicadores"),
            design.tabela_indicadores([
                ("Rentabilidade do mês", design.percentual(ind.mes)),
                ("No ano", design.percentual(ind.ano)),
                ("12 meses", design.percentual(ind.doze_meses)),
                ("Desde o início", design.percentual(ind.desde_inicio)),
            ]),
        ]

    # blocos 2 e 4 lado a lado — resumo financeiro e atribuição de performance
    par: list[list] = []
    if res:
        moeda = rel.moeda_local
        par.append([
            design.subtitulo("Resumo financeiro"),
            design.tabela(
                ["Rubrica", f"Valor ({moeda})"],
                [["Saldo anterior", design.moeda(res.saldo_anterior, moeda)],
                 ["Entradas", design.moeda(res.entradas, moeda)],
                 ["Saídas", design.moeda(res.saidas, moeda)],
                 ["Juros e amortizações", design.moeda(res.juros_amortizacoes, moeda)],
                 ["Resultado não realizado", design.moeda(res.resultado_nao_realizado, moeda)],
                 ["Impostos", design.moeda(res.impostos, moeda)]],
                larguras=[meia * 0.52, meia * 0.48],
                alinhamentos=["left", "right"],
                total=["Saldo atual", design.moeda(res.saldo_atual, moeda)],
            ),
        ])

    contribuicoes = rel.por_macroclasse()
    if contribuicoes:
        par.append([
            design.subtitulo("Atribuição de performance"),
            design.grafico_atribuicao(
                arte / "atribuicao.png",
                [m for m, _, _ in contribuicoes],
                [float(v) for _, _, v in contribuicoes],
                unidade=rel.moeda_local, largura=meia, altura=130,
            ),
            design.nota("TODO: ligar à contribuição de performance do período. "
                        "Hoje o gráfico mostra o saldo por macroclasse, não a "
                        "contribuição — a fonte ainda não a dá."),
        ])
    if par:
        historia.append(design.grade_de_blocos(par))

    # bloco 3 — alocação de ativos: uma rosca por segmento, tabela logo abaixo (§9)
    blocos = []
    for segmento in (dados.LOCAL, dados.INTERNACIONAL):
        linhas = rel.por_macroclasse(segmento)
        if not linhas:
            continue
        total = sum(v for _, _, v in linhas)
        moeda = rel.moeda_local if segmento == dados.LOCAL else rel.moeda_internacional
        desenho = design.rosca(
            arte / f"rosca_{segmento.lower()}.png",
            [m for m, _, _ in linhas], [float(v) for _, _, v in linhas],
            segmento=segmento, indices=[i for _, i, _ in linhas],
            centro=design.abreviado(total, moeda), centro_rotulo="Património",
            largura=meia, altura=185,
        )
        bloco = [design.subtitulo(segmento)]
        if desenho is not None:   # §9: total zero não desenha setores falsos
            bloco.append(desenho)
        bloco.append(design.tabela(
            ["Macroclasse", f"Saldo ({moeda})", "Alocação"],
            [[m, design.moeda(v, moeda), f"{design._pt_br(v / total * 100, 2)}%"]
             for m, _, v in linhas],
            larguras=[meia * f for f in (0.42, 0.36, 0.22)],
            alinhamentos=["left", "right", "right"],
            total=["Total", design.moeda(total, moeda), "100,00%"],
        ))
        blocos.append(bloco)
    if blocos:
        historia += [design.subtitulo("Alocação de ativos"),
                     design.grade_de_blocos(blocos)]

    return historia


# ── §7 Evolução patrimonial ───────────────────────────────────────────────────
def secao_evolucao(rel: dados.Relatorio, arte: Path) -> list:
    if not rel.evolucao:
        return []
    moeda = rel.moeda_local
    grafico = design.grafico_evolucao_patrimonial(
        arte / "evolucao.png",
        [e.competencia for e in rel.evolucao],
        [float(e.entradas_saidas) for e in rel.evolucao],
        [float(e.resultado_nao_realizado) for e in rel.evolucao],
        [float(e.patrimonio) for e in rel.evolucao],
        unidade=moeda,
    )
    tabela_mensal = design.tabela(
        ["Competência", f"Entradas − Saídas ({moeda})",
         f"Resultado não realizado ({moeda})", f"Património ({moeda})"],
        [[e.competencia, design.moeda(e.entradas_saidas, moeda),
          design.moeda(e.resultado_nao_realizado, moeda),
          design.moeda(e.patrimonio, moeda)] for e in rel.evolucao],
        larguras=_larguras(1, 1.3, 1.5, 1.4),
        alinhamentos=["left", "right", "right", "right"],
    )
    return [
        *design.abre_secao("Evolução patrimonial", 0.5),
        grafico,
        Spacer(1, 4),
        tabela_mensal,           # §7: tabela mensal imediatamente abaixo do gráfico
    ]


# ── §8 Rentabilidade ──────────────────────────────────────────────────────────
def secao_rentabilidade(rel: dados.Relatorio, arte: Path) -> list:
    if not rel.rentabilidade:
        return []
    competencias = rel.competencias or [f"M{i+1}" for i in range(len(rel.rentabilidade[0].mensal))]
    historia = [*design.abre_secao("Rentabilidade", 0.45), design.subtitulo("Matriz mensal")]

    historia.append(design.tabela(
        ["Série", *competencias, "No ano", "12 meses", "Desde o início"],
        [[linha.nome, *[design.percentual(v) for v in linha.mensal],
          design.percentual(linha.ano), design.percentual(linha.doze_meses),
          design.percentual(linha.desde_inicio)]
         for linha in rel.rentabilidade],
        larguras=_larguras(2.4, *([1] * len(competencias)), 1.1, 1.1, 1.3),
        alinhamentos=["left"] + ["right"] * (len(competencias) + 3),
    ))

    historia += [
        design.subtitulo("Rentabilidade acumulada"),
        design.grafico_rentabilidade_acumulada(
            arte / "acumulada.png", competencias,
            [(linha.nome, [float(v) for v in linha.acumulada()], linha.e_benchmark)
             for linha in rel.rentabilidade],
        ),
        design.nota("Acumulação composta: (1 + r1) x (1 + r2) x ... - 1. "
                    "Benchmarks em linha fina tracejada."),
    ]
    return historia


# ── §12 Detalhamento dos ativos ───────────────────────────────────────────────
def secao_detalhamento(rel: dados.Relatorio) -> list:
    if not rel.ativos:
        return []
    historia = [*design.abre_secao("Detalhamento dos ativos", 0.25)]
    larguras = _larguras(3.4, 1.0, 1.0, 1.5, 1.5, 0.8, 0.8, 0.8, 1.0, 0.9)
    cabecalho = ["Ativo", "Vencimento", "Início", "Saldo bruto", "Saldo líquido",
                 "Mês", "Ano", "12 m", "Desde início", "Alocação"]

    for segmento in (dados.LOCAL, dados.INTERNACIONAL):
        ativos = rel.ativos_do_segmento(segmento)
        if not ativos:
            continue
        moeda = rel.moeda_local if segmento == dados.LOCAL else rel.moeda_internacional
        linhas: list[list] = []
        recuos: dict[int, int] = {}
        negritos: list[int] = []

        # Hierarquia macroclasse → classe → ativo, com subtotal por classe (§12).
        for macro in dados.MACROCLASSES:
            da_macro = [a for a in ativos if a.macroclasse == macro]
            if not da_macro:
                continue
            negritos.append(len(linhas))
            linhas.append([macro, "", "",
                           design.moeda(sum(a.saldo_bruto for a in da_macro), moeda),
                           design.moeda(sum(a.saldo_liquido for a in da_macro), moeda),
                           "", "", "", "",
                           f"{design._pt_br(sum(a.alocacao for a in da_macro), 2)}%"])
            for classe in dict.fromkeys(a.classe for a in da_macro):
                da_classe = [a for a in da_macro if a.classe == classe]
                recuos[len(linhas)] = 1
                linhas.append([classe, "", "",
                               design.moeda(sum(a.saldo_bruto for a in da_classe), moeda),
                               design.moeda(sum(a.saldo_liquido for a in da_classe), moeda),
                               "", "", "", "",
                               f"{design._pt_br(sum(a.alocacao for a in da_classe), 2)}%"])
                for a in da_classe:
                    recuos[len(linhas)] = 2
                    linhas.append([
                        _celula(a.ativo),
                        a.vencimento.strftime("%d/%m/%Y") if a.vencimento else "",
                        a.inicio.strftime("%d/%m/%Y") if a.inicio else "",
                        design.moeda(a.saldo_bruto, moeda),
                        design.moeda(a.saldo_liquido, moeda),
                        design.percentual(a.mes), design.percentual(a.ano),
                        design.percentual(a.doze_meses), design.percentual(a.desde_inicio),
                        f"{design._pt_br(a.alocacao, 2)}%",
                    ])

        historia += [
            design.subtitulo(f"{segmento} — valores em {moeda}"),
            design.tabela(
                cabecalho, linhas, larguras=larguras,
                alinhamentos=["left", "center", "center", "right", "right",
                              "right", "right", "right", "right", "right"],
                recuos=recuos, negritos=negritos,
                total=["Total do segmento", "", "",
                       design.moeda(sum(a.saldo_bruto for a in ativos), moeda),
                       design.moeda(sum(a.saldo_liquido for a in ativos), moeda),
                       "", "", "", "", "100,00%"],
            ),
        ]
    historia.append(design.nota(
        "Célula vazia = a fonte não imprime o campo. Nada aqui é estimado."))
    return historia


# ── §13 Movimentações ─────────────────────────────────────────────────────────
def secao_movimentacoes(rel: dados.Relatorio) -> list:
    if not rel.movimentos:
        return []
    historia = [*design.abre_secao("Movimentações", 0.25)]
    larguras = _larguras(1.0, 1.3, 1.6, 3.2, 1.5, 1.0, 0.9, 1.5, 0.7)

    for segmento in (dados.LOCAL, dados.INTERNACIONAL):
        movimentos = sorted(
            (m for m in rel.movimentos if rel.segmento_de(m.instituicao) == segmento),
            key=lambda m: m.data,
        )
        if not movimentos:
            continue
        moeda = rel.moeda_local if segmento == dados.LOCAL else rel.moeda_internacional
        historia += [
            design.subtitulo(segmento),
            design.tabela(
                ["Data", "Tipo", "Instituição", "Ativo", "Valor bruto",
                 "IR", "IOF", "Valor líquido", "Moeda"],
                [[m.data.strftime("%d/%m/%Y"), m.tipo, _celula(m.instituicao),
                  _celula(m.ativo), design.moeda(m.valor_bruto, ""),
                  design.moeda(m.ir, ""), design.moeda(m.iof, ""),
                  design.moeda(m.valor_liquido, ""), m.moeda] for m in movimentos],
                larguras=larguras,
                alinhamentos=["center", "left", "left", "left", "right",
                              "right", "right", "right", "center"],
                total=["Total", "", "", "",
                       design.moeda(sum(m.valor_bruto for m in movimentos), ""),
                       design.moeda(sum((m.ir or D("0")) for m in movimentos), ""),
                       design.moeda(sum((m.iof or D("0")) for m in movimentos), ""),
                       design.moeda(sum(m.valor_liquido for m in movimentos), ""),
                       moeda],
            ),
        ]
    return historia


# ── §14 Vencimentos ───────────────────────────────────────────────────────────
def secao_vencimentos(rel: dados.Relatorio, arte: Path) -> list:
    """Criada só quando existirem registos — §14 manda omitir secção e índice."""
    if not rel.vencimentos:
        return []
    proximos = rel.vencimentos[:3]          # §14: tabela dos próximos três meses
    doze = rel.vencimentos[:12]             # §14: gráfico dos próximos 12 meses
    por_competencia: dict[str, float] = {}
    for v in doze:
        por_competencia[v.competencia] = por_competencia.get(v.competencia, 0.0) + float(v.valor)

    return [
        *design.abre_secao("Vencimentos", 0.4),
        design.subtitulo("Próximos três meses"),
        design.tabela(
            ["Competência", "Instituição", "Ativo", "Valor", "Moeda"],
            [[v.competencia, _celula(v.instituicao), _celula(v.ativo),
              design.moeda(v.valor, ""), v.moeda] for v in proximos],
            larguras=_larguras(1.2, 2.0, 3.4, 1.4, 0.8),
            alinhamentos=["center", "left", "left", "right", "center"],
        ),
        design.subtitulo("Fluxo dos próximos 12 meses"),
        design.grafico_vencimentos(
            arte / "vencimentos.png", list(por_competencia), list(por_competencia.values()),
            unidade=rel.moeda_local,
        ),
    ]


# ── §15 Ativos em acompanhamento ──────────────────────────────────────────────
def secao_acompanhamento(rel: dados.Relatorio) -> list:
    """Criada só quando houver registos relevantes (§15).

    TODO — os campos desta secção não estavam legíveis na fonte a partir da qual
    este esqueleto foi escrito. A secção fica ligada e omitida por falta de
    dados; quando os campos forem conhecidos, montar aqui a tabela como as
    outras, com `design.tabela`.
    """
    if not rel.acompanhamento:
        return []
    colunas = list(rel.acompanhamento[0])
    return [
        *design.abre_secao("Ativos em acompanhamento", 0.35),
        design.tabela(
            colunas,
            [[_celula(str(registo.get(c, ""))) for c in colunas]
             for registo in rel.acompanhamento],
            larguras=_larguras(*([1] * len(colunas))),
        ),
    ]


# ── Notas ─────────────────────────────────────────────────────────────────────
def secao_notas(rel: dados.Relatorio, problemas: list[str]) -> list:
    historia = [*design.abre_secao("Notas e pressupostos", 0.12),
                design.subtitulo("Consistência")]
    if problemas:
        for p in problemas:
            historia.append(design.nota(f"Contradição — {p}"))
    else:
        historia.append(design.nota(
            "Sem contradições: o resumo financeiro fecha, a evolução termina no "
            "saldo atual e a alocação de cada segmento soma 100%."))

    historia.append(design.subtitulo("Fonte e método"))
    for n in rel.notas:
        historia.append(design.nota(n))
    historia.append(design.nota(
        "Instituições consolidadas: " +
        "; ".join(f"{i.nome} ({i.segmento}, {i.moeda})" for i in rel.instituicoes) + "."))
    if rel.taxa_cambio is not None:
        historia.append(design.nota(
            f"Taxa de câmbio usada na consolidação: "
            f"{rel.moeda_internacional} 1,00 = {design.moeda(rel.taxa_cambio, rel.moeda_local)}."))
    historia.append(design.nota(
        "Campo vazio significa que a fonte não o imprime — não significa zero."))
    return historia


# ── Montagem ──────────────────────────────────────────────────────────────────
def construir(rel: dados.Relatorio, destino: Path) -> Path:
    """Escreve o PDF e os PNG dos gráficos ao lado. Devolve o caminho do PDF."""
    destino = Path(destino)
    arte = destino.parent / "graficos"
    problemas = dados.validar(rel)

    nota_cambial = ""
    if rel.tem_duas_moedas() and rel.taxa_cambio is not None:
        nota_cambial = (f"Consolidação a {rel.moeda_internacional} 1,00 = "
                        f"{design.moeda(rel.taxa_cambio, rel.moeda_local)}")

    documento = design.DocumentoBFO(
        destino, titulo=f"{rel.familia} — consolidação",
        competencia=f"Competência {rel.competencia}",
        nota_cambial=nota_cambial,
    )
    ctx = documento.contexto

    historia: list = []
    historia += secao_indice(rel)
    # As secções consolidadas misturam as duas moedas: a nota cambial acompanha-as
    # por todas as páginas que ocuparem (§5).
    historia += [design.MarcaCambial(ctx, True)]
    historia += secao_performance(rel, arte)
    historia += secao_evolucao(rel, arte)
    historia += secao_rentabilidade(rel, arte)
    historia += secao_detalhamento(rel)
    historia += secao_movimentacoes(rel)
    historia += [design.MarcaCambial(ctx, False)]
    historia += secao_vencimentos(rel, arte)
    historia += secao_acompanhamento(rel)
    historia += secao_notas(rel, problemas)

    documento.construir(historia)
    return destino


def main() -> None:
    p = argparse.ArgumentParser(description="Relatório BFO de consolidação")
    p.add_argument("origem", nargs="?", type=Path, help="fonte de dados (ver dados.carregar)")
    p.add_argument("--exemplo", action="store_true",
                   help="usa a carteira sintética em vez de uma fonte real")
    p.add_argument("--saida", type=Path, default=Path("out/relatorio_bfo.pdf"))
    args = p.parse_args()

    if args.exemplo or args.origem is None:
        rel = dados.exemplo()
    else:
        try:
            rel = dados.carregar(args.origem)
        except NotImplementedError as erro:
            raise SystemExit(f"{erro}\nPara ver o desenho a funcionar entretanto: "
                             f"python relatorio/relatorio.py --exemplo")

    caminho = construir(rel, args.saida)
    problemas = dados.validar(rel)
    print(f"Escrito {caminho}")
    print(f"Gráficos em {caminho.parent / 'graficos'}")
    if problemas:
        print(f"{len(problemas)} contradição(ões) — ver a página Notas")
    else:
        print("Sem contradições entre as vistas e os dados de origem")


if __name__ == "__main__":
    main()
