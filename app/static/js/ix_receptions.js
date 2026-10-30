/* Reception UI: canonical purchase writes, deterministic read-only preview. */
(() => {
  'use strict';
  const ctx = JSON.parse(document.getElementById('rc-context').textContent);
  const drawer = document.getElementById('rc-drawer');
  const body = document.getElementById('rc-drawer-body');
  const menu = document.getElementById('rc-menu');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = (value, currency) => ctx.canViewCost ? PurchaseMoney.format(value, currency) : '';
  const suffix = ctx.reference ? '?ref=1' : '';
  let selectedOrder, menuTrigger, orderRequest = 0;
  function message(text) {
    const node = document.getElementById('rc-message'); node.textContent = text; node.hidden = false;
    setTimeout(() => { node.hidden = true; }, 6000);
  }
  async function api(path, method='GET', data) {
    if (ctx.reference && method !== 'GET') throw new Error('La referencia no admite escrituras.');
    const response = await fetch(path + suffix, {method, credentials:'same-origin', headers:{'Content-Type':'application/json',Accept:'application/json', ...(method === 'GET' ? {} : {'X-CSRFToken':ctx.csrf})}, body:data === undefined ? undefined : JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
    return result;
  }
  function openDrawer(title, subtitle, html) {
    document.getElementById('rc-drawer-title').textContent = title;
    document.getElementById('rc-drawer-subtitle').textContent = subtitle;
    body.innerHTML = html;
    if (!drawer.open) drawer.showModal();
  }
  function poURL(row) { return '/purchases?detail=' + row.order_id + (ctx.reference ? '&ref=1' : ''); }
  function receiptURL(id, kind) { return '/purchases/receptions/' + id + '/' + kind + suffix; }
  async function detail(id, movements=false) {
    const row = await api('/api/purchases/receptions/' + id);
    const info = [['Recepción',row.number],['Orden de compra',row.order_number],['Proveedor',row.supplier.name],['Fecha recepción',row.received_at],['Responsable',row.responsible],['Estado',row.status_label],['Unidades recibidas',row.units],['Monto recibido',money(row.amount, row.currency)]];
    const lines = row.items.map(line => `<tr><td>${esc(line.product_name)}</td><td>${esc(line.product_code)}</td><td>${line.quantity}</td><td>${money(line.unit_cost, row.currency)}</td><td>${money(line.subtotal, row.currency)}</td><td><a href="#rc-inventory">MOV-${line.movement_id}</a></td></tr>`).join('');
    const inventory = row.movements.map(m => `<tr><td>MOV-${m.id}</td><td>${esc(m.product)}</td><td>${esc(m.movement_type)}</td><td>${m.quantity}</td><td>${m.previous_stock} → ${m.new_stock}</td></tr>`).join('');
    openDrawer('Detalle de recepción', row.number + ' · ' + row.status_label, `<div class="rc-scroll"><h3>INFORMACIÓN GENERAL</h3><div class="rc-info">${info.map(([label,value])=>`<p><strong>${label}</strong>${esc(value)}</p>`).join('')}</div><h3>PRODUCTOS RECIBIDOS</h3><div class="rc-lines"><table><thead><tr><th>Producto</th><th>Código</th><th>Cantidad</th><th>Costo unitario</th><th>Subtotal</th><th>Movimiento</th></tr></thead><tbody>${lines || '<tr><td colspan="6">Detalle no incluido en la referencia visual.</td></tr>'}</tbody></table></div><h3>NOTAS</h3><p class="rc-notes">${esc(row.notes || 'Sin notas.')}</p><h3 id="rc-inventory" tabindex="-1">INVENTARIO</h3><div class="rc-lines"><table><thead><tr><th>Referencia</th><th>Producto</th><th>Tipo</th><th>Cantidad</th><th>Stock</th></tr></thead><tbody>${inventory || '<tr><td colspan="5">Sin movimientos en esta referencia visual.</td></tr>'}</tbody></table></div><h3>AUDITORÍA</h3><p>Creada: ${esc(row.created_at)}</p></div><footer><button class="po-btn" data-close>Cerrar</button><a class="po-btn" href="${poURL(row)}">Ver orden de compra</a><a class="po-btn" target="_blank" rel="noopener" href="${receiptURL(id,'print')}">Imprimir</a><a class="po-btn is-primary" href="${receiptURL(id,'pdf')}">PDF</a></footer>`);
    if (!ctx.reference) correctionSection(row);
    if (movements) document.getElementById('rc-inventory').focus();
  }
  function correctionSection(row) {
    const history = row.corrections || [];
    const pending = history.some(item => item.status === 'pending');
    let html = '<h3>CORRECCIONES Y CANTIDADES EFECTIVAS</h3><p>El comprobante original se conserva. Las diferencias aprobadas generan movimientos compensatorios.</p>';
    html += `<p>Original: ${row.original_units} unidades. Recibido neto: ${row.units} unidades.</p>`;
    if (!row.supplier_snapshot_available) html += '<p>Documento histórico sin copia de los datos del proveedor al aprobarse; se muestran los actuales.</p>';
    html += history.map(item => `<article><h4>COR-${item.id} · ${esc(item.status)}</h4><p>${esc(item.reason)} · ${esc(item.requested_by)} · ${esc(item.created_at)} UTC</p><ul>${item.items.map(line=>`<li>${esc(line.product_name)}: ${line.previous_quantity} → ${line.target_quantity}${line.movement_id ? ' · MOV-'+line.movement_id : ''}</li>`).join('')}</ul>${item.decided_at ? `<p>Decidida por ${esc(item.decided_by)} · ${esc(item.decided_at)} UTC · ${esc(item.decision_note || '')}</p>` : ''}${ctx.canApprove && item.status === 'pending' && item.requested_by_user_id !== ctx.userId ? `<form class="rc-correction-decision" data-receipt="${row.id}" data-correction="${item.id}"><label>Motivo de rechazo<textarea name="reason" maxlength="1000"></textarea></label><p class="rc-error" role="alert"></p><button class="po-btn is-primary" type="submit" value="approve">Aprobar corrección</button><button class="po-btn" type="submit" value="reject">Rechazar corrección</button></form>` : ''}</article>`).join('');
    if (ctx.canReceive && row.can_correct && !pending) html += `<form id="rc-correction-form" data-receipt="${row.id}"><h4>Solicitar corrección</h4><p>Indique la cantidad total que debió quedar recibida de cada producto. Requiere aprobación administrativa.</p>${row.correction_items.map(line=>`<label>${esc(line.product_name)} · recibido neto: ${line.quantity_received}<input type="number" data-correction-line="${line.id}" min="0" max="${line.quantity_ordered}" step="1" value="${line.quantity_received}" required></label>`).join('')}<label>Motivo obligatorio<textarea name="reason" maxlength="1000" required></textarea></label><p class="rc-error" role="alert"></p><button class="po-btn is-primary" type="submit">Solicitar corrección</button></form>`;
    body.querySelector('.rc-scroll').insertAdjacentHTML('beforeend', html);
  }
  async function saveCorrection(form, action) {
    const buttons = [...form.querySelectorAll('button')];
    if (buttons.some(button=>button.disabled)) return;
    const error = form.querySelector('.rc-error'); error.textContent = '';
    const reason = form.elements.reason.value.trim();
    if (action === 'reject' && !reason) { error.textContent = 'Indique el motivo del rechazo.'; return; }
    buttons.forEach(button=>button.disabled=true);
    try {
      const path = '/api/purchases/receptions/'+form.dataset.receipt+'/corrections';
      if (action) await api(path+'/'+form.dataset.correction+'/'+action, 'POST', action === 'reject' ? {reason} : {});
      else await api(path, 'POST', {reason, items:[...form.querySelectorAll('[data-correction-line]')].map(input=>({line_id:Number(input.dataset.correctionLine),quantity:Number(input.value)}))});
      location.href = '/purchases/receptions?detail='+form.dataset.receipt;
    } catch (failure) { error.textContent = failure.message; buttons.forEach(button=>button.disabled=false); }
  }
  async function newReception(preselect) {
    selectedOrder = null;
    const data = await api('/api/purchases/receptions/options');
    openDrawer('Nueva recepción', 'Registra una nueva recepción de mercancía', `<form id="rc-receive-form"><div class="rc-scroll"><div class="rc-grid"><label>Orden de compra *<select name="order_id" required><option value="">Selecciona una orden</option>${data.orders.map(o=>`<option value="${o.id}">${esc(o.order_number)} - ${esc(o.supplier.name)}</option>`).join('')}</select></label><label>Proveedor<input name="supplier" readonly></label><label>Fecha de recepción *<input type="date" name="received_at" value="${ctx.today}" max="${ctx.today}" required></label><label>Responsable *<input value="${esc(ctx.responsible)}" readonly></label><label>Estado<input id="rc-status" value="Parcial" readonly aria-live="polite"></label></div><section class="rc-products"><header><h3>Productos de la orden</h3><p>Selecciona las cantidades recibidas para cada producto</p></header><div class="rc-lines" id="rc-order-lines"><p style="padding:16px">${data.orders.length ? 'Selecciona una orden de compra.' : 'No hay órdenes pendientes de recepción.'}</p></div></section><label>Notas<textarea name="notes" maxlength="1000" placeholder="Observaciones adicionales sobre la recepción..."></textarea></label><p class="rc-counter">0/1000</p><fieldset id="rc-partial" style="border:1px solid #ecd894;border-radius:8px;background:#fff5d6;padding:12px 14px;margin:12px 0" hidden><legend style="font-size:13px;font-weight:650;color:#9a3412;padding:0 6px">Cierre definitivo con faltante</legend><p style="font-size:12px;margin:0 0 8px">Llega menos de lo pedido. La orden se cerrará y el faltante no se recibirá después; para reponerlo se tramita una nueva solicitud.</p><label style="display:flex;flex-direction:row;align-items:center;gap:8px;margin:8px 0"><input type="checkbox" name="confirm_partial_closure" value="1" style="width:16px;height:16px;flex:none;margin:0;padding:0"> Confirmo el cierre definitivo de la orden con el faltante</label><label>Motivo concreto del faltante *<textarea name="closure_reason" minlength="10" maxlength="1000"></textarea></label></fieldset><p class="rc-error" role="alert"></p>${ctx.reference ? '<p>Referencia visual: no se guardan recepciones ni movimientos.</p>' : ''}</div><footer><button type="button" class="po-btn" data-close>Cancelar</button><button class="po-btn is-primary" type="submit" disabled>Guardar recepción</button></footer></form>`);
    const select = body.querySelector('[name=order_id]');
    select.addEventListener('change', () => loadOrder(select.value).catch(error => body.querySelector('.rc-error').textContent = error.message));
    body.querySelector('[name=notes]').addEventListener('input', e => body.querySelector('.rc-counter').textContent = e.target.value.length + '/1000');
    if (preselect || ctx.reference) {
      select.value = String(preselect || 43);
      if (select.value) await loadOrder(select.value);
    }
  }
  async function loadOrder(id) {
    const ticket = ++orderRequest;
    selectedOrder = null;
    body.querySelector('[type=submit]').disabled = true;
    body.querySelector('[name=supplier]').value = '';
    document.getElementById('rc-order-lines').textContent = id ? 'Cargando productos…' : 'Selecciona una orden de compra.';
    if (!id) return;
    const order = await api('/api/purchases/receptions/orders/' + id);
    if (ticket !== orderRequest || !drawer.open) return;
    selectedOrder = order;
    body.querySelector('[name=supplier]').value = order.supplier.name;
    body.querySelector('[name=received_at]').min = order.approved_date || order.ordered_date;
    const rows = order.items.map(line => `<tr><td>${esc(line.product_name)}<br><span>${esc(line.product_code)}</span></td><td>${line.quantity_ordered}</td><td>${line.quantity_received}</td><td>${line.quantity_pending}</td><td><input data-line="${line.id}" type="number" min="0" max="${line.quantity_pending}" step="1" value="${ctx.reference ? line.quantity_pending : 0}" required aria-label="Recibir ${esc(line.product_name)}"></td><td>${money(line.unit_cost, order.currency)}</td></tr>`).join('');
    document.getElementById('rc-order-lines').innerHTML = `<table><colgroup><col style="width:25%"><col><col><col><col style="width:17%"><col></colgroup><thead><tr><th>Producto</th><th>Cantidad ordenada</th><th>Recibida</th><th>Pendiente</th><th>Recibir ahora</th><th>Costo unitario</th></tr></thead><tbody>${rows}</tbody></table>`;
    document.getElementById('rc-order-lines').addEventListener('input', preview);
    preview();
  }
  function preview() {
    if (!selectedOrder) return;
    const inputs = [...body.querySelectorAll('[data-line]')];
    const valid = inputs.every(input => input.checkValidity());
    const positive = inputs.some(input => Number(input.value)>0);
    const complete = valid && inputs.every(input => Number(input.value) === Number(input.max));
    document.getElementById('rc-status').value = complete ? 'Completada' : 'Parcial: cierre definitivo con faltante';
    // Same contract as the Compras form; the server re-checks both fields before any movement.
    const form = document.getElementById('rc-receive-form');
    document.getElementById('rc-partial').hidden = complete;
    form.elements.confirm_partial_closure.required = !complete;
    form.elements.closure_reason.required = !complete;
    body.querySelector('[type=submit]').disabled = !valid || !positive;
  }
  async function save(form) {
    const error = form.querySelector('.rc-error');
    if (ctx.reference) { error.textContent = 'Referencia de solo lectura: no se creó ninguna recepción.'; return; }
    if (!selectedOrder) return;
    const button = form.querySelector('[type=submit]'); button.disabled = true;
    try {
      const inputs = [...form.querySelectorAll('[data-line]')];
      const items = inputs.filter(i=>Number(i.value)>0).map(i=>({line_id:Number(i.dataset.line),quantity:Number(i.value)}));
      const data = {received_at:form.elements.received_at.value,notes:form.elements.notes.value,items};
      if (inputs.some(i => Number(i.value) < Number(i.max))) {
        if (!form.elements.confirm_partial_closure.checked) throw new Error('Confirme el cierre definitivo de la orden con el faltante.');
        data.confirm_partial_closure = true;
        data.closure_reason = form.elements.closure_reason.value;
      }
      const result = await api('/api/purchases/' + selectedOrder.id + '/receptions', 'POST', data);
      const id = Math.max(...result.receptions.map(row=>row.id));
      location.href = '/purchases/receptions?detail=' + id;
    } catch (err) { error.textContent = err.message; button.disabled = false; }
  }
  function closeMenu() {
    menu.hidden = true;
    if (menuTrigger) menuTrigger.setAttribute('aria-expanded','false');
  }
  function showMenu(button) {
    closeMenu(); menuTrigger = button;
    const id = button.dataset.menu;
    const row = button.closest('tr');
    const po = row.querySelector('a').href;
    menu.innerHTML = `<button role="menuitem" data-detail="${id}">Ver detalle</button><a role="menuitem" href="${esc(po)}">Ver orden de compra</a><button role="menuitem" data-movements="${id}">Ver movimientos de inventario</button><a role="menuitem" target="_blank" rel="noopener" href="${receiptURL(id,'print')}">Imprimir comprobante</a><a role="menuitem" href="${receiptURL(id,'pdf')}">Descargar PDF</a>`;
    menu.hidden = false; button.setAttribute('aria-expanded','true');
    const r = button.getBoundingClientRect();
    menu.style.left = Math.max(8,Math.min(r.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8)) + 'px';
    menu.style.top = Math.max(8,Math.min(r.bottom+4,innerHeight-menu.offsetHeight-8)) + 'px';
    menu.querySelector('[role=menuitem]').focus();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('button,a');
    if (!button) { closeMenu(); return; }
    if (button.dataset.menu) { showMenu(button); return; }
    closeMenu();
    if (button.hasAttribute('data-close')) { button.closest('dialog').close(); return; }
    if (button.dataset.open) document.getElementById(button.dataset.open).showModal();
    if (button.id === 'rc-new') newReception().catch(e=>message(e.message));
    if (button.dataset.detail) detail(button.dataset.detail).catch(e=>message(e.message));
    if (button.dataset.movements) detail(button.dataset.movements,true).catch(e=>message(e.message));
  });
  document.addEventListener('keydown', e => {
    if (menu.hidden) return;
    if (e.key === 'Escape') { closeMenu(); menuTrigger.focus(); }
    if (['ArrowDown','ArrowUp'].includes(e.key)) {
      e.preventDefault(); const items = [...menu.querySelectorAll('[role=menuitem]')];
      items[(items.indexOf(document.activeElement) + (e.key === 'ArrowDown' ? 1 : items.length-1)) % items.length].focus();
    }
  });
  document.getElementById('rc-inline').addEventListener('change', e => { if (e.target.type !== 'search') e.currentTarget.requestSubmit(); });
  document.getElementById('rc-per-page').addEventListener('change', e => { const url = new URL(location.href);url.searchParams.set('per_page',e.target.value);url.searchParams.delete('page');location.href=url; });
  document.addEventListener('submit', e => {
    if (e.target.id === 'rc-correction-form') { e.preventDefault(); saveCorrection(e.target); }
    if (e.target.classList.contains('rc-correction-decision')) { e.preventDefault(); saveCorrection(e.target, e.submitter.value); }
    if (e.target.id === 'rc-receive-form') { e.preventDefault(); save(e.target); }
    if (e.target.id === 'rc-export-form') {
      e.preventDefault(); const params = new URLSearchParams(location.search);params.delete('page');params.delete('detail');
      params.set('format', e.target.elements.format.value);
      e.target.querySelectorAll('[type=checkbox]').forEach(input=>params.set(input.name,input.checked ? '1' : '0'));
      location.href = '/api/purchases/receptions/export?' + params;
      document.getElementById('rc-export').close();
    }
  });
  const params = new URLSearchParams(location.search);
  if (/^\d+$/.test(params.get('detail') || '')) detail(params.get('detail')).catch(e=>message(e.message));
  if (ctx.canReceive && /^\d+$/.test(params.get('order_id') || '')) newReception(params.get('order_id')).catch(e=>message(e.message));
})();
