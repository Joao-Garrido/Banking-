"""Normalização de valores extraídos do PDF.

Regra que governa este módulo: nunca devolver um default silencioso.
Um valor irreconhecível levanta NormalizeError — é melhor rebentar do que
somar um zero inventado.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

__all__ = [
    "NormalizeError",
    "strip_marker",
    "looks_like_amount_loose",
    "parse_amount_loose",
    "parse_amount",
    "parse_quantity",
    "parse_percent",
    "parse_date",
    "detect_currency",
    "is_blank",
    "looks_like_amount",
]


class NormalizeError(ValueError):
    """Valor presente no documento que não sabemos interpretar."""


# Células que o statement imprime como "sem valor". Só são aceites onde o
# chamador declarar explicitamente allow_blank=True.
BLANK_TOKENS = {"", "-", "--", "---", "—", "–", "n/a", "na", "n.a.", "none"}

# Símbolos de moeda que aparecem colados ao número.
_CURRENCY_SYMBOLS = {
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "CHF": "CHF",
}

_ISO_CURRENCY = re.compile(r"^[A-Z]{3}$")

# Formato US: vírgula = milhar, ponto = decimal.
_AMOUNT_BODY = re.compile(r"^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$")

_DATE_FORMATS = (
    ("%m/%d/%Y", re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")),
    ("%m/%d/%y", re.compile(r"^\d{1,2}/\d{1,2}/\d{2}$")),
    ("%m-%d-%Y", re.compile(r"^\d{1,2}-\d{1,2}-\d{4}$")),
    ("%b %d, %Y", re.compile(r"^[A-Za-z]{3} \d{1,2}, \d{4}$")),
    ("%B %d, %Y", re.compile(r"^[A-Za-z]{4,9} \d{1,2}, \d{4}$")),
    ("%d-%b-%Y", re.compile(r"^\d{1,2}-[A-Za-z]{3}-\d{4}$")),
    ("%Y-%m-%d", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
)

# Data sem ano — o statement escreve '12/8' nas tabelas de atividade porque o
# ano está no cabeçalho do período. Só é interpretável com o ano fornecido.
_SHORT_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})$")

# Um registo novo começa quase sempre por uma data. Usado por lines.py para
# distinguir início de registo de continuação de descrição.
DATE_PATTERN = re.compile(
    r"^(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r"|[A-Za-z]{3,9} \d{1,2}, \d{4}"
    r"|\d{1,2}-[A-Za-z]{3}-\d{4}"
    r"|\d{4}-\d{2}-\d{2})$"
)


def is_blank(text: str | None) -> bool:
    """True se a célula está vazia no sentido do documento."""
    if text is None:
        return True
    return text.strip().lower() in BLANK_TOKENS


def _strip_currency(text: str) -> tuple[str, str | None]:
    """Remove símbolo/código de moeda do início ou fim, devolve (resto, moeda)."""
    currency = None
    s = text.strip()
    for symbol, code in sorted(_CURRENCY_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        if s.startswith(symbol):
            currency = code
            s = s[len(symbol):].strip()
            break
        if s.endswith(symbol) and symbol not in {"$"}:
            currency = code
            s = s[: -len(symbol)].strip()
            break
    else:
        head, _, tail = s.partition(" ")
        if _ISO_CURRENCY.match(head) and tail:
            currency, s = head, tail.strip()
        else:
            head2, _, tail2 = s.rpartition(" ")
            if _ISO_CURRENCY.match(tail2) and head2:
                currency, s = tail2, head2.strip()
    return s, currency


def _to_decimal(body: str, *, raw: str) -> Decimal:
    if not _AMOUNT_BODY.match(body):
        raise NormalizeError(f"valor não reconhecido: {raw!r}")
    try:
        return Decimal(body.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - _AMOUNT_BODY já filtra
        raise NormalizeError(f"valor não reconhecido: {raw!r}") from exc


def parse_amount(
    text: str | None,
    *,
    allow_blank: bool = False,
    with_currency: bool = False,
):
    """`(1,234.56)` -> Decimal('-1234.56').

    Parênteses são negativo — esta é a regra que mais custa dinheiro quando
    falha, por isso é a primeira coisa que o módulo trata.

    allow_blank=True devolve None para células vazias ('-', '--', 'N/A').
    with_currency=True devolve (valor, moeda|None).
    """
    if is_blank(text):
        if allow_blank:
            return (None, None) if with_currency else None
        raise NormalizeError(f"célula vazia onde era esperado um valor: {text!r}")

    raw = str(text)
    s = raw.strip()

    # A moeda pode estar fora ou dentro dos parênteses: '$(1,234.56)' e
    # '($1,234.56)' são o mesmo número.
    s, currency = _strip_currency(s)

    negative = False
    for open_char, close_char in (("(", ")"), ("[", "]")):
        if s.startswith(open_char) and s.endswith(close_char):
            negative = True
            s = s[1:-1].strip()
            break

    if not currency:
        s, currency = _strip_currency(s)

    # Sinal explícito, prefixado ou sufixado (alguns relatórios usam 1,234.56-).
    if s.startswith("-") or s.startswith("−"):
        if negative:
            raise NormalizeError(f"sinal negativo duplicado: {raw!r}")
        negative = True
        s = s[1:].strip()
    elif s.endswith("-"):
        if negative:
            raise NormalizeError(f"sinal negativo duplicado: {raw!r}")
        negative = True
        s = s[:-1].strip()
    elif s.startswith("+"):
        s = s[1:].strip()

    if not currency:
        s, currency = _strip_currency(s)

    value = _to_decimal(s, raw=raw)
    if negative:
        value = -value

    return (value, currency) if with_currency else value


def parse_quantity(text: str | None, *, allow_blank: bool = False) -> Decimal | None:
    """Quantidade de unidades/ações. Mesma gramática do valor, sem moeda."""
    if is_blank(text):
        if allow_blank:
            return None
        raise NormalizeError(f"célula vazia onde era esperada uma quantidade: {text!r}")
    value, currency = parse_amount(text, with_currency=True)
    if currency:
        raise NormalizeError(f"quantidade com símbolo de moeda: {text!r}")
    return value


def parse_percent(text: str | None, *, allow_blank: bool = False) -> Decimal | None:
    """'12.5%' -> Decimal('12.5'). Não divide por 100 — mantém a escala impressa."""
    if is_blank(text):
        if allow_blank:
            return None
        raise NormalizeError(f"célula vazia onde era esperada uma percentagem: {text!r}")
    s = str(text).strip()
    if not s.endswith("%"):
        raise NormalizeError(f"percentagem sem '%': {text!r}")
    return parse_amount(s[:-1].strip())


def parse_date(
    text: str | None,
    *,
    allow_blank: bool = False,
    year: int | None = None,
) -> date | None:
    """Datas do statement. Formato desconhecido levanta, não adivinha.

    '12/8' (sem ano) só é aceite com `year` — o ano vem do período do statement,
    declarado no layout. Sem ele, adivinhar o ano seria inventar dados.
    """
    from datetime import datetime

    if is_blank(text):
        if allow_blank:
            return None
        raise NormalizeError(f"célula vazia onde era esperada uma data: {text!r}")
    s = str(text).strip()

    short = _SHORT_DATE.match(s)
    if short:
        if year is None:
            raise NormalizeError(
                f"data sem ano: {text!r} — declara 'statement_year' no layout.json "
                "para que possa ser interpretada"
            )
        month, day = int(short.group(1)), int(short.group(2))
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise NormalizeError(f"data inválida: {text!r} com ano {year}") from exc

    for fmt, pattern in _DATE_FORMATS:
        if pattern.match(s):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    raise NormalizeError(f"data não reconhecida: {text!r}")


def detect_currency(text: str | None) -> str | None:
    """Moeda declarada numa célula ('USD', '$'), ou None se não houver."""
    if is_blank(text):
        return None
    s = str(text).strip().strip("()")
    if _ISO_CURRENCY.match(s):
        return s
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if s == symbol:
            return code
    _, currency = _strip_currency(s)
    return currency


# Marcadores colados ao número nas tabelas de posições: '$5,958.99ST', '(17.53)LT'.
_TRAILING_MARKER = re.compile(r"(?<=[\d)])\s*[A-Za-z]{1,2}$")


def strip_marker(text: str) -> tuple[str, str | None]:
    """Separa o marcador de curto/longo prazo colado ao valor."""
    match = _TRAILING_MARKER.search(text.strip())
    if not match:
        return text, None
    return text[: match.start()].strip(), match.group(0).strip()


def looks_like_amount_loose(text: str | None) -> bool:
    """looks_like_amount, tolerando o marcador colado ('$5,958.99ST')."""
    if looks_like_amount(text):
        return True
    if is_blank(text):
        return False
    body, marker = strip_marker(str(text))
    return bool(marker) and looks_like_amount(body)


def parse_amount_loose(text: str | None):
    """Devolve (valor, marcador). Levanta NormalizeError como o parse_amount."""
    if is_blank(text):
        raise NormalizeError(f"célula vazia onde era esperado um valor: {text!r}")
    try:
        return parse_amount(text), None
    except NormalizeError:
        body, marker = strip_marker(str(text))
        if not marker:
            raise
        return parse_amount(body), marker


def looks_like_amount(text: str | None) -> bool:
    """Predicado sem exceções — para heurísticas (mapper, deteção de colunas)."""
    if is_blank(text):
        return False
    try:
        parse_amount(text)
    except NormalizeError:
        return False
    return True
