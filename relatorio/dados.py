"""Contrato de dados do relatório — o que o relatório precisa de receber.

**Esta é a metade por preencher.** `design.py` e `relatorio.py` já estão
completos: desenham o que este módulo lhes der. O que falta é uma coisa só —
`carregar()`, que tem de devolver um `Relatorio` a partir da fonte real
(o `out/statement.xlsx` do parser, uma API de custódia, uma base de dados).

Enquanto `carregar()` não existir, `exemplo()` devolve uma carteira sintética
com a mesma forma, para que o livro se possa gerar e ver desde o primeiro
minuto. Trocar uma pela outra não obriga a tocar em mais nada.

Três regras que o resto do código assume e que `validar()` verifica:

* **nada é inventado** — um campo que a fonte não dá fica `None`, nunca `0`;
* dinheiro é `Decimal`, nunca `float` (o `float` estraga cêntimos ao somar);
* a classe de ativo vem de `CLASSES` — classe fora da lista cai em "Outros" e
  o relatório diz que caiu, em vez de a esconder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Sequence

__all__ = [
    "CLASSES", "Posicao", "Conta", "SerieMensal", "Movimento", "Atribuicao",
    "Relatorio", "carregar", "exemplo", "validar",
]

# Ordem canónica das classes. É esta ordem que `design.SLOT_CLASSE` pinta —
# acrescentar uma classe implica dar-lhe um slot de cor lá, não deixar o
# gráfico escolher.
CLASSES: tuple[str, ...] = (
    "Ações", "Rendimento fixo", "Fundos", "Alternativos",
    "Imobiliário", "Liquidez", "Estruturados", "Outros",
)

D = Decimal


@dataclass(frozen=True)
class Posicao:
    """Uma linha de carteira, na moeda de reporte já convertida."""
    conta: str
    classe: str
    titulo: str
    isin: str | None
    moeda: str
    quantidade: Decimal | None
    preco: Decimal | None
    custo: Decimal | None            # None = a fonte não imprime custo
    valor: Decimal                   # valor de mercado na moeda de reporte
    rendimento_anual: Decimal | None  # yield, em %

    @property
    def mais_valia(self) -> Decimal | None:
        if self.custo is None:
            return None
        return self.valor - self.custo


@dataclass(frozen=True)
class Conta:
    """Uma conta/custodiante dentro da família."""
    id: str
    titular: str
    custodiante: str
    moeda: str


@dataclass(frozen=True)
class SerieMensal:
    """Património de fecho de mês. Ordenada, sem buracos."""
    mes: date
    patrimonio: Decimal


@dataclass(frozen=True)
class Movimento:
    data: date
    conta: str
    tipo: str        # entrada, levantamento, compra, venda, dividendo, juro, taxa…
    descricao: str
    valor: Decimal   # com sinal: saídas negativas


@dataclass(frozen=True)
class Atribuicao:
    """Uma rubrica da variação do período, para a cascata."""
    rubrica: str
    valor: Decimal
    e_total: bool = False  # True para abertura e fecho


@dataclass
class Relatorio:
    """Tudo o que o livro precisa. Um objeto, um período, uma moeda de reporte."""
    familia: str
    moeda: str
    inicio: date
    fim: date
    contas: list[Conta] = field(default_factory=list)
    posicoes: list[Posicao] = field(default_factory=list)
    serie: list[SerieMensal] = field(default_factory=list)
    movimentos: list[Movimento] = field(default_factory=list)
    atribuicao: list[Atribuicao] = field(default_factory=list)
    referencia: str = ""   # benchmark ou mandato, para a nota de rodapé
    notas: list[str] = field(default_factory=list)

    # ── vistas derivadas (usadas pelos gráficos; não precisam de ser alteradas)
    @property
    def patrimonio(self) -> Decimal:
        return sum((p.valor for p in self.posicoes), D("0"))

    def por_classe(self) -> list[tuple[str, Decimal]]:
        """Valor por classe, na ordem canónica — a ordem fixa a cor."""
        somas: dict[str, Decimal] = {}
        for p in self.posicoes:
            classe = p.classe if p.classe in CLASSES else "Outros"
            somas[classe] = somas.get(classe, D("0")) + p.valor
        return [(c, somas[c]) for c in CLASSES if c in somas]

    def por_conta_e_classe(self) -> tuple[list[str], list[tuple[str, list[Decimal]]]]:
        """Matriz conta × classe para a barra empilhada."""
        contas = [c.id for c in self.contas]
        series = []
        for classe in CLASSES:
            linha = [
                sum((p.valor for p in self.posicoes
                     if p.conta == conta and (p.classe if p.classe in CLASSES else "Outros") == classe),
                    D("0"))
                for conta in contas
            ]
            if any(linha):
                series.append((classe, linha))
        return contas, series

    def maiores_posicoes(self, n: int = 10) -> list[Posicao]:
        return sorted(self.posicoes, key=lambda p: p.valor, reverse=True)[:n]

    def peso(self, valor: Decimal) -> Decimal:
        total = self.patrimonio or D("1")
        return (valor / total) * 100


# ── O que falta ligar ─────────────────────────────────────────────────────────
def carregar(origem: Path) -> Relatorio:
    """Constrói o `Relatorio` a partir da fonte real.

    TODO — é aqui que entra o trabalho de dados, e só aqui:

    1. ler a origem (por exemplo o `out/statement.xlsx` produzido pelo parser,
       folhas POSICOES e MOVIMENTOS, ou várias, uma por custodiante);
    2. converter cada linha para `Posicao` / `Movimento`, com `Decimal` —
       `Decimal(str(valor))`, nunca `Decimal(float)`;
    3. converter moedas para `Relatorio.moeda` à taxa da data de posição,
       e registar a taxa usada em `notas`;
    4. mapear a classe do custodiante para uma de `CLASSES`;
    5. construir `serie` (fecho de cada mês) e `atribuicao` (abertura,
       entradas, saídas, rendimento, efeito de mercado, fecho) — a cascata
       tem de fechar: abertura + rubricas == fecho;
    6. correr `validar()` e abortar se devolver alguma contradição.

    Campo que a fonte não imprime fica `None`. Não estimar.
    """
    raise NotImplementedError(
        "carregar() ainda não está ligado à fonte real — usa exemplo() ou implementa este passo"
    )


def validar(rel: Relatorio) -> list[str]:
    """Contradições entre o que o relatório diz e o que os dados sustentam.

    Devolve a lista de problemas (vazia = consistente). O construtor do livro
    escreve-a na folha de notas: um relatório com contradições sai marcado,
    nunca sai calado.
    """
    problemas: list[str] = []
    if not rel.posicoes:
        problemas.append("Sem posições: o relatório não tem carteira para consolidar.")
    contas = {c.id for c in rel.contas}
    orfas = {p.conta for p in rel.posicoes} - contas
    if orfas:
        problemas.append(f"Posições em contas não declaradas: {', '.join(sorted(orfas))}.")
    fora = {p.classe for p in rel.posicoes if p.classe not in CLASSES}
    if fora:
        problemas.append(f"Classes fora da lista canónica (caem em 'Outros'): {', '.join(sorted(fora))}.")
    if rel.serie != sorted(rel.serie, key=lambda s: s.mes):
        problemas.append("Série mensal fora de ordem cronológica.")
    if rel.serie and rel.serie[-1].patrimonio != rel.patrimonio:
        problemas.append(
            f"Último ponto da série ({rel.serie[-1].patrimonio}) não bate com a soma "
            f"das posições ({rel.patrimonio})."
        )
    if rel.atribuicao:
        totais = [a for a in rel.atribuicao if a.e_total]
        if len(totais) == 2:
            abertura, fecho = totais[0].valor, totais[-1].valor
            variacoes = sum((a.valor for a in rel.atribuicao if not a.e_total), D("0"))
            if abertura + variacoes != fecho:
                problemas.append(
                    f"Cascata não fecha: {abertura} + {variacoes} ≠ {fecho}."
                )
    return problemas


# ── Carteira sintética, só para ver o desenho a funcionar ─────────────────────
def exemplo() -> Relatorio:
    """Dados inventados, com a forma certa. Trocar por `carregar()` quando houver fonte."""
    contas = [
        Conta("PT-HOLD", "Holding familiar", "Banco A", "EUR"),
        Conta("CH-PRIV", "Trust dos filhos", "Banco B", "CHF"),
        Conta("US-BRK", "Conta de corretagem", "Corretora C", "USD"),
    ]
    cru: Sequence[tuple[str, str, str, str, str, str]] = [
        # conta, classe, título, quantidade, preço, custo
        ("PT-HOLD", "Ações", "Índice global desenvolvido", "12000", "112.40", "980000"),
        ("PT-HOLD", "Rendimento fixo", "Dívida soberana 2031", "9000", "98.10", "905000"),
        ("PT-HOLD", "Liquidez", "Depósito à ordem", "1", "420000", "420000"),
        ("CH-PRIV", "Fundos", "Fundo multiativos conservador", "5200", "144.80", "700000"),
        ("CH-PRIV", "Alternativos", "Fundo de private equity", "1", "610000", "500000"),
        ("CH-PRIV", "Imobiliário", "Imobiliário europeu cotado", "7400", "48.20", "390000"),
        ("US-BRK", "Ações", "Tecnologia EUA", "3100", "286.50", "640000"),
        ("US-BRK", "Ações", "Saúde EUA", "2400", "131.70", "300000"),
        ("US-BRK", "Estruturados", "Nota com capital protegido", "1", "250000", "250000"),
        ("US-BRK", "Liquidez", "Fundo monetário", "1", "180000", "180000"),
    ]
    posicoes = [
        Posicao(conta=c, classe=cl, titulo=t, isin=None, moeda="EUR",
                quantidade=D(q), preco=D(p), custo=D(k), valor=D(q) * D(p),
                rendimento_anual=None)
        for c, cl, t, q, p, k in cru
    ]
    patrimonio = sum((p.valor for p in posicoes), D("0"))

    # A série termina exatamente no somatório das posições: o último ponto do
    # gráfico e o total da folha de posições são o mesmo número, sempre.
    curva = ["0.905", "0.912", "0.907", "0.923", "0.934", "0.929",
             "0.943", "0.955", "0.950", "0.966", "0.981", "1.000"]
    serie = [SerieMensal(date(2026, m, 1), (patrimonio * D(r)).quantize(D("0.01")))
             for m, r in enumerate(curva, start=1)]

    movimentos = [
        Movimento(date(2026, 2, 12), "PT-HOLD", "entrada", "Reforço da holding", D("250000")),
        Movimento(date(2026, 3, 30), "US-BRK", "dividendo", "Dividendos do trimestre", D("18400")),
        Movimento(date(2026, 5, 4), "CH-PRIV", "levantamento", "Distribuição aos beneficiários", D("-120000")),
        Movimento(date(2026, 6, 18), "PT-HOLD", "juro", "Cupão da dívida soberana", D("22500")),
        Movimento(date(2026, 9, 2), "US-BRK", "compra", "Reforço em saúde EUA", D("-300000")),
        Movimento(date(2026, 11, 21), "CH-PRIV", "taxa", "Comissão de gestão", D("-14300")),
    ]

    abertura = serie[0].patrimonio
    entradas = D("250000")
    saidas = D("-120000")
    rendimento = D("40900")
    taxas = D("-14300")
    mercado = patrimonio - (abertura + entradas + saidas + rendimento + taxas)
    atribuicao = [
        Atribuicao("Abertura", abertura, e_total=True),
        Atribuicao("Entradas", entradas),
        Atribuicao("Saídas", saidas),
        Atribuicao("Rendimento", rendimento),
        Atribuicao("Taxas", taxas),
        Atribuicao("Mercado", mercado),
        Atribuicao("Fecho", patrimonio, e_total=True),
    ]

    return Relatorio(
        familia="Família Exemplo",
        moeda="EUR",
        inicio=date(2026, 1, 1),
        fim=date(2026, 12, 31),
        contas=contas,
        posicoes=posicoes,
        serie=serie,
        movimentos=movimentos,
        atribuicao=atribuicao,
        referencia="mandato equilibrado",
        notas=[
            "Dados sintéticos: esta carteira é inventada para ver o desenho a funcionar.",
            "Substituir por dados.carregar() quando a fonte real estiver ligada.",
        ],
    )
