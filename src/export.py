"""Layout de exportação para consolidador (posição e movimento).

As folhas `POSICOES` e `MOVIMENTOS` seguem o layout que os sistemas de
consolidação esperam — `IDCLIENTE`, `IDATIVO`, `DT_POSICAO`,
`VLR_BRUTO_MOEDA_ORIGINAL`, `PU`, `DT_MOVIMENTO`, `TIPO_MOVIMENTO`… — para
poderem ser carregadas sem trabalho manual.

Três regras governam esta camada:

* **Campo que o documento não dá fica vazio, nunca a zero.** Um statement de
  custódia norte-americano não retém IR nem IOF: escrever `0` afirmaria que o
  imposto foi zero, e isso é uma afirmação sobre dinheiro que ninguém verificou.
  A folha `De-para` diz, campo a campo, o que ficou vazio e porquê.
* **O código do tipo de movimento é uma tradução, não uma interpretação.** O
  tipo original vai numa coluna ao lado; o que não tem correspondência clara sai
  como `OUTROS`, não como um palpite.
* **Explodir fundo exclusivo é opcional e conferido.** A soma da carteira
  explodida tem de ser igual ao valor da cota que substituiu; se não for, é erro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Sequence

from .model import Dataset, Domain
from .reconcile import Check, compare

__all__ = [
    "Explosion",
    "ExportSet",
    "POSICAO_COLUMNS",
    "MOVIMENTO_COLUMNS",
    "TIPO_MOVIMENTO",
    "build_export",
    "explosion_checks",
]


class Explosion:
    """Como tratar um fundo exclusivo na visão de posições."""

    NONE = "nao"              # o fundo é uma linha: a cota
    CONSOLIDATED = "consolidado"  # carteira do fundo agregada por classe
    DETAILED = "detalhado"    # carteira do fundo linha a linha

    ALL = (NONE, CONSOLIDATED, DETAILED)


POSICAO_COLUMNS = [
    "IDCLIENTE",
    "NOME_CLIENTE",
    "CONTA",
    "IDATIVO",
    "DESCRICAO_ATIVO",
    "CLASSE_ATIVO",
    "MOEDA",
    "DT_POSICAO",
    "DT_COMPRA",
    "QUANTIDADE",
    "PU",
    "CUSTO_UNITARIO",
    "VLR_CUSTO_MOEDA_ORIGINAL",
    "VLR_BRUTO_MOEDA_ORIGINAL",
    "VALOR_IR",
    "VALOR_IOF",
    "VALOR_LIQUIDO",
    "RESULTADO_NAO_REALIZADO",
    "RENDIMENTO_ANUAL_ESTIMADO",
    "TAXA_YIELD",
    "PESO_PCT",
    "FUNDO_EXCLUSIVO",
    "ORIGEM_EXPLOSAO",
    "PAGINA",
    "TABELA_ORIGEM",
]

MOVIMENTO_COLUMNS = [
    "IDCLIENTE",
    "NOME_CLIENTE",
    "CONTA",
    "DT_MOVIMENTO",
    "TIPO_MOVIMENTO",
    "TIPO_ORIGINAL",
    "IDATIVO",
    "DESCRICAO",
    "MOEDA",
    "QUANTIDADE",
    "PU",
    "VALOR",
    "VALOR_IR",
    "VALOR_IOF",
    "PAGINA",
    "TABELA_ORIGEM",
]

# De-para do tipo de movimento. À esquerda o que o custodiante imprime, à
# direita o código do consolidador. O que não estiver aqui sai como OUTROS —
# com o texto original preservado em TIPO_ORIGINAL.
TIPO_MOVIMENTO: dict[str, str] = {
    "Purchase": "A",
    "Automatic Investment": "A",
    "Dividend Reinvestment": "A",
    "Capital Gain Reinvestment": "A",
    "Deposit": "A",
    "Bought": "A",
    "Sale": "RP",
    "Sold": "RP",
    "Automatic Redemption": "RP",
    "Cash Transfer": "TRF",
    "Redemption": "RP",
    "Withdrawal": "RP",
    "Interest": "JUROS",
    "Dividend": "DIV",
    "Long Term Capital Gain": "DIV",
    "Short Term Capital Gain": "DIV",
    "Transfer into Account": "TRF",
    "Transfer out of Account": "TRF",
    "Funds Received": "TRF",
    "Funds Transferred": "TRF",
    "Exchange": "TRF",
    "Journal": "TRF",
    "Fee": "TX",
    "Service Charge": "TX",
    "Foreign Tax Withheld": "IR",
    "Adjustment": "OUTROS",
}

# Códigos que existem no layout mas que um statement de custódia estrangeiro
# nunca produz — ficam documentados para quem carrega o ficheiro saber que a
# ausência é do documento, não do parser.
CODIGOS_SEM_ORIGEM = {
    "AM": "amortização — o statement não distingue amortização de resgate",
    "COME_COTAS": "come-cotas — tributação brasileira, não existe neste custodiante",
    "RT": "resgate total — o statement não distingue total de parcial",
}

# Campos que este documento não fornece, com o motivo. Vão para a folha De-para.
CAMPOS_SEM_ORIGEM = {
    "VALOR_IR": "o statement não discrimina retenção de IR na posição",
    "VALOR_IOF": "o statement não discrimina IOF (tributo brasileiro)",
    "VALOR_LIQUIDO": "sem IR e IOF conhecidos, o líquido não é derivável do bruto",
}

_NON_WORD = re.compile(r"[^A-Z0-9]+")


@dataclass
class ExportSet:
    posicoes: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    movimentos: list[dict[str, Any]] = field(default_factory=list)
    mapping: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ auxiliares

def _idativo(row: dict) -> str:
    """Identificador estável do ativo: o ticker quando existe, senão a descrição.

    Tem de ser determinístico entre execuções — é a chave com que o consolidador
    liga a posição de hoje à de ontem.
    """
    symbol = (row.get("simbolo") or "").strip()
    if symbol:
        return symbol.upper()
    description = (row.get("titulo") or row.get("descricao") or "").strip().upper()
    return _NON_WORD.sub("_", description).strip("_")[:60] or "SEM_IDENTIFICACAO"


def _cliente(account: str | None, clients: dict[str, dict]) -> tuple[str | None, str]:
    """IDCLIENTE vem do mapeamento externo; sem ele, a conta serve de chave."""
    entry = clients.get(account or "", {})
    return entry.get("idcliente", account), entry.get("nome", "")


def _is_exclusive(row: dict, exclusive: dict[str, dict]) -> bool:
    return _idativo(row) in exclusive


def _posicao_row(
    row: dict,
    clients: dict[str, dict],
    moeda: str,
    dt_posicao: str | None,
    exclusive: dict[str, dict],
    origem_explosao: str | None = None,
) -> dict[str, Any]:
    idcliente, nome = _cliente(row.get("conta"), clients)
    idativo = _idativo(row)
    return {
        "IDCLIENTE": idcliente,
        "NOME_CLIENTE": nome,
        "CONTA": row.get("conta"),
        "IDATIVO": idativo,
        "DESCRICAO_ATIVO": row.get("titulo"),
        "CLASSE_ATIVO": row.get("classe_ativo"),
        "MOEDA": moeda,
        "DT_POSICAO": dt_posicao,
        "DT_COMPRA": row.get("data_compra"),
        "QUANTIDADE": row.get("quantidade"),
        "PU": row.get("preco_unitario"),
        "CUSTO_UNITARIO": row.get("custo_unitario"),
        "VLR_CUSTO_MOEDA_ORIGINAL": row.get("custo_total"),
        "VLR_BRUTO_MOEDA_ORIGINAL": row.get("valor_mercado"),
        "VALOR_IR": None,
        "VALOR_IOF": None,
        "VALOR_LIQUIDO": None,
        "RESULTADO_NAO_REALIZADO": row.get("mais_valia_nao_realizada"),
        "RENDIMENTO_ANUAL_ESTIMADO": row.get("rendimento_anual_estimado"),
        "TAXA_YIELD": row.get("yield_pct"),
        "PESO_PCT": row.get("peso_pct"),
        "FUNDO_EXCLUSIVO": "sim" if idativo in exclusive else "não",
        "ORIGEM_EXPLOSAO": origem_explosao,
        "PAGINA": row.get("pagina"),
        "TABELA_ORIGEM": row.get("tabela_origem"),
    }


def _movimento_row(
    row: dict, clients: dict[str, dict], moeda: str
) -> dict[str, Any]:
    idcliente, nome = _cliente(row.get("conta"), clients)
    tipo_original = row.get("tipo")
    return {
        "IDCLIENTE": idcliente,
        "NOME_CLIENTE": nome,
        "CONTA": row.get("conta"),
        "DT_MOVIMENTO": row.get("data"),
        "TIPO_MOVIMENTO": TIPO_MOVIMENTO.get(tipo_original, "OUTROS"),
        "TIPO_ORIGINAL": tipo_original or (row.get("descricao") or "")[:40],
        "IDATIVO": _idativo(row),
        "DESCRICAO": row.get("descricao"),
        "MOEDA": moeda,
        "QUANTIDADE": row.get("quantidade"),
        "PU": row.get("preco_unitario"),
        "VALOR": row.get("valor"),
        "VALOR_IR": None,
        "VALOR_IOF": None,
        "PAGINA": row.get("pagina"),
        "TABELA_ORIGEM": row.get("tabela_origem"),
    }


# -------------------------------------------------------------------- explosão

def _explode(
    posicoes: Sequence[dict],
    exclusive: dict[str, dict],
    mode: str,
) -> tuple[list[dict], list[str]]:
    """Substitui a cota do fundo exclusivo pela carteira que ele detém.

    A carteira vem do ficheiro de fundos exclusivos, não do statement — um
    statement de custódia mostra a cota, não o que está dentro dela.
    """
    if mode == Explosion.NONE:
        return list(posicoes), []

    resultado: list[dict] = []
    notes: list[str] = []
    explodidos = 0

    for posicao in posicoes:
        fundo = exclusive.get(posicao["IDATIVO"])
        carteira = (fundo or {}).get("carteira")
        if not fundo or not carteira:
            resultado.append(posicao)
            continue

        explodidos += 1
        linhas = _carteira_rows(posicao, carteira, mode)
        resultado.extend(linhas)

    if not explodidos:
        notes.append(
            f"explosão '{mode}' pedida, mas nenhuma posição corresponde a um fundo exclusivo "
            "com carteira declarada: a visão é igual à não explodida"
        )
    return resultado, notes


def _carteira_rows(posicao: dict, carteira: Sequence[dict], mode: str) -> list[dict]:
    total = sum(
        (Decimal(str(item["valor"])) for item in carteira if item.get("valor") is not None),
        Decimal("0"),
    )
    bruto = posicao.get("VLR_BRUTO_MOEDA_ORIGINAL")

    if mode == Explosion.CONSOLIDATED:
        por_classe: dict[str, Decimal] = {}
        for item in carteira:
            classe = item.get("classe") or "SEM CLASSE"
            por_classe[classe] = por_classe.get(classe, Decimal("0")) + Decimal(
                str(item.get("valor") or 0)
            )
        itens = [
            {"descricao": classe, "classe": classe, "valor": valor}
            for classe, valor in sorted(por_classe.items())
        ]
    else:
        itens = list(carteira)

    linhas = []
    for item in itens:
        linha = dict(posicao)
        linha.update(
            {
                "IDATIVO": (item.get("idativo") or _NON_WORD.sub("_", str(item["descricao"]).upper()))[:60],
                "DESCRICAO_ATIVO": item.get("descricao"),
                "CLASSE_ATIVO": item.get("classe") or posicao.get("CLASSE_ATIVO"),
                "QUANTIDADE": item.get("quantidade"),
                "PU": item.get("pu"),
                "CUSTO_UNITARIO": None,
                "VLR_CUSTO_MOEDA_ORIGINAL": item.get("custo"),
                "VLR_BRUTO_MOEDA_ORIGINAL": Decimal(str(item.get("valor") or 0)),
                "RESULTADO_NAO_REALIZADO": None,
                "PESO_PCT": None,
                "FUNDO_EXCLUSIVO": "não",
                "ORIGEM_EXPLOSAO": posicao["IDATIVO"],
            }
        )
        linhas.append(linha)

    # A soma da carteira tem de repor exatamente o valor da cota substituída;
    # `explosion_checks` confirma-o e falha se não repuser.
    if bruto is not None and total != bruto and linhas:
        linhas[0]["_delta_explosao"] = bruto - total
    return linhas


# ------------------------------------------------------------------ construção

def build_export(
    datasets: dict[str, Dataset],
    layout: dict,
    *,
    clients: dict[str, dict] | None = None,
    exclusive_funds: dict[str, dict] | None = None,
    moeda: str = "USD",
    modes: Sequence[str] = Explosion.ALL,
) -> ExportSet:
    clients = clients or {}
    exclusive = exclusive_funds or {}
    dt_posicao = layout.get("period_end")

    export = ExportSet()
    if dt_posicao is None:
        export.notes.append(
            "DT_POSICAO vazio: o período do statement não foi reconhecido — preenche "
            "'period_end' no layout antes de carregar o ficheiro"
        )

    base = [
        _posicao_row(row, clients, moeda, dt_posicao, exclusive)
        for row in datasets[Domain.POSITIONS].data_rows
    ]

    declarados = [p for p in base if p["FUNDO_EXCLUSIVO"] == "sim"]
    if exclusive and not declarados:
        export.notes.append(
            f"{len(exclusive)} fundo(s) exclusivo(s) declarado(s), nenhum encontrado nas "
            "posições: confirma os IDATIVO do ficheiro"
        )

    for mode in modes:
        linhas, notes = _explode(base, exclusive, mode)
        export.posicoes[mode] = linhas
        export.notes.extend(notes)

    movimentos = list(datasets[Domain.TRANSACTIONS].data_rows)
    movimentos += [
        row for row in datasets[Domain.TRANSFERS].data_rows if row.get("valor") is not None
    ]
    export.movimentos = [_movimento_row(row, clients, moeda) for row in movimentos]

    export.mapping = _mapping_rows(moeda, dt_posicao, clients)
    return export


def _mapping_rows(moeda: str, dt_posicao: str | None, clients: dict) -> list[dict[str, str]]:
    """De-para campo a campo: de onde veio, ou porque está vazio."""
    origens = {
        "IDCLIENTE": "mapeamento de clientes (--clientes); sem ele, o número da conta"
        if not clients
        else "mapeamento de clientes (--clientes)",
        "NOME_CLIENTE": "mapeamento de clientes (--clientes)" if clients else "",
        "CONTA": "cabeçalho da página do statement",
        "IDATIVO": "símbolo do título; sem símbolo, a descrição normalizada",
        "DESCRICAO_ATIVO": "descrição da posição no statement",
        "CLASSE_ATIVO": "título da tabela de origem (COMMON STOCKS, MUTUAL FUNDS, …)",
        "MOEDA": f"constante {moeda} — o statement é denominado nesta moeda",
        "DT_POSICAO": f"fim do período do statement ({dt_posicao})" if dt_posicao else "",
        "DT_COMPRA": "data do lote (Trade Date)",
        "QUANTIDADE": "coluna Quantity",
        "PU": "coluna Share Price",
        "CUSTO_UNITARIO": "coluna Unit Cost",
        "VLR_CUSTO_MOEDA_ORIGINAL": "coluna Total Cost",
        "VLR_BRUTO_MOEDA_ORIGINAL": "coluna Market Value",
        "RESULTADO_NAO_REALIZADO": "coluna Unrealized Gain/(Loss)",
        "RENDIMENTO_ANUAL_ESTIMADO": "coluna Est Ann Income",
        "TAXA_YIELD": "coluna Current Yield",
        "PESO_PCT": "calculado: valor da posição sobre o total da conta",
        "FUNDO_EXCLUSIVO": "ficheiro de fundos exclusivos (--fundos-exclusivos)",
        "ORIGEM_EXPLOSAO": "IDATIVO do fundo que deu origem à linha, quando explodido",
        "PAGINA": "página do PDF",
        "TABELA_ORIGEM": "tabela do statement de onde veio a linha",
        "DT_MOVIMENTO": "data do movimento",
        "TIPO_MOVIMENTO": "de-para do tipo impresso (ver folha)",
        "TIPO_ORIGINAL": "tipo tal como impresso no statement",
        "DESCRICAO": "descrição do movimento, sem o prefixo do tipo",
        "VALOR": "coluna Credits/(Debits)",
    }

    sem_config = {} if clients else {
        "NOME_CLIENTE": "requer o mapeamento de clientes (--clientes)",
        "IDCLIENTE": "sem mapeamento de clientes, fica o número da conta",
    }

    rows: list[dict[str, str]] = []
    for column in POSICAO_COLUMNS + [c for c in MOVIMENTO_COLUMNS if c not in POSICAO_COLUMNS]:
        motivo = CAMPOS_SEM_ORIGEM.get(column) or sem_config.get(column)
        rows.append(
            {
                "CAMPO": column,
                "ESTADO": "vazio" if motivo else ("preenchido" if origens.get(column) else "vazio"),
                "ORIGEM": origens.get(column, ""),
                "NOTA": motivo or "",
            }
        )
    for codigo, motivo in CODIGOS_SEM_ORIGEM.items():
        rows.append(
            {
                "CAMPO": f"TIPO_MOVIMENTO = {codigo}",
                "ESTADO": "não ocorre",
                "ORIGEM": "",
                "NOTA": motivo,
            }
        )
    return rows


# --------------------------------------------------------------- conferências

def explosion_checks(export: ExportSet, exclusive: dict[str, dict]) -> list[Check]:
    """A explosão não pode mudar o valor da carteira.

    Substituir a cota de um fundo pelas suas posições muda a *composição*, nunca
    o total. Se mudar, foi a carteira declarada que não bate com a cota.
    """
    checks: list[Check] = []
    base = export.posicoes.get(Explosion.NONE, [])
    total_base = _soma(base)

    for mode, linhas in export.posicoes.items():
        if mode == Explosion.NONE:
            continue
        checks.append(
            compare(
                f"Posições ({mode})",
                "total após explosão vs total das cotas",
                _soma(linhas),
                total_base,
                detail=(
                    f"{len(linhas)} linhas contra {len(base)}: explodir muda a composição, "
                    "nunca o total"
                ),
            )
        )
    return checks


def _soma(linhas: Sequence[dict]) -> Decimal:
    return sum(
        (
            linha["VLR_BRUTO_MOEDA_ORIGINAL"]
            for linha in linhas
            if isinstance(linha.get("VLR_BRUTO_MOEDA_ORIGINAL"), Decimal)
        ),
        Decimal("0"),
    )
