"""Fixture determinista del modo de referencia visual del módulo Análisis predictivo.

Solo se usa en debug con ``?ref=1`` (ver ``pages.py``). Reproduce los valores visibles en
``references/04_analisis_predictivo/*`` y en la referencia aprobada de Productos críticos
para validar la composición; no proviene de la base de datos ni la modifica. En modo normal
las vistas reciben ``None`` y muestran estados veraces de "no disponible".
"""


def _p(name, sku, thumb):
    return {"name": name, "sku": sku, "thumb": f"img/reference/{thumb}.png"}


def _s(text, tone=None, bold=False, arrow=None, size=None):
    """Segmento de texto de una línea secundaria de KPI."""
    return {"text": text, "tone": tone, "bold": bold, "arrow": arrow, "size": size}


def _kpi(icon, tone, label, value, lines, value_tone=None, value_size=None):
    return {"icon": icon, "tone": tone, "label": label, "value": value, "value_tone": value_tone,
            "value_size": value_size, "lines": lines}


def _pages(active, pages, ellipsis_after=None, last=None):
    items = [{"text": str(n), "active": n == active} for n in pages]
    if ellipsis_after is not None:
        items.append({"text": "...", "gap": True})
    if last is not None:
        items.append({"text": str(last), "active": False})
    return items


VS_PREV = "vs. período anterior"

# Miniaturas (recortes de referencia, solo modo visual)
CEMENTO = _p("Cemento Portland 50 kg", "CEM-050", "cemento")
VARILLA = _p("Varilla Corrugada 12 mm (6 m)", "VAR-12MM", "varilla")
PINTURA = _p("Pintura Blanca 18 L", "PIN-18BL", "pintura")
TUBO = _p("Tubo PVC Presión 1/2\" (3 m)", "TUB-12", "tubo-pvc")
TUBO_CORTO = _p("Tubo PVC Presión 1/2\"", "TUB-1/2", "tubo-pvc")
TALADRO = _p("Taladro Percutor 1/2\" 750W", "TAL-750", "taladro")
CABLE = _p("Cable THHN 12 AWG (m)", "CAB-12", "cable-rollo")
DISCO = _p("Disco de Corte 4 1/2\"", "DISC-4-1/2", "disco")
BROCA = _p("Broca para Concreto 1/2\"", "BROC-1/2", "broca")
PEGAMENTO = _p("Pegamento PVC 240 ml", "PEG-240", "pegamento")

# ---------------------------------------------------------------------------
# Pronóstico de demanda (01_pronostico_de_demanda.png)
# ---------------------------------------------------------------------------
FORECAST = {
    "search_placeholder": "Buscar productos, categorías, proveedores...",
    "kpis": [
        _kpi("ti-trending-up", "blue", "Productos analizados", "3,245",
             [{"left": [_s("100% del catálogo", "blue")]}]),
        _kpi("svg:shield-alert", "red", "En riesgo de quiebre", "18",
             [{"left": [_s("5", "red", True, "up"), _s(" vs. período anterior", "red")]}]),
        _kpi("svg:cart", "orange", "Requieren reposición", "32",
             [{"left": [_s("Ver recomendaciones", "orange")]}]),
        _kpi("svg:target", "purple", "Precisión del modelo", "87.4%",
             [{"left": [_s("3.2%", "green", True, "up"), _s(" vs. período anterior", "dark")]}]),
    ],
    "filters": {
        "search": "Buscar por producto, categoría o código...",
        "selects": [("Categoría", "Todas"), ("Proveedor", "Todos"), ("Horizonte", "30 días"), ("Método", "Todos")],
        "clear": True,
    },
    "table_title": "Pronóstico de demanda (próximos 30 días)",
    "rows": [
        {"p": CEMENTO, "cat": "Construcción", "stock": "48 uds.", "stock_state": ("Suficiente", "green"),
         "demand": "87 uds.", "delta": "± 8 uds.", "risk": ("Alto", "red"), "risk_note": "Quiebre en 12 días",
         "buy": "Comprar 60 uds.", "when": "Para el 08/09/2024"},
        {"p": VARILLA, "cat": "Construcción", "stock": "120 uds.", "stock_state": ("Suficiente", "green"),
         "demand": "145 uds.", "delta": "± 12 uds.", "risk": ("Medio", "yellow"), "risk_note": "Quiebre en 21 días",
         "buy": "Comprar 40 uds.", "when": "Para el 15/09/2024"},
        {"p": PINTURA, "cat": "Pinturas", "stock": "15 uds.", "stock_state": ("Bajo", "red"),
         "demand": "38 uds.", "delta": "± 5 uds.", "risk": ("Alto", "red"), "risk_note": "Quiebre en 7 días",
         "buy": "Comprar 25 uds.", "when": "Para el 05/09/2024"},
        {"p": _p("Tornillo Drywall 6 x 1\"", "TOR-61", "tornillo-drywall"), "cat": "Ferretería", "stock": "2,350 uds.",
         "stock_state": ("Suficiente", "green"), "demand": "1,890 uds.", "delta": "± 150 uds.", "risk": ("Bajo", "green"),
         "risk_note": "Sin riesgo", "buy": None, "when": "No requiere compra"},
        {"p": TUBO, "cat": "Plomería", "stock": "35 uds.", "stock_state": ("Moderado", "orange"),
         "demand": "60 uds.", "delta": "± 7 uds.", "risk": ("Medio", "yellow"), "risk_note": "Quiebre en 18 días",
         "buy": "Comprar 30 uds.", "when": "Para el 12/09/2024"},
        {"p": _p("Cable THHN 12 AWG (m)", "CAB-12AWG", "cable"), "cat": "Eléctrico", "stock": "90 mts.",
         "stock_state": ("Suficiente", "green"), "demand": "110 mts.", "delta": "± 10 mts.", "risk": ("Bajo", "green"),
         "risk_note": "Sin riesgo", "buy": None, "when": "No requiere compra"},
        {"p": _p("Cerámica Piso 45x45 cm", "CER-45X45", "ceramica"), "cat": "Acabados", "stock": "22 m²",
         "stock_state": ("Bajo", "red"), "demand": "55 m²", "delta": "± 6 m²", "risk": ("Alto", "red"),
         "risk_note": "Quiebre en 9 días", "buy": "Comprar 40 m²", "when": "Para el 07/09/2024"},
    ],
    "pagination": {"summary": "Mostrando 1 a 7 de 3,245 productos", "pages": _pages(1, [1, 2, 3, 4, 5], True, 464),
                   "per_page": "10 por página"},
    "donut": {"title": "Distribución de riesgo", "center_value": "3,245", "center_label": "Productos",
              "items": [("Alto riesgo", "18 (0.6%)", "red", 4), ("Riesgo medio", "65 (2.0%)", "orange", 14),
                        ("Bajo riesgo", "3,162 (97.4%)", "green", 82)]},
    "top": {"title": "Top 5 con mayor demanda estimada", "toggle": ["30 días", "90 días"],
            "rows": [("Cemento Portland 50 kg", 58, "87 uds."), ("Varilla Corrugada 12 mm", 66, "145 uds."),
                     ("Tubo PVC 1/2\" (3 m)", 40, "60 uds."), ("Pintura Blanca 18 L", 24, "38 uds."),
                     ("Tornillo Drywall 6 x 1\"", 100, "1,890 uds.")],
            "link": "Ver todos los productos"},
    "model_info": {"title": "Información del modelo",
                   "rows": [("Última actualización", "31/05/2024 08:30 a.m."), ("Algoritmo utilizado", "Prophet + Regresión"),
                            ("Datos utilizados", "Ventas históricas (730 días)")],
                   "link": "Ver detalles del modelo"},
}

# ---------------------------------------------------------------------------
# Productos críticos (referencia aprobada adjunta)
# ---------------------------------------------------------------------------
def _critical_row(p, cat, stock, minimum, demand, cover, risk, motive):
    return {"p": p, "cat": cat, "stock": stock, "minimum": minimum, "demand": demand, "cover": cover, "risk": risk,
            "motive": motive, "cover_tone": risk[1] if risk[1] in ("red", "orange") else None}


CRITICAL = {
    "search_placeholder": "Buscar productos, categorías, reportes...",
    "kpis": [
        _kpi("ti-alert-triangle", "red", "Productos críticos", "18",
             [{"left": [_s("12.5%", "red", True, "up"), _s(" vs. período anterior", "muted")]}]),
        _kpi("ti-cube", "red", "Sin stock", "6",
             [{"left": [_s("50.0%", "red", True, "up"), _s(" vs. período anterior", "muted")]}]),
        _kpi("ti-alert-triangle", "orange", "Bajo stock", "12",
             [{"left": [_s("14.3%", "red", True, "up"), _s(" vs. período anterior", "muted")]}]),
        _kpi("svg:chart-bar", "purple", "Rotación alta", "8",
             [{"left": [_s("33.3%", "red", True, "up"), _s(" vs. período anterior", "muted")]}]),
    ],
    "filters": {
        "search": "Buscar producto por nombre o código...",
        "selects": [("Categoría", "Todas"), ("Estado", "Todos"), ("Nivel de riesgo", "Todos")],
        "clear": True,
    },
    "table_title": "Productos críticos",
    "rows": [
        _critical_row(CEMENTO, "Construcción", "0 sacos", "10 sacos", "120 sacos", "0 días", ("Crítico", "red"), "Sin stock"),
        _critical_row(PINTURA, "Pinturas", "2 uds.", "10 uds.", "60 uds.", "1 día", ("Crítico", "red"), "Stock muy bajo"),
        _critical_row(_p("Varilla Corrugada 12 mm (6 m)", "VAR-12-6M", "varilla"), "Construcción", "8 uds.", "50 uds.",
                      "200 uds.", "2 días", ("Alto", "orange"), "Alta demanda"),
        _critical_row(_p("Tubo PVC Presión 1/2\" (3 m)", "TUB-1/2-3M", "tubo-pvc"), "Plomería", "15 uds.", "50 uds.",
                      "100 uds.", "5 días", ("Alto", "orange"), "Bajo stock"),
        _critical_row(_p("Bombillo LED 9W", "BOMB-LED-9W", "bombillo"), "Electricidad", "12 uds.", "50 uds.", "150 uds.",
                      "6 días", ("Alto", "orange"), "Rotación alta"),
        _critical_row(BROCA, "Herramientas", "20 uds.", "100 uds.", "80 uds.", "8 días", ("Medio", "yellow"), "Tendencia al alza"),
        _critical_row(_p("Cinta Métrica 5 m", "CINTA-5M", "cinta-metrica"), "Herramientas", "8 uds.", "30 uds.", "40 uds.",
                      "9 días", ("Medio", "yellow"), "Ventas crecientes"),
        _critical_row(PEGAMENTO, "Ferretería", "25 uds.", "50 uds.", "30 uds.", "25 días", ("Medio", "yellow"), "Stock bajo"),
    ],
    "pagination": {"summary": "Mostrando 1 a 8 de 18 productos", "pages": _pages(1, [1, 2, 3]), "per_page": "8 por página"},
    "donut": {"title": "Distribución por nivel de riesgo", "center_value": "18", "center_label": "Productos",
              "items": [("Crítico", "6 (33.3%)", "red", 6), ("Alto", "8 (44.4%)", "orange", 8),
                        ("Medio", "4 (22.2%)", "yellow", 4), ("Bajo", "0 (0.0%)", "green", 0)]},
    "top": {"title": "Top 5 productos por riesgo",
            "rows": [{"p": CEMENTO, "badge": ("Crítico", "red"), "sub": "Sin stock"},
                     {"p": PINTURA, "badge": ("Crítico", "red"), "sub": "2 uds. (mín. 10)"},
                     {"p": _p("Varilla Corrugada 12 mm", "VAR-12-6M", "varilla"), "badge": ("Alto", "orange"), "sub": "8 uds. (mín. 50)"},
                     {"p": _p("Tubo PVC 1/2\" (3 m)", "TUB-1/2-3M", "tubo-pvc"), "badge": ("Alto", "orange"), "sub": "15 uds. (mín. 50)"},
                     {"p": _p("Bombillo LED 9W", "BOMB-LED-9W", "bombillo"), "badge": ("Alto", "orange"), "sub": "12 uds. (mín. 50)"}]},
    "recommendation": {"title": "Recomendación",
                       "text": "Se recomienda generar una orden de compra para los productos en nivel crítico y alto a la "
                               "brevedad posible.",
                       "link": None, "link_text": "Ir a Reabastecimiento"},
}

# ---------------------------------------------------------------------------
# Reabastecimiento (02_reabastecimiento.png)
# ---------------------------------------------------------------------------
REPLENISHMENT = {
    "search_placeholder": "Buscar productos, clientes, reportes...",
    "kpis": [
        _kpi("svg:cart", "orange", "Productos por reabastecer", "32",
             [{"left": [_s("Ver recomendaciones", "orange", True), _s(" ›", "orange", True)]}]),
        _kpi("ti-alert-triangle", "red", "Prioridad alta", "12",
             [{"left": [_s("20.0%", "red", True, "down", "lg")]}, {"left": [_s(VS_PREV, "muted")]}]),
        _kpi("ti-currency-dollar", "green", "Monto estimado de compra", "$ 18,450.00",
             [{"left": [_s("15.6%", "green", True, "up")]}, {"left": [_s(VS_PREV, "muted")]}]),
        _kpi("ti-calendar", "blue", "Cobertura promedio", "14 días",
             [{"left": [_s("2 días", "green", True, "up")]}, {"left": [_s(VS_PREV, "muted")]}]),
    ],
    "filters": {
        "search": "Buscar producto por nombre o código...",
        "selects": [("Categoría", "Todas"), ("Proveedor", "Todos"), ("Horizonte", "30 días")],
        "clear": True,
    },
    "table_title": "Recomendaciones de reabastecimiento",
    "rows": [
        {"p": _p("Cemento Portland 50 kg", "CEM-50KG", "cemento"), "cat": "Construcción", "stock": "18 sacos", "demand": "120 sacos",
         "reorder": "40 sacos", "qty": "120 sacos", "prio": ("Alta", "red"), "date": "03/06/2024", "cost": "$ 3,600.00"},
        {"p": _p("Varilla Corrugada 12 mm (6 m)", "VAR-12-6M", "varilla"), "cat": "Construcción", "stock": "45 uds.", "demand": "200 uds.",
         "reorder": "80 uds.", "qty": "200 uds.", "prio": ("Alta", "red"), "date": "04/06/2024", "cost": "$ 4,600.00"},
        {"p": PINTURA, "cat": "Pinturas", "stock": "8 uds.", "demand": "60 uds.", "reorder": "20 uds.", "qty": "60 uds.",
         "prio": ("Media", "orange"), "date": "05/06/2024", "cost": "$ 2,100.00"},
        {"p": _p("Tubo PVC Presión 1/2\" (3 m)", "TUB-1/2-3M", "tubo-pvc"), "cat": "Plomería", "stock": "20 uds.", "demand": "100 uds.",
         "reorder": "30 uds.", "qty": "100 uds.", "prio": ("Media", "orange"), "date": "06/06/2024", "cost": "$ 850.00"},
        {"p": TALADRO, "cat": "Herramientas", "stock": "5 uds.", "demand": "25 uds.", "reorder": "8 uds.", "qty": "20 uds.",
         "prio": ("Media", "orange"), "date": "07/06/2024", "cost": "$ 2,980.00"},
        {"p": CABLE, "cat": "Electricidad", "stock": "120 m", "demand": "600 m", "reorder": "200 m", "qty": "600 m",
         "prio": ("Baja", "green"), "date": "08/06/2024", "cost": "$ 780.00"},
        {"p": BROCA, "cat": "Herramientas", "stock": "10 uds.", "demand": "50 uds.", "reorder": "15 uds.", "qty": "50 uds.",
         "prio": ("Baja", "green"), "date": "09/06/2024", "cost": "$ 340.00"},
        {"p": PEGAMENTO, "cat": "Ferretería", "stock": "6 uds.", "demand": "30 uds.", "reorder": "10 uds.", "qty": "30 uds.",
         "prio": ("Baja", "green"), "date": "10/06/2024", "cost": "$ 200.00"},
    ],
    "pagination": {"summary": "Mostrando 1 a 8 de 32 productos", "pages": _pages(1, [1, 2, 3, 4]), "per_page": "10 por página"},
    "donut": {"title": "Distribución de prioridad", "center_value": "32", "center_label": "productos",
              "items": [("Alta (12)", None, "red", 37), ("Media (14)", None, "orange", 44), ("Baja (6)", None, "green", 19)]},
    "top": {"title": "Top 5 compras sugeridas",
            "rows": [(VARILLA, "200 uds.", "$ 4,600.00"), (CEMENTO, "120 sacos", "$ 3,600.00"),
                     (TALADRO, "20 uds.", "$ 2,980.00"), (PINTURA, "60 uds.", "$ 2,100.00"), (TUBO, "100 uds.", "$ 850.00")]},
    "model_info": {"title": "Información del modelo",
                   "rows": [("Última actualización:", "31/05/2024 08:30 a.m."), ("Método:", "reorden + pronóstico"),
                            ("Datos utilizados:", "ventas históricas (730 días)"),
                            ("Variables:", "demanda, stock, lead time, estacionalidad")]},
}

# ---------------------------------------------------------------------------
# Precisión del modelo (03_precision_del_modelo.png)
# ---------------------------------------------------------------------------
ACCURACY = {
    "search_placeholder": "Buscar productos, clientes, reportes...",
    "kpis": [
        _kpi("ti-target", "blue", "Precisión general (MAPE)", "92.4%",
             [{"left": [_s("Excelente", "green", True)]},
              {"left": [_s("vs. análisis anterior", "muted")], "right": [_s("2.6%", "green", True, "up")]}], value_tone="green"),
        _kpi("ti-wave-sine", "purple", "Error promedio (MAPE)", "7.6%",
             [{"left": [_s("Bajo", "green", True)]},
              {"left": [_s("vs. análisis anterior", "muted")], "right": [_s("0.8%", "red", True, "up")]}], value_tone="purple"),
        _kpi("ti-circle-check", "green", "Sesgo promedio (ME)", "-0.6%",
             [{"left": [_s("Sin sesgo significativo", "green", True)]},
              {"left": [_s("vs. análisis anterior", "muted")], "right": [_s("0.3%", "green", True, "down")]}], value_tone="green"),
        _kpi("ti-activity", "orange", "Predicciones evaluadas", "1,248",
             [{"left": [_s("Últimos 30 días", "blue", True)]},
              {"left": [_s("vs. análisis anterior", "muted")], "right": [_s("156", "green", True, "up")]}], value_tone="orange"),
    ],
    "filters": {
        "search": "Buscar producto por nombre o código...",
        "selects": [("Categoría", "Todas"), ("Modelo", "Modelo de demanda v2.1")],
        "date": ("Período de evaluación", "Últimos 30 días"),
        "clear": False,
    },
    "tabs": ["Resumen de precisión", "Precisión por categoría", "Precisión por producto"],
    "metrics_title": "Métricas de precisión",
    "metrics": [
        ("MAPE", "Error porcentual absoluto medio", "7.6%", ("Excelente", "green")),
        ("MAE", "Error absoluto medio", "18.3 uds.", ("Excelente", "green")),
        ("RMSE", "Raíz del error cuadrático medio", "24.7 uds.", ("Bueno", "green")),
        ("ME (Sesgo)", "Error medio (sesgo)", "-0.6%", ("Sin sesgo", "green")),
        ("Precisión (1 - MAPE)", "Porcentaje de precisión del modelo", "92.4%", ("Excelente", "green")),
    ],
    "chart": {
        "title": "Predicción vs. Real (últimos 30 días)",
        "legend": [("Valores reales", "solid"), ("Valores predichos", "dashed")],
        "labels": ["01/05", "", "", "", "08/05", "", "", "", "15/05", "", "", "", "22/05", "", "", "", "30/05"],
        "real": [150, 175, 180, 165, 230, 185, 240, 205, 165, 185, 230, 195, 175, 240, 285, 195, 175],
        "pred": [175, 190, 170, 180, 205, 190, 215, 185, 200, 240, 210, 195, 225, 250, 215, 240, 210],
        "y_max": 300, "y_step": 50, "y_label": "Unidades",
    },
    "ape": {
        "title": "Distribución del error porcentual (APE)",
        "segments": [("APE ≤ 10% (Excelente)", "68.5%", "green", 68.5), ("10% < APE ≤ 20% (Bueno)", "21.3%", "yellow", 21.3),
                     ("20% < APE ≤ 30% (Aceptable)", "7.2%", "orange", 7.2), ("APE > 30% (Alto)", "3.0%", "red", 3.0)],
        "note": "El 89.8% de las predicciones tienen un error menor o igual al 20%.",
    },
    "pagination": {"summary": "Mostrando 1 a 6 de 6 métricas", "pages": [], "per_page": "10 por página"},
    "gauge": {"title": "Desempeño del modelo", "value": 92.4, "label": "92.4%", "verdict": "Excelente",
              "text": "El modelo tiene una excelente capacidad predictiva. Las predicciones son altamente confiables "
                      "para la toma de decisiones."},
    "errors": {"title": "Errores más comunes",
               "rows": [("Picos de demanda inesperados", "34.2%", "red"), ("Promociones no planificadas", "28.7%", "red"),
                        ("Estacionalidad atípica", "19.1%", "orange"), ("Datos históricos incompletos", "12.3%", "orange"),
                        ("Otros factores externos", "5.7%", "green")]},
    "info": {"title": "Información",
             "text": "La precisión del modelo se evalúa comparando los valores predichos con los valores reales de "
                     "ventas y demanda."},
}

# ---------------------------------------------------------------------------
# Tendencias (04_tendencias/*.png): una estructura, cuatro estados
# ---------------------------------------------------------------------------
_TREND_TABS = [("up", "Tendencia al alza"), ("down", "Tendencia a la baja"), ("stable", "Tendencia estable"), ("all", "Todas")]
_CATEGORY_SERIES = [("Construcción", "blue"), ("Ferretería", "green"), ("Pinturas", "orange"), ("Plomería", "purple"),
                    ("Electricidad", "red")]
_TREND_INFO = ("Las tendencias se calculan comparando las ventas del período seleccionado con el período anterior de "
               "igual duración.")
_DOWN_TOP = {"title": "Top 5 productos con mayor caída", "tone": "red", "arrow": "down",
             "rows": [(PEGAMENTO, "-33.0%"), (TALADRO, "-21.4%"), (TUBO_CORTO, "-18.3%"), (DISCO, "-17.2%"), (BROCA, "-14.6%")]}
_STABLE_KPI = _kpi("ti-arrow-right", "gray", "Demanda estable", "3,166", [{"left": [_s("66.4% del catálogo", "muted")]}])
_STABLE_KPI["icon_tone"] = "dark"


def _trend_row(p, cat, sales, variation, tone, cause, prev=None, badge=None):
    return {"p": p, "cat": cat, "sales": sales, "prev": prev, "variation": variation, "tone": tone, "badge": badge, "cause": cause}


def _trend_chart(direction, legend=_CATEGORY_SERIES):
    """Series generadas de forma determinista en el cliente a partir de inicio/fin por categoría."""
    ends = {"up": [(950, 1700), (450, 750), (250, 1000), (150, 450), (80, 400)],
            "down": [(1800, 950), (1050, 500), (550, 300), (300, 100), (120, 40)],
            "stable": [(1480, 1480), (1020, 1020), (560, 560), (200, 200), (30, 30)]}[direction]
    return {"labels": ["01/05", "08/05", "15/05", "22/05", "30/05"], "y_max": 2000, "y_step": 500,
            "series": [{"label": label, "tone": tone, "from": a, "to": b} for (label, tone), (a, b) in zip(legend, ends)]}


TRENDS = {
    "search_placeholder": "Buscar productos, clientes, reportes...",
    "tabs": _TREND_TABS,
    "states": {
        "up": {
            "subtitle": "Identifica productos con cambios significativos en su demanda y detecta oportunidades.",
            "kpis": [
                _kpi("ti-trending-up", "green", "Productos en tendencia al alza", "56",
                     [{"left": [_s("22.4% del catálogo", "muted")], "right": [_s("18.6%", "green", True, "up")]},
                      {"right": [_s(VS_PREV, "muted")]}]),
                _kpi("ti-trending-down", "red", "Productos en tendencia a la baja", "23",
                     [{"left": [_s("9.2% del catálogo", "muted")], "right": [_s("5.1%", "red", True, "up")]},
                      {"right": [_s(VS_PREV, "muted")]}]),
                dict(_STABLE_KPI, lines=[{"left": [_s("68.4% del catálogo", "muted")]}]),
                _kpi("svg:chart-bar", "purple", "Variación promedio de ventas", "+12.8%",
                     [{"left": [_s("Últimos 30 días", "blue")]}]),
            ],
            "trend_filter": "Todas",
            "table_title": "Productos en tendencia al alza",
            "rows": [
                _trend_row(CEMENTO, "Construcción", "1,280 uds.", "56.3%", "green", "Temporada de construcción"),
                _trend_row(VARILLA, "Construcción", "950 uds.", "42.1%", "green", "Aumento de proyectos"),
                _trend_row(PINTURA, "Pinturas", "430 uds.", "38.7%", "green", "Temporada de remodelación"),
                _trend_row(TUBO, "Plomería", "620 uds.", "34.5%", "green", "Mayor demanda en reparaciones"),
                _trend_row(_p("Lámpara LED 12W", "LAM-12W", "lampara"), "Electricidad", "310 uds.", "28.9%", "green",
                           "Promociones y ahorro energético"),
                _trend_row(PEGAMENTO, "Ferretería", "275 uds.", "24.6%", "green", "Proyectos residenciales"),
                _trend_row(_p("Arena Lavada 20 kg", "ARE-020", "arena"), "Construcción", "410 uds.", "22.3%", "green", "Obras locales"),
                _trend_row(_p("Cable THHN 12 AWG (m)", "CAB-12", "cable-anillo"), "Electricidad", "890 uds.", "19.8%", "green",
                           "Instalaciones eléctricas"),
            ],
            "pagination": {"summary": "Mostrando 1 a 8 de 56 productos", "pages": _pages(1, [1, 2, 3, 4, 5, 6, 7]),
                           "per_page": "8 por página"},
            "chart": _trend_chart("up"),
            "top": {"title": "Top 5 productos con mayor crecimiento", "tone": "green", "arrow": "up",
                    "rows": [(CEMENTO, "56.3%"), (_p("Varilla Corrugada 12 mm", "VAR-12MM", "varilla"), "42.1%"),
                             (PINTURA, "38.7%"), (TUBO_CORTO, "34.5%"), (_p("Lámpara LED 12W", "LAM-12W", "lampara"), "28.9%")]},
            "info": _TREND_INFO,
        },
        "down": {
            "subtitle": "Identifica productos con demanda en descenso y las posibles causas.",
            "kpis": [
                _kpi("ti-trending-down", "red", "Productos en tendencia a la baja", "23",
                     [{"left": [_s("9.2% del catálogo", "muted")], "right": [_s("5.1%", "red", True, "down")]},
                      {"right": [_s(VS_PREV, "muted")]}]),
                _kpi("ti-arrow-down-right", "red", "Mayor caída", "Pegamento PVC 240 ml",
                     [{"left": [_s("-33.0%", "red", True, None, "xl")]}, {"left": [_s(VS_PREV, "muted")]}], value_size="sm"),
                _STABLE_KPI,
                _kpi("svg:chart-bar", "purple", "Variación promedio de ventas", "-12.8%",
                     [{"left": [_s("Últimos 30 días", "blue")]}], value_tone="red"),
            ],
            "trend_filter": "Tendencia a la baja",
            "table_title": "Productos en tendencia a la baja",
            "rows": [
                _trend_row(PINTURA, "Pinturas", "439 uds.", "-13.7%", "red", "Menor actividad de remodelación"),
                _trend_row(TUBO_CORTO, "Plomería", "620 uds.", "-18.3%", "red", "Baja rotación reciente"),
                _trend_row(PEGAMENTO, "Ferretería", "275 uds.", "-33.0%", "red", "Sustitución por alternativa"),
                _trend_row(_p("Cable THHN 12 AWG (m)", "CAB-12", "cable-anillo"), "Electricidad", "890 uds.", "-12.5%", "red",
                           "Menor demanda residencial"),
                _trend_row(TALADRO, "Herramientas", "155 uds.", "-21.4%", "red", "Baja inversión en proyectos"),
                _trend_row(DISCO, "Abrasivos", "312 uds.", "-17.2%", "red", "Menor actividad de obra"),
                _trend_row(BROCA, "Herramientas", "198 uds.", "-14.6%", "red", "Baja rotación reciente"),
                _trend_row(_p("Cinta Métrica 5 m", "CINTA-5M", "cinta-metrica"), "Herramientas", "246 uds.", "-10.8%", "red",
                           "Fin de temporada"),
            ],
            "pagination": {"summary": "Mostrando 1 a 8 de 23 productos", "pages": _pages(1, [1, 2, 3, 4, 5, 6]),
                           "per_page": "8 por página"},
            "chart": _trend_chart("down"),
            "top": _DOWN_TOP,
            "info": ("Las tendencias a la baja se calculan comparando las ventas del período seleccionado con el período "
                     "anterior de igual duración."),
        },
        "stable": {
            "subtitle": "Identifica productos con comportamiento estable en el período analizado.",
            "kpis": [
                _kpi("ti-arrows-left-right", "blue", "Productos con tendencia estable", "29",
                     [{"left": [_s("11.6% del catálogo", "muted")], "right": [_s("4.8%", "green", True, "up")]},
                      {"right": [_s(VS_PREV, "muted")]}]),
                _kpi("ti-wave-sine", "blue", "Variación promedio", "±3.8%", [{"left": [_s(VS_PREV, "muted")]}]),
                _STABLE_KPI,
                _kpi("svg:chart-bar", "purple", "Variación promedio de ventas", "+1.2%",
                     [{"left": [_s("Últimos 30 días", "blue")]}], value_tone="green"),
            ],
            "trend_filter": "Tendencia estable",
            "table_title": "Productos con tendencia estable",
            "rows": [
                _trend_row(_p("Codo PVC 1/2\"", "COD-1/2", "codo-pvc"), "Plomería", "682 uds.", "+0.8%", "green", "Demanda constante"),
                _trend_row(_p("Bombillo LED 9W", "BOMB-LED-9W", "bombillo"), "Electricidad", "1,245 uds.", "-0.6%", "red",
                           "Patrón de compra regular"),
                _trend_row(_p("Cinta Aislante 19 mm x 20 m", "CINTA-19-20", "cinta-aislante"), "Electricidad", "978 uds.", "+1.1%",
                           "green", "Uso habitual en instalaciones"),
                _trend_row(_p("Llave de Paso 1/2\"", "LP-1/2", "llave-paso"), "Plomería", "564 uds.", "-1.3%", "red",
                           "Demanda de mantenimiento"),
                _trend_row(_p("Tornillo Phillips 6 x 1\"", "TOR-PH-6X1", "tornillo-phillips"), "Ferretería", "2,134 uds.", "+0.3%",
                           "green", "Consumo estable"),
                _trend_row(DISCO, "Abrasivos", "876 uds.", "-0.4%", "red", "Uso en mantenimiento"),
                _trend_row(_p("Rodillo de Felpa 9\"", "ROD-FEL-9", "rodillo"), "Pinturas", "732 uds.", "+1.5%", "green",
                           "Proyectos continuos"),
                _trend_row(_p("Silicón Transparente 280 ml", "SILIC-280", "silicon"), "Ferretería", "645 uds.", "-0.2%", "red",
                           "Demanda estable"),
            ],
            "pagination": {"summary": "Mostrando 1 a 8 de 29 productos", "pages": _pages(1, [1, 2, 3, 4, 5], True, 4),
                           "per_page": "8 por página"},
            "chart": _trend_chart("stable", [("Construcción", "blue"), ("Ferretería", "green"), ("Electricidad", "orange"),
                                             ("Plomería", "purple"), ("Pinturas", "red")]),
            "top": {"title": "Top 5 productos con mayor estabilidad", "tone": "blue", "arrow": None,
                    "rows": [(_p("Cinta Aislante 19 mm x 20 m", "CINTA-19-20", "cinta-aislante"), "±0.2%"),
                             (_p("Tornillo Phillips 6 x 1\"", "TOR-PH-6X1", "tornillo-phillips"), "±0.3%"),
                             (DISCO, "±0.4%"), (_p("Bombillo LED 9W", "BOMB-LED-9W", "bombillo"), "±0.6%"),
                             (_p("Codo PVC 1/2\"", "COD-1/2", "codo-pvc"), "±0.8%")]},
            "info": ("Las tendencias estables se calculan identificando productos con variación baja (dentro de ±5%) "
                     "respecto al período anterior de igual duración."),
        },
        "all": {
            "subtitle": "Identifica productos con crecimiento, descenso o demanda estable para tomar mejores decisiones.",
            "kpis": [
                _kpi("ti-trending-up", "green", "Productos en tendencia al alza", "58",
                     [{"left": [_s("22.4% del catálogo", "muted")], "right": [_s("8.7%", "green", True, "up")]},
                      {"right": [_s(VS_PREV, "muted")]}]),
                _kpi("ti-trending-down", "red", "Productos en tendencia a la baja", "41",
                     [{"left": [_s("15.8% del catálogo", "muted")], "right": [_s("-6.3%", "red", True, "down")]},
                      {"right": [_s(VS_PREV, "muted")]}]),
                dict(_STABLE_KPI, value="124", lines=[{"left": [_s("47.8% del catálogo", "muted")]}]),
                _kpi("svg:chart-bar", "purple", "Variación promedio de ventas", "+3.6%",
                     [{"left": [_s("Últimos 30 días", "blue")]}], value_tone="green"),
            ],
            "trend_filter": "Todas",
            "table_title": "Resumen general de tendencias",
            "rows": [
                _trend_row(PINTURA, "Pinturas", "439 uds.", "+13.7%", "green", "Mayor actividad de remodelación", "386 uds.",
                           ("Al alza", "green")),
                _trend_row(TUBO_CORTO, "Plomería", "620 uds.", "+18.3%", "green", "Baja rotación reciente", "508 uds.",
                           ("Al alza", "green")),
                _trend_row(PEGAMENTO, "Ferretería", "275 uds.", "-33.0%", "red", "Sustitución por alternativa", "410 uds.",
                           ("A la baja", "red")),
                _trend_row(_p("Cable THHN 12 AWG (m)", "CAB-12", "cable-anillo"), "Electricidad", "890 uds.", "-12.5%", "red",
                           "Menor demanda residencial", "1,015 uds.", ("A la baja", "red")),
                _trend_row(TALADRO, "Herramientas", "155 uds.", "-21.4%", "red", "Baja inversión en proyectos", "197 uds.",
                           ("A la baja", "red")),
                _trend_row(DISCO, "Abrasivos", "312 uds.", "-17.2%", "red", "Menor actividad de obra", "377 uds.",
                           ("A la baja", "red")),
                _trend_row(BROCA, "Herramientas", "198 uds.", "-14.6%", "red", "Baja rotación reciente", "232 uds.",
                           ("A la baja", "red")),
                _trend_row(_p("Cinta Métrica 5 m", "CINTA-5M", "cinta-metrica"), "Herramientas", "246 uds.", "-10.8%", "red",
                           "Fin de temporada", "276 uds.", ("A la baja", "red")),
                _trend_row(_p("Bombillo LED 9W", "LED-9W", "bombillo"), "Electricidad", "520 uds.", "0.0%", "gray",
                           "Demanda constante", "520 uds.", ("Estable", "gray")),
                _trend_row(_p("Tornillo Drywall 6 x 1 1/4\"", "TORN-6X11/4", "tornillo-drywall-14"), "Ferretería", "1,250 uds.",
                           "0.0%", "gray", "Demanda constante", "1,250 uds.", ("Estable", "gray")),
            ],
            "pagination": {"summary": "Mostrando 1 a 10 de 223 productos", "pages": _pages(1, [1, 2, 3, 4, 5, 6], True, 23),
                           "per_page": "10 por página"},
            "chart": _trend_chart("down"),
            "top": _DOWN_TOP,
            "info": _TREND_INFO,
        },
    },
}

TREND_STATES = tuple(key for key, _ in _TREND_TABS)


def trends_state(key):
    """Estado de Tendencias para ``?trend=``; valores desconocidos caen en la primera pestaña."""
    return key if key in TREND_STATES else TREND_STATES[0]
