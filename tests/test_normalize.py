"""Fase 2 — o teste que existe porque parênteses lidos como positivo custam
duas vezes o valor da linha."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.normalize import (
    NormalizeError,
    detect_currency,
    is_blank,
    looks_like_amount,
    parse_amount,
    parse_date,
    parse_percent,
    parse_quantity,
)


class TestParenteses:
    """Parênteses são negativo. Não é opinião, é o formato do documento."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("(1,234.56)", "-1234.56"),
            ("(0.01)", "-0.01"),
            ("($1,234.56)", "-1234.56"),
            ("$(1,234.56)", "-1234.56"),
            ("(12,500.00)", "-12500.00"),
            ("(500)", "-500"),
        ],
    )
    def test_parenteses_sao_negativo(self, text, expected):
        assert parse_amount(text) == Decimal(expected)

    def test_o_mesmo_numero_com_e_sem_parenteses_difere_pelo_sinal(self):
        assert parse_amount("(1,234.56)") == -parse_amount("1,234.56")

    def test_parentese_meio_aberto_nao_e_silenciosamente_positivo(self):
        with pytest.raises(NormalizeError):
            parse_amount("(1,234.56")


class TestMilharEDecimal:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("1,234.56", "1234.56"),
            ("1,195,590.55", "1195590.55"),
            ("0.00", "0"),
            ("15,340.55", "15340.55"),
            ("500,000", "500000"),
            ("98.75", "98.75"),
            ("1,000", "1000"),
        ],
    )
    def test_formato_us(self, text, expected):
        assert parse_amount(text) == Decimal(expected)

    def test_precisao_nao_se_perde(self):
        assert str(parse_amount("0.10") + parse_amount("0.20")) == "0.30"

    def test_agrupamento_errado_nao_passa(self):
        with pytest.raises(NormalizeError):
            parse_amount("1,23.456")


class TestSinaisEMoeda:
    @pytest.mark.parametrize(
        "text, expected",
        [("-1,234.56", "-1234.56"), ("1,234.56-", "-1234.56"), ("+1,234.56", "1234.56")],
    )
    def test_sinais_explicitos(self, text, expected):
        assert parse_amount(text) == Decimal(expected)

    def test_sinal_duplicado_levanta(self):
        with pytest.raises(NormalizeError):
            parse_amount("(-1,234.56)")

    def test_moeda_e_devolvida_a_parte(self):
        assert parse_amount("$1,234.56", with_currency=True) == (Decimal("1234.56"), "USD")
        assert parse_amount("USD 1,234.56", with_currency=True) == (Decimal("1234.56"), "USD")
        assert parse_amount("1,234.56 EUR", with_currency=True) == (Decimal("1234.56"), "EUR")

    def test_deteta_moeda_numa_celula_dedicada(self):
        assert detect_currency("USD") == "USD"
        assert detect_currency("$") == "USD"
        assert detect_currency("(CHF)") == "CHF"
        assert detect_currency("banana") is None


class TestValorInvalido:
    """Nunca um default silencioso — é isto que torna o erro impossível de esconder."""

    @pytest.mark.parametrize("text", ["", "   ", "-", "--", "N/A", "n/a", "—"])
    def test_celula_vazia_levanta_por_omissao(self, text):
        with pytest.raises(NormalizeError):
            parse_amount(text)

    @pytest.mark.parametrize("text", ["", "-", "N/A"])
    def test_celula_vazia_so_passa_se_for_pedido(self, text):
        assert parse_amount(text, allow_blank=True) is None
        assert is_blank(text)

    @pytest.mark.parametrize("text", ["abc", "12abc", "1.2.3", "1,,234", "$", "()"])
    def test_lixo_levanta(self, text):
        with pytest.raises(NormalizeError):
            parse_amount(text)

    def test_predicado_nao_levanta(self):
        assert looks_like_amount("(1,234.56)")
        assert not looks_like_amount("abc")
        assert not looks_like_amount("")


class TestQuantidadeEPercentagem:
    def test_quantidade(self):
        assert parse_quantity("1,200") == Decimal("1200")
        assert parse_quantity("(500)") == Decimal("-500")

    def test_quantidade_com_moeda_e_suspeita(self):
        with pytest.raises(NormalizeError):
            parse_quantity("$1,200")

    def test_percentagem_mantem_a_escala_impressa(self):
        assert parse_percent("12.5%") == Decimal("12.5")
        assert parse_percent("(0.25)%") == Decimal("-0.25")

    def test_percentagem_sem_simbolo_levanta(self):
        with pytest.raises(NormalizeError):
            parse_percent("12.5")


class TestDatas:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("01/05/2026", date(2026, 1, 5)),
            ("1/5/26", date(2026, 1, 5)),
            ("Jan 5, 2026", date(2026, 1, 5)),
            ("January 5, 2026", date(2026, 1, 5)),
            ("5-Jan-2026", date(2026, 1, 5)),
            ("2026-01-05", date(2026, 1, 5)),
        ],
    )
    def test_formatos_aceites(self, text, expected):
        assert parse_date(text) == expected

    @pytest.mark.parametrize("text", ["05/01/26 12:00", "2026", "n/d", "13/45/2026"])
    def test_data_desconhecida_levanta(self, text):
        with pytest.raises(NormalizeError):
            parse_date(text)
