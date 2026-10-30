"""Deterministic read-only purchase preview. Never imports or queries database models."""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN

NAMES = ["Ferremáx, C.A.", "Aceros del Centro, C.A.", "Materiales Premier, C.A.", "Construcción Total, C.A.",
         "Distribuidora Industrial, C.A.", "Suministros Ferreteros, C.A.", "Cemento Plus, C.A.", "Insumos del Norte, C.A."]
LABELS = {"pending": "Pendiente (flujo anterior)", "partial": "Parcialmente recibida", "completed": "Completada", "cancelled": "Cancelada"}
SUPPLIERS = [{"id": i + 1, "name": name, "tax_id": f"J-{30567894 + i}-1", "phone": "0412-5550100",
              "email": "contacto@example.invalid", "contact_name": "Contacto de referencia", "is_active": True} for i, name in enumerate(NAMES)]
PRODUCTS = [{"id": 1, "code": "REF-CEM-01", "name": "Cemento gris 42,5 kg", "unit_cost": "25.00"},
            {"id": 2, "code": "REF-ACE-02", "name": "Cabilla de acero 1/2 pulgada", "unit_cost": "40.00"}]


def orders():
    rows = []
    totals = ["58250", "46780", "31920", "22340", "12450", "18400", "8950", "9160"]
    units = [12, 8, 15, 6, 10, 9, 5, 7]
    for i in range(42):
        index = i % 8
        quantity = units[index]
        status = ["pending", "partial", "completed", "partial", "completed", "pending", "completed", "partial"][index]
        if i >= 8:
            status = "pending" if i < 13 else "partial" if i < 24 else "completed"
        received = quantity if status == "completed" else quantity // 2 if status == "partial" else 0
        total = Decimal(totals[index] if i < 8 else "14000" if i == 41 else "8000")
        cost = (total / quantity).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        final_cost = total - cost * (quantity - 1)
        number = 42 - i
        row = {"id": number, "order_number": f"OC-{number:06d}", "supplier": deepcopy(SUPPLIERS[index]),
               "creator": {"id": 1, "name": "Administrador"}, "ordered_at": f"2024-05-{max(1, 31-i):02d}T12:00:00",
               "estimated_delivery_date": ["2024-06-04", "2024-06-02", "2024-05-28", "2024-05-30", "2024-05-25", "2024-05-31", "2024-05-22", "2024-05-26"][index],
               "status": status, "status_label": LABELS[status], "notes": "Orden de referencia. Vista previa sin escrituras.",
               "currency": "USD", "subtotal": f"{total:.2f}", "total": f"{total:.2f}", "units": quantity, "line_count": 2,
               "received_units": received, "received_percent": int(received * 100 / quantity), "pending_units": quantity - received,
               "pending_amount": f"{max(0, quantity - 1 - received) * cost + (final_cost if received < quantity else 0):.2f}", "receptions": [],
               "items": [{"id": number * 2, "product_id": 1, "product_code": PRODUCTS[0]["code"], "product_name": PRODUCTS[0]["name"],
                          "quantity_ordered": quantity - 1, "quantity_received": min(received, quantity - 1), "quantity_pending": max(0, quantity - 1 - received),
                          "unit_cost": f"{cost:.2f}", "line_total": f"{cost * (quantity - 1):.2f}"},
                         {"id": number * 2 + 1, "product_id": 2, "product_code": PRODUCTS[1]["code"], "product_name": PRODUCTS[1]["name"],
                          "quantity_ordered": 1, "quantity_received": int(received == quantity), "quantity_pending": int(received != quantity),
                          "unit_cost": f"{final_cost:.2f}", "line_total": f"{final_cost:.2f}"}]}
        if received:
            row["receptions"] = [{"id": number, "received_at": row["ordered_at"][:10], "responsible": "Administrador", "notes": "Recepción de referencia", "items": [{"line_id": line["id"], "quantity": line["quantity_received"], "movement_id": line["id"]} for line in row["items"] if line["quantity_received"]]}]
        row["ordered_date"] = row["ordered_at"][:10]
        rows.append(row)
    return rows


def view(params):
    from app.services.purchase_query_service import filters, page_values
    values = filters(params, reference=True)
    rows = orders()
    search = values["search"].casefold()
    rows = [row for row in rows if (not search or search in (row["order_number"] + row["supplier"]["name"] + str(row["items"])).casefold())
            and (not values["status"] or values["status"] == row["status"])
            and (not values["supplier_id"] or values["supplier_id"] == row["supplier"]["id"])
            and (not values["start"] or row["ordered_at"][:10] >= values["start"])
            and (not values["end"] or row["ordered_at"][:10] <= values["end"])]
    counts = {key: sum(row["status"] == key for row in rows) for key in LABELS}
    relevant = sum(counts[key] for key in ("pending", "partial", "completed"))
    ranked = sorted([{"name": supplier["name"], "amount": sum((Decimal(row["total"]) for row in rows if row["supplier"]["id"] == supplier["id"] and row["status"] != "cancelled"), Decimal(0))} for supplier in SUPPLIERS], key=lambda row: row["amount"], reverse=True)[:3]
    top = [{"name": row["name"], "currency": "USD", "amount": f"{row['amount']:.2f}", "percent": int(row["amount"] * 100 / ranked[0]["amount"]) if ranked[0]["amount"] else 0} for row in ranked if row["amount"]]
    pagination = page_values(params, len(rows))
    return {"available": True, "filters": values, "suppliers": deepcopy(SUPPLIERS), "all_items": rows,
            "items": rows[(pagination["page"] - 1) * pagination["per_page"]:pagination["page"] * pagination["per_page"]],
            "pagination": pagination, "upcoming": [row for row in orders()[:8] if row["status"] in ("pending", "partial")][:3],
            "kpis": {"count": len(rows), "total": f"{sum((Decimal(row['total']) for row in rows if row['status'] != 'cancelled'), Decimal(0)):.2f}",
                     "totals_by_currency": [{"currency": "USD", "amount": f"{sum((Decimal(row['total']) for row in rows if row['status'] != 'cancelled'), Decimal(0)):.2f}"}],
                     "pending": counts["pending"] + counts["partial"], "completed": counts["completed"], "completed_percent": f"{counts['completed'] * 100 / relevant:.1f}%" if relevant else "Sin órdenes",
                     "comparison": None, "counts": counts, "relevant": relevant, "top_suppliers": top}}


def detail(order_id):
    return next((row for row in orders() if row["id"] == order_id), None)
