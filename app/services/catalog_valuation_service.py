"""Inventory valuations separated by the product reference cost currency."""
from decimal import Decimal

from sqlalchemy import case, func

from app.extensions import db
from app.models import Product
from app.utils.catalog_currency import CURRENCIES


def common_inventory_currency():
    rows = (db.session.query(Product.purchase_price_currency).filter(Product.is_active.is_(True),
            Product.purchase_price_currency.in_(CURRENCIES)).distinct().all())
    return rows[0][0] if len(rows) == 1 else None


def empty_valuation():
    return {"totals_by_currency": [], "unknown_currency_products": 0, "unknown_currency_units": 0}


def valuations():
    valid = Product.purchase_price_currency.in_(CURRENCIES)
    amount = func.sum(case((valid, Product.current_stock * Product.purchase_price), else_=None))
    rows = (db.session.query(Product.category_id, Product.purchase_price_currency,
            func.count(Product.id), func.sum(Product.current_stock), amount)
            .filter(Product.is_active.is_(True)).group_by(Product.category_id, Product.purchase_price_currency).all())
    overall, categories, totals = empty_valuation(), {}, {}
    for category_id, currency, count, units, value in rows:
        category = categories.setdefault(category_id, empty_valuation())
        if currency not in CURRENCIES:
            for entry in (overall, category):
                entry["unknown_currency_products"] += int(count)
                entry["unknown_currency_units"] += int(units or 0)
            continue
        value = Decimal(value or 0)
        category["totals_by_currency"].append({"currency": currency, "amount": f"{value:.2f}"})
        totals[currency] = totals.get(currency, Decimal(0)) + value
    overall["totals_by_currency"] = [{"currency": currency, "amount": f"{value:.2f}"}
                                      for currency, value in sorted(totals.items())]
    for category in categories.values():
        category["totals_by_currency"].sort(key=lambda item: item["currency"])
        for item in category["totals_by_currency"]:
            total = totals[item["currency"]]
            item["share"] = round(float(Decimal(item["amount"]) * 100 / total), 1) if total else 0
    return overall, categories


def scalar_valuation(valuation):
    totals = valuation["totals_by_currency"]
    if len(totals) != 1:
        return None, None
    return Decimal(totals[0]["amount"]), totals[0]["currency"]
