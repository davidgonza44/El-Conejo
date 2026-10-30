/* Fixture determinista del modo de referencia visual de Inicio.

   Solo se carga en debug con /dashboard?ref=1. Reproduce los valores
   visibles en references/01_inicio/01_inicio.png para validar la
   composición; no proviene de la base de datos ni la modifica. */

window.INICIO_REFERENCE = {
    kpis: [
        { icon: "ti-currency-dollar", tone: "blue", solid: true, label: "Ventas de hoy",
          value: "$ 18,450.00", trend: "12.4%", sub: "vs. ayer $ 16,410.00" },
        { icon: "svg:cube", tone: "green", label: "Productos en stock",
          value: "3,245", trend: "8.2%", sub: "vs. ayer 3,000" },
        { icon: "svg:users", tone: "purple", label: "Clientes atendidos",
          value: "47", trend: "6.8%", sub: "vs. ayer 44" },
        { icon: "svg:alert-triangle", tone: "orange", label: "Alertas activas",
          value: "12", trend: "9.1%", trendTone: "red", sub: "vs. ayer 11" },
    ],
    sales: {
        labels: ["25 may.", "26 may.", "27 may.", "28 may.", "29 may.", "30 may.", "31 may."],
        series: [
            { label: "Ventas ($)", data: [11000, 14200, 19300, 14300, 16200, 14300, 18450] },
            { label: "Ventas semana anterior ($)", data: [4600, 7000, 11000, 7000, 11000, 7800, 8900], dashed: true },
        ],
        yMax: 25000,
        yStep: 5000,
        tickSuffix: "K",
        tooltip: { index: 6, title: "31 may. 2024", value: "$ 18,450.00" },
    },
    category: {
        centerValue: "3,245",
        centerLabel: "Productos totales",
        items: [
            { label: "Herramientas eléctricas", value: 901, sub: "28% (901)" },
            { label: "Herramientas manuales", value: 778, sub: "24% (778)" },
            { label: "Materiales de construcción", value: 713, sub: "22% (713)" },
            { label: "Ferretería básica", value: 512, sub: "16% (512)" },
            { label: "Protección y seguridad", value: 228, sub: "7% (228)" },
            { label: "Otros", value: 113, sub: "3% (113)" },
        ],
    },
    activity: [
        { icon: "svg:cart", tone: "green", title: "Venta #VTA-000156",
          desc: "Cliente: Constructora del Norte S.A.", time: "Hoy, 11:42 a.m.", amount: "+ $ 8,920.00", direction: "up" },
        { icon: "svg:cube", tone: "blue", title: "Entrada de inventario #ENT-00089",
          desc: "Proveedor: Importadora Ferremax", time: "Hoy, 10:15 a.m.", amount: "+ 320 uds.", direction: "up" },
        { icon: "svg:cart", tone: "orange", title: "Venta #VTA-000155",
          desc: "Cliente: Publico en general", time: "Hoy, 09:08 a.m.", amount: "+ $ 2,450.00", direction: "up" },
        { icon: "svg:alert-triangle", tone: "red", title: "Ajuste de inventario #AJU-00023",
          desc: "Motivo: Conteo físico", time: "Ayer, 04:35 p.m.", amount: "- 12 uds.", direction: "down" },
        { icon: "svg:cube", tone: "blue", title: "Entrada de inventario #ENT-00088",
          desc: "Proveedor: Aceros y Más S.A.", time: "Ayer, 03:20 p.m.", amount: "+ 150 uds.", direction: "up" },
    ],
    lowStock: [
        { icon: "ti-building", title: "Cemento Portland 50kg", desc: "Stock actual: 8 unidades", badge: "Bajo stock", depleted: false },
        { icon: "ti-circle-dashed", title: "Disco de Corte 7\"", desc: "Stock actual: 5 unidades", badge: "Bajo stock", depleted: false },
        { icon: "ti-line", title: "Broca para Concreto 1/2\"", desc: "Stock actual: 7 unidades", badge: "Bajo stock", depleted: false },
        { icon: "svg:screw", title: "Tornillo para Madera 1 1/2\"", desc: "Stock actual: 12 unidades", badge: "Bajo stock", depleted: false },
        { icon: "ti-ruler-measure", title: "Cinta Métrica 5m", desc: "Stock actual: 6 unidades", badge: "Agotado", depleted: true },
    ],
    /* thumb: miniatura local de referencia (app/static/img/reference); el icono es el respaldo. */
    top: [
        { icon: "ti-tool", thumb: "/static/img/reference/taladro.png", product: "Taladro Percutor 1/2\" 750W", category: "Herramientas eléctricas", units: "148", revenue: "$ 59,200.00" },
        { icon: "ti-circle-dashed", thumb: "/static/img/reference/disco.png", product: "Disco de Corte 4 1/2\"", category: "Herramientas manuales", units: "436", revenue: "$ 31,320.00" },
        { icon: "ti-building", thumb: "/static/img/reference/cemento.png", product: "Cemento Portland 50kg", category: "Materiales de construcción", units: "312", revenue: "$ 27,040.00" },
        { icon: "svg:screw", thumb: "/static/img/reference/tornillo-madera.png", product: "Tornillo para Madera 1 1/2\"", category: "Ferretería básica", units: "522", revenue: "$ 6,780.00" },
        { icon: "ti-ruler-measure", thumb: "/static/img/reference/cinta-metrica.png", product: "Cinta Métrica 5m", category: "Herramientas manuales", units: "398", revenue: "$ 5,970.00" },
    ],
};
