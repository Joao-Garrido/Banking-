"""Camada canónica: das tabelas do PDF para as vistas de que um family office precisa.

As vistas contêm **apenas linhas de dados**: os subtotais e totais que o
documento imprime ficam nas folhas em bruto e na folha 'Totais impressos'. Assim
somar uma coluna inteira nunca conta o mesmo dinheiro duas vezes.

A varredura (`tables.py`) devolve o documento tal como está desenhado — dezenas
de tabelas, cada uma com os nomes de coluna do banco. Isto é fiel, mas não é
utilizável: ninguém quer abrir catorze folhas para responder "quanto é que este
cliente tem em ações".

Este módulo mapeia cada tabela a um **domínio** (posições, movimentos,
mais-valias, transferências, alocação, sumário) e cada coluna do banco a um
**campo canónico**, produzindo tabelas únicas e consolidadas entre contas.

Duas regras que este módulo não quebra:

* nada é inventado — um campo que o documento não imprime fica vazio;
* nada se perde — cada linha canónica aponta para a tabela, página e conta de
  origem, e o total de cada domínio é conferido contra as tabelas que lhe deram
  origem (`dataset_checks`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Sequence

import re as _re

from .reconcile import TOLERANCE, Check, compare

# 'Total' isolado fecha um título; 'TOTAL MUTUAL FUNDS' fecha a tabela.
_PLAIN_TOTAL = _re.compile(r"^totals?$", _re.IGNORECASE)

__all__ = [
    "Domain",
    "Dataset",
    "DOMAIN_ORDER",
    "DOMAIN_TITLES",
    "classify_table",
    "build_datasets",
    "dataset_checks",
    "portfolio_checks",
    "account_summaries",
    "AccountSummary",
    "SUMMABLE_DOMAINS",
]

# Domínios cuja coluna de valor faz sentido somar. 'Alocação' e 'Sumário' são
# rubricas heterogéneas — um total ali seria um número sem significado.
SUMMABLE_DOMAINS = {
    "posicoes",
    "movimentos",
    "rendimento",
    "mais_valias",
    "transferencias",
}


# --------------------------------------------------------------------- domínios

class Domain:
    POSITIONS = "posicoes"
    TRANSACTIONS = "movimentos"
    INCOME = "rendimento"
    REALIZED = "mais_valias"
    TRANSFERS = "transferencias"
    ALLOCATION = "alocacao"
    OVERVIEW = "sumario"
    OTHER = "outras"


DOMAIN_ORDER = [
    Domain.OVERVIEW,
    Domain.POSITIONS,
    Domain.ALLOCATION,
    Domain.TRANSACTIONS,
    Domain.INCOME,
    Domain.REALIZED,
    Domain.TRANSFERS,
    Domain.OTHER,
]

DOMAIN_TITLES = {
    Domain.OVERVIEW: "Sumário por conta",
    Domain.POSITIONS: "Posições",
    Domain.ALLOCATION: "Alocação",
    Domain.TRANSACTIONS: "Movimentos",
    Domain.INCOME: "Rendimento",
    Domain.REALIZED: "Mais-valias realizadas",
    Domain.TRANSFERS: "Transferências e eventos",
    Domain.OTHER: "Outras tabelas",
}

# Título da tabela -> domínio. A ordem importa: a primeira regra que casa manda.
DOMAIN_PATTERNS: list[tuple[str, str]] = [
    (r"gain/?\(?loss\)?|missing\s+cost", Domain.REALIZED),
    (r"transfers?|corporate\s+actions", Domain.TRANSFERS),
    (r"activity|cash\s+flow", Domain.TRANSACTIONS),
    (r"allocation", Domain.ALLOCATION),
    (r"overview|change\s+in\s+value|balance\s+sheet|summary", Domain.OVERVIEW),
    (
        r"stocks|funds|holdings|bonds|securities|cash,|bank\s+deposit|money\s+market"
        r"|options|annuities|preferred|structured|fixed\s+income|treasury",
        Domain.POSITIONS,
    ),
]

# Movimentos que são rendimento. Só isto — uma compra não vira dividendo por
# aparecer na mesma tabela.
INCOME_TYPES = re.compile(
    r"^(dividend|interest|long\s+term\s+capital\s+gain|short\s+term\s+capital\s+gain"
    r"|capital\s+gain|income)\b",
    re.IGNORECASE,
)
# 'Dividend Reinvestment' é a aplicação do dividendo, não rendimento novo:
# contá-lo duplicava o rendimento do período.
INCOME_EXCLUDE = re.compile(r"reinvestment", re.IGNORECASE)

# Tipos de movimento que o statement imprime no início da descrição.
ACTIVITY_TYPES = [
    "Dividend Reinvestment",
    "Automatic Redemption",
    "Cash Transfer",
    "Bought",
    "Sold",
    "Long Term Capital Gain",
    "Short Term Capital Gain",
    "Capital Gain Reinvestment",
    "Transfer into Account",
    "Transfer out of Account",
    "Funds Received",
    "Funds Transferred",
    "Foreign Tax Withheld",
    "Automatic Investment",
    "Dividend",
    "Interest",
    "Deposit",
    "Withdrawal",
    "Purchase",
    "Sale",
    "Redemption",
    "Exchange",
    "Fee",
    "Service Charge",
    "Journal",
    "Adjustment",
]
_ACTIVITY_RE = re.compile(
    r"^(" + "|".join(re.escape(t) for t in ACTIVITY_TYPES) + r")\b", re.IGNORECASE
)

_SYMBOL = re.compile(r"\(([A-Z0-9./]{1,8})\)\s*$")


# ------------------------------------------------------------ campos canónicos

# Cada campo aponta para os nomes de coluna do banco que o representam. O
# primeiro que existir na tabela ganha; nenhum existir deixa o campo vazio.
FIELD_SOURCES: dict[str, list[str]] = {
    "quantidade": [r"^quantity$", r"quantity"],
    "preco_unitario": [r"^share_price", r"price"],
    "custo_unitario": [r"^unit_cost", r"unit_cost"],
    "custo_total": [r"^total_cost$", r"orig_adj_total_cost", r"total_cost"],
    "valor_mercado": [r"^market_value$", r"market_value"],
    "mais_valia_nao_realizada": [r"^unrealized_gain_loss$", r"unrealized_gain_loss"],
    "rendimento_anual_estimado": [r"est_ann_income"],
    "yield_pct": [r"^current_yield", r"yield"],
    "juros_corridos": [r"accrued_interest"],
    "valor": [r"^amount$", r"credits_debits", r"^amount", r"pending_credits_debits"],
    "receita_venda": [r"sales_proceeds", r"proceeds"],
    "mais_valia_realizada": [r"realized_gain_loss"],
}

POSITION_FIELDS = [
    "conta",
    "designacao_conta",
    "classe_ativo",
    "titulo",
    "simbolo",
    "data_compra",
    "quantidade",
    "custo_unitario",
    "preco_unitario",
    "custo_total",
    "valor_mercado",
    "mais_valia_nao_realizada",
    "rendimento_anual_estimado",
    "yield_pct",
    "peso_pct",
    "pagina",
    "tabela_origem",
]

TRANSACTION_FIELDS = [
    "conta",
    "designacao_conta",
    "data",
    "tipo",
    "descricao",
    "simbolo",
    "quantidade",
    "preco_unitario",
    "valor",
    "tabela",
    "pagina",
    "tabela_origem",
]

REALIZED_FIELDS = [
    "conta",
    "designacao_conta",
    "prazo",
    "titulo",
    "simbolo",
    "data_compra",
    "data_venda",
    "quantidade",
    "receita_venda",
    "custo_total",
    "mais_valia_realizada",
    "pagina",
    "tabela_origem",
]

TRANSFER_FIELDS = [
    "conta",
    "designacao_conta",
    "data",
    "tipo",
    "descricao",
    "quantidade",
    "valor",
    "pagina",
    "tabela_origem",
]

ALLOCATION_FIELDS = [
    "conta",
    "designacao_conta",
    "rubrica",
    "valor",
    "peso_pct",
    "pagina",
    "tabela_origem",
]

OVERVIEW_FIELDS = [
    "conta",
    "designacao_conta",
    "rubrica",
    "valor",
    "pagina",
    "tabela_origem",
]

DOMAIN_FIELDS = {
    Domain.POSITIONS: POSITION_FIELDS,
    Domain.TRANSACTIONS: TRANSACTION_FIELDS,
    Domain.INCOME: TRANSACTION_FIELDS,
    Domain.REALIZED: REALIZED_FIELDS,
    Domain.TRANSFERS: TRANSFER_FIELDS,
    Domain.ALLOCATION: ALLOCATION_FIELDS,
    Domain.OVERVIEW: OVERVIEW_FIELDS,
}

# Campos que somam dinheiro, por domínio — usados na conferência e nos totais.
DOMAIN_TOTAL_FIELD = {
    Domain.POSITIONS: "valor_mercado",
    Domain.TRANSACTIONS: "valor",
    Domain.INCOME: "valor",
    Domain.REALIZED: "mais_valia_realizada",
    Domain.TRANSFERS: "valor",
    Domain.ALLOCATION: "valor",
    Domain.OVERVIEW: "valor",
}


# Nome legível e formato de cada campo canónico. O Excel não deve mostrar
# 'valor_mercado' a quem vai ler o relatório.
FIELD_LABELS = {
    "conta": "Conta",
    "designacao_conta": "Designação",
    "classe_ativo": "Classe de ativo",
    "titulo": "Título",
    "simbolo": "Símbolo",
    "data_compra": "Data de compra",
    "data": "Data",
    "data_venda": "Data de venda",
    "prazo": "Prazo",
    "tipo": "Tipo",
    "descricao": "Descrição",
    "rubrica": "Rubrica",
    "quantidade": "Quantidade",
    "custo_unitario": "Custo unitário",
    "preco_unitario": "Preço",
    "custo_total": "Custo total",
    "valor_mercado": "Valor de mercado",
    "mais_valia_nao_realizada": "Mais-valia potencial",
    "mais_valia_realizada": "Mais-valia realizada",
    "receita_venda": "Receita da venda",
    "rendimento_anual_estimado": "Rend. anual estimado",
    "yield_pct": "Yield %",
    "peso_pct": "Peso %",
    "valor": "Valor",
    "juros_corridos": "Juros corridos",
    "tipo_linha": "Tipo de linha",
    "tabela": "Tabela",
    "pagina": "Pág.",
    "tabela_origem": "Tabela de origem",
}

MONEY_FIELDS = {
    "custo_unitario", "preco_unitario", "custo_total", "valor_mercado",
    "mais_valia_nao_realizada", "mais_valia_realizada", "receita_venda",
    "rendimento_anual_estimado", "valor", "juros_corridos",
}
QUANTITY_FIELDS = {"quantidade"}
PERCENT_FIELDS = {"yield_pct", "peso_pct"}
DATE_FIELDS = {"data", "data_compra", "data_venda"}

FIELD_WIDTHS = {
    "titulo": 44,
    "descricao": 56,
    "rubrica": 40,
    "classe_ativo": 34,
    "designacao_conta": 26,
    "tabela": 30,
    "tabela_origem": 30,
    "conta": 17,
    "tipo": 22,
    "pagina": 7,
    "tipo_linha": 12,
}


@dataclass
class Dataset:
    domain: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    # True quando as posições foram reduzidas a uma linha por título, usando o
    # 'Total' impresso pelo banco em vez da soma dos lotes.
    collapsed: bool = False

    @property
    def title(self) -> str:
        return DOMAIN_TITLES[self.domain]

    @property
    def fields(self) -> list[str]:
        return DOMAIN_FIELDS.get(self.domain, [])

    @property
    def total_field(self) -> str | None:
        return DOMAIN_TOTAL_FIELD.get(self.domain)

    @property
    def data_rows(self) -> list[dict[str, Any]]:
        return [row for row in self.rows if row.get("tipo_linha") == "data"]

    def total(self, account: str | None = None) -> Decimal:
        """Soma das linhas de dados. Totais e subtotais do documento ficam de
        fora — somá-los outra vez contava o mesmo dinheiro duas vezes."""
        field_name = self.total_field
        if not field_name:
            return Decimal("0")
        return sum(
            (
                row[field_name]
                for row in self.data_rows
                if isinstance(row.get(field_name), Decimal)
                and (account is None or row.get("conta") == account)
            ),
            Decimal("0"),
        )


# ------------------------------------------------------------------ utilitários

def classify_table(title: str) -> str:
    lowered = (title or "").lower()
    for pattern, domain in DOMAIN_PATTERNS:
        if re.search(pattern, lowered):
            return domain
    return Domain.OTHER


def _column_for(field_name: str, available: Sequence[str]) -> str | None:
    for pattern in FIELD_SOURCES.get(field_name, []):
        for name in available:
            if re.search(pattern, name):
                return name
    return None


def _mapping(fields: Iterable[str], available: Sequence[str]) -> dict[str, str]:
    return {
        field_name: column
        for field_name in fields
        if (column := _column_for(field_name, available))
    }


def _symbol_of(text: str) -> str | None:
    match = _SYMBOL.search(text.strip())
    return match.group(1) if match else None


# Grafia canónica do tipo, independente de como o banco a escreveu ('DIVIDEND',
# 'Dividend'): quem filtra a coluna quer um valor só por tipo.
_CANONICAL_TYPE = {t.lower(): t for t in ACTIVITY_TYPES}


def _activity_type(description: str) -> str | None:
    match = _ACTIVITY_RE.match(description.strip())
    if not match:
        return None
    return _CANONICAL_TYPE.get(match.group(1).lower(), match.group(1))


def _without_type(description: str) -> str:
    """Descrição sem o prefixo do tipo — o tipo já tem coluna própria."""
    match = _ACTIVITY_RE.match(description.strip())
    return description[match.end():].strip() if match else description.strip()


def _strip_symbol(text: str) -> str:
    return _SYMBOL.sub("", text.strip()).strip()


# ----------------------------------------------------------------- construção

def build_datasets(
    tables: Sequence, accounts: Sequence[dict], *, collapse_lots: bool = True
) -> dict[str, Dataset]:
    """Constrói as vistas canónicas a partir das tabelas extraídas.

    collapse_lots=True (o normal) dá uma linha por título; False mantém o
    detalhe lote a lote, para quem precisa de base de custo por lote.
    """
    labels = {a["id"]: (a.get("label") or "") for a in accounts}
    datasets = {domain: Dataset(domain=domain) for domain in DOMAIN_ORDER}

    for result in tables:
        domain = classify_table(result.spec.title)
        dataset = datasets[domain]
        dataset.sources.append(result.spec.title)

        builder = _BUILDERS.get(domain)
        if builder is None:
            continue
        if domain == Domain.POSITIONS:
            builder(result, labels, dataset, collapse_lots)
        else:
            builder(result, labels, dataset)

    datasets[Domain.POSITIONS].collapsed = collapse_lots
    _add_weights(datasets[Domain.POSITIONS])
    datasets[Domain.INCOME] = _income_from(datasets[Domain.TRANSACTIONS])
    return datasets


def _base_row(result, labels: dict[str, str], row: dict) -> dict[str, Any]:
    account = row.get("account")
    return {
        "conta": account,
        "designacao_conta": labels.get(account, ""),
        "pagina": row.get("source_page"),
        "tabela_origem": result.spec.title,
    }


def _numbers(row: dict, mapping: dict[str, str], fields: Iterable[str]) -> dict[str, Any]:
    return {name: row.get(mapping[name]) for name in fields if name in mapping}


def _collapse_lots(result) -> list[dict]:
    """Uma linha por título, não por lote.

    O statement imprime cada posição lote a lote e fecha o título com uma linha
    'Total'. Para uma carteira, o que interessa é o título — e o 'Total' que o
    banco imprime é melhor do que a soma que nós faríamos, porque é o número
    contra o qual o cliente vai conferir. Onde não há 'Total' (títulos de lote
    único), a própria linha do título é a posição.

    A soma dos lotes contra esse 'Total' continua a ser conferida em
    `tables.table_checks`: colapsar aqui não faz perder a prova.
    """
    por_grupo: dict[str, list[dict]] = {}
    ordem: list[str] = []
    for row in result.rows:
        if row["row_type"] not in ("data", "total"):
            continue
        grupo = row.get("group") or row.get("label") or ""
        if grupo not in por_grupo:
            por_grupo[grupo] = []
            ordem.append(grupo)
        por_grupo[grupo].append(row)

    colapsadas: list[dict] = []
    for grupo in ordem:
        linhas = por_grupo[grupo]
        total = next(
            (r for r in linhas if r["row_type"] == "total" and _PLAIN_TOTAL.match(r["label"].strip())),
            None,
        )
        if total is not None:
            # A linha 'Total' não traz a descrição do título: vem do grupo.
            colapsada = dict(total)
            colapsada["row_type"] = "data"
            colapsada["label"] = grupo
            colapsada["description"] = grupo
            # Data de compra deixa de fazer sentido: são vários lotes.
            colapsada["date"] = None
            colapsada["dates"] = []
            colapsadas.append(colapsada)
            continue
        colapsadas.extend(r for r in linhas if r["row_type"] == "data")

    return colapsadas


def _build_positions(result, labels, dataset: Dataset, collapse_lots: bool = True) -> None:
    columns = [c["name"] for c in result.spec.columns]
    mapping = _mapping(POSITION_FIELDS, columns)
    linhas = _collapse_lots(result) if collapse_lots else result.rows

    for row in linhas:
        if row["row_type"] != "data":
            continue
        title = row.get("group") or row.get("description") or ""
        entry = _base_row(result, labels, row)
        entry.update(
            {
                "classe_ativo": result.spec.title,
                "titulo": _strip_symbol(title),
                "simbolo": _symbol_of(title),
                "data_compra": row.get("date"),
                "tipo_linha": row["row_type"],
            }
        )
        entry.update(_numbers(row, mapping, POSITION_FIELDS))
        dataset.rows.append(entry)


def _build_transactions(result, labels, dataset: Dataset) -> None:
    columns = [c["name"] for c in result.spec.columns]
    mapping = _mapping(TRANSACTION_FIELDS, columns)

    for row in result.rows:
        if row["row_type"] != "data":
            continue
        description = row.get("description") or ""
        entry = _base_row(result, labels, row)
        entry.update(
            {
                "data": row.get("date"),
                "tipo": _activity_type(description),
                "descricao": _without_type(description),
                "simbolo": _symbol_of(description),
                "tabela": result.spec.title,
                "tipo_linha": row["row_type"],
            }
        )
        entry.update(_numbers(row, mapping, TRANSACTION_FIELDS))
        dataset.rows.append(entry)


def _build_realized(result, labels, dataset: Dataset) -> None:
    columns = [c["name"] for c in result.spec.columns]
    mapping = _mapping(REALIZED_FIELDS, columns)
    prazo = (
        "Longo prazo"
        if "long" in result.spec.title.lower()
        else "Curto prazo"
        if "short" in result.spec.title.lower()
        else result.spec.title
    )

    for row in result.rows:
        if row["row_type"] != "data":
            continue
        title = row.get("group") or row.get("description") or ""
        entry = _base_row(result, labels, row)
        entry.update(
            {
                "prazo": prazo,
                "titulo": _strip_symbol(title),
                "simbolo": _symbol_of(title),
                # As tabelas de mais-valias imprimem data de compra e de venda,
                # por esta ordem, na mesma célula de descrição.
                "data_compra": (row.get("dates") or [None])[0],
                "data_venda": (row.get("dates") or [None])[-1],
                "tipo_linha": row["row_type"],
            }
        )
        entry.update(_numbers(row, mapping, REALIZED_FIELDS))
        dataset.rows.append(entry)


def _build_transfers(result, labels, dataset: Dataset) -> None:
    columns = [c["name"] for c in result.spec.columns]
    mapping = _mapping(TRANSFER_FIELDS, columns)

    for row in result.rows:
        if row["row_type"] != "data":
            continue
        description = row.get("description") or ""
        entry = _base_row(result, labels, row)
        entry.update(
            {
                "data": row.get("date"),
                "tipo": _activity_type(description) or result.spec.title.title(),
                "descricao": _without_type(description),
                "tipo_linha": row["row_type"],
            }
        )
        entry.update(_numbers(row, mapping, TRANSFER_FIELDS))
        dataset.rows.append(entry)


def _build_simple(fields: list[str]):
    def builder(result, labels, dataset: Dataset) -> None:
        columns = [c["name"] for c in result.spec.columns]
        mapping = _mapping(fields, columns)
        # Nestes quadros a coluna de valor é a que a varredura já elegeu.
        value_column = result.spec.amount_column

        for row in result.rows:
            if row["row_type"] != "data":
                continue
            entry = _base_row(result, labels, row)
            entry.update(
                {
                    "rubrica": row.get("description") or "",
                    "valor": row.get(value_column) if value_column else None,
                    "tipo_linha": row["row_type"],
                }
            )
            entry.update({k: v for k, v in _numbers(row, mapping, fields).items() if v is not None})
            dataset.rows.append(entry)

    return builder


_BUILDERS = {
    Domain.POSITIONS: _build_positions,
    Domain.TRANSACTIONS: _build_transactions,
    Domain.REALIZED: _build_realized,
    Domain.TRANSFERS: _build_transfers,
    Domain.ALLOCATION: _build_simple(ALLOCATION_FIELDS),
    Domain.OVERVIEW: _build_simple(OVERVIEW_FIELDS),
}


def _add_weights(dataset: Dataset) -> None:
    """Peso de cada posição no total da sua conta. Calculado, não impresso."""
    totals: dict[str | None, Decimal] = {}
    for row in dataset.rows:
        if row.get("tipo_linha") != "data":
            continue
        value = row.get("valor_mercado")
        if isinstance(value, Decimal):
            totals[row.get("conta")] = totals.get(row.get("conta"), Decimal("0")) + value

    for row in dataset.rows:
        total = totals.get(row.get("conta"))
        value = row.get("valor_mercado")
        if row.get("tipo_linha") == "data" and total and isinstance(value, Decimal):
            row["peso_pct"] = (value / total * 100).quantize(Decimal("0.01"))


def _income_from(transactions: Dataset) -> Dataset:
    """Rendimento = movimentos cujo tipo é dividendo, juro ou mais-valia distribuída.

    Derivado dos movimentos, não de uma tabela própria: é o que o statement dá.
    Reinvestimentos ficam de fora — são a aplicação do rendimento, não rendimento.
    """
    income = Dataset(domain=Domain.INCOME, sources=list(transactions.sources))
    for row in transactions.rows:
        if row.get("tipo_linha") != "data":
            continue
        tipo = row.get("tipo") or ""
        if INCOME_TYPES.match(tipo) and not INCOME_EXCLUDE.search(tipo):
            income.rows.append(dict(row))
    return income


# --------------------------------------------------------------- conferências

def _mapped_column(result, field_name: str) -> str | None:
    return _column_for(field_name, [c["name"] for c in result.spec.columns])


def dataset_checks(datasets: dict[str, Dataset], tables: Sequence) -> list[Check]:
    """A camada canónica não pode perder nem duplicar dinheiro.

    Para cada domínio, a soma das linhas de dados é comparada com a soma da
    *mesma* coluna nas tabelas de origem. Se um mapeamento de coluna estiver
    errado, é aqui que aparece — e uma tabela cuja coluna de valor não tem
    equivalente canónico é dita em voz alta, não ignorada.
    """
    checks: list[Check] = []
    by_domain: dict[str, list] = {}
    for result in tables:
        by_domain.setdefault(classify_table(result.spec.title), []).append(result)

    for domain in DOMAIN_ORDER:
        dataset = datasets.get(domain)
        if dataset is None or domain in (Domain.INCOME, Domain.OTHER) or not dataset.rows:
            continue
        field_name = dataset.total_field
        if not field_name:
            continue

        origin = Decimal("0")
        sem_coluna: list[str] = []
        for result in by_domain.get(domain, []):
            column = (
                _mapped_column(result, field_name)
                if domain not in (Domain.ALLOCATION, Domain.OVERVIEW)
                else result.spec.amount_column
            )
            if not column:
                if any(row["row_type"] == "data" for row in result.rows):
                    sem_coluna.append(result.spec.title)
                continue
            origin += sum(
                (
                    row[column]
                    for row in result.rows
                    if row["row_type"] == "data" and isinstance(row.get(column), Decimal)
                ),
                Decimal("0"),
            )

        data_rows = [row for row in dataset.rows if row.get("tipo_linha") == "data"]
        canonical = sum(
            (row[field_name] for row in data_rows if isinstance(row.get(field_name), Decimal)),
            Decimal("0"),
        )

        detail = (
            f"{len(data_rows)} linhas de {len(by_domain.get(domain, []))} tabela(s) "
            f"na coluna {field_name!r}"
        )
        # Com as posições reduzidas a uma linha por título, a vista soma os
        # 'Total' impressos e a origem soma os lotes. O statement arredonda cada
        # um ao cêntimo, por isso a folga é de um cêntimo por linha de origem —
        # a mesma regra usada em todas as outras conferências.
        tolerancia = TOLERANCE
        if domain == Domain.POSITIONS and dataset.collapsed:
            origem_linhas = sum(
                len([r for r in result.rows if r["row_type"] == "data"])
                for result in by_domain.get(domain, [])
            )
            tolerancia = Decimal("0.01") * max(origem_linhas, 1)
            detail += (
                " — a vista usa o 'Total' que o banco imprime para cada título, "
                "a origem soma os lotes"
            )

        checks.append(
            compare(
                dataset.title,
                "vista canónica vs tabelas de origem",
                canonical,
                origin,
                detail=detail,
                tolerance=tolerancia,
            )
        )

        for title in sem_coluna:
            checks.append(
                Check(
                    section=dataset.title,
                    name="coluna de valor sem equivalente",
                    passed=False,
                    extracted=title,
                    expected=field_name,
                    detail=(
                        f"a tabela {title!r} não tem coluna que corresponda a {field_name!r}: "
                        "as suas linhas aparecem na vista sem valor, e ficam fora do total"
                    ),
                    severity="aviso",
                )
            )

    return checks


# Rótulos do total de uma conta, por ordem de preferência.
ACCOUNT_TOTAL_LABELS = [
    r"^total\s+value\b",
    r"^total\s+market\s+value\b",
]


@dataclass
class AccountSummary:
    """O retrato de uma conta: o que está lá, e se fecha com o que o banco diz."""

    id: str
    label: str
    pages: list[int]
    positions: int
    market_value: Decimal
    unsettled: Decimal
    unsettled_rows: int
    printed_total: Decimal | None

    @property
    def reconciles(self) -> bool | None:
        if self.printed_total is None:
            return None
        slack = Decimal("0.01") * max(self.positions, 1)
        return abs(self.market_value + self.unsettled - self.printed_total) <= slack


def account_summaries(
    datasets: dict[str, Dataset], tables: Sequence, accounts: Sequence[dict]
) -> list[AccountSummary]:
    positions = datasets.get(Domain.POSITIONS)
    transactions = datasets.get(Domain.TRANSACTIONS)
    summaries: list[AccountSummary] = []

    for account in accounts:
        account_id = account["id"]
        rows = [
            row for row in (positions.data_rows if positions else [])
            if row.get("conta") == account_id
        ]
        unsettled_rows = [
            row
            for row in (transactions.data_rows if transactions else [])
            if row.get("conta") == account_id and "unsettled" in (row.get("tabela") or "").lower()
        ]
        summaries.append(
            AccountSummary(
                id=account_id,
                label=account.get("label") or "",
                pages=list(account.get("pages") or []),
                positions=len(rows),
                market_value=sum(
                    (r["valor_mercado"] for r in rows if isinstance(r.get("valor_mercado"), Decimal)),
                    Decimal("0"),
                ),
                unsettled=sum(
                    (r["valor"] for r in unsettled_rows if isinstance(r.get("valor"), Decimal)),
                    Decimal("0"),
                ),
                unsettled_rows=len(unsettled_rows),
                printed_total=_printed_account_total(tables, account_id),
            )
        )
    return summaries


def portfolio_checks(
    datasets: dict[str, Dataset], tables: Sequence, accounts: Sequence[dict]
) -> list[Check]:
    """A prova de topo: as posições extraídas somam o valor da conta.

    Numa conta com compras ainda por liquidar, o valor das posições excede o
    total impresso exatamente nesse montante — o statement já conta os títulos
    comprados, e a contrapartida em dinheiro só entra na liquidação. Por isso a
    conferência é `posições + por liquidar == total da conta`.
    """
    checks: list[Check] = []
    for summary in account_summaries(datasets, tables, accounts):
        if summary.printed_total is None:
            continue
        detail = f"{summary.positions} posições" + (
            f" + {summary.unsettled_rows} operações por liquidar ({summary.unsettled})"
            if summary.unsettled_rows
            else ""
        )
        checks.append(
            compare(
                DOMAIN_TITLES[Domain.POSITIONS],
                f"conta {summary.id}: posições vs total impresso",
                summary.market_value + summary.unsettled,
                summary.printed_total,
                detail=detail,
                tolerance=Decimal("0.01") * max(summary.positions, 1),
            )
        )
    return checks


def _printed_account_total(tables: Sequence, account_id: str) -> Decimal | None:
    for pattern in ACCOUNT_TOTAL_LABELS:
        regex = re.compile(pattern, re.IGNORECASE)
        for result in tables:
            if result.spec.account != account_id:
                continue
            column = result.spec.amount_column
            for row in result.rows:
                if row["row_type"] != "total" or not regex.match(row["label"].strip()):
                    continue
                value = row.get(column)
                if isinstance(value, Decimal):
                    return value
    return None
