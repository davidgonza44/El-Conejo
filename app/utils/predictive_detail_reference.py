"""Deterministic visual-only detail; never queries or writes the database."""
from datetime import date, timedelta
from app.utils import predictive_reference as ref


def detail(sku):
    products = [v for v in vars(ref).values() if isinstance(v, dict) and v.get("sku")]
    product = next((p for p in products if p["sku"] == sku), None)
    if product is None:
        # Some page fixtures define additional products inline.
        products = [r["p"] for page in (ref.FORECAST, ref.REPLENISHMENT, ref.CRITICAL)
                    for r in page.get("rows", []) if "p" in r]
        product = next((p for p in products if p["sku"] == sku), None)
    if product is None:
        return None
    today = date(2026, 9, 14)
    values = [float([3, 2, 6, 11, 4, 3, 6, 3, 4, 6, 3, 5, 4, 10, 16, 8, 5, 5, 2, 5, 3, 3, 7, 4, 6, 11, 16, 5, 2, 3][i % 30]) for i in range(90)]
    periods = [{"days": h, "start": today.isoformat(), "end": (today + timedelta(days=h-1)).isoformat(),
                "total": total, "daily_rate": total/h, "interval": interval,
                "path": [total / h + ((i % 5) - sum(j % 5 for j in range(h)) / h) * .12
                         for i in range(h)], "model": "Ejemplo visual"}
               for h, total, interval in [(7, 22, 6), (30, 87, 18), (90, 260, 55)]]
    visual_history = [7 + i * .045 + [0, -1, .3, 1, -.4, 2, -.2][i % 7] for i in range(60)]
    visual_path = [10 + i * .035 + [0, -.6, -.9, .2, -.3, .8][i % 6] for i in range(30)]
    return {"reference": True, "today": today.isoformat(),
            "visual_demand": {"dates": [(today - timedelta(days=90-round(i*89/35))).isoformat() for i in range(36)],
                              "values": values[:36]},
            "visual_forecast": {"dates": [(today - timedelta(days=60-i)).isoformat() for i in range(60)],
                                "values": visual_history, "path": visual_path,
                                "lower": [v - 2 - i * .16 for i, v in enumerate(visual_path)],
                                "upper": [v + 2 + i * .23 for i, v in enumerate(visual_path)]},
            "product": {"id": None, "name": product["name"], "sku": sku, "unit": "sacos" if sku == "CEM-050" else "uds.",
                        "image": "/static/" + product["thumb"], "category": "Construcción", "suppliers": ["Cementos Nacionales, C.A."], "stock": 0, "minimum": 10},
            "risk": {"level": "critical", "cover": 0},
            "history": {"dates": [(today - timedelta(days=90-i)).isoformat() for i in range(90)], "values": values,
                        "total": sum(values), "average": sum(values)/90, "maximum": max(values), "minimum": min(values), "source": "Fixture visual"},
            "periods": periods, "forecast_kind": "model", "wape": 11.1, "trend": {"label": "Estable", "variation": 2.1},
            "eligibility": "Ejemplo determinista: no representa datos LIVE.",
            "recommendation": {"lead_time_days": 7, "lead_time_source": "assumed", "lead_time_demand": periods[1]["daily_rate"] * 7, "safety_stock": 10,
                               "reorder_point": periods[1]["daily_rate"] * 7 + 10, "inventory_position": 120, "suggested_quantity": 0, "unit_cost": "8.50"},
            "pending": 120, "orders": [{"number": "OC-00045", "supplier": "Cementos Nacionales", "status": "pending", "status_label": "Pendiente", "pending": 100, "expected": "2026-09-18"},
                                        {"number": "OC-00043", "supplier": "Distribuidora Lara", "status": "partial", "status_label": "Parcial", "pending": 20, "expected": "2026-09-20"}],
            "sales": [{"date": day, "document": document, "customer": customer, "quantity": quantity}
                      for day, document, customer, quantity in [
                          ("2026-09-12", "FAC-001245", "Constructora Alfa", 10),
                          ("2026-09-10", "FAC-001233", "Servicios Lara", 8),
                          ("2026-09-08", "FAC-001198", "Cliente General", 6),
                          ("2026-09-05", "FAC-001176", "Inversiones RC", 12),
                          ("2026-09-02", "FAC-001150", "Cliente General", 4)]],
            "movements": [{"date": day, "type": "Venta", "document": document,
                           "entry": 0, "exit": quantity, "stock": stock}
                          for day, document, quantity, stock in [
                              ("2026-09-12", "FAC-001245", 10, 0),
                              ("2026-09-10", "FAC-001233", 8, 10),
                              ("2026-09-08", "FAC-001198", 6, 18),
                              ("2026-09-05", "FAC-001176", 12, 24),
                              ("2026-09-02", "FAC-001150", 4, 36)]],
            "permissions": {"sales": True, "inventory": True, "purchases": True}, "can_order": False, "suppliers": []}
