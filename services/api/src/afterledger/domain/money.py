"""Money in its own currency, formatted the way each country writes it. Arithmetic is Decimal."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from babel.numbers import format_decimal

# Locale whose digit grouping each currency is written in (Indian lakh grouping for INR).
_GROUPING_LOCALE = {"INR": "en_IN", "JPY": "ja_JP", "GBP": "en_GB", "USD": "en_US", "SGD": "en_SG",
                    "EUR": "de_DE", "AED": "en_AE", "EGP": "en_EG", "PHP": "en_PH"}
_SYMBOL = {"INR": "₹", "JPY": "¥", "GBP": "£", "USD": "$", "SGD": "S$", "EUR": "€", "AED": "AED ",
           "EGP": "EGP ", "PHP": "₱"}
_NO_MINOR = {"JPY"}


def quantize(amount: Decimal, currency: str = "INR") -> Decimal:
    exp = Decimal("1") if currency in _NO_MINOR else Decimal("0.01")
    return Decimal(amount).quantize(exp, ROUND_HALF_UP)


def format_amount(amount: Decimal | int | float | str, currency: str = "INR", *, symbol: bool = True,
                  decimals: bool = True) -> str:
    """₹6,41,870.00  £12,400.00  ¥1,234,000  S$18,240.55"""
    value = Decimal(str(amount))
    locale = _GROUPING_LOCALE.get(currency, "en_US")
    if currency in _NO_MINOR or not decimals:
        pattern = "#,##,##0" if currency == "INR" else "#,##0"
        text = format_decimal(value.quantize(Decimal("1"), ROUND_HALF_UP), format=pattern, locale=locale)
    else:
        pattern = "#,##,##0.00" if currency == "INR" else "#,##0.00"
        text = format_decimal(quantize(value, currency), format=pattern, locale=locale)
    if currency == "EUR" and locale.startswith("de"):
        return f"{text} €" if symbol else text
    return f"{_SYMBOL.get(currency, currency + ' ')}{text}" if symbol else text


