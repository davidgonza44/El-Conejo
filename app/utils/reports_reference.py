"""Fixture determinista del modo de referencia visual de "Dashboard de Reportes".

Solo se usa en debug con ``?ref=1`` (ver ``pages.reports``). Reproduce los valores visibles en
la referencia aprobada a 1600x900 (KPI, gráficos, rankings, movimientos, alertas y el resumen de
Análisis predictivo integrado); no proviene de la base de datos ni la modifica. En modo normal
``/reports`` redirige a Inicio, donde viven los reportes reales.
"""
from app.utils.inventory_reference import (
    BROCA, CEMENTO, CINTA, DESTORNILLADORES, DISCO, NIVEL, SEARCH, TALADRO, TORNILLO, VS_ABRIL, _kpi,
)

DISCO_7 = {"name": "Disco de Corte 7\"", "thumb": DISCO["thumb"]}
ALTO = ("Alto", "red")
MEDIO = ("Medio", "orange")

REPORTS = {
    "search_placeholder": SEARCH,
    "date_range": "01 may. 2024 - 31 may. 2024",
    "compare": "Comparar con: 01 abr. 2024 - 30 abr. 2024",
    # (etiqueta, icono, tono del icono); etiqueta None = botón solo icono
    "actions": [("Filtros", "ti-filter", None), (None, "ti-refresh", None),
                ("Exportar PDF", "ti-file-type-pdf", "red"), ("Exportar CSV", "ti-file-type-csv", "green")],
    "kpis": [
        _kpi("ti-currency-dollar", "blue", "Ventas del mes", "$ 486,250.00", VS_ABRIL, "18.6%", solid=True),
        _kpi("ti-moneybag", "green", "Ingresos", "$ 524,830.00", VS_ABRIL, "16.2%"),
        _kpi("svg:alert-triangle", "orange", "Productos con bajo stock", "23", VS_ABRIL, "4", "red"),
        _kpi("svg:tag", "purple", "Ticket promedio", "$ 1,256.80", VS_ABRIL, "7.3%"),
        _kpi("ti-clipboard-list", "teal", "Órdenes de compra", "14", VS_ABRIL, "2"),
    ],
    "sales_chart": {
        "title": "Ventas de los últimos 12 meses", "range": "Mensual",
        "labels": ["Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic", "Feb", "Mar", "Abr", "May"],
        "series": [
            {"label": "Ventas ($)", "data": [260000, 280000, 400000, 400000, 355000, 445000, 445000, 500000, 395000, 500000, 500000], "fill": True},
            {"label": "Ventas año anterior ($)", "data": [120000, 160000, 245000, 320000, 205000, 285000, 225000, 320000, 245000, 285000, 445000], "dashed": True},
        ],
        "legend": [("Ventas ($)", False), ("Ventas año anterior ($)", True)],
        "y_max": 600000, "y_step": 100000,
        "tooltip": {"index": 10, "title": "May 2024", "lines": [("$ 486,250.00", True)], "dx": -68, "dy": 14},
    },
    "category_chart": {
        "title": "Ventas por categoría", "range": "Por ingresos",
        "labels": [["Herramientas", "eléctricas"], ["Herramientas", "manuales"], ["Materiales de", "construcción"],
                   ["Ferretería", "básica"], ["Protección y", "seguridad"], ["Otros"]],
        "values": [198450, 126870, 72340, 48260, 27180, 13150],
        "value_labels": ["$198,450", "$126,870", "$72,340", "$48,260", "$27,180", "$13,150"],
        "y_max": 250000, "y_step": 50000,
    },
    "donut": {
        "title": "Distribución del inventario", "center_value": "3,245", "center_label": "Productos totales",
        "items": [("Herramientas eléctricas", "28% (901)", "blue", 901), ("Herramientas manuales", "24% (778)", "green", 778),
                  ("Materiales construcción", "22% (713)", "orange", 713), ("Ferretería básica", "16% (512)", "purple", 512),
                  ("Protección y seguridad", "7% (228)", "teal", 228), ("Otros", "3% (113)", "gray", 113)],
    },
    "top": {
        "title": "Productos más vendidos", "link": "Ver todo",
        "columns": ["#", "Producto", "Categoría", "Unidades", "Ingresos"],
        "rows": [(TALADRO, "Herramientas eléctricas", "148", "$ 59,200.00"),
                 (DISCO, "Herramientas manuales", "436", "$ 31,320.00"),
                 (CEMENTO, "Materiales de construcción", "312", "$ 27,040.00"),
                 (DESTORNILLADORES, "Herramientas manuales", "215", "$ 18,700.00"),
                 (NIVEL, "Herramientas manuales", "184", "$ 14,720.00")],
        "footer": "Mostrando 1 a 5 de 5 productos",
    },
    "activity": {
        "title": "Movimientos recientes", "link": "Ver todos",
        "rows": [("svg:cart", "green", "Venta #VTA-000156", "Cliente: Constructora del Norte S.A.", "Hoy, 11:42 a.m.", "+ $ 8,920.00", "up"),
                 ("svg:cube", "blue", "Entrada de inventario #ENT-00089", "Proveedor: Importadora Ferremax", "Hoy, 10:15 a.m.", "+ 320 uds.", "up"),
                 ("svg:cart", "orange", "Venta #VTA-000155", "Cliente: Publico en general", "Hoy, 09:08 a.m.", "+ $ 2,450.00", "up"),
                 ("svg:alert-triangle", "red", "Ajuste de inventario #AJU-00023", "Motivo: Conteo físico", "Ayer, 04:35 p.m.", "- 12 uds.", "down"),
                 ("svg:cube", "blue", "Entrada de inventario #ENT-00088", "Proveedor: Aceros y Más S.A.", "Ayer, 03:20 p.m.", "+ 150 uds.", "up")],
    },
    "alerts": {
        "title": "Alertas de stock", "link": "Ver todas", "more": "Ver todas las alertas",
        "rows": [(CEMENTO["name"], "Stock actual: 8 unidades"), (DISCO_7["name"], "Stock actual: 5 unidades"),
                 (BROCA["name"], "Stock actual: 7 unidades"), (TORNILLO["name"], "Stock actual: 12 unidades"),
                 (CINTA["name"], "Stock actual: 6 unidades")],
    },
    "predictive": {
        "title": "Análisis predictivo", "subtitle": "Resumen de pronósticos, riesgo y reabastecimiento",
        # (icono, tono, etiqueta, valor, línea secundaria)
        "kpis": [("svg:chart-bar", "blue", "Productos analizados", "3,245", "Productos en el modelo"),
                 ("svg:alert-triangle", "red", "En riesgo de quiebre", "18", "Productos (0.6%)"),
                 ("svg:cart", "blue", "Requieren reposición", "32", "Productos sugeridos"),
                 ("svg:target", "purple", "Precisión del modelo", "87.4%", "MAE del 12.6%")],
        "forecast": {
            "title": "Pronóstico de demanda", "subtitle": "Demanda proyectada para los próximos 30 días",
            "range": "Próximos 30 días",
            "labels": [f"{d} jun" for d in range(1, 31)],
            "tick_labels": ["1 jun", "5 jun", "10 jun", "15 jun", "20 jun", "25 jun", "30 jun"],
            "history": [50000, 55000, 45000, 55000, 65000, 75000, 72000, 77000, 65000, 76000, 88000, 102000, 96000, 100000, 106000],
            "forecast": [106000, 110000, 114000, 118000, 121000, 123000, 126000, 130000, 136000, 142000, 148000, 154000, 160000, 167000, 172000, 178000],
            "y_max": 200000, "y_step": 50000, "split_index": 14,
            "legend": [("Demanda histórica", False), ("Demanda pronosticada", True)],
        },
        "risk": {
            "title": "Distribución de riesgo", "center_value": "3,245", "center_label": "Productos analizados",
            "items": [("Alto", "5% (162)", "red", 162), ("Medio", "18% (583)", "orange", 583), ("Bajo", "77% (2,500)", "green", 2500)],
        },
        "critical": {
            "title": "Top productos críticos", "link": "Ver todos",
            "columns": ["#", "Producto", "Riesgo", "Stock actual", "Acción sugerida"],
            "rows": [(CEMENTO["name"], ALTO, "8", "Comprar 200 uds."), (DISCO_7["name"], ALTO, "5", "Comprar 100 uds."),
                     (BROCA["name"], MEDIO, "7", "Comprar 50 uds."), (TORNILLO["name"], MEDIO, "12", "Comprar 100 uds."),
                     (CINTA["name"], MEDIO, "6", "Comprar 30 uds.")],
        },
    },
}
