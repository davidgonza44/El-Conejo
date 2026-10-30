"""Fixture determinista del modo de referencia visual del módulo Inventario.

Solo se usa en debug con ``?ref=1`` (ver ``pages.py``). Reproduce los valores visibles en las
referencias aprobadas de Resumen, Movimientos, Categorías y Proveedores para validar la
composición a 1600x900; no proviene de la base de datos ni la modifica. En modo normal las
vistas nuevas reciben ``None`` y muestran un estado veraz de "no disponible", y las vistas
existentes (/inventory, /categories) siguen usando sus plantillas y datos reales.
"""


def _p(name, thumb, sku=None):
    return {"name": name, "sku": sku, "thumb": f"img/reference/{thumb}.png"}


def _kpi(icon, tone, label, value, sub, trend=None, trend_tone="green", arrow="up", solid=False, sub_trend=None):
    """Tarjeta KPI: icono, etiqueta, valor, variación (texto/tono/flecha) y línea secundaria.

    ``sub_trend`` coloca la variación al inicio de la línea secundaria (Compras del mes)."""
    return {"icon": icon, "tone": tone, "solid": solid, "label": label, "value": value,
            "trend": trend, "trend_tone": trend_tone, "arrow": arrow, "sub": sub, "sub_trend": sub_trend}


def _pages(active, pages, ellipsis_after=None, last=None):
    items = [{"text": str(n), "active": n == active} for n in pages]
    if ellipsis_after is not None:
        items.append({"text": "...", "gap": True})
    if last is not None:
        items.append({"text": str(last), "active": False})
    return items


SEARCH = "Buscar productos, clientes, reportes..."

# Miniaturas (recortes de referencia, solo modo visual)
CEMENTO = _p("Cemento Portland 50kg", "cemento", "CP50KG")
TALADRO = _p("Taladro Percutor 1/2\" 750W", "taladro", "HPT750W-12")
DISCO = _p("Disco de Corte 4 1/2\"", "disco", "DC45-4.5")
TORNILLO = _p("Tornillo para Madera 1 1/2\"", "tornillo-madera", "TM112")
CANDADO = _p("Candado de Latón 40mm", "candado", "CAD40")
PINTURA_NEGRA = _p("Pintura Esmalte Negro 1/4gl", "pintura-esmalte", "PNT-1/4NG")
CINTA = _p("Cinta Métrica 5m", "cinta-metrica", "CM5M")
BROCA = _p("Broca para Concreto 1/2\"", "broca", "BCON12")
DESTORNILLADORES = _p("Juego de Destornilladores 6 pzs", "destornilladores", "JDT-6")
NIVEL = _p("Nivel de Aluminio 24\"", "nivel", "STHT42074")
TALADRO_20V = _p("Taladro Inalámbrico 20V", "taladro-inalambrico", "TAL-20V")

# ---------------------------------------------------------------------------
# Resumen de Inventario
# ---------------------------------------------------------------------------
SUMMARY = {
    "search_placeholder": SEARCH,
    "kpis": [
        _kpi("svg:cube", "green", "Productos en stock", "3,245", "vs. ayer 3,000", "8.2%"),
        _kpi("svg:alert-triangle", "orange", "Bajo stock", "23", "vs. ayer 19", "4", "red"),
        _kpi("svg:cube", "red", "Sin stock", "8", "vs. ayer 6", "2", "red"),
        _kpi("ti-currency-dollar", "blue", "Valor del inventario", "$ 524,830.00", "vs. ayer $ 451,820.00", "16.2%",
             solid=True),
    ],
    "chart": {
        "title": "Movimientos de inventario en los últimos 7 días",
        "range": "Últimos 7 días",
        "labels": ["25 may.", "26 may.", "27 may.", "28 may.", "29 may.", "30 may.", "31 may."],
        "series": [
            {"label": "Entradas (unidades)", "data": [480, 520, 790, 460, 700, 590, 810]},
            {"label": "Salidas (unidades)", "data": [190, 270, 500, 270, 470, 340, 680], "dashed": True},
        ],
        "y_max": 1000, "y_step": 200,
        "tooltip": {"index": 6, "title": "31 may. 2024", "lines": [("Entradas: 820", False), ("Salidas: 540", True)]},
    },
    "donut": {
        "title": "Inventario por categoría", "center_value": "3,245", "center_label": "Productos totales",
        "items": [("Herramientas eléctricas", "25% (810)", "blue", 810), ("Herramientas manuales", "24% (778)", "green", 778),
                  ("Materiales de construcción", "22% (713)", "orange", 713), ("Ferretería básica", "16% (512)", "purple", 512),
                  ("Protección y seguridad", "7% (228)", "teal", 228), ("Otros", "3% (104)", "gray", 104)],
    },
    "alerts": {
        "title": "Alertas de inventario", "link": "Ver todas",
        "rows": [(CEMENTO, "Stock actual: 8 unidades", ("Bajo stock", "orange")),
                 (TALADRO, "Stock actual: 9 unidades", ("Bajo stock", "orange")),
                 (DISCO, "Stock actual: 5 unidades", ("Bajo stock", "orange")),
                 (TORNILLO, "Stock actual: 0 unidades", ("Agotado", "red")),
                 (CANDADO, "Stock actual: 0 unidades", ("Agotado", "red"))],
    },
    "activity": {
        "title": "Movimientos recientes", "link": "Ver todos",
        "rows": [("ti-download", "green", "Entrada de inventario #ENT-00095", "Proveedor: Importadora Ferremax",
                  "Hoy, 11:42 a.m.", "+ 320 uds.", "up"),
                 ("svg:cart", "blue", "Venta #VTA-000156", "Cliente: Constructora del Norte S.A.",
                  "Hoy, 10:15 a.m.", "- 18 uds.", "down"),
                 ("ti-adjustments-horizontal", "orange", "Ajuste de inventario #AJU-00023", "Motivo: Corrección de conteo",
                  "Ayer, 04:35 p.m.", "- 12 uds.", "down"),
                 ("svg:cube", "green", "Entrada de inventario #ENT-00088", "Proveedor: Aceros y Más S.A.",
                  "Ayer, 03:20 p.m.", "+ 150 uds.", "up"),
                 ("svg:cart", "blue", "Venta #VTA-000155", "Cliente: Palma Verde Constructora",
                  "Ayer, 02:05 p.m.", "- 7 uds.", "down")],
    },
    "low_stock": {
        "title": "Productos con menor stock", "link": "Ver todas",
        "columns": ["Producto", "Categoría", "Stock actual", "Stock mínimo", "Estado", "Última actualización"],
        "rows": [(CEMENTO, "Materiales de construcción", "8", "20", ("Bajo stock", "orange"), "Hoy, 11:42 a.m."),
                 (TALADRO, "Herramientas eléctricas", "9", "15", ("Bajo stock", "orange"), "Hoy, 10:18 a.m."),
                 (DISCO, "Herramientas manuales", "5", "20", ("Bajo stock", "orange"), "Hoy, 10:15 a.m."),
                 (TORNILLO, "Ferretería básica", "0", "30", ("Agotado", "red"), "Ayer, 09:30 a.m."),
                 (CANDADO, "Ferretería básica", "0", "10", ("Agotado", "red"), "Ayer, 08:50 a.m."),
                 (PINTURA_NEGRA, "Materiales de construcción", "2", "12", ("Bajo stock", "orange"), "Ayer, 08:20 a.m.")],
    },
    # (producto, salidas 30d, stock actual, tono del stock, ancho de barra %)
    "rotation": {
        "title": "Productos con mayor rotación",
        "columns": ["Producto", "Salidas (30d)", "Stock actual", "Rotación relativa"],
        "rows": [(CEMENTO, "320", "8", "orange", 100), (TALADRO, "184", "9", "orange", 58),
                 (DISCO, "142", "5", "orange", 44), (PINTURA_NEGRA, "128", "2", "orange", 40),
                 (CANDADO, "96", "0", "red", 30)],
        "total_label": "Total unidades movidas", "total": "1,284 uds.",
    },
    "footer": ("© 2024 Ferretería Pro. Todos los derechos reservados.", "Versión 2.0.0"),
}

# ---------------------------------------------------------------------------
# Movimientos de Inventario
# ---------------------------------------------------------------------------
ENTRADA = ("Entrada", "green")
SALIDA = ("Salida", "red")
AJUSTE = ("Ajuste", "purple")
COMPLETADO = ("Completado", "green")
PENDIENTE = ("Pendiente", "orange")


def _mov(date, time, kind, ref, product, qty, before, after, user, status):
    """Fila del historial; el signo de la cantidad y la dirección del stock fijan el color."""
    qty_tone = "red" if qty.startswith("-") else "green"
    after_tone = "green" if int(after) >= int(before) else "red"
    return {"date": date, "time": time, "kind": kind, "ref": ref, "product": product, "qty": qty,
            "qty_tone": qty_tone, "before": before, "after": after, "after_tone": after_tone,
            "user": user, "status": status}


MOVEMENTS = {
    "search_placeholder": SEARCH,
    "search": "Buscar por producto, código o referencia...",
    "actions": [("Filtros", "ti-filter", False), ("Exportar", "ti-download", False),
                ("Nuevo movimiento", "ti-plus", True)],
    "filters": {
        "inline": [("Tipo", "Todos")],
        "stacked": [("Responsable", "Todos")],
        "date": ("Rango de fechas", "01 may. 2024 - 31 may. 2024"),
        "clear": "Limpiar filtros",
    },
    "kpis": [
        _kpi("svg:download", "blue", "Entradas del día", "128", "vs. ayer  114", "12.4%", solid=True),
        _kpi("svg:upload", "orange", "Salidas del día", "94", "vs. ayer  97", "3.1%", "red", "down", solid=True),
        _kpi("svg:adjust", "purple", "Ajustes realizados", "16", "vs. ayer  15", "6.8%", solid=True),
        _kpi("svg:adjust", "teal", "Movimientos registrados", "—", "Historial total", solid=True),
    ],
    "history": {
        "title": "Historial de movimientos",
        "columns": ["Fecha", "Tipo", "Referencia", "Producto", "Cantidad", "Stock anterior", "Stock actual",
                    "Responsable", "Estado", "Acciones"],
        "rows": [
            _mov("31 may. 2024", "11:02 a.m.", ENTRADA, "ENT-0098", TALADRO, "10", "138", "148",
                 "Carlos Mendoza", COMPLETADO),
            _mov("31 may. 2024", "09:45 a.m.", SALIDA, "SAL-0187", CEMENTO, "-5", "317", "312",
                 "María Rodríguez", COMPLETADO),
            _mov("31 may. 2024", "09:10 a.m.", AJUSTE, "AJU-0026", DISCO, "-2", "98", "96",
                 "Jorge Ramírez", COMPLETADO),
            _mov("30 may. 2024", "02:15 p.m.", ENTRADA, "ENT-0097", BROCA, "50", "0", "50",
                 "Carlos Mendoza", COMPLETADO),
            _mov("30 may. 2024", "11:40 a.m.", SALIDA, "SAL-0186", TORNILLO, "-100", "320", "220",
                 "Pedro Gómez", COMPLETADO),
            _mov("29 may. 2024", "05:25 p.m.", AJUSTE, "AJU-0025", PINTURA_NEGRA, "+3", "9", "12",
                 "María Rodríguez", PENDIENTE),
        ],
        "pagination": {"summary": "Mostrando 1 a 6 de 6 movimientos", "pages": _pages(1, range(1, 2)),
                       "per_page": "8 por página"},
    },
    "donut": {
        "title": "Movimientos por tipo", "center_value": "238", "center_label": "Total",
        "items": [("Entradas", ("128", "(53.8%)"), "blue", 128), ("Salidas", ("94", "(39.5%)"), "orange", 94),
                  ("Ajustes", ("16", "(6.7%)"), "purple", 16)],
    },
    "activity": {
        "title": "Actividad reciente", "link": "Ver toda la actividad",
        "rows": [("svg:download", "green", "Entrada de inventario #ENT-0098", TALADRO["name"], "Hoy, 11:02 a.m.", "+ 10 uds.", "up"),
                 ("svg:upload", "red", "Salida de inventario #SAL-0187", CEMENTO["name"], "Hoy, 09:45 a.m.", "- 5 uds.", "down"),
                 ("svg:adjust", "purple", "Ajuste de stock #AJU-0026", DISCO["name"], "Hoy, 09:10 a.m.", "- 2 uds.", "down"),
                 ("svg:download", "green", "Entrada de inventario #ENT-0097", BROCA["name"], "Ayer, 02:15 p.m.", "+ 50 uds.", "up")],
    },
}

# ---------------------------------------------------------------------------
MOVEMENTS["kpis"][3]["value"] = str(len(MOVEMENTS["history"]["rows"]))

# Datos adicionales de las capas visuales.
# Ninguna interacción de referencia persiste estos valores.
MOVEMENTS["layers"] = {
    "date": "31 may. 2024 11:02 a.m.",
    "users": ["Carlos Mendoza", "María Rodríguez", "Jorge Ramírez", "Ana Torres", "Pedro Gómez", "Luis Fernández"],
    "types": ["Entrada", "Salida", "Ajuste"],
    "statuses": ["Completado", "Pendiente"],
    "categories": ["Herramientas Eléctricas", "Herramientas manuales", "Materiales de construcción", "Ferretería básica"],
    "details": {
        "AJU-0025": {
            "category": "Pinturas", "reason": "Conteo físico",
            "notes": "Ajuste de inventario por conteo físico",
        },
        "ENT-0098": {
            "category": "Herramientas Eléctricas", "subcategory": "Taladros", "barcode": "7501234567890",
            "reason": "Compra a proveedor",
            "notes": "Recepción de mercancía según factura FAC-4487.\nProveedor: Herramientas del Centro S.A. de C.V.",
        },
    },
    "extra_activities": [
        _mov("29 may. 2024", "03:18 p.m.", ENTRADA, "ENT-0096", _p('Clavos 2\"', "tornillo-madera", "CLAVO2"),
             "200", "0", "200", "Luis Fernández", COMPLETADO),
        _mov("29 may. 2024", "11:22 a.m.", SALIDA, "SAL-0185", _p("Pegamento PVC 1/4gl", "pintura-esmalte", "PEG-1/4"),
             "-8", "20", "12", "Ana Torres", COMPLETADO),
    ],
}

# Gestión de Productos
# ---------------------------------------------------------------------------
DISPONIBLE = ("Disponible", "green")
BAJO_STOCK = ("Bajo stock", "orange")
AGOTADO = ("Agotado", "red")
VS_ABRIL = "vs. 01 abr. - 30 abr. 2024"


def _prod(code, product, brand, category, price, stock, status, updated):
    """Fila del catálogo; el tono del stock sigue al estado (verde/naranja/rojo)."""
    return {"code": code, "product": product, "brand": brand, "category": category, "price": price,
            "stock": stock, "stock_tone": status[1], "status": status, "updated": updated}


PRODUCTS = {
    "search_placeholder": SEARCH,
    "search": "Buscar producto, código o categoría...",
    "actions": [("Filtros", "ti-filter", False), ("Importar CSV", "ti-upload", False),
                ("Exportar", "ti-download", False), ("Nuevo producto", "ti-plus", True)],
    "filters": {"inline": [("Categoría", "Todas"), ("Estado", "Todos"), ("Proveedor", "Todas")],
                "clear": "Limpiar filtros"},
    "kpis": [
        _kpi("svg:cube", "blue", "Total de productos", "1,247", VS_ABRIL, "12.5%"),
        _kpi("svg:alert-triangle", "orange", "Bajo stock", "28", VS_ABRIL, "6", "red"),
        _kpi("svg:cube", "red", "Sin stock", "12", VS_ABRIL, "3", "red"),
        _kpi("svg:tag", "green", "Categorías activas", "16", VS_ABRIL, "2"),
    ],
    "catalog": {
        "title": "Catálogo de productos", "link": "Ver todo el catálogo",
        "columns": ["Imagen", "Código", "Producto", "Categoría", "Precio", "Stock", "Estado",
                    "Última actualización", "Acciones"],
        "rows": [
            _prod("TAL-750W", TALADRO, "Bosch - GSB 13 RE", "Herramientas eléctricas", "$ 1,850.00", "24", DISPONIBLE, "Hoy, 11:42 a.m."),
            _prod("DIS-4-1/2", DISCO, "DeWalt - DW8062", "Herramientas manuales", "$ 28.50", "156", DISPONIBLE, "Hoy, 10:15 a.m."),
            _prod("CEM-50KG", CEMENTO, "Cemex - Tipo I", "Materiales de construcción", "$ 245.00", "8", BAJO_STOCK, "Hoy, 09:08 a.m."),
            _prod("DES-6PZ", DESTORNILLADORES, "Truper - JDT-6", "Herramientas manuales", "$ 320.00", "42", DISPONIBLE, "Ayer, 04:35 p.m."),
            _prod("NIV-24", NIVEL, "Stanley - STHT42074", "Herramientas manuales", "$ 285.00", "18", BAJO_STOCK, "Ayer, 02:20 p.m."),
            _prod("BRO-1/2", BROCA, "Truper - BCF-1/2", "Herramientas eléctricas", "$ 45.00", "0", AGOTADO, "Ayer, 11:05 a.m."),
            _prod("CIN-5M", CINTA, "Stanley - STHT30266", "Herramientas manuales", "$ 95.00", "67", DISPONIBLE, "Ayer, 09:20 a.m."),
            _prod("TOR-1-1/2", TORNILLO, "Fixser - TMA112", "Ferretería básica", "$ 0.85", "12", BAJO_STOCK, "01 may., 04:15 p.m."),
        ],
        "pagination": {"summary": "Mostrando 1 a 8 de 1,247 productos", "pages": _pages(1, range(1, 6), True, 156),
                       "per_page": "8 por página"},
    },
    # (nombre, cantidad, porcentaje, ancho de barra en % del carril)
    "categories": {
        "title": "Categorías", "link": "Ver todas",
        "rows": [("Herramientas eléctricas", "285", "23%", 39), ("Herramientas manuales", "341", "27%", 52),
                 ("Materiales de construcción", "268", "21%", 41), ("Ferretería básica", "187", "15%", 28),
                 ("Protección y seguridad", "98", "8%", 17), ("Otros", "68", "5%", 9)],
    },
    "alerts": {
        "title": "Alertas de inventario", "link": "Ver todas",
        "rows": [(CEMENTO, "Stock actual: 8 unidades", BAJO_STOCK),
                 (BROCA, "Stock actual: 0 unidades", AGOTADO),
                 (CINTA, "Stock actual: 3 unidades", BAJO_STOCK),
                 (TORNILLO, "Stock actual: 12 unidades", BAJO_STOCK),
                 (DISCO, "Stock actual: 15 unidades", BAJO_STOCK)],
    },
    "recent": {"title": "Productos recientes", "link": "Ver todos", "product": TALADRO_20V,
               "code": "Código: TAL-20V", "stock": "Stock: 35", "when": "Hoy, 11:30 a.m."},
}

# ---------------------------------------------------------------------------
# Categorías
# ---------------------------------------------------------------------------
ACTIVA = ("Activa", "green")


def _cat(icon, tone, name, description, products, value):
    return {"icon": icon, "tone": tone, "name": name, "description": description,
            "products": f"{products} productos", "value": value, "status": ACTIVA}


CATEGORIES = {
    # Synthetic layer data, available only in the existing debug reference route.
    "layers": {
        "created": "15 de enero de 2024, 10:30 a. m.",
        "updated": "10 de octubre de 2024, 02:15 p. m.",
        "recent": [
            {"name": 'Taladro Percutor 1/2” 800W', "sku": "EL-001", "price": "$ 1,250.00", "thumb": "img/reference/taladro.png"},
            {"name": 'Sierra Circular 7 1/4” 1500W', "sku": "EL-002", "price": "$ 2,890.00"},
            {"name": 'Esmeriladora Angular 4 1/2”', "sku": "EL-003", "price": "$ 980.00"},
            {"name": "Rotomartillo SDS Plus", "sku": "EL-004", "price": "$ 1,760.00"},
            {"name": 'Lijadora Orbital 1/4”', "sku": "EL-005", "price": "$ 850.00"},
        ],
    },
    "search_placeholder": SEARCH,
    "actions": [("Exportar", "ti-download", False), ("Nueva categoría", "ti-plus", True)],
    "kpis": [
        _kpi("svg:box", "blue", "Total categorías", "12", "Activas: 12  ·  Inactivas: 0", solid="plain"),
        _kpi("svg:tag", "green", "Productos en categorías", "3,245", "En todas las categorías", solid="plain"),
        _kpi("svg:cube", "purple", "Valor total en inventario", "$ 486,250.00", "En todas las categorías", solid="plain"),
    ],
    "donut": {
        "title": "Distribución por categorías", "center_value": "", "center_label": "",
        "items": [("Herramientas eléctricas", "28%", "blue", 28), ("Herramientas manuales", "24%", "green", 24),
                  ("Materiales de construcción", "22%", "orange", 22), ("Ferretería básica", "16%", "purple", 16),
                  ("Protección y seguridad", "7%", "teal", 7), ("Otros", "3%", "gray", 3)],
    },
    "filters": {"search": "Buscar categoría...", "selects": [("Estado", "Todas"), ("Ordenar por", "Nombre (A-Z)")],
                "clear": "Limpiar filtros"},
    "table": {
        "title": "Listado de categorías",
        "columns": ["Categoría", "Descripción", "Productos", "Valor en inventario", "Estado", "Acciones"],
        "rows": [
            _cat("svg:bolt", "blue", "Herramientas eléctricas", "Herramientas eléctricas y accesorios", "901", "$ 136,450.00"),
            _cat("svg:wrench", "green", "Herramientas manuales", "Herramientas manuales de uso general", "778", "$ 117,280.00"),
            _cat("svg:box", "orange", "Materiales de construcción", "Materiales para construcción y acabados", "713", "$ 107,190.00"),
            _cat("svg:hammer", "purple", "Ferretería básica", "Tornillos, clavos, tuercas y elementos de fijación", "512", "$ 77,850.00"),
            _cat("svg:shield", "teal", "Protección y seguridad", "Equipos de protección personal y seguridad", "228", "$ 34,120.00"),
            _cat("svg:dots", "gray", "Otros", "Otros productos y accesorios varios", "113", "$ 13,360.00"),
        ],
        "pagination": {"summary": "Mostrando 1 a 6 de 12 categorías", "pages": _pages(1, range(1, 3)),
                       "per_page": "10 por página"},
    },
    "footer": SUMMARY["footer"],
}

# ---------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------
ACTIVO = ("Activo", "green")
INACTIVO = ("Inactivo", "red")
HERRAMIENTAS = ("Herramientas", "blue")
MATERIALES = ("Materiales", "green")
CONSTRUCCION = ("Construcción", "purple")


def _sup(avatar, tone, name, rif, contact, role, category, phone, email, status, last, total):
    return {"avatar": avatar, "tone": tone, "name": name, "rif": rif, "contact": contact, "role": role,
            "category": category, "phone": phone, "email": email, "status": status, "last": last, "total": total}


SUPPLIERS = {
    "search_placeholder": SEARCH,
    "actions": [("Filtros", "ti-filter", False), ("Exportar CSV", "ti-upload", False),
                ("Nuevo proveedor", "ti-plus", True)],
    "kpis": [
        _kpi("svg:users", "blue", "Total Proveedores", "28", "Activos: 24  ·  Inactivos: 4"),
        _kpi("svg:cart", "green", "Compras del mes", "$ 248,750.00", "vs. 01 abr. - 30 abr. 2024", sub_trend="15.6%"),
        _kpi("svg:cube-o", "purple", "Órdenes de compra", "16", "Pendientes: 5  ·  Completadas: 11"),
        _kpi("svg:star-o", "orange", "Proveedores preferidos", "8", "Con mejor desempeño"),
    ],
    "filters": {"search": "Buscar proveedor por nombre, RIF o contacto...",
                "selects": [("Estado", "Todos"), ("Categoría", "Todas"), ("Ciudad", "Todas")],
                "clear": "Limpiar filtros"},
    "table": {
        "columns": ["Proveedor", "Contacto", "Categoría", "Teléfono", "Correo", "Estado", "Última compra",
                    "Monto total compras", "Acciones"],
        "rows": [
            _sup("HF", "navy", "Ferremax, C.A.", "J-30567894-1", "Carlos Mendoza", "Gerente de Ventas",
                 HERRAMIENTAS, "0412-555.1234", "ventas@ferremax.com", ACTIVO, "31/05/2024", "$ 58,250.00"),
            _sup("AC", "outline-red", "Aceros del Centro, C.A.", "J-29765432-7", "María López", "Ejecutiva Comercial",
                 MATERIALES, "0424-444.5678", "contacto@aceroscentro.com", ACTIVO, "30/05/2024", "$ 46,780.00"),
            _sup("svg:home", "orange", "Construcción Total, C.A.", "J-31234567-9", "Luis Fernández", "Representante",
                 CONSTRUCCION, "0416-777.8899", "ventas@construcciontotal.com", ACTIVO, "29/05/2024", "$ 38,610.00"),
            _sup("DI", "green", "Distribuidora Industrial, C.A.", "J-29543210-3", "Ana Pérez", "Coordinadora",
                 HERRAMIENTAS, "0414-222.3344", "info@distindustrial.com", INACTIVO, "15/04/2024", "$ 12,450.00"),
            _sup("MP", "yellow", "Materiales Premier, C.A.", "J-30987654-2", "Jorge Ramírez", "Gerente General",
                 MATERIALES, "0412-888.2211", "ventas@materialespremier.com", ACTIVO, "28/05/2024", "$ 31,920.00"),
            _sup("SF", "teal", "Suministros Ferreteros, C.A.", "J-31654321-8", "Pedro González", "Ejecutivo de Cuenta",
                 HERRAMIENTAS, "0416-333.4455", "pedro@suministrosf.com", ACTIVO, "27/05/2024", "$ 22,340.00"),
            _sup("CP", "purple", "Cemento Plus, C.A.", "J-30123456-0", "Andrés Silva", "Asesor Comercial",
                 CONSTRUCCION, "0412-999.6677", "comercial@cementoplus.com", ACTIVO, "26/05/2024", "$ 18,400.00"),
            _sup("IN", "gray", "Insumos del Norte, C.A.", "J-29098765-4", "Valentina Rojas", "Representante",
                 MATERIALES, "0414-666.7788", "ventas@insumosnorte.com", INACTIVO, "10/03/2024", "$ 6,150.00"),
        ],
        "pagination": {"summary": "Mostrando 1 a 8 de 28 proveedores", "pages": _pages(1, range(1, 5)),
                       "per_page": "8 por página"},
    },
}
