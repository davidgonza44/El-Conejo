/* Reference fixtures only: no fetch, storage, download, or persistence API. */
(function () {
    'use strict';
    const dialog = document.getElementById('pl-dialog');
    if (!dialog || new URLSearchParams(location.search).get('ref') !== '1') return;
    const $ = id => document.getElementById(`pl-${id}`);
    const imageEditor = window.createProductImageEditor();
    const baseRows = [...document.querySelectorAll('.iv-catalog-table tbody tr')];
    const products = baseRows.map((row, index) => {
        const cells = row.cells;
        return Object.freeze({
            name: row.querySelector('.px-product-name').textContent.trim(),
            code: cells[1].textContent.trim(), image: row.querySelector('img').src,
            category: cells[3].textContent.trim(), brand: row.querySelector('.px-product-sku').textContent.trim().split(' - ')[0],
            model: row.querySelector('.px-product-sku').textContent.trim(),
            price: Number(cells[4].textContent.replace(/[^\d.]/g, '')), stock: Number(cells[5].textContent.trim()),
            status: cells[6].textContent.trim(), updated: cells[7].textContent.trim(),
            supplier: index % 2 ? 'Ferremax, C.A.' : 'Distribuidora Andina',
            unit: 'Unidad', cost: [1250, 18, 175, 210, 190, 28, 60, 0.5][index], minimum: [10, 20, 15, 10, 20, 10, 15, 20][index],
            description: index === 0 ? 'Taladro percutor de 750W con mandril de 1/2”. Ideal para trabajos en concreto, metal y madera.' : `${row.querySelector('.px-product-name').textContent.trim()}. Producto de demostración del catálogo de referencia.`,
        });
    });
    let selected = products[0], returnFocus, oldOverflow, oldPadding, page = 1, timer;
    const titles = {detail: 'Detalle del producto', edit: 'Editar producto', catalog: 'Catálogo completo de productos', export: 'Exportar catálogo'};
    titles.import = 'Importar productos desde CSV';
    titles.advanced = 'Filtros avanzados';
    const subtitles = {catalog: 'Visualiza todos los productos del inventario', export: 'Selecciona el formato y las opciones de exportación', import: 'Carga un archivo CSV para importar productos al catálogo', advanced: 'Refina la búsqueda de productos con múltiples criterios'};
    let csvSelected = false;
    let appliedFilters = null;
    const currency = value => `$ ${Number(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    const node = (tag, text, className) => {
        const item = document.createElement(tag);
        if (text !== undefined) item.textContent = text;
        if (className) item.className = className;
        return item;
    };
    function unlock() {
        document.body.style.overflow = oldOverflow;
        document.body.style.paddingRight = oldPadding;
    }
    function close() {
        if (!dialog.open) return;
        dialog.close();
        unlock();
    }
    document.addEventListener('product:before-layer-open', event => {
        if (event.detail !== 'pl') close();
    });
    dialog.addEventListener('close', () => {
        if (document.querySelector('dialog[open]')) return;
        unlock();
        returnFocus?.focus({preventScroll: true});
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('click', event => {
        if (event.target !== dialog) return;
        const box = dialog.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close();
    });
    dialog.querySelectorAll('[data-pl-close]').forEach(button => button.addEventListener('click', close));
    function badge(status) {
        const result = node('span', status, 'pl-badge');
        result.dataset.status = status;
        return result;
    }
    function productHeader() {
        $('image').src = selected.image;
        $('image').alt = selected.name;
        $('name').textContent = selected.name;
        $('brand').textContent = selected.model;
        $('code').textContent = `Código: ${selected.code}`;
        $('status').textContent = selected.status;
        $('status').dataset.status = selected.status;
    }
    function detail() {
        $('detail').replaceChildren();
        const groups = [
            ['Información general', [['Categoría', selected.category], ['Marca', selected.brand], ['Proveedor', selected.supplier], ['Estado', selected.status], ['Última actualización', selected.updated]]],
            ['Precios', [['Precio de venta', currency(selected.price)], ['Costo', currency(selected.cost)]]],
            ['Inventario', [['Stock actual', selected.stock], ['Stock mínimo', selected.minimum]]],
        ];
        groups.forEach(([title, values]) => {
            const section = node('section', undefined, 'pl-section');
            const list = node('dl');
            values.forEach(([label, value]) => {
                const valueNode = node('dd');
                valueNode.append(label === 'Estado' ? badge(value) : document.createTextNode(value));
                list.append(node('dt', label), valueNode);
            });
            section.append(node('h3', title), list);
            $('detail').append(section);
        });
        const description = node('section', undefined, 'pl-section');
        description.append(node('h3', 'Descripción'), node('p', selected.description));
        $('detail').append(description);
    }
    const options = {
        category: [...new Set(products.map(p => p.category))], supplier: ['Distribuidora Andina', 'Ferremax, C.A.'],
        unit: ['Unidad', 'Caja', 'Metro', 'Kilogramo'],
        status: ['Disponible', 'Bajo stock', 'Agotado'],
    };
    Object.entries(options).forEach(([key, values]) => {
        values.forEach(value => $(`field-${key}`).add(new Option(value, value)));
        if ($(`filter-${key}`)) values.forEach(value => $(`filter-${key}`).add(new Option(value, value)));
        if ($(`advanced-${key}`)) values.forEach(value => $(`advanced-${key}`).add(new Option(value, value)));
    });
    function edit() {
        imageEditor.reset({ image_url: selected.image });
        $('field-stock').readOnly = true;
        [...$('edit').elements].forEach(field => { field.value = selected[field.name] ?? ''; });
        ['price', 'cost'].forEach(key => { $(`field-${key}`).value = selected[key].toFixed(2); });
        $('field-status').dataset.status = selected.status;
    }
    $('field-status').addEventListener('change', event => { event.target.dataset.status = event.target.value; });
    function actionButton(product, view) {
        const button = node('button', undefined, 'pl-row-button');
        button.type = 'button';
        button.setAttribute('aria-label', `${view === 'detail' ? 'Ver detalle de' : 'Editar'} ${product.name}`);
        button.append($(view === 'detail' ? 'eye-icon' : 'pencil-icon').content.cloneNode(true));
        button.addEventListener('click', () => open(view, product));
        return button;
    }
    function renderCatalog() {
        const query = $('search').value.trim().toLocaleLowerCase('es');
        const filtered = products.filter(product =>
            `${product.name} ${product.code} ${product.category}`.toLocaleLowerCase('es').includes(query) &&
            ['category', 'status', 'supplier'].every(key => !$(`filter-${key}`).value || product[key] === $(`filter-${key}`).value));
        const size = Number($('per-page').value), pages = Math.max(1, Math.ceil(filtered.length / size));
        page = Math.min(page, pages);
        $('rows').replaceChildren();
        filtered.slice((page - 1) * size, page * size).forEach(product => {
            const row = node('tr'), image = node('img');
            image.src = product.image; image.alt = '';
            const actions = node('span');
            actions.append(actionButton(product, 'detail'), actionButton(product, 'edit'));
            const stock = node('span', product.stock, 'pl-stock');
            stock.dataset.status = product.status;
            [image, product.code, product.name, product.category, currency(product.price), stock, badge(product.status), actions].forEach(value => {
                const cell = node('td');
                cell.append(value instanceof Node ? value : document.createTextNode(value));
                row.append(cell);
            });
            $('rows').append(row);
        });
        if (!filtered.length) {
            const row = node('tr'), cell = node('td', 'No se encontraron productos con estos filtros.');
            cell.colSpan = 8; row.append(cell); $('rows').append(row);
        }
        $('summary').textContent = `Mostrando ${filtered.length ? (page - 1) * size + 1 : 0} a ${Math.min(page * size, filtered.length)} de ${filtered.length} productos`;
        $('pages').replaceChildren();
        [['Anterior', page - 1], ...Array.from({length: pages}, (_, i) => [String(i + 1), i + 1]), ['Siguiente', page + 1]].forEach(([label, target]) => {
            const button = node('button', label);
            button.type = 'button'; button.disabled = target < 1 || target > pages;
            if (label === String(page)) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', () => { page = target; renderCatalog(); $('pages').querySelector('[aria-current]')?.focus(); });
            $('pages').append(button);
        });
    }
    ['search', 'filter-category', 'filter-status', 'filter-supplier', 'per-page'].forEach(id => {
        $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { page = 1; renderCatalog(); });
    });
    function open(view, product = selected) {
        selected = product;
        if (!dialog.open) {
            document.dispatchEvent(new CustomEvent('product:before-layer-open', {detail: 'pl'}));
            document.querySelectorAll('dialog[open]').forEach(layer => layer.close());
            returnFocus = document.activeElement;
            oldOverflow = document.body.style.overflow;
            oldPadding = document.body.style.paddingRight;
            const gutter = innerWidth - document.documentElement.clientWidth;
            if (gutter) document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight) + gutter}px`;
            document.body.style.overflow = 'hidden';
        }
        dialog.dataset.view = view;
        $('title').textContent = titles[view];
        $('subtitle').textContent = subtitles[view] || '';
        $('subtitle').hidden = ['detail', 'edit'].includes(view);
        ['detail', 'edit', 'catalog', 'export', 'import', 'advanced'].forEach(id => { $(id).hidden = id !== view; });
        $('product').hidden = !['detail', 'edit'].includes(view);
        $('cancel').textContent = ['edit', 'export', 'import', 'advanced'].includes(view) ? 'Cancelar' : 'Cerrar';
        $('reset-filters').hidden = view !== 'advanced';
        $('action').hidden = view === 'catalog';
        $('action').replaceChildren();
        if (!['import', 'advanced'].includes(view)) $('action').append($(view === 'export' ? 'download-icon' : 'pencil-icon').content.cloneNode(true));
        $('action').append(document.createTextNode({detail: 'Editar producto', edit: 'Guardar cambios', export: 'Exportar', import: 'Importar productos', advanced: 'Aplicar filtros'}[view] || ''));
        $('feedback').hidden = true;
        if (view === 'detail' || view === 'edit') productHeader();
        if (view === 'detail') detail();
        if (view === 'edit') edit();
        if (view === 'catalog') renderCatalog();
        if (view === 'export') $('export').reset();
        if (view === 'import') resetImport();
        if (view === 'advanced') restoreFilters();
        dialog.querySelector('.pl-body').scrollTop = 0;
        if (!dialog.open) dialog.showModal();
        (view === 'edit' ? $('field-name') : view === 'catalog' ? $('search') : dialog.querySelector('[data-pl-close]')).focus({preventScroll: true});
    }
    function simulate() {
        const view = dialog.dataset.view;
        if (view === 'detail') { open('edit'); return; }
        if (view === 'edit' && !$('edit').reportValidity()) return;
        if (view === 'import' && !csvSelected) {
            csvFeedback('Selecciona un archivo CSV de hasta 10 MB para simular la importación.', true);
            $('csv-file').focus();
            return;
        }
        if (view === 'advanced') {
            if (!$('advanced').reportValidity()) return;
            appliedFilters = Object.fromEntries(new FormData($('advanced')));
        }
        close();
        $('feedback').textContent = view === 'edit' ? 'Simulación completada. No se modificó el producto ni el stock.' : 'Simulación completada. No se generó ni descargó ningún archivo.';
        if (view === 'import') $('feedback').textContent = 'Importación simulada con éxito. No se subió el archivo ni se crearon productos.';
        // ponytail: filters are a visual simulation; leave the approved fixture table/pagination intact.
        if (view === 'advanced') $('feedback').textContent = 'Filtros aplicados en simulación. La tabla de referencia permanece sin cambios.';
        $('feedback').hidden = false;
        clearTimeout(timer); timer = setTimeout(() => { $('feedback').hidden = true; }, 7000);
    }
    $('action').addEventListener('click', simulate);
    ['edit', 'export', 'import', 'advanced'].forEach(id => $(id).addEventListener('submit', event => { event.preventDefault(); simulate(); }));
    function csvFeedback(message, error = false) {
        $('csv-feedback').textContent = message;
        $('csv-feedback').hidden = !message;
        $('csv-feedback').classList.toggle('is-error', error);
        $('csv-file').setAttribute('aria-invalid', String(error));
    }
    function resetImport() {
        $('import').reset();
        csvSelected = false;
        csvFeedback('');
        $('template-feedback').hidden = true;
        $('drop-zone').classList.remove('has-file', 'is-dragging');
    }
    function selectCsv(files) {
        const file = files[0];
        csvSelected = false;
        $('drop-zone').classList.remove('has-file');
        if (!file) { csvFeedback(''); return; }
        if (files.length !== 1 || !/\.csv$/i.test(file.name) || file.size > 10 * 1024 * 1024) {
            $('csv-file').value = '';
            csvFeedback('Selecciona un solo archivo CSV de hasta 10 MB.', true);
            return;
        }
        // Metadata only: never read contents, create object URLs, or transfer this file.
        csvSelected = true;
        $('drop-zone').classList.add('has-file');
        csvFeedback(`${file.name} · ${(file.size / 1024).toLocaleString('es', {maximumFractionDigits: 1})} KB · Listo para simular`);
    }
    $('csv-file').addEventListener('change', event => selectCsv(event.target.files));
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(type => {
        $('drop-zone').addEventListener(type, event => {
            event.preventDefault();
            $('drop-zone').classList.toggle('is-dragging', ['dragenter', 'dragover'].includes(type));
            if (type === 'drop') { $('csv-file').value = ''; selectCsv(event.dataTransfer.files); }
        });
    });
    $('template-download').addEventListener('click', () => { $('template-feedback').hidden = false; });
    function syncDates() {
        $('advanced').querySelectorAll('input[type=date]').forEach(input => input.parentElement.classList.toggle('has-value', Boolean(input.value)));
    }
    function restoreFilters() {
        $('advanced').reset();
        if (appliedFilters) Object.entries(appliedFilters).forEach(([key, value]) => { $('advanced').elements.namedItem(key).value = value; });
        syncDates();
    }
    $('advanced').addEventListener('input', syncDates);
    $('reset-filters').addEventListener('click', () => { appliedFilters = null; restoreFilters(); });
    function bind(trigger, view, product) {
        if (!trigger) return;
        trigger.removeAttribute('aria-disabled');
        trigger.setAttribute('aria-haspopup', 'dialog'); trigger.setAttribute('aria-controls', dialog.id);
        trigger.addEventListener('click', event => { event.preventDefault(); open(view, product); });
    }
    baseRows.forEach((row, i) => {
        bind(row.querySelector('[aria-label="Ver detalle"]'), 'detail', products[i]);
        bind(row.querySelector('[aria-label="Editar"]'), 'edit', products[i]);
    });
    bind(document.querySelector('.iv-catalog .ix-link-btn'), 'catalog');
    bind([...document.querySelectorAll('.iv-prod-toolbar button')].find(button => button.textContent.trim() === 'Exportar'), 'export');
    bind([...document.querySelectorAll('.iv-prod-toolbar button')].find(button => button.textContent.trim() === 'Importar CSV'), 'import');
    bind([...document.querySelectorAll('.iv-prod-toolbar button')].find(button => button.textContent.trim() === 'Filtros'), 'advanced');
})();
