"""Camada visual do relatório de consolidação — tokens, estilos Excel e gráficos.

Este módulo é a parte "difícil de acertar a olho": paleta, tipografia, formatos
numéricos, espaçamento e as regras de desenho dos gráficos. **Está completo.**
Quem estender o relatório não deve inventar cores nem formatos aqui — deve
consumir os tokens abaixo, para que todas as folhas e todos os gráficos leiam
como um só sistema.

Regras que este módulo não quebra (e que quem mexer aqui não deve quebrar):

* cor categórica é atribuída por **entidade**, em ordem fixa (`SLOT_CLASSE`),
  nunca ciclada e nunca por ranking — filtrar séries não repinta as que ficam;
* nunca dois eixos y no mesmo gráfico: duas medidas de escala diferente são
  dois gráficos;
* sequencial = um tom, claro→escuro; divergente = dois tons + cinzento neutro;
* ≥2 séries ⇒ legenda sempre presente, e até 4 séries também rotuladas
  diretamente — a identidade nunca depende só da cor;
* texto veste tokens de texto (`INK`, `INK_2`, `MUTED`), nunca a cor da série;
* cores de estado (bom/aviso/grave/crítico) são reservadas e vêm sempre com
  ícone + palavra, nunca cor sozinha;
* marcas finas, grelha recessiva, rótulos seletivos — nunca um número em cada
  ponto.

A paleta é a instância de referência validada (checagem de banda de luminosidade,
piso de croma, separação para daltonismo e contraste). Trocar por uma paleta de
marca implica revalidar: não basta trocar os hex.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib

matplotlib.use("Agg")  # sem display: renderiza para ficheiro

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, NamedStyle, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.worksheet import Worksheet

__all__ = [
    "xl", "SERIES", "SLOT_CLASSE", "SEQ", "DIVERGENTE", "ESTADO", "SURFACE", "INK",
    "FMT_DINHEIRO", "FMT_PERCENT", "cor_da_classe", "registar_estilos",
    "preparar_folha", "titulo_folha", "cabecalho_tabela", "escrever_tabela",
    "kpi_tile", "faixa_kpi", "barra_de_peso", "colar_grafico",
    "grafico_rosca", "grafico_barras_empilhadas", "grafico_linha",
    "grafico_ranking", "grafico_cascata",
]

# ── Paleta ────────────────────────────────────────────────────────────────────
# Slots categóricos, ordem fixa. O 9.º elemento nunca ganha um tom novo: agrupa-se
# em "Outros" ou parte-se o gráfico em pequenos múltiplos.
SERIES: tuple[str, ...] = (
    "#2a78d6",  # 1 azul
    "#eb6834",  # 2 laranja
    "#1baf7a",  # 3 água
    "#eda100",  # 4 amarelo
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 verde
    "#4a3aa7",  # 7 violeta
    "#e34948",  # 8 vermelho
)
# Formas com muitas comparações par-a-par (dispersão, bolhas, mapas) só validam
# até três slots — acima disso, agrupar ou facetar.
SERIES_MAX_TODOS_OS_PARES = 3

# Sequencial (magnitude contínua): um só tom, claro→escuro.
SEQ: tuple[str, ...] = (
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
    "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
    "#184f95", "#104281", "#0d366b",
)
# Divergente (polaridade): dois polos + cinzento neutro ao meio.
DIVERGENTE = {"positivo": "#2a78d6", "neutro": "#f0efec", "negativo": "#e34948"}

# Estado — reservado. Nunca serve de "série 4". Vai sempre com ícone + palavra.
ESTADO = {
    "bom": "#0ca30c",
    "aviso": "#fab219",
    "grave": "#ec835a",
    "critico": "#d03b3b",
}
ICONE_ESTADO = {"bom": "▲", "aviso": "!", "grave": "!", "critico": "▼"}

# Superfícies e tinta.
SURFACE = "#fcfcfb"    # fundo do gráfico
PLANE = "#f9f9f7"      # plano da página
INK = "#0b0b0b"        # tinta primária
INK_2 = "#52514e"      # tinta secundária
MUTED = "#898781"      # eixos, rótulos de apoio
GRID = "#e1e0d9"       # grelha, fio de cabelo
BASELINE = "#c3c2b7"   # linha de base / eixo
DELTA_BOM = "#006300"  # texto de variação positiva (não é a cor de estado)

FONT = "DejaVu Sans"  # pilha sans do sistema; trocar pela da casa se houver

# ── Amarração cor↔classe de ativo ─────────────────────────────────────────────
# A cor segue a ENTIDADE, não a posição na lista. Uma classe mantém o mesmo tom
# na capa, na folha de alocação e em qualquer gráfico futuro.
SLOT_CLASSE: dict[str, int] = {
    "Ações": 0,
    "Rendimento fixo": 1,
    "Fundos": 2,
    "Alternativos": 3,
    "Imobiliário": 4,
    "Liquidez": 5,
    "Estruturados": 6,
    "Outros": 7,
}


def cor_da_classe(classe: str) -> str:
    """Tom fixo de uma classe de ativo. Classe desconhecida cai em 'Outros'."""
    return SERIES[SLOT_CLASSE.get(classe, SLOT_CLASSE["Outros"])]


# ── Formatos numéricos Excel ──────────────────────────────────────────────────
FMT_DINHEIRO = '#,##0.00;[Red]-#,##0.00'
FMT_DINHEIRO_CURTO = '#,##0;[Red]-#,##0'
FMT_PERCENT = '0.0"%"'
FMT_PERCENT_SINAL = '+0.0"%";[Red]-0.0"%";0.0"%"'
FMT_QUANTIDADE = '#,##0.000'
FMT_DATA = 'yyyy-mm-dd'

def xl(cor: str) -> str:
    """Hex para openpyxl: sem '#', em maiúsculas (o Excel só aceita aRGB/RGB hex)."""
    return cor.lstrip("#").upper()


_THIN = Side(style="thin", color=xl(GRID))


def registar_estilos(wb) -> None:
    """Regista os estilos nomeados uma vez por livro (idempotente)."""
    existentes = {s.name for s in wb._named_styles}
    defs: list[NamedStyle] = []

    corpo = NamedStyle("fo_corpo")
    corpo.font = Font(name=FONT, size=10, color=xl(INK))
    corpo.alignment = Alignment(vertical="center")
    corpo.border = Border(bottom=_THIN)
    defs.append(corpo)

    dinheiro = NamedStyle("fo_dinheiro")
    dinheiro.font = Font(name=FONT, size=10, color=xl(INK))
    dinheiro.number_format = FMT_DINHEIRO
    dinheiro.alignment = Alignment(horizontal="right", vertical="center")
    dinheiro.border = Border(bottom=_THIN)
    defs.append(dinheiro)

    percent = NamedStyle("fo_percent")
    percent.font = Font(name=FONT, size=10, color=xl(INK_2))
    percent.number_format = FMT_PERCENT
    percent.alignment = Alignment(horizontal="right", vertical="center")
    percent.border = Border(bottom=_THIN)
    defs.append(percent)

    total = NamedStyle("fo_total")
    total.font = Font(name=FONT, size=10, bold=True, color=xl(INK))
    total.number_format = FMT_DINHEIRO
    total.alignment = Alignment(horizontal="right", vertical="center")
    total.fill = PatternFill("solid", fgColor="EDF3FC")
    total.border = Border(top=Side(style="thin", color=xl(BASELINE)), bottom=Side(style="double", color=xl(BASELINE)))
    defs.append(total)

    for estilo in defs:
        if estilo.name not in existentes:
            wb.add_named_style(estilo)


# ── Chassis da folha ──────────────────────────────────────────────────────────
def preparar_folha(ws: Worksheet, *, larguras: Sequence[float] = (), tab: str | None = None,
                   paisagem: bool = True) -> None:
    """Desliga a grelha, fixa larguras e prepara a folha para impressão/PDF.

    Desligar a grelha é o que mais separa uma folha desenhada de uma folha
    despejada: o branco passa a ser espaço, não ruído.
    """
    ws.sheet_view.showGridLines = False
    if tab:
        ws.sheet_properties.tabColor = xl(tab)
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.orientation = "landscape" if paisagem else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_margins.left = ws.page_margins.right = 0.4


def titulo_folha(ws: Worksheet, titulo: str, subtitulo: str = "", *, linha: int = 2,
                 coluna: int = 2) -> int:
    """Bloco de título. Devolve a primeira linha livre abaixo."""
    c = ws.cell(row=linha, column=coluna, value=titulo)
    c.font = Font(name=FONT, size=18, bold=True, color=xl(INK))
    ws.row_dimensions[linha].height = 26
    if subtitulo:
        s = ws.cell(row=linha + 1, column=coluna, value=subtitulo)
        s.font = Font(name=FONT, size=10, color=xl(MUTED))
        ws.row_dimensions[linha + 1].height = 16
        return linha + 3
    return linha + 2


def cabecalho_tabela(ws: Worksheet, linha: int, coluna: int, nomes: Sequence[str],
                     alinhamentos: Sequence[str] = ()) -> None:
    """Cabeçalho de tabela: fundo tinta, texto branco, uma linha, sem bordas gordas."""
    for i, nome in enumerate(nomes):
        c = ws.cell(row=linha, column=coluna + i, value=nome)
        c.font = Font(name=FONT, size=9, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=xl(INK))
        horiz = alinhamentos[i] if i < len(alinhamentos) else "left"
        c.alignment = Alignment(horizontal=horiz, vertical="center", wrap_text=True)
    ws.row_dimensions[linha].height = 22


def escrever_tabela(ws: Worksheet, linha: int, coluna: int, nomes: Sequence[str],
                    linhas: Iterable[Sequence], *, estilos: Sequence[str] = (),
                    alinhamentos: Sequence[str] = (), zebra: bool = True,
                    total: Sequence | None = None, autofiltro: bool = True) -> int:
    """Escreve uma tabela completa (cabeçalho, corpo, total) e devolve a linha seguinte.

    `estilos` nomeia, por coluna, um dos estilos registados em `registar_estilos`
    ('fo_corpo', 'fo_dinheiro', 'fo_percent'). O que não for nomeado usa corpo.
    """
    cabecalho_tabela(ws, linha, coluna, nomes, alinhamentos)
    r = linha + 1
    for n, valores in enumerate(linhas):
        for i, valor in enumerate(valores):
            c = ws.cell(row=r, column=coluna + i, value=valor)
            c.style = estilos[i] if i < len(estilos) and estilos[i] else "fo_corpo"
            if zebra and n % 2 == 1:
                c.fill = PatternFill("solid", fgColor="F7F7F5")
        ws.row_dimensions[r].height = 18
        r += 1
    if total is not None:
        for i, valor in enumerate(total):
            c = ws.cell(row=r, column=coluna + i, value=valor)
            c.style = "fo_total" if i and valor is not None else "fo_corpo"
            if i == 0:
                c.font = Font(name=FONT, size=10, bold=True, color=xl(INK))
                c.fill = PatternFill("solid", fgColor="EDF3FC")
        r += 1
    if autofiltro and r > linha + 1:
        ref = (f"{get_column_letter(coluna)}{linha}:"
               f"{get_column_letter(coluna + len(nomes) - 1)}{r - 1}")
        ws.auto_filter.ref = ref
        ws.freeze_panes = ws.cell(row=linha + 1, column=coluna)
    return r + 1


def barra_de_peso(ws: Worksheet, intervalo: str, cor: str = SERIES[0]) -> None:
    """Barra de dados numa coluna de peso/percentagem — magnitude lida de relance."""
    ws.conditional_formatting.add(
        intervalo,
        DataBarRule(start_type="num", start_value=0, end_type="max",
                    color=xl(cor), showValue=True, minLength=None, maxLength=None),
    )


def kpi_tile(ws: Worksheet, linha: int, coluna: int, rotulo: str, valor,
             *, formato: str = FMT_DINHEIRO_CURTO, delta: float | None = None,
             delta_rotulo: str = "", largura: int = 3) -> None:
    """Um cartão de indicador: rótulo discreto, número grande, variação com ícone.

    A variação leva sempre ícone **e** palavra: quem não distingue as cores lê
    na mesma se subiu ou desceu.
    """
    ws.merge_cells(start_row=linha, start_column=coluna,
                   end_row=linha, end_column=coluna + largura - 1)
    ws.merge_cells(start_row=linha + 1, start_column=coluna,
                   end_row=linha + 1, end_column=coluna + largura - 1)
    ws.merge_cells(start_row=linha + 2, start_column=coluna,
                   end_row=linha + 2, end_column=coluna + largura - 1)

    r = ws.cell(row=linha, column=coluna, value=rotulo.upper())
    r.font = Font(name=FONT, size=8, bold=True, color=xl(MUTED))
    r.alignment = Alignment(horizontal="left", vertical="center")

    v = ws.cell(row=linha + 1, column=coluna, value=valor)
    v.font = Font(name=FONT, size=20, bold=True, color=xl(INK))
    v.number_format = formato
    v.alignment = Alignment(horizontal="left", vertical="center")

    d = ws.cell(row=linha + 2, column=coluna)
    if delta is None:
        d.value = delta_rotulo or ""
        d.font = Font(name=FONT, size=9, color=xl(MUTED))
    else:
        icone = ICONE_ESTADO["bom"] if delta >= 0 else ICONE_ESTADO["critico"]
        palavra = "acima" if delta >= 0 else "abaixo"
        d.value = f"{icone} {abs(delta):.1f}% {palavra} {delta_rotulo}".strip()
        d.font = Font(name=FONT, size=9, bold=True,
                      color=xl(DELTA_BOM if delta >= 0 else ESTADO["critico"]))
    d.alignment = Alignment(horizontal="left", vertical="center")

    for offset, altura in ((0, 14), (1, 28), (2, 16)):
        ws.row_dimensions[linha + offset].height = altura
    for offset in range(3):
        for i in range(largura):
            ws.cell(row=linha + offset, column=coluna + i).fill = PatternFill(
                "solid", fgColor=xl(PLANE))


def faixa_kpi(ws: Worksheet, linha: int, coluna: int, tiles: Sequence[dict],
              *, largura: int = 3, espaco: int = 1) -> int:
    """Uma fila de cartões. Devolve a primeira linha livre abaixo."""
    c = coluna
    for tile in tiles:
        kpi_tile(ws, linha, c, largura=largura, **tile)
        c += largura + espaco
    return linha + 4


# ── Gráficos ──────────────────────────────────────────────────────────────────
def _figura(largura_pol: float, altura_pol: float):
    fig, ax = plt.subplots(figsize=(largura_pol, altura_pol), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(BASELINE)
        ax.spines[lado].set_linewidth(0.8)
    ax.tick_params(colors=MUTED, labelsize=7.5, length=0, pad=6)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _titulo(ax, titulo: str, subtitulo: str = "", x: float = 0.0) -> None:
    """Título e subtítulo alinhados à esquerda. `x` em coordenadas de eixo, para
    encostar o título aos rótulos quando estes ficam fora da área de desenho."""
    ax.set_title(titulo, color=INK, fontsize=11.5, fontweight="bold", loc="left",
                 x=x, pad=18 if subtitulo else 10)
    if subtitulo:
        ax.text(x, 1.03, subtitulo, transform=ax.transAxes, color=MUTED, fontsize=8, va="bottom")


def _x_dos_rotulos(fig, ax) -> float:
    """Onde começa a coluna de rótulos do eixo y, em coordenadas de eixo."""
    fig.canvas.draw()
    caixa = ax.get_yaxis().get_tightbbox(fig.canvas.get_renderer())
    if caixa is None:
        return 0.0
    return min(0.0, ax.transAxes.inverted().transform((caixa.x0, 0))[0])


def _ponta_redonda(fig, ax, barras) -> None:
    """Arredonda a **ponta de dados** das barras horizontais, sem esticar a barra.

    A barra é encurtada de meio-raio e a tampa redonda repõe exatamente esse
    meio-raio: o comprimento total continua a valer o valor. A base fica reta,
    encostada à linha de zero — é ela que ancora a leitura.

    Chamar depois de fixar `set_xlim`: o raio é calculado em píxeis e convertido
    para unidades de dados com os limites já definidos.
    """
    fig.canvas.draw()
    inv = ax.transData.inverted()
    for barra in barras:
        altura_px = abs(ax.transData.transform((0, barra.get_height()))[1]
                        - ax.transData.transform((0, 0))[1])
        raio_px = altura_px / 2
        raio_x = inv.transform((raio_px, 0))[0] - inv.transform((0, 0))[0]
        largura = barra.get_width()
        if largura <= raio_x * 1.5:  # barra curta demais para valer a pena
            continue
        barra.set_width(largura - raio_x)
        ax.plot(largura - raio_x, barra.get_y() + barra.get_height() / 2,
                marker="o", markersize=altura_px * 72 / fig.dpi,
                color=barra.get_facecolor(), markeredgewidth=0,
                zorder=barra.zorder, clip_on=False)


def _guardar(fig, destino: Path) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig)
    return destino


def colar_grafico(ws: Worksheet, png: Path, ancora: str, largura_pol: float,
                  altura_pol: float) -> None:
    """Insere o PNG à escala certa (renderizado a 200 dpi, apresentado a 96)."""
    img = XLImage(str(png))
    img.width = int(largura_pol * 96)
    img.height = int(altura_pol * 96)
    ws.add_image(img, ancora)


def grafico_rosca(destino: Path, rotulos: Sequence[str], valores: Sequence[float],
                  *, titulo: str, subtitulo: str = "", centro: str = "",
                  centro_rotulo: str = "", largura: float = 5.2,
                  altura: float = 4.0) -> Path:
    """Composição de uma carteira. Cores por classe (fixas), buraco para o total.

    Rosca só se presta a **uma** partição de um todo com poucas fatias; acima de
    seis fatias, barras ordenadas leem melhor.
    """
    cores = [cor_da_classe(r) for r in rotulos]
    fig, ax = plt.subplots(figsize=(largura, altura), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    total = sum(valores) or 1
    fatias, _ = ax.pie(
        valores, colors=cores, startangle=90, counterclock=False,
        wedgeprops=dict(width=0.34, edgecolor=SURFACE, linewidth=2),  # 2px de folga
    )
    # Rótulo direto nas fatias com peso; as pequenas ficam só na legenda.
    for fatia, valor in zip(fatias, valores):
        peso = valor / total
        if peso < 0.07:
            continue
        ang = (fatia.theta2 + fatia.theta1) / 2
        x, y = 0.83 * math.cos(math.radians(ang)), 0.83 * math.sin(math.radians(ang))
        ax.text(x, y, f"{peso*100:.0f}%", ha="center", va="center",
                color="#ffffff", fontsize=8.5, fontweight="bold")
    if centro:
        ax.text(0, 0.06, centro, ha="center", va="center", color=INK, fontsize=15, fontweight="bold")
        ax.text(0, -0.16, centro_rotulo, ha="center", va="center", color=MUTED, fontsize=8)
    ax.set_title(titulo, color=INK, fontsize=11.5, fontweight="bold", loc="left",
                 pad=18 if subtitulo else 10)
    if subtitulo:
        ax.text(0, 1.02, subtitulo, transform=ax.transAxes, color=MUTED, fontsize=8, va="bottom")
    ax.legend(fatias, rotulos, loc="center left", bbox_to_anchor=(1.0, 0.5),
              frameon=False, fontsize=8, labelcolor=INK_2, handlelength=0.9, handleheight=0.9)
    return _guardar(fig, destino)


def grafico_barras_empilhadas(destino: Path, categorias: Sequence[str],
                              series: Sequence[tuple[str, Sequence[float]]],
                              *, titulo: str, subtitulo: str = "", unidade: str = "%",
                              largura: float = 7.6, altura: float = 4.0) -> Path:
    """Alocação comparada entre entidades/contas. Barras horizontais, folga de 2px."""
    fig, ax = _figura(largura, altura)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    base = [0.0] * len(categorias)
    for nome, valores in series:
        barras = ax.barh(categorias, valores, left=base, height=0.42,
                         color=cor_da_classe(nome), label=nome,
                         edgecolor=SURFACE, linewidth=2)
        for i, (b, v) in enumerate(zip(barras, valores)):
            if v / (sum(s[1][i] for s in series) or 1) > 0.12:  # rótulo seletivo
                ax.text(base[i] + v / 2, b.get_y() + b.get_height() / 2, f"{v:,.0f}",
                        ha="center", va="center", color="#ffffff", fontsize=8, fontweight="bold")
        base = [b + v for b, v in zip(base, valores)]
    ax.invert_yaxis()
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}{unidade}"))
    ax.set_ylabel("")
    _titulo(ax, titulo, subtitulo, x=_x_dos_rotulos(fig, ax))
    ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, ncols=min(4, len(series)),
              loc="upper center", bbox_to_anchor=(0.5, -0.08), handlelength=0.9, handleheight=0.9)
    return _guardar(fig, destino)


def grafico_linha(destino: Path, eixo_x: Sequence[str],
                  series: Sequence[tuple[str, Sequence[float]]],
                  *, titulo: str, subtitulo: str = "", unidade: str = "",
                  largura: float = 7.6, altura: float = 3.6) -> Path:
    """Evolução no tempo. Um só eixo y — sempre. Rótulo direto na última observação.

    Duas medidas de escala diferente (património e rendibilidade, por exemplo)
    NÃO partilham este gráfico: ou são dois gráficos, ou vão indexadas a uma base
    comum.
    """
    fig, ax = _figura(largura, altura)
    for i, (nome, valores) in enumerate(series):
        cor = SERIES[i % len(SERIES)]
        ax.plot(eixo_x, valores, color=cor, linewidth=2, label=nome,
                marker="o", markevery=[-1], markersize=6,
                markerfacecolor=cor, markeredgecolor=SURFACE, markeredgewidth=2)
        if len(series) <= 4:  # rótulo direto, além da legenda
            ax.annotate(f"{nome} {valores[-1]:,.0f}{unidade}",
                        xy=(len(eixo_x) - 1, valores[-1]), xytext=(8, 0),
                        textcoords="offset points", color=INK_2, fontsize=8,
                        fontweight="bold", va="center")
    ax.set_xlim(-0.2, len(eixo_x) - 0.6)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}{unidade}"))
    ax.margins(y=0.18)
    _titulo(ax, titulo, subtitulo)
    if len(series) > 1:
        ax.legend(frameon=False, fontsize=8, labelcolor=INK_2, loc="upper left",
                  ncols=min(4, len(series)), handlelength=1.2)
    return _guardar(fig, destino)


def grafico_ranking(destino: Path, rotulos: Sequence[str], valores: Sequence[float],
                    *, titulo: str, subtitulo: str = "", unidade: str = "",
                    cor: str = SERIES[0], largura: float = 6.4,
                    altura: float = 4.0) -> Path:
    """Maiores posições. Ordem é o encoding — a cor aqui não diz nada, logo é uma só."""
    pares = sorted(zip(rotulos, valores), key=lambda p: p[1], reverse=True)
    nomes = [p[0] for p in pares]
    vals = [p[1] for p in pares]
    fig, ax = _figura(largura, altura)
    # Sem grelha e sem eixo x: o valor vai escrito ao fim de cada barra, e a
    # ordem já é o encoding. Um eixo aqui seria tinta a dizer o mesmo duas vezes.
    ax.grid(visible=False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="x", labelbottom=False)
    ax.set_xlim(0, max(vals) * 1.26 if vals else 1)
    barras = ax.barh(nomes, vals, height=0.52, color=cor)
    ax.invert_yaxis()
    _ponta_redonda(fig, ax, barras)
    for b, v in zip(barras, vals):  # valor em tinta de texto, nunca na cor da série
        ax.text(v * 1.03, b.get_y() + b.get_height() / 2, f"{v:,.0f}{unidade}",
                va="center", ha="left", color=INK_2, fontsize=8, fontweight="bold")
    ax.tick_params(axis="y", labelsize=8, colors=INK_2)
    _titulo(ax, titulo, subtitulo, x=_x_dos_rotulos(fig, ax))
    return _guardar(fig, destino)


def grafico_cascata(destino: Path, etapas: Sequence[tuple[str, float, bool]],
                    *, titulo: str, subtitulo: str = "", unidade: str = "",
                    largura: float = 7.6, altura: float = 3.8) -> Path:
    """Da posição de abertura à de fecho, rubrica a rubrica.

    `etapas` é (rótulo, valor, é_total). Polaridade usa o par divergente
    (positivo/negativo) — não as cores de estado, que ficam reservadas.
    """
    fig, ax = _figura(largura, altura)
    base = 0.0
    xs, alturas, bases, cores, niveis = [], [], [], [], []
    for rotulo, valor, e_total in etapas:
        xs.append(rotulo)
        if e_total:
            bases.append(0.0); alturas.append(valor); cores.append(SEQ[10])
            base = valor
        else:
            bases.append(base); alturas.append(valor)
            cores.append(DIVERGENTE["positivo"] if valor >= 0 else DIVERGENTE["negativo"])
            base += valor
        niveis.append(base)
    barras = ax.bar(xs, alturas, bottom=bases, width=0.58, color=cores,
                    edgecolor=SURFACE, linewidth=2)
    # Conectores: ligam o fim de uma rubrica ao início da seguinte. São eles que
    # tornam legível uma rubrica pequena demais para se ver como barra.
    for i in range(len(etapas) - 1):
        ax.plot([i + 0.30, i + 1 - 0.30], [niveis[i], niveis[i]],
                color=BASELINE, linewidth=0.9, linestyle=(0, (3, 3)), zorder=0)
    ax.axhline(0, color=BASELINE, linewidth=0.8)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}{unidade}"))
    ax.tick_params(axis="x", labelsize=8, colors=INK_2)
    ax.margins(y=0.16)
    folga = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.015
    for (rotulo, valor, e_total), b, fundo in zip(etapas, barras, bases):
        topo = max(fundo, fundo + valor)
        ax.text(b.get_x() + b.get_width() / 2, topo + folga,
                f"{valor:,.0f}" if e_total else f"{valor:+,.0f}",
                ha="center", va="bottom", color=INK_2, fontsize=8, fontweight="bold")
    _titulo(ax, titulo, subtitulo)
    return _guardar(fig, destino)
