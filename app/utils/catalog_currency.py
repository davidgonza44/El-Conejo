"""Exact catalog denominations. Never infer, normalize or convert a currency."""
from app.services.exceptions import ValidationError

CURRENCIES = ("USD", "VES")
PRICE_FIELDS = ("sale_price", "purchase_price")


def require_currency(value, field):
    if not isinstance(value, str) or value not in CURRENCIES:
        raise ValidationError(f"El campo '{field}' requiere USD o VES, exactamente.")
    return value


def validate_price_pairs(data, *, creation=False):
    for price in PRICE_FIELDS:
        currency = price + "_currency"
        if creation or price in data or currency in data:
            if price not in data or currency not in data:
                raise ValidationError(f"Indique conjuntamente '{price}' y '{currency}'.")
            require_currency(data[currency], currency)
