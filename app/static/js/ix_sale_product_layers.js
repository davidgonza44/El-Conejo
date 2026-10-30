/* Reference sale only: ephemeral selections, no requests, storage or camera APIs. */
(() => {
    'use strict';
    const fixture = document.getElementById('spl-fixtures');
    if (!fixture || document.documentElement.dataset.referenceMode !== '1') return;
    const $ = id => document.getElementById(`spl-${id}`);
    const money = value => `$ ${value.toFixed(2)}`;
    const number = text => Number(text.replace(/[^\d.-]/g, ''));
    const escape = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const lines = document.querySelector('.sa-lines-table tbody');
    const template = lines.rows[0].cloneNode(true);
    const catalog = JSON.parse(fixture.textContent).map(row => ({
        code: row.code, name: row.product.name, image: `/static/${row.product.thumb}`,
        price: number(row.price), stock: Number(row.stock), category: row.category, status: row.status[0],
    }));
    const sale = new Map();
    [...lines.rows].forEach(row => {
        const code = row.querySelector('.px-product-sku').textContent.trim();
        const product = catalog.find(p => p.code === code);
        product.price = number(row.cells[2].textContent);
        sale.set(code, {quantity:number(row.cells[3].textContent), discount:number(row.cells[4].textContent), row});
    });
    const selection = new Map();
    const drafts = new Map();
    let scanned = catalog[0];
    let opener = null;
    let active = null;
    let previousOverflow = '';
    const productMarkup = p => `<div class="spl-product"><img src="${escape(p.image)}" alt=""><div class="spl-product-name">${escape(p.name)}<small>${escape(p.code)}</small></div></div>`;
    const remaining = p => p.stock - (sale.get(p.code)?.quantity || 0);
    const feedback = (kind, text) => { $(`${kind}-feedback`).textContent = text; };

    function renderProducts() {
        const query = $('search').value.trim().toLocaleLowerCase('es');
        const products = catalog.filter(p => `${p.name} ${p.code}`.toLocaleLowerCase('es').includes(query)
            && (!$('category').value || p.category === $('category').value)
            && (!$('stock').value || p.status === $('stock').value));
        $('products').innerHTML = products.map(p => {
            const selected = selection.has(p.code);
            const disabled = remaining(p) < 1;
            const tone = p.stock === 0 ? 'out' : p.status === 'Bajo stock' ? 'low' : 'available';
            return `<tr data-code="${escape(p.code)}" aria-selected="${selected}"><td>${productMarkup(p)}</td>
                <td class="spl-${tone}">${p.stock}</td><td>${money(p.price)}</td>
                <td><input type="number" min="1" max="${Math.max(1,remaining(p))}" step="1" value="${drafts.get(p.code) || 1}" aria-label="Cantidad de ${escape(p.name)}" ${disabled ? 'disabled' : ''}></td>
                <td><button type="button" class="spl-button" aria-pressed="${selected}" ${disabled ? 'disabled' : ''}>${selected ? 'Agregado' : 'Agregar'}</button></td></tr>`;
        }).join('');
        $('empty').hidden = products.length !== 0;
    }
    function renderSelection() {
        $('count').textContent = `Productos seleccionados: ${selection.size}`;
        $('selected').innerHTML = [...selection].map(([code, qty]) => `<li>${escape(catalog.find(p => p.code === code).name)} × ${qty}</li>`).join('');
        $('confirm').disabled = selection.size === 0;
    }
    function validate(input, product) {
        const valid = Number.isInteger(Number(input.value)) && Number(input.value) >= 1 && Number(input.value) <= remaining(product);
        input.setAttribute('aria-invalid', String(!valid));
        input.setCustomValidity(valid ? '' : `Ingresa una cantidad entre 1 y ${remaining(product)} (incluye lo agregado a la venta).`);
        if (!valid) feedback('drawer', `Cantidad inválida. Disponible para agregar: ${remaining(product)}.`);
        else feedback('drawer', '');
        return valid;
    }
    function updateSale() {
        let subtotal = 0;
        let discount = 0;
        [...sale].forEach(([code, item], index) => {
            const p = catalog.find(product => product.code === code);
            const row = item.row;
            row.cells[0].textContent = index + 1;
            row.querySelector('img').src = p.image;
            row.querySelector('.px-product-name').textContent = p.name;
            row.querySelector('.px-product-sku').textContent = p.code;
            row.cells[2].textContent = money(p.price);
            const quantity = row.querySelector('.sa-qty');
            quantity.firstChild.textContent = item.quantity;
            quantity.setAttribute('aria-valuenow', item.quantity);
            const gross = Math.round(p.price * 100) * item.quantity;
            const reduction = Math.round(gross * item.discount / 100);
            subtotal += gross;
            discount += reduction;
            row.querySelector('.sa-disc').firstChild.textContent = `${item.discount}%`;
            row.cells[5].querySelector('strong').textContent = money((gross - reduction) / 100);
            row.querySelector('.sa-remove').setAttribute('aria-label', `Quitar ${p.name}`);
            if (!row.isConnected) lines.append(row);
        });
        document.querySelector('.sa-count').textContent = `${sale.size} productos agregados`;
        const totals = document.querySelectorAll('.sa-totals dd');
        [subtotal, discount, subtotal - discount].forEach((value, index) => { totals[index].textContent = money(value / 100); });
    }
    function addBatch(batch, kind) {
        for (const [code, quantity] of batch) {
            const p = catalog.find(product => product.code === code);
            if (!Number.isInteger(quantity) || quantity < 1 || quantity > remaining(p)) {
                feedback(kind, `Stock fixture insuficiente para ${p.name}. Disponible para agregar: ${remaining(p)}.`);
                return false;
            }
        }
        batch.forEach((quantity, code) => {
            const item = sale.get(code) || {quantity:0, discount:0, row:template.cloneNode(true)};
            item.quantity += quantity;
            sale.set(code, item);
        });
        updateSale();
        return true;
    }
    function renderScan() {
        $('scan-confirm').disabled = !scanned || remaining(scanned) < 1;
        $('last-product').innerHTML = scanned ? `<div class="spl-last-row">${productMarkup(scanned).replace('<small>', '<small>Código: ')}<div class="spl-last-price"><strong>${money(scanned.price)}</strong><small>Stock: ${scanned.stock}</small></div><button type="button" class="spl-button" id="spl-scan-add" ${remaining(scanned) < 1 ? 'disabled' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>Agregar</button></div>` : '<p>Producto no encontrado</p>';
    }
    function resolveCode() {
        const code = $('code').value.trim().toUpperCase();
        scanned = catalog.find(p => p.code.toUpperCase() === code) || null;
        feedback('scan', scanned ? '' : 'Producto no encontrado');
        renderScan();
    }
    function closeLayer() {
        if (!active) return;
        active.close();
        active = null;
        document.body.style.overflow = previousOverflow;
        opener?.focus();
    }
    function openLayer(kind, trigger) {
        if (active) closeLayer();
        opener = trigger;
        previousOverflow = document.body.style.overflow;
        active = $(kind);
        if (kind === 'drawer') {
            selection.clear(); drafts.clear();
            $('search').value = ''; $('category').value = ''; $('stock').value = '';
            feedback('drawer', ''); renderProducts(); renderSelection();
        } else {
            scanned = catalog[0]; $('code').value = '';
            feedback('scan', ''); renderScan();
        }
        document.body.style.overflow = 'hidden';
        active.showModal();
        $(kind === 'drawer' ? 'search' : 'code').focus();
    }
    document.querySelectorAll('.sa-products-actions button').forEach(button => {
        const kind = button.textContent.trim() === 'Escanear' ? 'scanner' : 'drawer';
        button.addEventListener('click', () => openLayer(kind, button));
    });
    document.querySelectorAll('.spl-dialog').forEach(dialog => {
        dialog.addEventListener('keydown', event => {
            if (event.key !== 'Tab') return;
            const focusable = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), select, summary')]
                .filter(element => element.getClientRects().length);
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault(); last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault(); first.focus();
            }
        });
        dialog.addEventListener('cancel', event => { event.preventDefault(); closeLayer(); });
        dialog.querySelectorAll('[data-spl-close]').forEach(button => button.addEventListener('click', closeLayer));
        dialog.addEventListener('click', event => {
            const bounds = dialog.getBoundingClientRect();
            if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) closeLayer();
        });
    });
    ['search','category','stock'].forEach(id => $(id).addEventListener('input', renderProducts));
    $('products').addEventListener('input', event => {
        if (!event.target.matches('input')) return;
        const code = event.target.closest('tr').dataset.code;
        const p = catalog.find(product => product.code === code);
        drafts.set(code, event.target.value);
        if (validate(event.target, p) && selection.has(code)) selection.set(code, Number(event.target.value));
        else selection.delete(code);
        const button = event.target.closest('tr').querySelector('button');
        button.textContent = selection.has(code) ? 'Agregado' : 'Agregar';
        button.setAttribute('aria-pressed', String(selection.has(code)));
        event.target.closest('tr').setAttribute('aria-selected', String(selection.has(code)));
        renderSelection();
    });
    $('products').addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        const row = button.closest('tr');
        const p = catalog.find(product => product.code === row.dataset.code);
        const input = row.querySelector('input');
        if (!validate(input, p)) { input.reportValidity(); return; }
        selection.set(p.code, Number(input.value));
        row.setAttribute('aria-selected', 'true');
        button.textContent = 'Agregado'; button.setAttribute('aria-pressed', 'true');
        renderSelection();
    });
    $('confirm').addEventListener('click', () => { if (selection.size && addBatch(selection, 'drawer')) closeLayer(); });
    $('code').addEventListener('input', resolveCode);
    $('code').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); resolveCode(); } });
    $('simulate').addEventListener('click', () => { $('code').value = catalog[0].code; resolveCode(); });
    $('last-product').addEventListener('click', event => {
        if (!event.target.closest('#spl-scan-add') || !scanned) return;
        if (addBatch(new Map([[scanned.code, 1]]), 'scan')) {
            feedback('scan', `${scanned.name}: se agregó 1 unidad a la venta simulada.`);
            renderScan();
        }
    });
    $('scan-confirm').addEventListener('click', () => {
        if (scanned && addBatch(new Map([[scanned.code, 1]]), 'scan')) closeLayer();
    });
})();
