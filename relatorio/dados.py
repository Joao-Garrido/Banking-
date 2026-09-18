"""Contrato de dados do Relatório BFO — o que o relatório precisa de receber.

**Esta é a metade por preencher.** `design.py` e `relatorio.py` estão completos:
desenham e paginam o que este módulo lhes der. Falta uma coisa só — `carregar()`,
que tem de devolver um `Relatorio` a partir da fonte real.

Os campos abaixo são os que o guia nomeia, secção a secção:

    §7  Evolução           competência, entradas − saídas, resultado não
                           realizado, património
    §8  Rentabilidade      matriz mensal: carteira consolidada, macroclasses e
                           benchmarks, com mês, ano, 12 meses e desde o início
    §10 Performance        indicadores superiores e resumo financeiro
    §12 Detalhamento       macroclasse, classe, ativo, vencimento, data de
                           início, saldo bruto, saldo líquido, mês, ano,
                           12 meses, desde o início, instituição, alocação
    §13 Movimentações      data, tipo, instituição, ativo, valor bruto, IR,
                           IOF, valor líquido, moeda
    §14 Vencimentos        competência, instituição, ativo, valor, moeda

Três regras que o resto do código assume e que `validar()` verifica:

* **nada é inventado** (§2.6) — campo que a fonte não dá fica `None`, nunca `0`,
  e um indicador indisponível não é preenchido com valor artificial (§2.7);
* dinheiro é `Decimal`, nunca `float` — `float` estraga cêntimos ao somar;
* o segmento é `Local` ou `Internacional`, porque é ele que escolhe a cor
  (§3.3: Local fica no vermelho, Internacional no azul-marinho).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

__all__ = [
    "LOCAL", "INTERNACIONAL", "MACROCLASSES", "Instituicao", "Ativo",
    "Movimento", "EvolucaoMes", "LinhaRentabilidade", "ResumoFinanceiro",
    "Indicadores", "Vencimento", "Relatorio", "carregar", "exemplo", "validar",
]

LOCAL, INTERNACIONAL = "Local", "Internacional"

# Ordem canónica das macroclasses. É a posição nesta lista que escolhe o tom na
# paleta auxiliar (§3.2) — por isso acrescentar uma macroclasse é acrescentá-la
# aqui, e não deixar o gráfico escolher cor à sorte.
MACROCLASSES: tuple[str, ...] = (
    "Renda fixa", "Renda variável", "Multimercado", "Alternativos",
    "Caixa", "Outros",
)

D = Decimal


@dataclass(frozen=True)
class Instituicao:
    """Um custodiante. §6 pagina o relatório em blocos de instituição."""
    nome: str
    segmento: str              # LOCAL ou INTERNACIONAL
    moeda: str                 # "R$" ou "USD"


@dataclass(frozen=True)
class Ativo:
    """Uma linha do detalhamento (§12)."""
    instituicao: str
    macroclasse: str
    classe: str
    ativo: str
    moeda: str
    saldo_bruto: Decimal
    saldo_liquido: Decimal
    alocacao: Decimal                  # % sobre o património do segmento
    vencimento: date | None = None     # None = sem vencimento (ação, fundo…)
    inicio: date | None = None
    mes: Decimal | None = None         # rentabilidades em %, None se a fonte
    ano: Decimal | None = None         # não as imprime — nunca zero
    doze_meses: Decimal | None = None
    desde_inicio: Decimal | None = None


@dataclass(frozen=True)
class Movimento:
    """Uma linha de movimentações (§13)."""
    data: date
    tipo: str
    instituicao: str
    ativo: str
    valor_bruto: Decimal
    valor_liquido: Decimal
    moeda: str
    ir: Decimal | None = None
    iof: Decimal | None = None


@dataclass(frozen=True)
class EvolucaoMes:
    """Um ponto da evolução patrimonial (§7). `competencia` em MM/AAAA."""
    competencia: str
    entradas_saidas: Decimal
    resultado_nao_realizado: Decimal
    patrimonio: Decimal


@dataclass(frozen=True)
class LinhaRentabilidade:
    """Uma linha da matriz mensal (§8): carteira, macroclasse ou benchmark."""
    nome: str
    mensal: tuple[Decimal | None, ...]   # uma entrada por competência do período
    ano: Decimal | None = None
    doze_meses: Decimal | None = None
    desde_inicio: Decimal | None = None
    e_benchmark: bool = False

    def acumulada(self) -> list[Decimal]:
        """Acumulação composta (§8): ∏(1 + r) − 1, em %.

        Mês sem rentabilidade conta como zero na composição **e fica assinalado
        em `validar()`** — não se inventa o que falta, mas também não se parte a
        série.
        """
        fator, saida = D("1"), []
        for r in self.mensal:
            fator *= (1 + (r or D("0")) / 100)
            saida.append((fator - 1) * 100)
        return saida


@dataclass(frozen=True)
class ResumoFinanceiro:
    """§10 — o quadro que explica a variação do saldo no período."""
    saldo_anterior: Decimal
    entradas: Decimal
    saidas: Decimal                    # negativo
    juros_amortizacoes: Decimal
    resultado_nao_realizado: Decimal
    impostos: Decimal                  # negativo
    saldo_atual: Decimal


@dataclass(frozen=True)
class Indicadores:
    """§10 — indicadores superiores, em %."""
    mes: Decimal | None
    ano: Decimal | None
    doze_meses: Decimal | None
    desde_inicio: Decimal | None


@dataclass(frozen=True)
class Vencimento:
    """§14 — um vencimento futuro. `competencia` em MM/AAAA."""
    competencia: str
    instituicao: str
    ativo: str
    valor: Decimal
    moeda: str


@dataclass
class Relatorio:
    """Tudo o que o relatório precisa. Um objeto, uma competência."""
    familia: str
    competencia: str                   # MM/AAAA
    moeda_local: str = "R$"
    moeda_internacional: str = "USD"
    taxa_cambio: Decimal | None = None  # usada na nota cambial do rodapé (§5)
    instituicoes: list[Instituicao] = field(default_factory=list)
    ativos: list[Ativo] = field(default_factory=list)
    movimentos: list[Movimento] = field(default_factory=list)
    evolucao: list[EvolucaoMes] = field(default_factory=list)
    rentabilidade: list[LinhaRentabilidade] = field(default_factory=list)
    resumo: ResumoFinanceiro | None = None
    indicadores: Indicadores | None = None
    vencimentos: list[Vencimento] = field(default_factory=list)
    acompanhamento: list[dict] = field(default_factory=list)  # §15 — ver nota
    notas: list[str] = field(default_factory=list)

    # ── vistas derivadas (consumidas pelos gráficos e tabelas) ────────────────
    @property
    def competencias(self) -> list[str]:
        return [e.competencia for e in self.evolucao]

    @property
    def patrimonio(self) -> Decimal:
        return sum((a.saldo_liquido for a in self.ativos), D("0"))

    def segmento_de(self, instituicao: str) -> str:
        for i in self.instituicoes:
            if i.nome == instituicao:
                return i.segmento
        return LOCAL

    def moeda_de(self, instituicao: str) -> str:
        for i in self.instituicoes:
            if i.nome == instituicao:
                return i.moeda
        return self.moeda_local

    def ativos_do_segmento(self, segmento: str) -> list[Ativo]:
        return [a for a in self.ativos if self.segmento_de(a.instituicao) == segmento]

    def por_macroclasse(self, segmento: str | None = None) -> list[tuple[str, int, Decimal]]:
        """(macroclasse, índice canónico, saldo). O índice é que fixa a cor."""
        alvo = self.ativos if segmento is None else self.ativos_do_segmento(segmento)
        somas: dict[str, Decimal] = {}
        for a in alvo:
            chave = a.macroclasse if a.macroclasse in MACROCLASSES else "Outros"
            somas[chave] = somas.get(chave, D("0")) + a.saldo_liquido
        return [(m, i, somas[m]) for i, m in enumerate(MACROCLASSES) if m in somas]

    def tem_duas_moedas(self) -> bool:
        """Se o relatório consolida BRL e USD — decide a nota cambial (§5)."""
        return len({i.moeda for i in self.instituicoes}) > 1


# ── O que falta ligar ─────────────────────────────────────────────────────────
def carregar(origem: Path) -> Relatorio:
    """Constrói o `Relatorio` a partir da fonte real.

    TODO — é aqui que entra o trabalho de dados, e só aqui:

    1. ler a origem (posições e movimentos por custodiante);
    2. converter cada linha para `Ativo` / `Movimento`, com `Decimal` —
       `Decimal(str(valor))`, nunca `Decimal(float)`;
    3. classificar cada instituição em `LOCAL` ou `INTERNACIONAL` e mapear a
       classe do custodiante para uma de `MACROCLASSES`;
    4. montar `evolucao` (uma entrada por competência), `rentabilidade`
       (carteira consolidada, macroclasses e benchmarks), `resumo` e
       `indicadores`;
    5. montar `vencimentos` só se existirem — §14 manda omitir a secção **e a
       entrada do índice** quando não houver;
    6. correr `validar()` e tratar o que vier: um relatório com contradições
       sai marcado, nunca sai calado.

    Campo que a fonte não imprime fica `None`. Não estimar, não preencher com
    zero (§2.6 e §2.7).
    """
    raise NotImplementedError(
        "carregar() ainda não está ligado à fonte real — usa exemplo() ou implementa este passo"
    )


def validar(rel: Relatorio) -> list[str]:
    """Contradições entre o que o relatório diz e o que os dados sustentam.

    Lista vazia = consistente. O que sair daqui vai para a folha de notas do
    PDF: um número que não fecha aparece assinalado, não desaparece.
    """
    problemas: list[str] = []
    if not rel.ativos:
        problemas.append("Sem ativos: não há carteira para consolidar.")

    conhecidas = {i.nome for i in rel.instituicoes}
    orfas = {a.instituicao for a in rel.ativos} - conhecidas
    if orfas:
        problemas.append(f"Ativos em instituições não declaradas: {', '.join(sorted(orfas))}.")

    fora = {a.macroclasse for a in rel.ativos if a.macroclasse not in MACROCLASSES}
    if fora:
        problemas.append(f"Macroclasses fora da lista canónica (caem em 'Outros'): "
                         f"{', '.join(sorted(fora))}.")

    if rel.resumo:
        r = rel.resumo
        fecho = (r.saldo_anterior + r.entradas + r.saidas + r.juros_amortizacoes
                 + r.resultado_nao_realizado + r.impostos)
        if fecho != r.saldo_atual:
            problemas.append(
                f"Resumo financeiro não fecha: {fecho} calculado contra "
                f"{r.saldo_atual} declarado (diferença de {fecho - r.saldo_atual})."
            )

    if rel.evolucao and rel.resumo and rel.evolucao[-1].patrimonio != rel.resumo.saldo_atual:
        problemas.append(
            f"Último ponto da evolução ({rel.evolucao[-1].patrimonio}) não bate com o "
            f"saldo atual do resumo ({rel.resumo.saldo_atual})."
        )

    n = len(rel.evolucao)
    for linha in rel.rentabilidade:
        if n and len(linha.mensal) != n:
            problemas.append(
                f"Série '{linha.nome}' tem {len(linha.mensal)} meses para "
                f"{n} competências do período."
            )
        if any(v is None for v in linha.mensal):
            faltam = sum(1 for v in linha.mensal if v is None)
            problemas.append(
                f"Série '{linha.nome}': {faltam} mês(es) sem rentabilidade — "
                f"contam como zero na acumulação composta."
            )

    for segmento in (LOCAL, INTERNACIONAL):
        ativos = rel.ativos_do_segmento(segmento)
        if not ativos:
            continue
        soma = sum((a.alocacao for a in ativos), D("0"))
        if abs(soma - 100) > D("0.05"):
            problemas.append(f"Alocação de {segmento} soma {soma}%, não 100%.")

    if rel.tem_duas_moedas() and rel.taxa_cambio is None:
        problemas.append("Relatório consolida BRL e USD sem taxa de câmbio para a nota "
                         "cambial do rodapé (§5).")
    return problemas


# ── Carteira sintética, só para ver o desenho a funcionar ─────────────────────
def exemplo() -> Relatorio:
    """Dados inventados com a forma certa. Trocar por `carregar()` quando houver fonte."""
    instituicoes = [
        Instituicao("Banco Alfa", LOCAL, "R$"),
        Instituicao("Corretora Beta", LOCAL, "R$"),
        Instituicao("Offshore Gama", INTERNACIONAL, "USD"),
    ]

    cru = [
        # instituição, macroclasse, classe, ativo, bruto, líquido, venc., início
        ("Banco Alfa", "Renda fixa", "CDB pós-fixado", "CDB Alfa 110% CDI",
         "18400000", "17920000", date(2028, 6, 15), date(2023, 6, 15)),
        ("Banco Alfa", "Renda fixa", "Tesouro IPCA+", "NTN-B 2035",
         "12250000", "11930000", date(2035, 5, 15), date(2022, 3, 2)),
        ("Banco Alfa", "Caixa", "Conta corrente", "Disponibilidades",
         "2310000", "2310000", None, None),
        ("Corretora Beta", "Renda variável", "Ações Brasil", "Carteira de ações local",
         "24600000", "23870000", None, date(2021, 9, 1)),
        ("Corretora Beta", "Multimercado", "Macro", "Fundo Macro Beta FIC FIM",
         "16800000", "16310000", None, date(2022, 1, 10)),
        ("Corretora Beta", "Alternativos", "Private equity", "Fundo PE Beta II",
         "9400000", "9400000", date(2031, 12, 31), date(2020, 11, 20)),
        ("Offshore Gama", "Renda fixa", "Global bonds", "Global Bond Fund",
         "6200000", "6050000", None, date(2021, 4, 12)),
        ("Offshore Gama", "Renda variável", "Equities globais", "Global Equity Fund",
         "9800000", "9540000", None, date(2021, 4, 12)),
        ("Offshore Gama", "Caixa", "Money market", "USD Money Market",
         "2900000", "2900000", None, None),
    ]
    ativos: list[Ativo] = []
    for inst, macro, classe, nome, bruto, liquido, venc, inicio in cru:
        moeda = next(i.moeda for i in instituicoes if i.nome == inst)
        ativos.append(Ativo(
            instituicao=inst, macroclasse=macro, classe=classe, ativo=nome,
            moeda=moeda, saldo_bruto=D(bruto), saldo_liquido=D(liquido),
            alocacao=D("0"), vencimento=venc, inicio=inicio,
            mes=None, ano=None, doze_meses=None, desde_inicio=None,
        ))

    # Alocação: % dentro do próprio segmento, com o resto a cair na última linha
    # para fechar exatamente 100,00 (§11 pede duas casas; arredondar cada linha
    # à parte deixaria a coluna a somar 99,99).
    completos: list[Ativo] = []
    for segmento in (LOCAL, INTERNACIONAL):
        do_segmento = [a for a in ativos if
                       next(i.segmento for i in instituicoes if i.nome == a.instituicao) == segmento]
        total = sum((a.saldo_liquido for a in do_segmento), D("0"))
        acumulado = D("0")
        for i, a in enumerate(do_segmento):
            if i < len(do_segmento) - 1:
                peso = (a.saldo_liquido / total * 100).quantize(D("0.01"))
                acumulado += peso
            else:
                peso = D("100.00") - acumulado
            completos.append(Ativo(**{**a.__dict__, "alocacao": peso}))
    ativos = completos

    patrimonio = sum((a.saldo_liquido for a in ativos), D("0"))

    competencias = ["03/2026", "04/2026", "05/2026", "06/2026", "07/2026", "08/2026"]
    fluxos = ["1850000", "-620000", "0", "2400000", "-310000", "0"]
    nao_realizado = ["980000", "-1240000", "1610000", "720000", "-430000", "1180000"]
    evolucao, saldo = [], D("94970000")
    for competencia, fluxo, rnr in zip(competencias, fluxos, nao_realizado):
        saldo += D(fluxo) + D(rnr)
        evolucao.append(EvolucaoMes(competencia, D(fluxo), D(rnr), saldo))
    # O último ponto é o património: a evolução e o detalhamento contam o mesmo.
    evolucao[-1] = EvolucaoMes(competencias[-1], evolucao[-1].entradas_saidas,
                               evolucao[-1].resultado_nao_realizado, patrimonio)

    rentabilidade = [
        LinhaRentabilidade("Carteira consolidada",
                           tuple(D(v) for v in ("1.08", "-0.42", "1.71", "0.94", "-0.31", "1.22")),
                           ano=D("4.28"), doze_meses=D("9.640"), desde_inicio=D("38.12")),
        LinhaRentabilidade("Renda fixa",
                           tuple(D(v) for v in ("0.92", "0.88", "0.95", "0.90", "0.93", "0.89")),
                           ano=D("5.61"), doze_meses=D("11.20"), desde_inicio=D("34.80")),
        LinhaRentabilidade("Renda variável",
                           tuple(D(v) for v in ("2.40", "-3.10", "4.05", "1.60", "-2.20", "2.85")),
                           ano=D("5.35"), doze_meses=D("8.10"), desde_inicio=D("41.90")),
        LinhaRentabilidade("CDI",
                           tuple(D(v) for v in ("0.87", "0.84", "0.90", "0.86", "0.88", "0.85")),
                           ano=D("5.29"), doze_meses=D("10.75"), desde_inicio=D("33.40"),
                           e_benchmark=True),
        LinhaRentabilidade("IBOV",
                           tuple(D(v) for v in ("2.10", "-3.60", "3.80", "1.20", "-2.60", "2.40")),
                           ano=D("3.12"), doze_meses=D("6.40"), desde_inicio=D("29.70"),
                           e_benchmark=True),
    ]

    movimentos = [
        Movimento(date(2026, 8, 4), "Aplicação", "Banco Alfa", "CDB Alfa 110% CDI",
                  D("-3000000"), D("-3000000"), "R$"),
        Movimento(date(2026, 8, 7), "Resgate", "Corretora Beta", "Fundo Macro Beta FIC FIM",
                  D("1500000"), D("1418000"), "R$", ir=D("-82000"), iof=D("0")),
        Movimento(date(2026, 8, 12), "Dividendo", "Corretora Beta", "Carteira de ações local",
                  D("196000"), D("196000"), "R$"),
        Movimento(date(2026, 8, 19), "Cupão", "Banco Alfa", "NTN-B 2035",
                  D("410000"), D("343400"), "R$", ir=D("-66600"), iof=D("0")),
        Movimento(date(2026, 8, 21), "Aplicação", "Offshore Gama", "Global Equity Fund",
                  D("-800000"), D("-800000"), "USD"),
        Movimento(date(2026, 8, 28), "Taxa", "Offshore Gama", "Conta de custódia",
                  D("-24000"), D("-24000"), "USD"),
    ]

    resumo = ResumoFinanceiro(
        saldo_anterior=D("98230000"), entradas=D("3000000"), saidas=D("-1500000"),
        juros_amortizacoes=D("606000"), resultado_nao_realizado=D("1180000"),
        impostos=D("-148600"), saldo_atual=patrimonio,
    )
    # A carteira sintética existe para exercitar o desenho: o resumo fecha por
    # construção, com o resultado não realizado a absorver a diferença.
    resumo = ResumoFinanceiro(
        **{**resumo.__dict__,
           "resultado_nao_realizado": patrimonio - (
               resumo.saldo_anterior + resumo.entradas + resumo.saidas
               + resumo.juros_amortizacoes + resumo.impostos)},
    )

    vencimentos = [
        Vencimento("09/2026", "Banco Alfa", "CDB Alfa 108% CDI", D("2400000"), "R$"),
        Vencimento("10/2026", "Banco Alfa", "LCA Alfa 2026", D("1750000"), "R$"),
        Vencimento("11/2026", "Corretora Beta", "Debênture Beta 2026", D("980000"), "R$"),
        Vencimento("01/2027", "Banco Alfa", "CDB Alfa 112% CDI", D("3200000"), "R$"),
        Vencimento("03/2027", "Offshore Gama", "Treasury 2027", D("1400000"), "USD"),
    ]

    return Relatorio(
        familia="Família Exemplo", competencia="08/2026",
        taxa_cambio=D("5.42"), instituicoes=instituicoes, ativos=ativos,
        movimentos=movimentos, evolucao=evolucao, rentabilidade=rentabilidade,
        resumo=resumo,
        indicadores=Indicadores(mes=D("1.22"), ano=D("4.28"),
                                doze_meses=D("9.64"), desde_inicio=D("38.12")),
        vencimentos=vencimentos,
        acompanhamento=[],  # §15: sem registos → a secção não é criada
        notas=[
            "Dados sintéticos: esta carteira é inventada para ver o desenho a funcionar.",
            "Substituir por dados.carregar() quando a fonte real estiver ligada.",
        ],
    )
