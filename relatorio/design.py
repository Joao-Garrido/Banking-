"""Camada visual do Relatório BFO — tokens, chassis de página, tabelas e gráficos.

Implementa o **Guia visual e de estilo do Relatório BFO**. Cada bloco aponta a
secção do guia que o manda fazer assim; as secções citadas são as do guia:

    §3  paleta          §4  tipografia      §5  cabeçalho, rodapé e margens
    §6  paginação       §7  evolução        §8  rentabilidade
    §9  roscas          §10 performance     §11 tabelas

Este módulo é a parte que costuma sair mal quando se pede "um relatório
bonito": cor, densidade, hierarquia e composição. Está completo e é o único
sítio onde se escolhem cores, tamanhos e espaçamentos — quem montar páginas
novas consome os tokens daqui em vez de inventar os seus.

Duas notas de fidelidade ao guia, deliberadas e assinaladas onde acontecem:

* §7 manda a evolução patrimonial ter **dois eixos y** (movimentações à
  esquerda, património à direita). É contra a prática corrente de visualização
  — dois eixos deixam comparar grandezas que não são comparáveis — mas é o que
  o guia exige, e é a convenção destes relatórios. Fica implementado como
  mandado, com o título da unidade em cada eixo, que é a mitigação possível.
* §11 manda "negativos entre parênteses ou com sinal, mas manter um padrão
  único". Escolhido: **dinheiro entre parênteses, percentagem com sinal**. Uma
  coluna nunca mistura os dois. Se a casa preferir o contrário, muda-se em
  `moeda()` e `percentual()` e nada mais.

Tipos de letra: o texto usa Helvetica (§4, embutida no PDF, métrica de Arial);
os gráficos usam a primeira de Arial/Liberation Sans/DejaVu Sans que exista na
máquina — Liberation tem a métrica da Arial, por isso o desenho não muda.
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Flowable, Frame, Image, LongTable,
    PageTemplate, Paragraph, Table, TableStyle,
)

__all__ = [
    "VERMELHO_LOCAL", "AZUL_INTERNACIONAL", "AZUL_TITULO", "CINZA_TEXTO",
    "cor_de_classe", "moeda", "percentual", "abreviado", "numero", "eixo_percent",
    "ESTILOS", "DocumentoBFO", "MarcaCambial", "titulo_pagina", "abre_secao",
    "titulo_capa", "rotulo_capa", "subtitulo",
    "nota", "tabela", "tabela_indicadores", "grade_de_blocos", "LARGURA_UTIL",
    "grafico_evolucao_patrimonial", "grafico_rentabilidade_acumulada",
    "rosca", "grafico_atribuicao", "grafico_vencimentos",
]

# ── §3.1 Cores principais ─────────────────────────────────────────────────────
VERMELHO_LOCAL = "#CC092F"        # Local, Onshore, BRL, barras principais
AZUL_INTERNACIONAL = "#191C38"    # Internacional, Offshore, USD, linha patrimonial
AZUL_TITULO = "#1A4056"           # títulos, subtítulos, cabeçalhos, divisórias
CINZA_TEXTO = "#464646"           # corpo, rótulos, eixos, notas
CINZA_MEDIO = "#B8B8B8"           # resultado não realizado, séries auxiliares
CINZA_GRADE = "#E0E0E0"           # grade e separadores discretos
CINZA_CABECALHO = "#F0F2F4"       # fundo dos cabeçalhos de tabela
CINZA_ALTERNADO = "#FAFAFA"       # zebra striping
BRANCO = "#FFFFFF"

# ── §3.2 Paleta auxiliar para classes ─────────────────────────────────────────
# Várias classes numa mesma rosca ou gráfico. A ordem é fixa: a mesma classe
# recebe sempre o mesmo tom (§3.3 — "a mesma classe deve manter a mesma cor").
CLASSES_LOCAL = ("#CC092F", "#A93D4B", "#8F6A6B", "#6A393A", "#757788", "#CFC9C1")
CLASSES_INTERNACIONAL = ("#191C38", "#474960", "#757788", "#9A9BA8", "#CFC9C1", "#DCDCDC")

LOCAL, INTERNACIONAL = "Local", "Internacional"


def cor_de_classe(segmento: str, indice: int) -> str:
    """Tom de uma classe dentro do seu segmento (§3.2).

    O índice é a posição **canónica** da classe na lista de classes do
    relatório — não a posição no gráfico. É isso que garante que filtrar ou
    reordenar não repinta nada. Passada a sexta classe, o guia não define tom:
    agrupa-se o resto em "Outros" com o último cinza, em vez de inventar cor.
    """
    escala = CLASSES_LOCAL if segmento == LOCAL else CLASSES_INTERNACIONAL
    return escala[min(indice, len(escala) - 1)]


# ── §4 Tipografia ─────────────────────────────────────────────────────────────
FONTE = "Helvetica"
FONTE_BOLD = "Helvetica-Bold"

# Tamanhos dentro das bandas do guia, no topo de cada banda: o guia pede
# densidade de relatório institucional "sem prejudicar a leitura".
PT_TITULO = 10      # §4: título da página, 8 a 10 pt
PT_SUBTITULO = 8    # §4: subtítulo de secção, 6 a 8 pt
PT_INDICADOR = 12   # §4: indicador principal, 9 a 12 pt
PT_TABELA = 6       # §4: corpo e cabeçalho de tabela, 4,5 a 6 pt
PT_NOTA = 5         # §4: notas e fontes, 4 a 5 pt
PT_RODAPE = 5       # §4: rodapé, 4 a 5 pt

_CANDIDATAS = ("Arial", "Liberation Sans", "DejaVu Sans")
_INSTALADAS = {f.name for f in font_manager.fontManager.ttflist}
FONTE_GRAFICO = next((f for f in _CANDIDATAS if f in _INSTALADAS), "DejaVu Sans")
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": [FONTE_GRAFICO],
    "axes.unicode_minus": False,
})

ESTILOS = {
    "titulo": ParagraphStyle(
        "titulo", fontName=FONTE, fontSize=PT_TITULO, leading=PT_TITULO * 1.3,
        textColor=colors.HexColor(AZUL_TITULO), spaceAfter=3, alignment=TA_LEFT),
    "subtitulo": ParagraphStyle(
        "subtitulo", fontName=FONTE_BOLD, fontSize=PT_SUBTITULO,
        leading=PT_SUBTITULO * 1.35, textColor=colors.HexColor(AZUL_TITULO),
        spaceBefore=6, spaceAfter=3, alignment=TA_LEFT),
    "indicador": ParagraphStyle(
        "indicador", fontName=FONTE_BOLD, fontSize=PT_INDICADOR,
        leading=PT_INDICADOR * 1.15, textColor=colors.HexColor(AZUL_TITULO),
        alignment=TA_LEFT),
    "rotulo": ParagraphStyle(
        "rotulo", fontName=FONTE, fontSize=PT_NOTA, leading=PT_NOTA * 1.4,
        textColor=colors.HexColor(CINZA_TEXTO), alignment=TA_LEFT),
    "corpo": ParagraphStyle(
        "corpo", fontName=FONTE, fontSize=PT_TABELA, leading=PT_TABELA * 1.45,
        textColor=colors.HexColor(CINZA_TEXTO), alignment=TA_LEFT),
    "nota": ParagraphStyle(
        "nota", fontName=FONTE, fontSize=PT_NOTA, leading=PT_NOTA * 1.5,
        textColor=colors.HexColor(CINZA_TEXTO), spaceBefore=4, alignment=TA_LEFT),
    # A capa tem estilo próprio para **não** entrar no índice: o índice lista as
    # secções, não a si mesmo.
    "capa": ParagraphStyle(
        "capa", fontName=FONTE, fontSize=PT_TITULO + 3, leading=(PT_TITULO + 3) * 1.3,
        textColor=colors.HexColor(AZUL_TITULO), spaceAfter=3, alignment=TA_LEFT),
    "capa_sub": ParagraphStyle(
        "capa_sub", fontName=FONTE_BOLD, fontSize=PT_SUBTITULO,
        leading=PT_SUBTITULO * 1.35, textColor=colors.HexColor(AZUL_TITULO),
        spaceBefore=6, spaceAfter=3, alignment=TA_LEFT),
    "toc1": ParagraphStyle(
        "toc1", fontName=FONTE, fontSize=PT_SUBTITULO, leading=PT_SUBTITULO * 1.8,
        textColor=colors.HexColor(AZUL_TITULO)),
    "toc2": ParagraphStyle(
        "toc2", fontName=FONTE, fontSize=PT_TABELA, leading=PT_TABELA * 2,
        leftIndent=12, textColor=colors.HexColor(CINZA_TEXTO)),
}

# ── §5 Margens e área útil ────────────────────────────────────────────────────
PAGINA = A4
MARGEM_LATERAL = 14 * mm
MARGEM_SUPERIOR = 16 * mm   # deixa o cabeçalho respirar
MARGEM_INFERIOR = 12 * mm
LARGURA_UTIL = PAGINA[0] - 2 * MARGEM_LATERAL
ALTURA_UTIL = PAGINA[1] - MARGEM_SUPERIOR - MARGEM_INFERIOR
# §5 pede conteúdo em 85% a 92% da área útil. Com estas margens, a mancha ocupa
# ~90% da largura da folha: dentro da banda, e igual em todas as páginas.
OCUPACAO = LARGURA_UTIL / PAGINA[0]
assert 0.85 <= OCUPACAO <= 0.92, f"mancha em {OCUPACAO:.0%} da folha, fora de §5"


# ── §11 Formatação numérica ───────────────────────────────────────────────────
def _pt_br(valor: Decimal | float, casas: int) -> str:
    """1234567.89 → '1.234.567,89' (§11: padrão brasileiro)."""
    texto = f"{abs(Decimal(str(valor))):,.{casas}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def moeda(valor: Decimal | float | None, unidade: str = "R$", casas: int = 2) -> str:
    """Valor monetário. Negativo **entre parênteses** — o padrão único (§11).

    `None` devolve vazio: o guia proíbe preencher indicador indisponível com
    valor artificial (§2.7).
    """
    if valor is None:
        return ""
    corpo = f"{unidade} {_pt_br(valor, casas)}".strip()
    return f"({corpo})" if Decimal(str(valor)) < 0 else corpo


def numero(valor: Decimal | float | None, casas: int = 2) -> str:
    if valor is None:
        return ""
    corpo = _pt_br(valor, casas)
    return f"({corpo})" if Decimal(str(valor)) < 0 else corpo


def percentual(valor: Decimal | float | None, casas: int = 2) -> str:
    """Percentagem com duas casas e **sinal explícito** (§11)."""
    if valor is None:
        return ""
    return f"{'-' if Decimal(str(valor)) < 0 else '+'}{_pt_br(valor, casas)}%"


def eixo_percent(valor: float, casas: int = 1) -> str:
    """Marca de eixo em percentagem, com o sinal à frente.

    `_pt_br` formata o módulo — num eixo que atravessa o zero, esquecer o sinal
    transforma −1,0% em 1,0% e inverte a leitura do gráfico.
    """
    return f"{'-' if valor < 0 else ''}{_pt_br(valor, casas)}%"


def abreviado(valor: Decimal | float, unidade: str = "R$") -> str:
    """Rótulo curto de gráfico: 'R$ 146,6 mi', 'USD 18,8 mi' (§7).

    O sinal vai à frente, não entre parênteses: num eixo que atravessa o zero,
    parênteses leem-se mal e o eixo já mostra a linha de zero. Os parênteses de
    §11 são para as tabelas, onde não há eixo a dar o contexto.
    """
    v = float(valor)
    sinal = "-" if v < 0 else ""
    for corte, sufixo in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if abs(v) >= corte:
            return f"{sinal}{unidade} {_pt_br(v / corte, 1)}{sufixo}".strip()
    return f"{sinal}{unidade} {_pt_br(v, 0)}".strip()


# ── §5 Cabeçalho, rodapé e paginação ──────────────────────────────────────────
class _CanvasNumerado(rl_canvas.Canvas):
    """Desenha cabeçalho e rodapé sabendo o total de páginas.

    O guia manda `Página X de Y` com o total "calculado somente após a
    paginação final" (§5): as páginas ficam guardadas e só se desenham no fim,
    quando `Y` já é conhecido.
    """

    def __init__(self, *args, contexto=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._paginas: list[dict] = []
        self._contexto = contexto or {}

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self._cabecalho()
            self._rodape(total)
            super().showPage()
        super().save()

    def _cabecalho(self) -> None:
        """Título à esquerda, marca à direita, fio fino por baixo, fundo branco."""
        ctx = self._contexto
        y = PAGINA[1] - MARGEM_SUPERIOR + 6 * mm
        self.setFont(FONTE, PT_TITULO)
        self.setFillColor(colors.HexColor(AZUL_TITULO))
        self.drawString(MARGEM_LATERAL, y, ctx.get("titulo", ""))
        self.setFont(FONTE_BOLD, PT_TITULO)
        self.drawRightString(PAGINA[0] - MARGEM_LATERAL, y, ctx.get("marca", "BFO"))
        self.setStrokeColor(colors.HexColor(CINZA_GRADE))
        self.setLineWidth(0.4)
        self.line(MARGEM_LATERAL, y - 2.5 * mm, PAGINA[0] - MARGEM_LATERAL, y - 2.5 * mm)

    def _rodape(self, total: int) -> None:
        """Competência à esquerda, `Página X de Y` à direita, nota cambial (§5)."""
        ctx = self._contexto
        y = MARGEM_INFERIOR - 5 * mm
        self.setFont(FONTE, PT_RODAPE)
        self.setFillColor(colors.HexColor(CINZA_TEXTO))
        if ctx.get("competencia"):
            self.drawString(MARGEM_LATERAL, y, ctx["competencia"])
        if self._pageNumber in ctx.get("cambiais", set()) and ctx.get("nota_cambial"):
            self.drawCentredString(PAGINA[0] / 2, y, ctx["nota_cambial"])
        self.drawRightString(PAGINA[0] - MARGEM_LATERAL, y,
                             f"Página {self._pageNumber} de {total}")


class MarcaCambial(Flowable):
    """Liga e desliga a nota cambial do rodapé (§5).

    Não desenha nada: comuta um interruptor. Enquanto estiver ligado, cada
    página por onde o conteúdo passar fica marcada como consolidando BRL e USD
    — é assim que uma secção que atravessa cinco páginas leva a nota nas cinco,
    e não só na primeira.

        historia += [MarcaCambial(ctx, True), *conteudo_consolidado,
                     MarcaCambial(ctx, False)]
    """

    width = height = 0

    def __init__(self, contexto: dict, ligar: bool = True):
        super().__init__()
        self._contexto = contexto
        self._ligar = ligar

    def draw(self) -> None:
        self._contexto["cambial_ativo"] = self._ligar
        if self._ligar:
            self._contexto.setdefault("cambiais", set()).add(self.canv.getPageNumber())


class DocumentoBFO(BaseDocTemplate):
    """Documento com o chassis do guia: margens, cabeçalho, rodapé e índice.

    O índice é construído em duas passagens (`multiBuild`), como §6 pede —
    "gerar depois que todas as páginas estiverem prontas".
    """

    def __init__(self, destino: Path, titulo: str, competencia: str = "",
                 nota_cambial: str = "", marca: str = "BFO"):
        super().__init__(
            str(destino), pagesize=PAGINA,
            leftMargin=MARGEM_LATERAL, rightMargin=MARGEM_LATERAL,
            topMargin=MARGEM_SUPERIOR, bottomMargin=MARGEM_INFERIOR,
            title=titulo, author="Relatório BFO",
        )
        self.contexto = {
            "titulo": titulo, "competencia": competencia, "marca": marca,
            "nota_cambial": nota_cambial, "secoes": {}, "cambiais": set(),
            "cambial_ativo": False,
        }
        moldura = Frame(MARGEM_LATERAL, MARGEM_INFERIOR, LARGURA_UTIL, ALTURA_UTIL,
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
                        id="corpo")
        self.addPageTemplates([PageTemplate(id="BFO", frames=[moldura])])

    def afterFlowable(self, flowable) -> None:
        """Alimenta o índice, o título do cabeçalho e a marcação cambial."""
        if self.contexto.get("cambial_ativo"):
            self.contexto["cambiais"].add(self.page)
        if not isinstance(flowable, Paragraph):
            return
        estilo = flowable.style.name
        if estilo in ("titulo", "subtitulo"):
            texto = flowable.getPlainText()
            nivel = 0 if estilo == "titulo" else 1
            self.notify("TOCEntry", (nivel, texto, self.page))

    def construir(self, historia: Sequence) -> None:
        # Cada passagem do multiBuild repagina: o que foi recolhido na anterior
        # já não vale, por isso limpa-se antes de voltar a recolher.
        self.contexto["secoes"].clear()
        self.contexto["cambiais"].clear()
        self.contexto["cambial_ativo"] = False

        def fabrica(*args, **kwargs):
            return _CanvasNumerado(*args, contexto=self.contexto, **kwargs)
        self.multiBuild(list(historia), canvasmaker=fabrica)


def titulo_pagina(texto: str) -> Paragraph:
    return Paragraph(texto, ESTILOS["titulo"])


def titulo_capa(texto: str) -> Paragraph:
    return Paragraph(texto, ESTILOS["capa"])


def rotulo_capa(texto: str) -> Paragraph:
    return Paragraph(texto, ESTILOS["capa_sub"])


def abre_secao(texto: str, minimo: float = 0.42) -> list:
    """Começa uma secção sem forçar página nova.

    Uma quebra fixa por secção deixaria a metade de baixo em branco, que §5 e
    §2.1 proíbem. Em vez disso o conteúdo corre, e o salto só acontece quando
    falta espaço para o título ter companhia — assim nunca fica um título
    órfão no fundo da página nem uma página quase vazia. `minimo` é a fração da
    altura útil que a secção precisa de ter à frente para começar aqui.
    """
    return [CondPageBreak(ALTURA_UTIL * minimo), titulo_pagina(texto)]


def subtitulo(texto: str) -> Paragraph:
    return Paragraph(texto, ESTILOS["subtitulo"])


def nota(texto: str) -> Paragraph:
    return Paragraph(texto, ESTILOS["nota"])


# ── §11 Tabelas ───────────────────────────────────────────────────────────────
def tabela(cabecalho: Sequence[str], linhas: Sequence[Sequence], *,
           larguras: Sequence[float] | None = None,
           alinhamentos: Sequence[str] = (), total: Sequence | None = None,
           recuos: dict[int, int] | None = None,
           negritos: Sequence[int] = ()) -> LongTable:
    """Tabela no estilo do guia: cabeçalho claro, zebra, fios finos, total a bold.

    `recuos` é `{índice da linha do corpo: nível}` e `negritos` uma lista de
    índices — servem à hierarquia macroclasse → classe → ativo de §12, onde a
    macroclasse vai a bold e cada nível desce um recuo. O recuo é padding real
    da célula: espaços no texto não servem, o parágrafo apara-os.

    Usa `LongTable` com `repeatRows=1`: quando a tabela transborda, o cabeçalho
    repete-se na página seguinte (§11 e §13).
    """
    dados = [list(cabecalho)] + [list(linha) for linha in linhas]
    if total is not None:
        dados.append(list(total))

    estilo = [
        ("FONTNAME", (0, 0), (-1, -1), FONTE),
        ("FONTSIZE", (0, 0), (-1, -1), PT_TABELA),
        ("LEADING", (0, 0), (-1, -1), PT_TABELA * 1.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor(CINZA_TEXTO)),
        ("TOPPADDING", (0, 0), (-1, -1), 1.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        # cabeçalho: fundo #F0F2F4, texto #1A4056, semibold
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(CINZA_CABECALHO)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(AZUL_TITULO)),
        ("FONTNAME", (0, 0), (-1, 0), FONTE_BOLD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # separadores finos, sem bordas grossas à volta das células
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor(CINZA_GRADE)),
    ]
    for i in range(1, len(linhas) + 1):  # zebra branco / #FAFAFA
        if i % 2 == 0:
            estilo.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor(CINZA_ALTERNADO)))
    for i, horiz in enumerate(alinhamentos):
        estilo.append(("ALIGN", (i, 0), (i, -1), horiz.upper()))
    for i, nivel in (recuos or {}).items():
        estilo.append(("LEFTPADDING", (0, i + 1), (0, i + 1), 3 + 7 * nivel))
    for i in negritos:
        estilo.append(("FONTNAME", (0, i + 1), (-1, i + 1), FONTE_BOLD))
    if total is not None:
        estilo += [
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor(CINZA_CABECALHO)),
            ("FONTNAME", (0, -1), (-1, -1), FONTE_BOLD),
            ("TEXTCOLOR", (0, -1), (-1, -1), colors.HexColor(AZUL_TITULO)),
            ("LINEABOVE", (0, -1), (-1, -1), 0.5, colors.HexColor(CINZA_GRADE)),
        ]

    t = LongTable(dados, colWidths=larguras, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle(estilo))
    return t


def tabela_indicadores(pares: Sequence[tuple[str, str]], *,
                       largura: float = LARGURA_UTIL) -> Table:
    """Fila de indicadores superiores (§10): rótulo pequeno, número grande.

    Sem caixas nem faixas — §5 proíbe caixas pretas e cabeçalhos coloridos em
    excesso. A hierarquia faz-se pelo tamanho e pela cor do número.
    """
    rotulos = [Paragraph(r.upper(), ESTILOS["rotulo"]) for r, _ in pares]
    valores = [Paragraph(v, ESTILOS["indicador"]) for _, v in pares]
    t = Table([rotulos, valores], colWidths=[largura / len(pares)] * len(pares), hAlign="LEFT")
    t.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, 0), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("LINEBELOW", (0, 1), (-1, 1), 0.4, colors.HexColor(CINZA_GRADE)),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
    ]))
    return t


def grade_de_blocos(blocos: Sequence[Sequence], *, largura: float = LARGURA_UTIL,
                    espaco: float = 6) -> Table | list:
    """Distribui blocos em 1, 2 ou 3 colunas, conforme quantos são (§6 e §9).

    Um bloco → maior e centralizado; dois → duas colunas; três → três colunas.
    Nunca mais de três na mesma linha; o quarto começa uma linha nova.
    """
    if not blocos:
        return []
    if len(blocos) == 1:
        return list(blocos[0])
    colunas = min(3, len(blocos))
    largura_coluna = (largura - espaco * (colunas - 1)) / colunas
    linhas = [blocos[i:i + colunas] for i in range(0, len(blocos), colunas)]
    celulas = [[list(b) for b in linha] + [""] * (colunas - len(linha)) for linha in linhas]
    t = Table(celulas, colWidths=[largura_coluna] * colunas, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), espaco),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), espaco),
    ]))
    return t


# ── Gráficos ──────────────────────────────────────────────────────────────────
def _figura(largura_pt: float, altura_pt: float, *, baixo: float = 0.22,
            esquerda: float = 0.085, direita: float = 0.985, topo: float = 0.95):
    """Figura dimensionada em pontos de PDF: 6 pt no gráfico é 6 pt na folha.

    As margens são explícitas — e não `bbox_inches="tight"` — de propósito: o
    corte automático muda o tamanho do PNG, e reescalá-lo para a largura da
    mancha ampliaria também o texto, que deixaria de medir os 4,5 a 6 pt que §4
    manda. Aqui a figura sai com o tamanho exato que ocupa na folha, e o texto
    do gráfico mede o mesmo que o texto das tabelas ao lado.
    """
    fig, ax = plt.subplots(figsize=(largura_pt / 72, altura_pt / 72), dpi=300)
    fig.subplots_adjust(left=esquerda, right=direita, top=topo, bottom=baixo)
    fig.patch.set_facecolor(BRANCO)
    ax.set_facecolor(BRANCO)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(CINZA_GRADE)
        ax.spines[lado].set_linewidth(0.5)
    ax.tick_params(colors=CINZA_TEXTO, labelsize=PT_NOTA, length=0, pad=3)
    ax.grid(axis="y", color=CINZA_GRADE, linewidth=0.5)  # §7: grade horizontal cinza-clara
    ax.set_axisbelow(True)
    return fig, ax


def _guardar(fig, destino: Path, largura_pt: float, altura_pt: float) -> Image:
    """Grava e devolve a imagem à escala 1:1 — o PNG ocupa na folha exatamente
    os pontos com que a figura foi criada."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, facecolor=BRANCO)
    plt.close(fig)
    img = Image(str(destino), width=largura_pt, height=altura_pt)
    img.hAlign = "LEFT"
    return img


def _legenda(ax, ncols: int) -> None:
    """Legenda horizontal abaixo do gráfico (§7). Sem caixa, em tinta de texto."""
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), frameon=False,
              fontsize=PT_NOTA, labelcolor=CINZA_TEXTO, ncols=ncols,
              handlelength=1.1, handleheight=0.8, columnspacing=1.6)


def _titulo_eixo(ax, esquerda: str, direita: str = "") -> None:
    """§7: título da unidade em cada eixo."""
    ax.set_ylabel(esquerda, color=CINZA_TEXTO, fontsize=PT_NOTA, labelpad=4)
    if direita:
        ax.right_ax.set_ylabel(direita, color=CINZA_TEXTO, fontsize=PT_NOTA, labelpad=4)


def grafico_evolucao_patrimonial(
        destino: Path, competencias: Sequence[str],
        entradas_saidas: Sequence[float], resultado_nao_realizado: Sequence[float],
        patrimonio: Sequence[float], *, unidade: str = "R$",
        largura: float = LARGURA_UTIL, altura: float = 150) -> Image:
    """§7 — barras de movimentações + linha de património.

    **Dois eixos y, por exigência do guia**: movimentações à esquerda,
    património à direita, porque as duas grandezas não partilham escala (§7
    "Escalas"). Cada eixo leva o título da sua unidade e a sua cor de série,
    que é o que permite ler qual pertence a qual — sem isso, um gráfico de dois
    eixos é ilegível.
    """
    # margem à direita para o segundo eixo caber inteiro, sem cortar rótulos
    fig, ax = _figura(largura, altura, esquerda=0.105, direita=0.885, baixo=0.24)
    x = range(len(competencias))
    largura_barra = 0.38

    b1 = ax.bar([i - largura_barra / 2 for i in x], entradas_saidas, largura_barra,
                color=VERMELHO_LOCAL, label="Entradas − Saídas")
    b2 = ax.bar([i + largura_barra / 2 for i in x], resultado_nao_realizado, largura_barra,
                color=CINZA_MEDIO, label="Resultado não realizado")
    ax.axhline(0, color=CINZA_TEXTO, linewidth=0.6)  # §7: linha de zero visível

    eixo_p = ax.twinx()
    ax.right_ax = eixo_p
    eixo_p.set_facecolor("none")
    for lado in ("top", "left", "bottom"):
        eixo_p.spines[lado].set_visible(False)
    eixo_p.spines["right"].set_color(CINZA_GRADE)
    eixo_p.spines["right"].set_linewidth(0.5)
    eixo_p.tick_params(colors=CINZA_TEXTO, labelsize=PT_NOTA, length=0, pad=3)
    linha, = eixo_p.plot(list(x), patrimonio, color=AZUL_INTERNACIONAL, linewidth=1.2,
                         marker="o", markersize=2.2, label="Património")

    # §7 Escalas: 10% de folga acima do máximo e abaixo do mínimo, em cada eixo.
    _folgar(ax, list(entradas_saidas) + list(resultado_nao_realizado), zero=True)
    _folgar(eixo_p, list(patrimonio))

    ax.set_xticks(list(x))
    ax.set_xticklabels(competencias, fontsize=PT_NOTA, color=CINZA_TEXTO)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: abreviado(v, unidade)))
    eixo_p.yaxis.set_major_formatter(FuncFormatter(lambda v, _: abreviado(v, unidade)))
    _titulo_eixo(ax, f"Movimentações ({unidade})", f"Património ({unidade})")

    # §7 Rótulos: nos pontos finais, não em todos — e sem se sobreporem.
    eixo_p.annotate(abreviado(patrimonio[-1], unidade), xy=(len(x) - 1, patrimonio[-1]),
                    xytext=(0, 6), textcoords="offset points", ha="right",
                    fontsize=PT_NOTA, color=AZUL_INTERNACIONAL, fontweight="bold")
    eixo_p.annotate(abreviado(patrimonio[0], unidade), xy=(0, patrimonio[0]),
                    xytext=(0, 6), textcoords="offset points", ha="left",
                    fontsize=PT_NOTA, color=AZUL_INTERNACIONAL)

    marcas = [b1, b2, linha]
    ax.legend(marcas, [m.get_label() for m in marcas], loc="upper center",
              bbox_to_anchor=(0.5, -0.16), frameon=False, fontsize=PT_NOTA,
              labelcolor=CINZA_TEXTO, ncols=3, handlelength=1.1, handleheight=0.8)
    return _guardar(fig, destino, largura, altura)


def _folgar(ax, valores: Sequence[float], *, zero: bool = False) -> None:
    """Margem mínima de 10% acima do máximo e abaixo do mínimo (§7 Escalas)."""
    if not valores:
        return
    baixo, alto = min(valores), max(valores)
    if zero:
        baixo, alto = min(baixo, 0), max(alto, 0)
    folga = (alto - baixo) * 0.10 or abs(alto or 1) * 0.10
    ax.set_ylim(baixo - folga, alto + folga)


def _rotulos_sem_sobrepor(fig, ax, altura_pt: float,
                          rotulos: Sequence[tuple[float, str, str, bool]]) -> None:
    """Escreve rótulos no fim de cada série sem que se toquem (§7 e §8).

    Quando duas séries acabam no mesmo sítio, os rótulos empilham-se e ficam
    ilegíveis — o guia proíbe expressamente que se sobreponham. Aqui ordenam-se
    por altura e empurra-se cada um para cima o mínimo necessário para respeitar
    a entrelinha; a linha continua a acabar onde acaba, só o texto se afasta.
    """
    fig.canvas.draw()
    altura_eixo = ax.get_window_extent().height * 72 / fig.dpi
    limites = ax.get_ylim()
    minimo = (limites[1] - limites[0]) * (PT_NOTA * 1.25 / max(altura_eixo, 1))

    ordenados = sorted(rotulos, key=lambda r: r[0])
    ys = [r[0] for r in ordenados]
    for i in range(1, len(ys)):
        ys[i] = max(ys[i], ys[i - 1] + minimo)
    excesso = ys[-1] - limites[1] if ys and ys[-1] > limites[1] else 0
    for i in range(len(ys)):  # se subiu acima do topo, desce o conjunto todo
        ys[i] -= excesso

    x = ax.get_xlim()[1]
    for y, (_, texto, cor, forte) in zip(ys, ordenados):
        ax.annotate(texto, xy=(x, y), xytext=(3, 0), textcoords="offset points",
                    va="center", ha="left", fontsize=PT_NOTA, color=cor,
                    fontweight="bold" if forte else "normal", annotation_clip=False)


def grafico_rentabilidade_acumulada(
        destino: Path, competencias: Sequence[str],
        series: Sequence[tuple[str, Sequence[float], bool]], *,
        largura: float = LARGURA_UTIL, altura: float = 150) -> Image:
    """§8 — rentabilidade acumulada composta, em percentagem.

    `series` é (nome, valores, é_benchmark). A carteira consolidada vai na
    linha de maior destaque; os benchmarks em linhas mais finas e tracejadas.
    """
    # margem à direita para os rótulos do último ponto de cada série (§8)
    fig, ax = _figura(largura, altura, esquerda=0.085, direita=0.88, baixo=0.24)
    x = range(len(competencias))
    finais: list[tuple[float, str, str, bool]] = []
    for i, (nome, valores, e_benchmark) in enumerate(series):
        if e_benchmark:
            cor, espessura, traco = CINZA_MEDIO, 0.7, (0, (3, 2))
        else:
            cor = AZUL_INTERNACIONAL if i == 0 else cor_de_classe(LOCAL, i)
            espessura, traco = (1.4 if i == 0 else 1.0), "solid"
        ax.plot(list(x), valores, color=cor, linewidth=espessura, linestyle=traco,
                label=nome, marker="o", markevery=[-1], markersize=2.4)
        finais.append((valores[-1], percentual(valores[-1]), cor,
                       not e_benchmark and i == 0))
    ax.axhline(0, color=CINZA_TEXTO, linewidth=0.6)  # §8: linha horizontal em 0%
    ax.set_xticks(list(x))
    ax.set_xticklabels(competencias, fontsize=PT_NOTA, color=CINZA_TEXTO)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: eixo_percent(v)))
    ax.set_ylabel("Rentabilidade acumulada (%)", color=CINZA_TEXTO, fontsize=PT_NOTA, labelpad=4)
    ax.set_xlim(-0.3, len(competencias) - 1)
    _legenda(ax, ncols=min(4, len(series)))
    _rotulos_sem_sobrepor(fig, ax, altura, finais)   # §8: rótulo no último ponto
    return _guardar(fig, destino, largura, altura)


def rosca(destino: Path, rotulos: Sequence[str], valores: Sequence[float], *,
          segmento: str = LOCAL, indices: Sequence[int] | None = None,
          centro: str = "", centro_rotulo: str = "Património",
          largura: float = 160, altura: float = 150) -> Image:
    """§9 — alocação. Valor patrimonial no centro, percentagem em cada setor.

    Não desenha setores falsos: se o total for zero, devolve `None` e a secção
    mostra só a tabela ou é omitida (§9). Uma categoria única aparece com
    `100,00%` escrito.
    """
    total = sum(valores)
    if total <= 0:
        return None
    cores = [cor_de_classe(segmento, i) for i in (indices or range(len(rotulos)))]
    fig, ax = plt.subplots(figsize=(largura / 72, altura / 72), dpi=300)
    fig.patch.set_facecolor(BRANCO)
    # Espaço em baixo para a legenda; o anel usa o resto, por isso o diâmetro
    # acompanha o espaço que a página lhe der (§9).
    fig.subplots_adjust(left=0.02, right=0.98, top=0.99, bottom=0.22)
    fatias, _ = ax.pie(valores, colors=cores, startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.30, edgecolor=BRANCO, linewidth=0.8))
    for fatia, valor in zip(fatias, valores):
        peso = valor / total
        if peso < 0.06:  # setor pequeno fica só na legenda, para não sobrepor
            continue
        ang = math.radians((fatia.theta1 + fatia.theta2) / 2)
        ax.text(0.85 * math.cos(ang), 0.85 * math.sin(ang), f"{_pt_br(peso * 100, 2)}%",
                ha="center", va="center", color=BRANCO, fontsize=PT_NOTA, fontweight="bold")
    if centro:
        ax.text(0, 0.06, centro, ha="center", va="center",
                color=AZUL_TITULO, fontsize=PT_SUBTITULO, fontweight="bold")
        ax.text(0, -0.16, centro_rotulo, ha="center", va="center",
                color=CINZA_TEXTO, fontsize=PT_NOTA)
    ax.legend(fatias, rotulos, loc="upper center", bbox_to_anchor=(0.5, 0.02),
              frameon=False, fontsize=PT_NOTA, labelcolor=CINZA_TEXTO,
              ncols=2, handlelength=0.8, handleheight=0.8, columnspacing=1.0)
    return _guardar(fig, destino, largura, altura)


def grafico_atribuicao(destino: Path, macroclasses: Sequence[str],
                       valores: Sequence[float], *, unidade: str = "R$",
                       largura: float = LARGURA_UTIL, altura: float = 120) -> Image:
    """§10 — atribuição de performance por macroclasse, com eixo zero.

    Positivos e negativos claramente diferenciados: vermelho institucional para
    contribuição positiva, cinza médio para negativa (§3.3 — o vermelho é a cor
    de destaque positivo da carteira Local; nada aqui usa cor fora da paleta).
    """
    fig, ax = _figura(largura, altura)
    cores = [VERMELHO_LOCAL if v >= 0 else CINZA_MEDIO for v in valores]
    barras = ax.bar(list(macroclasses), valores, width=0.5, color=cores)
    ax.axhline(0, color=CINZA_TEXTO, linewidth=0.6)
    _folgar(ax, list(valores), zero=True)
    folga = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.02
    for b, v in zip(barras, valores):  # §10: rótulos de valores
        ax.text(b.get_x() + b.get_width() / 2,
                (v + folga) if v >= 0 else (v - folga),
                abreviado(v, unidade), ha="center",
                va="bottom" if v >= 0 else "top",
                fontsize=PT_NOTA, color=CINZA_TEXTO)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: abreviado(v, unidade)))
    ax.tick_params(axis="x", labelsize=PT_NOTA, colors=CINZA_TEXTO)
    ax.set_ylabel(f"Contribuição ({unidade})", color=CINZA_TEXTO, fontsize=PT_NOTA, labelpad=4)
    return _guardar(fig, destino, largura, altura)


def grafico_vencimentos(destino: Path, competencias: Sequence[str],
                        valores: Sequence[float], *, unidade: str = "R$",
                        largura: float = LARGURA_UTIL, altura: float = 110) -> Image:
    """§14 — fluxo de vencimentos dos próximos 12 meses.

    Rótulo só sobre as barras relevantes — o guia pede "rótulo de valor sobre
    barras relevantes", não sobre todas.
    """
    fig, ax = _figura(largura, altura)
    barras = ax.bar(list(competencias), valores, width=0.55, color=AZUL_INTERNACIONAL)
    maximo = max(valores) if valores else 0
    for b, v in zip(barras, valores):
        if maximo and v >= maximo * 0.25:
            ax.text(b.get_x() + b.get_width() / 2, v + maximo * 0.03,
                    abreviado(v, unidade), ha="center", va="bottom",
                    fontsize=PT_NOTA, color=CINZA_TEXTO)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: abreviado(v, unidade)))
    ax.tick_params(axis="x", labelsize=PT_NOTA, colors=CINZA_TEXTO)
    ax.set_ylabel(f"Vencimentos ({unidade})", color=CINZA_TEXTO, fontsize=PT_NOTA, labelpad=4)
    ax.margins(y=0.16)
    return _guardar(fig, destino, largura, altura)
