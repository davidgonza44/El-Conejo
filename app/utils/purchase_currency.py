"""Presentation only: never converts prices or assumes a historical currency."""
from decimal import Decimal


def format_amount(value, currency):
    if value is None:
        return "—"
    symbol = {"USD": "$", "VES": "Bs."}.get(currency, currency or "Moneda no registrada ·")
    return f"{symbol} {Decimal(str(value)):,.2f}"
