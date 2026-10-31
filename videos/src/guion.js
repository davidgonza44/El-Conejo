// Guion de los videos tutoriales: un video por funcionalidad, un paso por captura.
// Los textos resumen la Guía de uso del sistema (app/templates/help.html).
export const VIDEOS = [
    {
        id: 'ingreso-e-inicio', titulo: 'Ingreso e Inicio', rol: 'Todos los roles',
        resumen: 'Cómo entrar al sistema y qué muestra la pantalla de Inicio.',
        pasos: [
            { captura: 'login-vacio', titulo: 'Pantalla de ingreso', texto: 'Escribe tu usuario o correo y tu contraseña. Si la olvidaste, usa «¿Olvidaste tu contraseña?».' },
            { captura: 'login-datos', titulo: 'Entrar', texto: 'Con los datos completos, pulsa «Ingresar». Cada rol ve solo los módulos que puede usar.' },
            { captura: 'inicio-propuestas', titulo: 'Propuestas pendientes', texto: 'El administrador ve aquí los productos, proveedores y categorías que esperan su aprobación.' },
            { captura: 'inicio-indicadores', titulo: 'Indicadores del día', texto: 'Una tira con lo esencial: productos en stock, clientes atendidos y alertas activas.' },
            { captura: 'inicio-listas', titulo: 'Lo que hay que atender', texto: 'Los movimientos recientes y los productos agotados o por debajo del mínimo.' },
            { captura: 'inicio-menu', titulo: 'Menú lateral', texto: 'Agrupa los módulos: Inventario, Ventas, Compras, Chatbot, Reportes y análisis, y Configuración.' },
            { captura: 'inicio-busqueda', titulo: 'Búsqueda global', texto: 'Encuentra productos y clientes por nombre o código desde cualquier pantalla.' },
        ],
    },
    {
        id: 'productos', titulo: 'Productos', rol: 'Asistente y administrador',
        resumen: 'El catálogo: proponer un producto y aprobarlo.',
        pasos: [
            { captura: 'prod-lista', titulo: 'Catálogo de productos', texto: 'Código, nombre, categoría, precio, stock y estado de cada producto activo.' },
            { captura: 'prod-filtros', titulo: 'Filtros', texto: 'Filtra por categoría, estado operativo, proveedor o estado de aprobación.' },
            { captura: 'prod-nuevo', titulo: 'Proponer un producto', texto: 'El asistente completa los datos obligatorios y el stock inicial, que queda como movimiento de entrada.' },
            { captura: 'prod-pendiente', titulo: 'Queda pendiente', texto: 'Un producto nuevo no se activa solo: espera la revisión del administrador.' },
            { captura: 'prod-aprobar', titulo: 'Aprobar o rechazar', texto: 'El administrador revisa el detalle y aprueba. El rechazo exige un motivo.' },
        ],
    },
    {
        id: 'categorias-y-catalogo', titulo: 'Categorías y catálogo visual', rol: 'Asistente, administrador y ventas',
        resumen: 'Cómo se organizan los productos y cómo los ve el mostrador.',
        pasos: [
            { captura: 'cat-lista', titulo: 'Categorías', texto: 'Cada categoría tiene su color e icono, y muestra cuántos productos activos agrupa.' },
            { captura: 'cat-nueva', titulo: 'Nueva categoría', texto: 'El asistente la propone con nombre y descripción; el administrador la aprueba.' },
            { captura: 'catalogo', titulo: 'Catálogo visual', texto: 'Fichas de los productos activos con su existencia, para consultar rápido en el mostrador.' },
        ],
    },
    {
        id: 'movimientos', titulo: 'Movimientos de inventario', rol: 'Inventario',
        resumen: 'Entradas y salidas de mercancía con trazabilidad completa.',
        pasos: [
            { captura: 'mov-lista', titulo: 'Historial de movimientos', texto: 'Cada cambio de stock queda con fecha, responsable, cantidad, stock anterior y resultante.' },
            { captura: 'mov-nuevo', titulo: 'Registrar una entrada', texto: 'Elige el tipo, busca el producto, escribe la cantidad y el motivo.' },
            { captura: 'mov-guardado', titulo: 'Stock actualizado', texto: 'Al guardar, el stock cambia al instante y el movimiento aparece en el listado.' },
            { captura: 'mov-error', titulo: 'Salidas imposibles', texto: 'Una salida mayor que la existencia se rechaza y el aviso aparece junto a la cantidad.' },
            { captura: 'mov-exportar', titulo: 'Exportar', texto: 'Descarga el listado filtrado en Excel, CSV o PDF, con el membrete de la empresa.' },
        ],
    },
    {
        id: 'cortes-y-conciliaciones', titulo: 'Cortes y conciliaciones', rol: 'Administrador e inventario',
        resumen: 'Comparar el stock del sistema con el conteo físico.',
        pasos: [
            { captura: 'conc-inicio', titulo: 'Cortes de inventario', texto: 'Registra el conteo físico y revisa las diferencias. El stock solo cambia cuando un administrador aprueba el ajuste.' },
        ],
    },
    {
        id: 'clientes-y-notas', titulo: 'Clientes y notas de entrega', rol: 'Ventas',
        resumen: 'Registrar clientes y documentar cada despacho.',
        pasos: [
            { captura: 'cli-lista', titulo: 'Clientes', texto: 'Razón social, RIF, teléfono y correo. La búsqueda filtra mientras escribes.' },
            { captura: 'cli-nuevo', titulo: 'Nuevo cliente', texto: 'Completa los datos y guarda. El RIF y el correo no pueden repetirse.' },
            { captura: 'nota-lista', titulo: 'Notas de entrega', texto: 'Cada nota documenta un despacho y descuenta el inventario. No sustituye a una factura.' },
            { captura: 'nota-nueva', titulo: 'Emitir una nota', texto: 'Escribe el cliente y agrega cada producto con su cantidad. Solo se ofrecen productos con existencia.' },
            { captura: 'nota-revisar', titulo: 'Revisar antes de emitir', texto: 'El sistema muestra cliente, líneas, total y el aviso de salida de inventario.' },
            { captura: 'nota-emitida', titulo: 'Nota emitida', texto: 'Al confirmar, la nota recibe su número y el stock se descuenta.' },
            { captura: 'nota-detalle', titulo: 'PDF con membrete', texto: 'Desde el detalle se descarga el PDF con el logo y el RIF de la empresa.' },
        ],
    },
    {
        id: 'compras', titulo: 'Compras', rol: 'Inventario, asistente y administrador',
        resumen: 'El ciclo de compra, con un responsable distinto en cada paso.',
        pasos: [
            { captura: 'comp-solicitudes', titulo: 'Solicitud de reposición', texto: 'Inventario indica qué productos y cantidades faltan.' },
            { captura: 'comp-ordenes', titulo: 'Órdenes de compra', texto: 'El asistente cotiza y prepara la orden; el listado muestra el estado de cada una.' },
            { captura: 'comp-borrador', titulo: 'Borrador sin aprobar', texto: 'Mientras no se aprueba, la orden se imprime con la marca de borrador y no se envía al proveedor.' },
            { captura: 'comp-aprobada', titulo: 'Orden aprobada', texto: 'El administrador la aprueba y la impresión indica quién lo hizo.' },
            { captura: 'comp-recepciones', titulo: 'Recepción', texto: 'Inventario registra lo recibido y el stock aumenta con su movimiento.' },
            { captura: 'comp-proveedores', titulo: 'Proveedores', texto: 'El asistente propone altas y cambios; los datos oficiales cambian solo al aprobarse.' },
        ],
    },
    {
        id: 'reportes-y-analisis', titulo: 'Reportes y análisis', rol: 'Administrador, inventario y asistente',
        resumen: 'Todas las gráficas e indicadores, reunidos en una sección.',
        pasos: [
            { captura: 'rep-salud', titulo: 'Panorama', texto: 'La salud del inventario: cuántos productos están disponibles, en bajo stock o agotados.' },
            { captura: 'rep-graficas', titulo: 'Gráficas', texto: 'Ventas de la semana e inventario por categoría, calculados con los datos registrados.' },
            { captura: 'rep-indice', titulo: 'Índice de reportes', texto: 'Accesos directos a cada reporte del sistema.' },
            { captura: 'rep-ventas', titulo: 'Reporte de ventas', texto: 'Elige el período y cambia de pestaña: resumen, producto, cliente, categoría y método de pago.' },
            { captura: 'rep-inventario', titulo: 'Reporte de inventario', texto: 'Existencias, entradas y salidas, rotación y valor del inventario.' },
        ],
    },
    {
        id: 'analisis-predictivo', titulo: 'Análisis predictivo', rol: 'Administrador, inventario y asistente',
        resumen: 'Pronóstico de demanda y recomendación de reabastecimiento.',
        pasos: [
            { captura: 'pred-demanda', titulo: 'Pronóstico de demanda', texto: 'Estima la demanda por producto. Sin historial suficiente, lo dice en lugar de inventar un pronóstico.' },
            { captura: 'pred-fuente', titulo: 'Fuente de análisis', texto: 'Elige entre el histórico importado y los datos operativos; las fuentes no se mezclan.' },
            { captura: 'pred-reabastecimiento', titulo: 'Reabastecimiento', texto: 'Cantidades sugeridas según la demanda estimada, el stock actual y el mínimo.' },
            { captura: 'pred-criticos', titulo: 'Productos críticos', texto: 'Los productos que requieren atención antes de quedarse sin existencia.' },
            { captura: 'pred-precision', titulo: 'Precisión del modelo', texto: 'Error medido fuera de muestra (WAPE, MAE), para saber cuánto confiar en el pronóstico.' },
        ],
    },
    {
        id: 'importacion-historica', titulo: 'Importación histórica', rol: 'Administrador',
        resumen: 'Cargar el historial de ventas que alimenta el pronóstico.',
        pasos: [
            { captura: 'hist-importar', titulo: 'Importar un CSV', texto: 'Descarga la plantilla, complétala y súbela. El lote se revisa y se simula antes de confirmarlo.' },
            { captura: 'hist-diagnostico', titulo: 'Diagnóstico de historial', texto: 'Indica si los datos alcanzan para pronosticar y qué falta en cada producto.' },
        ],
    },
    {
        id: 'chatbot', titulo: 'Chatbot', rol: 'Todos los roles',
        resumen: 'Preguntas sobre el catálogo sin navegar por las pantallas.',
        pasos: [
            { captura: 'chat-inicio', titulo: 'Consultas rápidas', texto: 'Botones para las preguntas más comunes según tu rol: buscar, precio y agotados.' },
            { captura: 'chat-pregunta', titulo: 'Escribe tu pregunta', texto: 'Para un resultado exacto, incluye el código del producto.' },
            { captura: 'chat-respuesta', titulo: 'Respuesta', texto: 'Responde con los datos que tu rol puede ver. No crea ni modifica nada.' },
            { captura: 'chat-agotados', titulo: 'Productos agotados', texto: 'Una consulta rápida lista los productos sin existencia.' },
        ],
    },
    {
        id: 'configuracion-y-perfil', titulo: 'Configuración y perfil', rol: 'Administrador y soporte TI',
        resumen: 'Datos de la empresa, usuarios, seguridad y respaldos.',
        pasos: [
            { captura: 'conf-general', titulo: 'General', texto: 'Nombre, RIF y logo de la empresa: forman el membrete de los PDF, Excel e impresiones.' },
            { captura: 'conf-usuarios', titulo: 'Usuarios y roles', texto: 'Alta de usuarios, asignación de rol y permisos de cada rol.' },
            { captura: 'conf-seguridad', titulo: 'Seguridad', texto: 'Política de contraseñas, verificación en dos pasos y sesiones abiertas.' },
            { captura: 'conf-respaldo', titulo: 'Respaldo', texto: 'Copias de seguridad de la base de datos.' },
            { captura: 'perfil', titulo: 'Mi perfil', texto: 'Cada persona actualiza sus datos, preferencias y contraseña.' },
        ],
    },
    {
        id: 'guia-de-uso', titulo: 'Guía de uso', rol: 'Todos los roles',
        resumen: 'La ayuda del sistema, siempre a mano en el menú.',
        pasos: [
            { captura: 'guia-inicio', titulo: 'Guía de uso', texto: 'Explica cada módulo paso a paso. Muestra solo las secciones que tu rol puede usar.' },
            { captura: 'guia-buscar', titulo: 'Buscar en la guía', texto: 'Escribe una palabra y la guía muestra solo las secciones que coinciden.' },
        ],
    },
];
