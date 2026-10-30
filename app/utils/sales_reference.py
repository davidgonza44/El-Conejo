"""Fixture determinista del modo de referencia visual del módulo Ventas.

Solo se usa en debug con ``?ref=1`` (ver ``pages.py``). Reproduce los valores visibles en el
collage aprobado de Ventas (Dashboard, Lista, Nueva venta, Detalle, Cotizaciones, Pedidos,
Clientes, Reportes de ventas e Impresión/PDF) para validar la composición a 1600 px de ancho;
no proviene de la base de datos ni la modifica. En modo normal las vistas reciben ``None`` y
muestran un estado veraz de "no disponible" que enlaza con las notas de entrega reales.
"""
from app.utils.inventory_reference import BROCA, CEMENTO, DISCO, TALADRO, TORNILLO, VS_ABRIL, _kpi, _pages, PRODUCTS

SEARCH = "Buscar productos, clientes, ventas..."
DATE_RANGE = "01 may. 2024 - 31 may. 2024"
COMPARE = "Comparar con: 01 abr. 2024 - 30 abr. 2024"

# Estados (etiqueta, tono) compartidos por ventas, cotizaciones y pedidos.
COMPLETADA = ("Completada", "green")
EN_PROCESO = ("En proceso", "orange")
CANCELADA = ("Cancelada", "red")
COMPLETADO = ("Completado", "green")
PENDIENTE = ("Pendiente", "orange")
CANCELADO = ("Cancelado", "red")
ENVIADA = ("Enviada", "blue")
ACEPTADA = ("Aceptada", "green")
VENCIDA = ("Vencida", "red")
RECHAZADA = ("Rechazada", "red")
ACTIVO = ("Activo", "green")

# Clientes de referencia (nombre, RIF, teléfono, correo).
CONSTRUCTORA = ("Constructora del Norte S.A.", "J-12345678-9", "0412-555-1234", "contacto@constructora.com")
FERRETERIA_SJ = ("Ferretería San José", "J-98765432-1", "0412-333-5678", "ventas@ferreteriasj.com")
INVERSIONES = ("Inversiones López C.A.", "J-11223344-5", "0412-777-8899", "info@inversioneslopez.com")
DISTRIBUIDORA = ("Distribuidora Ramos", "J-55667788-2", "0412-111-2222", "ventas@ramosdistrib.com")
PUBLICO = ("Público en general", "–", "–", "–")

SALE_NUMBER = "VTA-000156"

# Menú del módulo: (clave, texto, endpoint). Un solo orden para todas las pantallas.
MENU = [
    ("sales_new", "Nueva venta", "pages.sales_new"),
    ("sales_quotes", "Cotizaciones", "pages.sales_quotes"),
    ("sales_orders", "Pedidos", "pages.sales_orders"),
    ("sales_customers", "Clientes", "pages.sales_customers"),
]


def _sale(number, date, customer, total, method, status):
    return {"number": number, "date": date, "customer": customer, "total": total, "method": method, "status": status}


def _filters(*labels):
    """Selectores de la barra de filtros: (icono, etiqueta)."""
    icons = {"fechas": "ti-calendar", "estados": "ti-tag", "pago": "ti-credit-card", "filtros": "ti-filter"}
    return [(icons[key], label) for key, label in labels]


SALES_CHART = {
    "title": "Ventas de los últimos 12 meses", "range": "Mensual",
    "labels": ["Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic", "Feb", "Mar", "Abr", "May"],
    "series": [
        {"label": "Ventas ($)", "data": [260000, 280000, 400000, 400000, 355000, 445000, 445000, 500000, 395000, 500000, 500000]},
        {"label": "Ventas año anterior ($)", "data": [120000, 160000, 245000, 320000, 205000, 285000, 225000, 320000, 245000, 285000, 445000], "dashed": True},
    ],
    "legend": [("Ventas ($)", False), ("Ventas año anterior ($)", True)],
    "y_max": 600000, "y_step": 150000,
    "tooltip": {"index": 10, "title": "May 2024", "lines": [("$ 486,250.00", True)], "dx": -78, "dy": 18},
}

CATEGORY_DONUT = {
    "title": "Ventas por categoría", "range": "Ingresos",
    "center_value": "$ 486,250", "center_label": "Ventas totales",
    "items": [("Herramientas eléctricas", "41% ($198,450)", "blue", 198450),
              ("Herramientas manuales", "26% ($126,870)", "green", 126870),
              ("Materiales de construcción", "15% ($72,340)", "orange", 72340),
              ("Ferretería básica", "10% ($48,260)", "purple", 48260),
              ("Protección y seguridad", "6% ($27,180)", "teal", 27180),
              ("Otros", "3% ($13,150)", "gray", 13150)],
}

PAYMENT_BARS = {
    "title": "Métodos de pago", "range": "Este mes",
    "bars": {"values": [194500, 145875, 97250, 48625], "value_labels": ["$ 194,500", "$ 145,875", "$ 97,250", "$ 48,625"],
             "tones": ["blue", "green", "orange", "purple"], "y_max": 200000, "y_step": 50000,
             "x_labels": [["Transferencia", "40%"], ["Efectivo", "30%"], ["Punto de venta", "20%"], ["Crédito", "10%"]]},
}

# ---------------------------------------------------------------------------
# Dashboard de Ventas
# ---------------------------------------------------------------------------
DASHBOARD = {
    "search_placeholder": SEARCH,
    "date_range": DATE_RANGE, "compare": COMPARE,
    "kpis": [
        _kpi("ti-currency-dollar", "blue", "Ventas del mes", "$ 486,250.00", VS_ABRIL, "18.6%", solid=True),
        _kpi("svg:cart", "green", "Órdenes de venta", "142", VS_ABRIL, "12.5%"),
        _kpi("svg:tag", "purple", "Ticket promedio", "$ 3,424.30", VS_ABRIL, "5.3%"),
        _kpi("svg:user", "orange", "Clientes nuevos", "28", VS_ABRIL, "17.5%"),
    ],
    "sales_chart": SALES_CHART,
    "donut": CATEGORY_DONUT,
    "payments": PAYMENT_BARS,
    # (icono, tono, número, cliente, fecha, total, estado)
    "latest": {
        "title": "Últimas órdenes de venta", "columns": ["#", "Cliente", "Fecha", "Total", "Estado"],
        "rows": [("svg:cart", "blue", "#VTA-000154", CONSTRUCTORA[0], "Hoy, 11:42 a.m.", "$ 18,450.00", COMPLETADA),
                 ("svg:cart", "green", "#VTA-000153", "Talleres San José", "Hoy, 10:15 a.m.", "$ 4,320.00", COMPLETADA),
                 ("ti-clock", "blue", "#VTA-000152", PUBLICO[0], "Ayer, 04:35 p.m.", "$ 2,875.00", EN_PROCESO),
                 ("ti-lock", "blue", "#VTA-000151", "Ferretería El Águila C.A.", "Ayer, 03:20 p.m.", "$ 12,600.00", COMPLETADA),
                 ("svg:alert-triangle", "red", "#VTA-000150", INVERSIONES[0], "Ayer, 02:05 p.m.", "$ 1,985.00", CANCELADA)],
    },
    # (n, fecha, cliente, productos, método, estado, total)
    "recent": {
        "title": "Ventas recientes", "link": "Ver todas",
        "columns": ["#", "Fecha", "Cliente", "Productos", "Método de pago", "Estado", "Total"],
        "rows": [(1, "Hoy, 11:42 a.m.", CONSTRUCTORA[0], "12 productos", "Transferencia", COMPLETADA, "$ 18,450.00"),
                 (2, "Hoy, 10:15 a.m.", "Talleres San José", "5 productos", "Efectivo", COMPLETADA, "$ 4,320.00"),
                 (3, "Ayer, 04:35 p.m.", PUBLICO[0], "3 productos", "Tarjeta", EN_PROCESO, "$ 2,875.00"),
                 (4, "Ayer, 03:20 p.m.", "Ferretería El Águila C.A.", "8 productos", "Efectivo", COMPLETADA, "$ 12,600.00"),
                 (5, "Ayer, 02:05 p.m.", INVERSIONES[0], "1 producto", "Crédito", CANCELADA, "$ 1,985.00")],
        "pagination": {"summary": "Mostrando 1 a 5 de 142 ventas", "pages": _pages(1, range(1, 4), True, 29), "per_page": None},
    },
    # (n, cliente, ventas, órdenes)
    "customers": {
        "title": "Clientes destacados", "columns": ["#", "Cliente", "Ventas (Bs.)", "Órdenes"],
        "rows": [(1, CONSTRUCTORA[0], "$ 124,850", "28"), (2, "Talleres San José", "$ 89,430", "24"),
                 (3, INVERSIONES[0], "$ 76,920", "18"), (4, "Ferretería El Águila C.A.", "$ 65,300", "16"),
                 (5, PUBLICO[0], "$ 54,210", "42")],
    },
}

# ---------------------------------------------------------------------------
# Lista de Ventas
# ---------------------------------------------------------------------------
SALES_LIST = {
    "search_placeholder": SEARCH,
    "detail": SALE_NUMBER,
    "search": "Buscar por número de venta, cliente o producto...",
    "primary": ("Nueva venta", "ti-plus"),
    "filters": _filters(("fechas", "Todas las fechas"), ("estados", "Todos los estados"),
                        ("pago", "Todos los métodos de pago"), ("filtros", "Más filtros")),
    "columns": ["#", "Fecha", "Cliente", "Total", "Método de pago", "Estado"],
    "rows": [
        _sale("VTA-000156", "31/05/2024 10:42 AM", CONSTRUCTORA[0], "$ 59,200.00", "Transferencia", COMPLETADA),
        _sale("VTA-000155", "31/05/2024 09:15 AM", PUBLICO[0], "$ 16,750.00", "Efectivo", COMPLETADA),
        _sale("VTA-000154", "30/05/2024 04:30 PM", FERRETERIA_SJ[0], "$ 27,340.00", "Tarjeta", EN_PROCESO),
        _sale("VTA-000153", "30/05/2024 11:20 AM", INVERSIONES[0], "$ 12,680.00", "Efectivo", COMPLETADA),
        _sale("VTA-000152", "29/05/2024 03:45 PM", DISTRIBUIDORA[0], "$ 8,450.00", "Crédito", CANCELADA),
        _sale("VTA-000151", "29/05/2024 11:15 AM", PUBLICO[0], "$ 6,230.00", "Efectivo", COMPLETADA),
        _sale("VTA-000150", "28/05/2024 02:20 PM", CONSTRUCTORA[0], "$ 19,890.00", "Transferencia", COMPLETADA),
        _sale("VTA-000149", "28/05/2024 10:05 AM", FERRETERIA_SJ[0], "$ 22,450.00", "Tarjeta", COMPLETADA),
        _sale("VTA-000148", "27/05/2024 04:10 PM", INVERSIONES[0], "$ 9,780.00", "Efectivo", CANCELADA),
        _sale("VTA-000147", "27/05/2024 09:30 AM", PUBLICO[0], "$ 4,320.00", "Efectivo", COMPLETADA),
    ],
    "pagination": {"summary": "Mostrando 1 a 10 de 142 ventas", "pages": _pages(1, range(1, 4), True, 15), "per_page": None},
}

# ---------------------------------------------------------------------------
# Nueva venta (presentación; no crea ventas ni altera stock)
# ---------------------------------------------------------------------------
NEW_SALE = {
    # Reuse the deterministic catalog; existing sale-line prices remain authoritative.
    "catalog": PRODUCTS["catalog"]["rows"],
    "search_placeholder": SEARCH,
    "back": "Nueva venta",
    "customer": {
        "title": "Información del cliente", "search": "Buscar cliente por nombre, RIF o teléfono...",
        "name": CONSTRUCTORA[0], "line1": "RIF: J-12345678-9 · Tel: (0412) 555-1234", "line2": "Ref: crédito",
    },
    "sale": {
        "title": "Información de la venta",
        # (etiqueta, valor, con desplegable)
        "fields": [("Fecha de venta", "31/05/2024", False), ("Vendedor", "Administrador", True),
                   ("Método de pago", "Transferencia", True), ("Condición de pago", "Contado", True)],
    },
    "products": {
        "title": "Productos", "search": "Buscar producto por nombre o código...",
        "actions": [("Escanear", "ti-scan", False), ("Agregar producto", "ti-plus", True)],
        "columns": ["#", "Producto", "Precio Unit.", "Cantidad", "Descuento", "Total", ""],
        # (producto, código, precio, cantidad, descuento, total)
        "rows": [(TALADRO, "TAL-750W", "$ 120.00", "2", "0%", "$ 240.00"),
                 (DISCO, "DIS-4-1/2", "$ 2.50", "10", "0%", "$ 25.00"),
                 (CEMENTO, "CEM-50KG", "$ 8.90", "5", "0%", "$ 44.50")],
        "count": "3 productos agregados",
        "totals": [("Subtotal", "$ 309.50", False), ("Descuento", "$ 0.00", False), ("Total", "$ 309.50", True)],
    },
}

# ---------------------------------------------------------------------------
# Detalle de venta
# ---------------------------------------------------------------------------
SALE_ITEMS = [(TALADRO, "TAL-001", "$ 120.00", "10", "0%", "$ 1,200.00"),
              (DISCO, "DIS-004", "$ 2.50", "50", "0%", "$ 125.00"),
              (CEMENTO, "CEM-001", "$ 8.90", "200", "0%", "$ 1,780.00"),
              (BROCA, "BRO-002", "$ 3.20", "100", "0%", "$ 320.00"),
              (TORNILLO, "TOR-001", "$ 0.15", "500", "0%", "$ 75.00")]

SALE_DETAIL = {
    "search_placeholder": SEARCH,
    "number": SALE_NUMBER, "title": f"Venta #{SALE_NUMBER}", "status": COMPLETADA,
    "subtitle": "Información detallada de la venta.",
    "actions": [("Imprimir", "ti-printer"), ("Enviar", "ti-send"), ("Más acciones", "ti-dots")],
    "info": {
        "title": "Información general",
        # (etiqueta, valor, estilo: None | "link" | "badge"; línea secundaria)
        "rows": [("Fecha y hora", "31/05/2024 10:42 AM", None, None),
                 ("Cliente", CONSTRUCTORA[0], "link", "RIF: J-12345678-9"),
                 ("Vendedor", "Administrador", "link", None),
                 ("Método de pago", "Transferencia", None, None),
                 ("Condición de pago", "Contado", None, None),
                 ("Estado", COMPLETADA, "badge", None)],
    },
    "totals": {
        "title": "Totales",
        "rows": [("Subtotal", "$ 59,200.00"), ("Descuento", "$ 0.00"), ("Impuestos (IVA 16%)", "$ 9,472.00")],
        "total": ("Total", "$ 68,672.00"),
    },
    "tabs": ["Productos (12)", "Pagos (1)", "Notas", "Historial"],
    "columns": ["#", "Producto", "Código", "Precio Unit.", "Cantidad", "Descuento", "Total"],
    "rows": SALE_ITEMS,
    "pagination": {"summary": "Mostrando 1 a 5 de 12 productos", "pages": _pages(1, range(1, 4)), "per_page": None},
}

# ---------------------------------------------------------------------------
# Cotizaciones
# ---------------------------------------------------------------------------
QUOTES = {
    "search_placeholder": SEARCH,
    "search": "Buscar por número de cotización, cliente o producto...",
    "primary": ("Nueva cotización", "ti-plus"),
    "filters": _filters(("fechas", "Todas las fechas"), ("estados", "Todos los estados"), ("filtros", "Más filtros")),
    "columns": ["#", "Fecha", "Cliente", "Validez", "Total", "Estado", ""],
    "rows": [
        ("COT-000089", "31/05/2024", CONSTRUCTORA[0], "30 días", "$ 52,300.00", ENVIADA),
        ("COT-000088", "30/05/2024", FERRETERIA_SJ[0], "15 días", "$ 18,450.00", PENDIENTE),
        ("COT-000087", "30/05/2024", PUBLICO[0], "7 días", "$ 7,890.00", ACEPTADA),
        ("COT-000086", "29/05/2024", INVERSIONES[0], "30 días", "$ 23,670.00", VENCIDA),
        ("COT-000085", "29/05/2024", DISTRIBUIDORA[0], "15 días", "$ 12,340.00", PENDIENTE),
        ("COT-000084", "28/05/2024", CONSTRUCTORA[0], "30 días", "$ 48,900.00", ACEPTADA),
        ("COT-000083", "27/05/2024", PUBLICO[0], "7 días", "$ 5,670.00", RECHAZADA),
        ("COT-000082", "27/05/2024", FERRETERIA_SJ[0], "15 días", "$ 16,780.00", ENVIADA),
        ("COT-000081", "26/05/2024", INVERSIONES[0], "30 días", "$ 22,450.00", PENDIENTE),
        ("COT-000080", "26/05/2024", DISTRIBUIDORA[0], "15 días", "$ 9,890.00", ACEPTADA),
    ],
    "pagination": {"summary": "Mostrando 1 a 10 de 89 cotizaciones", "pages": _pages(1, range(1, 4), True, 9), "per_page": None},
}

# ---------------------------------------------------------------------------
# Pedidos
# ---------------------------------------------------------------------------
ORDERS = {
    "search_placeholder": SEARCH,
    "search": "Buscar por número de pedido, cliente o producto...",
    "primary": ("Nuevo pedido", "ti-plus"),
    "filters": _filters(("fechas", "Todas las fechas"), ("estados", "Todos los estados"), ("filtros", "Más filtros")),
    "columns": ["#", "Fecha", "Cliente", "Productos", "Entrega estimada", "Estado"],
    "rows": [
        ("PED-000067", "31/05/2024", CONSTRUCTORA[0], "15", "05/06/2024", EN_PROCESO),
        ("PED-000066", "30/05/2024", FERRETERIA_SJ[0], "8", "03/06/2024", PENDIENTE),
        ("PED-000065", "30/05/2024", PUBLICO[0], "3", "01/06/2024", EN_PROCESO),
        ("PED-000064", "29/05/2024", INVERSIONES[0], "12", "05/06/2024", COMPLETADO),
        ("PED-000063", "29/05/2024", DISTRIBUIDORA[0], "6", "04/06/2024", PENDIENTE),
        ("PED-000062", "28/05/2024", CONSTRUCTORA[0], "20", "06/06/2024", EN_PROCESO),
        ("PED-000061", "28/05/2024", FERRETERIA_SJ[0], "10", "01/06/2024", CANCELADO),
        ("PED-000060", "27/05/2024", PUBLICO[0], "4", "01/06/2024", COMPLETADO),
        ("PED-000059", "27/05/2024", INVERSIONES[0], "8", "04/06/2024", EN_PROCESO),
        ("PED-000058", "26/05/2024", DISTRIBUIDORA[0], "5", "03/06/2024", PENDIENTE),
    ],
    "pagination": {"summary": "Mostrando 1 a 10 de 67 pedidos", "pages": _pages(1, range(1, 4), True, 7), "per_page": None},
}

# ---------------------------------------------------------------------------
# Clientes (desde Ventas)
# ---------------------------------------------------------------------------
CUSTOMERS = {
    "search_placeholder": SEARCH,
    "search": "Buscar por nombre, RIF, teléfono o correo...",
    "primary": ("Nuevo cliente", "ti-plus"),
    "columns": ["Nombre / Razón Social", "RIF", "Teléfono", "Correo", "Estado"],
    "rows": [c + (ACTIVO,) for c in (CONSTRUCTORA, FERRETERIA_SJ, INVERSIONES, DISTRIBUIDORA, PUBLICO)],
    "pagination": {"summary": "Mostrando 1 a 5 de 284 clientes", "pages": _pages(1, range(1, 4), True, 57), "per_page": None},
}

# ---------------------------------------------------------------------------
# Reportes de ventas
# ---------------------------------------------------------------------------
SALES_REPORTS = {
    "date_range": DATE_RANGE, "compare": COMPARE,
    "export": ("Exportar", "ti-download"),
    "tabs": ["Resumen", "Ventas por producto", "Ventas por cliente", "Ventas por categoría", "Métodos de pago", "Tendencias"],
    # En el panel las tarjetas no llevan línea secundaria (solo etiqueta, valor y variación).
    "kpis": [
        _kpi("ti-currency-dollar", "blue", "Ventas totales", "$ 486,250.00", None, "18.6%", solid=True),
        _kpi("svg:cart", "green", "Total de ventas", "142", None, "12.5%"),
        _kpi("svg:tag", "purple", "Ticket promedio", "$ 3,424.30", None, "5.3%"),
        _kpi("svg:user", "orange", "Clientes nuevos", "78", None, "13.4%"),
    ],
    "trend": {
        "title": "Tendencia de ventas", "range": "Mensual",
        "legend": [("Ventas ($)", "blue"), ("Número de ventas", "gray")],
        "labels": ["Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic", "Feb", "Mar", "Abr", "May"],
        "bars": [260000, 300000, 350000, 300000, 300000, 300000, 320000, 400000, 340000, 350000, 400000],
        "line": [120, 130, 150, 148, 148, 148, 152, 168, 160, 160, 160],
        "y_max": 600000, "y_step": 100000, "y2_max": 200, "y2_step": 50,
    },
}

# ---------------------------------------------------------------------------
# Impresión / PDF de venta (vista previa de referencia)
# ---------------------------------------------------------------------------
SALE_PRINT = {
    "file": f"venta_{SALE_NUMBER}.pdf", "page": "1 / 1", "zoom": "100%",
    "brand": ("Ferretería Pro", "Sistema de Gestión"),
    "doc_title": "FACTURA DE VENTA", "number": f"#{SALE_NUMBER}",
    "company": ["Ferretería Pro C.A.", "RIF: J-00000000-0", "Av. Bolívar, Valencia, Venezuela",
                "Tel: (0241) 000-0000", "info@ferreteriapro.com"],
    # (etiqueta, valor, tono)
    "meta": [("Fecha:", "31/05/2024 10:42 AM", None), ("Método de pago:", "Transferencia", None),
             ("Condición de pago:", "Contado", None), ("Estado:", "Completada", "green")],
    "customer": ["Cliente:", CONSTRUCTORA[0], "RIF: J-12345678-9", "Tel: (0412) 555-1234", "Dirección: Valencia, Carabobo"],
    "columns": ["#", "Producto", "Código", "Cantidad", "P. Unitario", "Subtotal"],
    "rows": [(p["name"], code, qty, price, total) for p, code, price, qty, _d, total in SALE_ITEMS[:3]],
    "totals": [("Subtotal:", "$ 59,200.00"), ("IVA (16%):", "$ 9,472.00")],
    "total": ("TOTAL:", "$ 68,672.00"),
}
