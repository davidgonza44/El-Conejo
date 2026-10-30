"""Deterministic visual-only fixtures; never consult or mutate supplier persistence."""
from datetime import datetime
from decimal import Decimal

from app.utils.inventory_reference import SUPPLIERS
from app.services.supplier_query_service import filters, pagination


def records():
    rows = []
    for i in range(28):
        source = SUPPLIERS["table"]["rows"][i % 8]
        supplier_id = [1, 2, 4, 5, 3, 6, 7, 8][i] if i < 8 else i + 1
        rows.append(dict(id=supplier_id, tone=i % 8, name=source["name"] if i < 8 else f"Proveedor de referencia {i + 1:02d}",
            tax_id=source["rif"] if i < 8 else f"REF-{i + 1:04d}", contact_name=source["contact"], contact_title=source["role"],
            phone=source["phone"], email=source["email"], category=source["category"][0],
            is_active=i not in (3, 7, 15, 23), is_preferred=i < 8, address="Av. Principal, Centro Empresarial",
            city=("Valencia", "Caracas", "Maracay")[i % 3], state_province="Carabobo" if i % 3 == 0 else "",
            notes="Proveedor de referencia visual.", created_at="2024-01-01T12:00:00", updated_at="2024-05-31T12:00:00",
            total=source["total"].replace("$", "").replace(",", "").strip(), last=datetime.strptime(source["last"], "%d/%m/%Y").date().isoformat(),
            orders=2 if i < 8 else 0, pending=1 if i < 5 else 0, completed=1 if i < 5 else 2 if i < 8 else 0,
            avatar=source["avatar"] if not source["avatar"].startswith("svg:") else "CT", recent_orders=[]))
    for row in rows:
        row['totals_by_currency'] = [{'currency': 'USD', 'amount': row['total']}]
    return rows


def matches(row, f):
    if f["search"] and f["search"].casefold() not in " ".join(row[key] for key in ("name", "tax_id", "contact_name")).casefold():
        return False
    if any(f[key] and row[key] != f[key] for key in ("category", "city")):
        return False
    if f["status"] and row["is_active"] != (f["status"] == "active"):
        return False
    if f["preferred"] and row["is_preferred"] != (f["preferred"] == "yes"):
        return False
    if f["last_from"] and row["last"] < f["last_from"] or f["last_to"] and row["last"] > f["last_to"]:
        return False
    return matches_amount(row, f)


def matches_amount(row, f):
    amount = next((Decimal(value['amount']) for value in row['totals_by_currency'] if value['currency'] == f['currency']), None)
    if (f['min_total'] or f['max_total']) and amount is None:
        return False
    if f["min_total"] and amount < Decimal(f["min_total"]) or f["max_total"] and amount > Decimal(f["max_total"]):
        return False
    return True


def listing(params):
    f = filters(params)
    rows = [row for row in records() if matches(row, f)]
    # The unfiltered reference preserves the screenshot's deliberate row ordering.
    if params.get("sort"):
        key = f["sort"].split("_")[0]
        if key == "total":
            amounts = {r['id']: next((Decimal(value['amount']) for value in r['totals_by_currency']
                                      if value['currency'] == f['currency']), None) for r in rows}
            rows.sort(key=lambda r: (-(amounts[r['id']] or 0) if f['sort'].endswith('desc') else amounts[r['id']] or 0, r['id']))
            rows.sort(key=lambda r: amounts[r['id']] is None)
        else:
            rows.sort(key=lambda r: r[key].casefold(), reverse=f["sort"].endswith("desc"))
    p = pagination(params, len(rows))
    return {"available": True, "filters": f, "pagination": p, "all_items": rows,
            "items": rows[(p["page"] - 1) * p["per_page"]:p["page"] * p["per_page"]],
            "categories": ["Herramientas", "Materiales", "Construcción"], "cities": ["Valencia", "Caracas", "Maracay"],
            "kpis": dict(suppliers=28, active=24, inactive=4, preferred=8, total="248750.00",
                         totals_by_currency=[{'currency': 'USD', 'amount': '248750.00'}],
                         comparison="+15.6% vs. 01 abr. - 30 abr. 2024", orders=16, pending=5, completed=11)}


def detail(supplier_id):
    return next((row for row in records() if row["id"] == supplier_id), None)
