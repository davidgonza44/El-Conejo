/* One purchase interface; preview mutations are intercepted before any network call. */
(() => {
  'use strict';
  const context = JSON.parse(document.getElementById('po-context').textContent);
  const dialog = document.getElementById('po-dialog');
  const body = document.getElementById('po-dialog-body');
  const menu = document.getElementById('po-menu');
  const supplierDialog = document.getElementById('po-supplier-dialog');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const money = (value, currency = documentCurrency) => context.canViewCost ? PurchaseMoney.format(value, currency) : '';
  let documentCurrency = null;
  let lines = [], options = {suppliers:[], products:[]}, current = null, mode = '', searchTimer, toastTimer, menuTrigger, quoteRequest = 0, productRequest = 0;

  function toast(message) {
    const node = document.getElementById('po-message');
    node.textContent = message; node.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; }, 6500);
  }
  async function api(path, method = 'GET', data) {
    if (context.reference && method !== 'GET') throw new Error('La referencia no admite escrituras.');
    if (context.reference) path += (path.includes('?') ? '&' : '?') + 'ref=1';
    const response = await fetch(path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json', Accept:'application/json', ...(method === 'GET' ? {} : {'X-CSRFToken':context.csrf})}, body:data === undefined ? undefined : JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
    return result;
  }
  function open(title, content) {
    document.getElementById('po-dialog-title').textContent = title;
    body.innerHTML = content;
    if (!dialog.open) dialog.showModal();
  }
  const previewHelp = () => context.reference ? '<p class="po-form-help">Vista previa determinista. Guardar no modifica datos ni inventario.</p>' : '';
  const errorBox = () => '<p class="po-form-error" role="alert"></p>';
  const footer = label => `<footer><button type="button" class="po-btn" data-close>Cancelar</button><button type="submit" class="po-btn is-primary">${label}</button></footer>`;
  const printURL = order => `/purchases/${order.id}/print${context.reference ? '?ref=1' : ''}`;
  function closeMenu() {
    menu.hidden = true;
    if (menuTrigger) menuTrigger.setAttribute('aria-expanded', 'false');
  }

  function actions(order, button) {
    const choices = [['detail', 'Ver detalle']];
    if (context.canWrite && !order.replenishment_request_id && ['pending_approval','pending'].includes(order.status)) choices.push(['edit','Editar orden']);
    if (canRenew(order)) choices.push(['renew','Cambiar cotización']);
    if (context.canReceive && order.status === 'approved') choices.push(['receive','Registrar recepción']);
    if (canRequestClosure(order)) choices.push(['close_request','Solicitar cierre por falta de disponibilidad']);
    if (canDecideClosure(order)) {
      choices.push(['close_approve','Aprobar cierre'], ['close_reject','Rechazar cierre']);
    }
    if (canRequestShortage(order)) choices.push(['shortage','Solicitar reposición del faltante']);
    choices.push(['print', order.status === 'pending_approval' ? 'Imprimir borrador' : 'Imprimir']);
    if (context.canCancel && ['pending_approval','approved','pending'].includes(order.status)) choices.push(['cancel','Cancelar orden']);
    if (canDecide(order)) choices.push(['approve','Aprobar orden'], ['reject','Rechazar orden']);
    closeMenu(); menuTrigger = button;
    menu.innerHTML = choices.map(([action, label]) => `<button role="menuitem" data-action="${action}" data-id="${order.id}" class="${action === 'cancel' ? 'is-danger' : ''}">${label}</button>`).join('');
    menu.hidden = false; button.setAttribute('aria-expanded', 'true');
    const rect = button.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8)) + 'px';
    menu.style.top = Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8)) + 'px';
    menu.querySelector('button').focus();
  }

  const canDecide = order => context.canApprove && order.status === 'pending_approval' && order.creator.id !== context.userId;
  // A closed order expects nothing more; its shortage is shown as not supplied, never as pending.
  function supplyFields(order) {
    if (order.expecting_supply !== false) return [['Monto por recibir', money(order.pending_amount, order.currency)]];
    const missing = PurchaseMoney.count(order.quantity_not_supplied, 'unidad', 'unidades');
    const amount = context.canViewCost && order.quantity_not_supplied ? ' · ' + money(order.not_supplied_amount, order.currency) : '';
    return [['Monto por recibir', 'Sin mercancía por recibir: la orden está cerrada'], ['No suministrado', missing + amount]];
  }
  const canRenew = order => context.canWrite && order.replenishment_request_id && order.status === 'pending_approval';
  const SHORTAGE_STATUSES = ['closed_partial','closed_unavailable','closed_price_change'];
  // List rows carry no request links; the server rejects duplicates and the detail shows the existing request.
  const canRequestShortage = order => context.canRequestShortage && SHORTAGE_STATUSES.includes(order.status) && order.quantity_not_supplied > 0
    && !(order.shortage_requests || []).some(covers);
  // The server decides; without its flag, only a cancelled request stops covering the shortage.
  const covers = row => row.covers_shortage ?? row.status !== 'cancelled';
  function shortageSection(order) {
    const rows = (order.shortage_requests || []).map(row => {
      const generated = row.purchase_order_number ? ` · ${escape(row.purchase_order_number)} ${escape(row.purchase_order_status_label || '')}` : '';
      const freed = covers(row) ? '' : ' · <small>Ya no cubre el faltante: puede volver a solicitarse</small>';
      return `<li><a href="/purchases/requests/${Number(row.id)}">${escape(row.request_number)}</a> · ${escape(row.status_label)}${generated}${freed}</li>`;
    }).join('');
    return rows ? `<h3>Solicitudes por el faltante</h3><ul>${rows}</ul>` : '';
  }
  const canRequestClosure = order => context.canCloseRequest && order.status === 'approved' && !order.received_units && order.closure_request?.status !== 'pending';
  const canDecideClosure = order => context.canCloseApprove && order.status === 'approved' && !order.received_units && order.closure_request?.status === 'pending' && order.closure_request.requested_by_user_id !== context.userId;
  function closureButtons(order) {
    if (canRequestClosure(order)) return `<button class="po-btn" data-action="close_request" data-id="${order.id}">Solicitar cierre por falta de disponibilidad</button>`;
    if (!canDecideClosure(order)) return '';
    return `<button class="po-btn is-primary" data-action="close_approve" data-id="${order.id}">Aprobar cierre</button><button class="po-btn" data-action="close_reject" data-id="${order.id}">Rechazar cierre</button>`;
  }
  const closureKind = request => request.kind === 'unavailable' ? 'Falta de disponibilidad' : 'Cambio de precio';
  function closureHistory(order) {
    const request = order.closure_request;
    if (!request) return '';
    const labels = {pending:'Pendiente de revisión', approved:'Aprobada', rejected:'Rechazada', cancelled:'Sin efecto por recepción'};
    const fields = [['Tipo de cierre',closureKind(request)], ['Solicitado por',request.requested_by],
      ['Fecha de solicitud (UTC)',request.requested_at], ['Motivo del cierre',request.reason], ['Resultado',labels[request.status] || request.status]];
    if (request.decided_by_user_id) fields.push(['Decidido por',request.decided_by || 'Usuario ' + request.decided_by_user_id]);
    if (request.decided_at) fields.push([request.status === 'cancelled' ? 'Fecha de finalización (UTC)' : 'Fecha de decisión (UTC)',request.decided_at]);
    if (request.decision_note) fields.push(['Nota de decisión',request.decision_note]);
    return `<h3>Solicitud de cierre comercial</h3><div class="po-detail-grid">${fields.map(([label,value])=>`<p><strong>${label}</strong>${escape(value)}</p>`).join('')}</div>`;
  }
  function decisionButtons(order) {
    if (!canDecide(order)) return '';
    return `<button class="po-btn is-danger" data-action="reject" data-id="${order.id}">Rechazar</button><button class="po-btn is-primary" data-action="approve" data-id="${order.id}">Aprobar</button>`;
  }
  // Presentation only: the detail reads as a document; every field and action keeps its source and condition.
  const WARNING_ICON = '<svg class="st-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>';
  const fieldGrid = fields => `<div class="po-detail-grid">${fields.map(([label,value])=>`<p><strong>${label}</strong>${escape(value)}</p>`).join('')}</div>`;
  const numberCell = (label, value, alert = false) => `<td data-label="${label}" class="ds-num${alert && Number(value) > 0 ? ' po-qty-alert' : ''}">${value}</td>`;
  const costCell = (label, value) => `<td data-label="${label}" class="ds-num">${value || '<span class="ds-na">—</span>'}</td>`;
  function lineRows(order) {
    return order.items.map(line => `<tr><th scope="row" data-label="Producto"><span class="po-line-name">${escape(line.product_name)}</span><span class="po-line-code">${escape(line.product_code)}</span></th>${numberCell('Pedido', line.quantity_ordered)}${numberCell('Recibido', line.quantity_received)}${numberCell('Por recibir', line.quantity_expected ?? line.quantity_pending)}${numberCell('No suministrado', line.quantity_not_supplied, true)}${costCell('Costo', money(line.unit_cost, order.currency))}${costCell('Importe', money(line.line_total, order.currency))}</tr>`).join('');
  }
  function supplierPanel(supplier) {
    const contact = [supplier.contact_name, supplier.phone, supplier.email].filter(Boolean);
    const lines = contact.length ? contact.map(item => `<p>${escape(item)}</p>`).join('') : '<p class="ds-na">Sin contacto</p>';
    return `<section class="po-doc-panel po-doc-supplier" aria-labelledby="po-doc-supplier"><h3 id="po-doc-supplier">Proveedor</h3><strong>${escape(supplier.name)}</strong><span class="po-doc-label">RIF</span><p>${escape(supplier.tax_id)}</p><span class="po-doc-label">Contacto</span>${lines}</section>`;
  }
  function documentSummary(order) {
    const total = context.canViewCost ? money(order.total, order.currency) : '<span class="ds-na">—</span>';
    return `<dl class="po-doc-summary"><div><dt>Estado</dt><dd><span class="po-badge is-${escape(order.status)}"><span class="po-badge-dot" aria-hidden="true"></span>${escape(order.status_label)}</span></dd></div><div><dt>Entrega estimada</dt><dd>${escape(order.estimated_delivery_date || 'Sin fecha')}</dd></div><div><dt>Recibido</dt><dd>${escape(order.received_percent)}%</dd></div><div class="is-amount"><dt>Total</dt><dd>${total}</dd></div></dl>`;
  }
  function totalsBlock(order) {
    if (!context.canViewCost) return '';
    return `<dl class="po-totals"><div><dt>Subtotal</dt><dd>${money(order.subtotal, order.currency)}</dd></div><div class="is-total"><dt>Total</dt><dd>${money(order.total, order.currency)}</dd></div></dl>`;
  }
  async function detail(id) {
    const order = await api('/api/purchases/' + id); current = order;
    // Proveedor, RIF y contacto viven en su panel; estado, entrega, recibido y total, en el resumen superior.
    const fields = [['Fecha de orden', order.ordered_date], ['Creada por', order.creator.name], ...supplyFields(order)];
    const source = order.replenishment_request_id ? `<p class="po-doc-source"><strong>Solicitud de reposición de origen:</strong> <a href="/purchases/requests/${Number(order.replenishment_request_id)}">${escape(order.replenishment_request_number || 'Ver solicitud')}</a></p>` : '';
    if (order.approved_at) fields.push(['Aprobada por', order.approved_by], ['Fecha de aprobación (UTC)', order.approved_at]);
    if (order.rejected_at) fields.push(['Rechazada por', order.rejected_by], ['Fecha de rechazo (UTC)', order.rejected_at], ['Motivo', order.rejection_reason]);
    if (order.closed_at && order.closure_reason) fields.push(['Motivo de cierre', order.closure_reason]);
    const draft = order.status === 'pending_approval' ? `<div class="po-notice ds-callout" role="note">${WARNING_ICON}<div><strong>Borrador pendiente de aprobación</strong>No es una orden de compra definitiva.</div></div>` : '';
    const shortage = canRequestShortage(order) ? `<button class="po-btn" data-action="shortage" data-id="${order.id}">Solicitar reposición del faltante</button>` : '';
    const receipts = order.receptions.map(receipt => `<tr><td>${receipt.id}</td><td>${escape(receipt.received_at)}</td><td>${escape(receipt.responsible)}</td><td class="ds-num">${receipt.items.reduce((sum,line)=>sum+line.quantity,0)}</td><td>${escape(receipt.notes)}</td></tr>`).join('');
    const units = `${PurchaseMoney.count(order.units, 'unidad', 'unidades')} · ${PurchaseMoney.count(order.line_count, 'renglón', 'renglones')}`;
    const notes = order.notes ? `<p class="po-doc-notes">${escape(order.notes)}</p>` : '<p class="po-doc-notes is-empty">Sin notas.</p>';
    const lines = `<div class="po-doc-section-head"><h3>Productos</h3><span>${units}</span></div><div class="po-lines-wrap"><table class="po-doc-lines"><thead><tr><th scope="col">Producto</th><th scope="col" class="ds-num">Pedido</th><th scope="col" class="ds-num">Recibido</th><th scope="col" class="ds-num">Por recibir</th><th scope="col" class="ds-num">No suministrado</th><th scope="col" class="ds-num">Costo</th><th scope="col" class="ds-num">Importe</th></tr></thead><tbody>${lineRows(order)}</tbody></table></div>${totalsBlock(order)}`;
    const history = `<h3>Historial de recepciones</h3><div class="po-lines-wrap"><table><thead><tr><th scope="col">N°</th><th scope="col">Fecha</th><th scope="col">Responsable</th><th scope="col" class="ds-num">Unidades</th><th scope="col">Notas</th></tr></thead><tbody>${receipts || '<tr><td colspan="5" class="ds-na">Sin recepciones registradas.</td></tr>'}</tbody></table></div>`;
    const actions = `<footer><div class="po-doc-footer-start"><a class="po-btn" href="${printURL(order)}" target="_blank" rel="noopener">Imprimir</a></div><button class="po-btn" data-close>Cerrar</button>${closureButtons(order)}${shortage}${decisionButtons(order)}</footer>`;
    const general = `<section class="po-doc-panel" aria-labelledby="po-doc-general"><h3 id="po-doc-general">Información general</h3>${fieldGrid(fields)}${source}</section>`;
    open((order.status === 'pending_approval' ? 'Borrador de orden ' : 'Orden de compra ') + order.order_number, `${previewHelp()}${draft}${documentSummary(order)}<div class="po-doc-columns">${supplierPanel(order.supplier)}${general}</div>${closureHistory(order)}${shortageSection(order)}${lines}<h3>Notas</h3>${notes}${history}${actions}`);
  }

  function renderLines() {
    const tbody = document.getElementById('po-edit-lines');
    if (!tbody) return;
    tbody.innerHTML = lines.map((line, index) => `<tr><td style="width:40%">${escape(line.product_name)}<small style="display:block">${escape(line.product_code)} · Precio de la cotización${line.minimum_order_quantity ? ' · Cantidad mínima del proveedor (informativa): ' + escape(line.minimum_order_quantity) : ''}</small></td><td><input type="number" min="1" max="2147483647" step="1" value="${line.quantity}" data-line="${index}" data-field="quantity" aria-label="Cantidad ${escape(line.product_name)}" required></td><td><input type="number" min="0" max="9999999999.99" step="0.01" value="${escape(line.unit_cost)}" readonly data-line="${index}" data-field="unit_cost" aria-label="Costo unitario de la orden ${escape(line.product_name)}" required></td><td data-line-total="${index}">${money(Number(line.quantity) * Number(line.unit_cost))}</td><td style="width:45px"><button type="button" class="po-icon-btn" data-remove="${index}" aria-label="Quitar ${escape(line.product_name)}">×</button></td></tr>`).join('');
    document.getElementById('po-edit-total').textContent = 'Total estimado: ' + money(lines.reduce((sum,line)=>sum + Number(line.quantity)*Number(line.unit_cost), 0));
  }
  function showProducts(products) {
    const results = document.getElementById('po-product-results');
    if (!results) return;
    options.products = products;
    results.innerHTML = products.map(product => `<button type="button" data-product="${product.id}"${lines.some(line=>line.product_id === product.id) ? ' disabled' : ''}>${escape(product.code)} · ${escape(product.name)} <small>· Precio cotizado ${money(product.unit_cost)}${product.minimum_order_quantity ? ' · mín. ' + escape(product.minimum_order_quantity) + ' (informativa)' : ''}</small></button>`).join('') || '<p class="po-form-help">No hay productos activos coincidentes.</p>';
  }
  // Only existing drafts are edited; new orders are prepared from a replenishment request.
  async function edit(id) {
    current = await api('/api/purchases/' + id);
    mode = 'edit';
    documentCurrency = current?.currency || null;
    options = await api('/api/purchases/options');
    const partial = ['approved','partial'].includes(current?.status);
    lines = current ? current.items.map(line=>({...line, quantity:line.quantity_ordered})) : [];
    const suppliers = options.suppliers.map(supplier=>`<option value="${supplier.id}"${current?.supplier.id === supplier.id ? ' selected' : ''}>${escape(supplier.name)} · ${escape(supplier.tax_id)}</option>`).join('');

    open('Editar borrador ' + current.order_number, `<form id="po-order-form">${previewHelp()}${partial ? '<p class="po-form-help">Orden aprobada o parcialmente recibida: proveedor y renglones bloqueados para preservar el historial. Solo puede editar fecha y notas.</p>' : ''}<div class="po-form-grid"><label>Proveedor${partial ? `<input value="${escape(current.supplier.name)}" disabled>` : `<select name="supplier_id" required><option value="">Seleccione un proveedor</option>${suppliers}</select></label><label>Cotización vigente<select name="supplier_quote_id" required disabled><option value="">Seleccione primero un proveedor</option></select>`}</label><label>Moneda de la cotización<select name="currency" disabled aria-label="Moneda heredada de la cotización"><option value="USD">USD ($)</option><option value="VES">VES (Bs.)</option></select></label><label>Fecha estimada de entrega<input type="date" name="estimated_delivery_date" value="${escape(current?.estimated_delivery_date || '')}"></label><label class="po-wide">Notas<textarea name="notes" maxlength="1000">${escape(current?.notes || '')}</textarea></label></div>${partial ? '' : '<h3>Productos</h3><p class="po-form-help">Seleccione una cotización para cargar sus productos y precios. El precio cotizado se verifica al guardar.</p><label>Buscar por nombre o código<div class="po-product-search"><input type="search" id="po-product-search" autocomplete="off" placeholder="Buscar producto activo"></div></label><div id="po-product-results" class="po-picker-results"></div><div class="po-lines-wrap"><table><thead><tr><th style="width:40%">Producto</th><th>Cantidad</th><th>Costo unitario de la orden</th><th>Importe</th><th style="width:45px"></th></tr></thead><tbody id="po-edit-lines"></tbody></table></div><p id="po-edit-total" class="po-total"></p>'}${errorBox()}<p class="po-form-help">Crear una orden no modifica ni reserva existencias. El total definitivo se calcula en el servidor.</p>${footer('Guardar orden')}</form>`);
    body.querySelector('[name=currency]').value = documentCurrency || '';
    if (!partial) {
      renderLines();
      await reloadQuotes(current?.supplier_quote_id);

    }
  }

  async function reception(id) {
    current = await api('/api/purchases/' + id); mode = 'receive';
    const rows = current.items.filter(line=>line.quantity_pending > 0).map(line=>`<tr><td>${escape(line.product_name)}<br><small>${escape(line.product_code)}</small></td><td>${line.quantity_ordered}</td><td>${line.quantity_received}</td><td>${line.quantity_pending}</td><td><input type="number" name="line-${line.id}" min="0" max="${line.quantity_pending}" step="1" value="0" aria-label="Recibir ${escape(line.product_name)}" required></td></tr>`).join('');
    open('Registrar recepción · ' + current.order_number, `<form id="po-reception-form">${previewHelp()}<p>Proveedor: ${escape(current.supplier.name)}</p><div class="po-form-grid"><label>Fecha de recepción<input type="date" name="received_at" min="${current.ordered_date}" max="${context.today}" value="${context.today}" required></label><label>Responsable<input value="Usuario autenticado actual" disabled></label></div><div class="po-lines-wrap"><table><thead><tr><th style="width:40%">Producto</th><th>Pedido</th><th>Recibido</th><th>Pendiente</th><th>Recibir ahora</th></tr></thead><tbody>${rows}</tbody></table></div><label>Notas<textarea name="notes" maxlength="1000"></textarea></label>${partialClosureFields()}<p class="po-form-help">Solo las cantidades recibidas aumentan el inventario. La orden admite una sola recepción y la operación completa es atómica.</p>${errorBox()}${footer('Confirmar recepción')}</form>`);
    togglePartialClosure();
  }
  // Same contract as the Recepciones form; the server re-checks both fields before any movement.
  // Inline styles keep the compiled stylesheet untouched; the dialog styles every input as a full-width field.
  const PARTIAL_BOX = 'border:1px solid #ecd894;border-radius:8px;background:#fff5d6;padding:12px 14px;margin:14px 0';
  const PARTIAL_LEGEND = 'font-size:13px;font-weight:650;color:#9a3412;padding:0 6px;width:auto;margin:0';
  const PARTIAL_CHECK = 'display:flex;flex-direction:row;align-items:center;gap:8px;margin:8px 0';
  const PARTIAL_INPUT = 'width:16px;height:16px;flex:none;margin:0;padding:0';
  const partialClosureFields = () => `<fieldset id="po-partial" class="po-wide" style="${PARTIAL_BOX}" hidden><legend style="${PARTIAL_LEGEND}">Cierre definitivo con faltante</legend><p class="po-form-help">Llega menos de lo pedido. La orden se cerrará y el faltante no se recibirá después; para reponerlo se tramita una nueva solicitud.</p><label style="${PARTIAL_CHECK}"><input type="checkbox" name="confirm_partial_closure" value="1" style="${PARTIAL_INPUT}"> Confirmo el cierre definitivo de la orden con el faltante</label><label>Motivo concreto del faltante<textarea name="closure_reason" minlength="10" maxlength="1000"></textarea></label></fieldset>`;
  function receivingPartial(form) {
    return current.items.some(line => line.quantity_pending > 0 && Number(form.elements['line-' + line.id]?.value || 0) < line.quantity_pending);
  }
  function togglePartialClosure() {
    const form = document.getElementById('po-reception-form'), box = document.getElementById('po-partial');
    if (!form || !box) return;
    const partial = receivingPartial(form);
    box.hidden = !partial;
    form.elements.confirm_partial_closure.required = partial;
    form.elements.closure_reason.required = partial;
  }

  async function confirmation(id, action) {
    current = await api('/api/purchases/' + id); mode = action;
    if (action === 'close_request') {
      open('Solicitar cierre por falta de disponibilidad · ' + current.order_number, `<form id="po-confirm-form">${previewHelp()}<p>La orden sigue aprobada hasta que un administrador autorice el cierre. El cierre no registra recepción ni cambia el stock. Si se necesita la mercancía, debe iniciarse una nueva solicitud de reposición, cotización y orden de compra.</p><label>Tipo de cierre<select name="kind" required><option value="unavailable">Falta de disponibilidad</option><option value="price_change">Cambio de precio informado por el proveedor</option></select></label><label>Motivo obligatorio<textarea name="reason" required maxlength="1000"></textarea></label>${errorBox()}${footer('Solicitar cierre')}</form>`);
      return;
    }
    if (action === 'close_approve' || action === 'close_reject') {
      const reject = action === 'close_reject';
      open((reject ? 'Rechazar' : 'Aprobar') + ' cierre · ' + current.order_number, `<form id="po-confirm-form">${previewHelp()}${closureHistory(current)}<p>${reject ? 'La orden seguirá aprobada y disponible para recepción.' : 'Se cerrará la orden sin recepción ni cambios de stock. Se conservan proveedor, productos, cantidades y precios originales.'}</p>${reject ? '<label>Motivo<textarea name="reason" required maxlength="1000"></textarea></label>' : ''}${errorBox()}${footer(reject ? 'Rechazar cierre' : 'Aprobar cierre')}</form>`);
      return;
    }
    if (['approve','reject'].includes(action)) {
      const reject = action === 'reject';
      open((reject ? 'Rechazar' : 'Aprobar') + ' orden ' + current.order_number,
        `<form id="po-confirm-form">${previewHelp()}<p>La decisión quedará registrada con tu usuario y fecha. No modifica existencias.</p>${reject ? '<label>Motivo de rechazo<textarea name="reason" required maxlength="1000"></textarea></label>' : ''}${errorBox()}${footer(reject ? 'Confirmar rechazo' : 'Confirmar aprobación')}</form>`);
      return;
    }
    if (action === 'shortage') {
      if (!canRequestShortage(current)) {
        open('Faltante de ' + current.order_number, `${shortageSection(current) || '<p>La orden no tiene un faltante que pueda solicitarse.</p>'}<footer><button class="po-btn is-primary" data-close>Cerrar</button></footer>`);
        return;
      }
      const rows = current.items.filter(line => line.quantity_not_supplied > 0).map(line => `<tr><td>${escape(line.product_code)}<br>${escape(line.product_name)}</td><td>${line.quantity_not_supplied}</td></tr>`).join('');
      open('Solicitar reposición del faltante · ' + current.order_number, `<form id="po-confirm-form">${previewHelp()}<p>Se creará una solicitud de reposición nueva con las cantidades no suministradas. Requiere preparación y aprobación como cualquier compra. La orden original no cambia.</p><div class="po-lines-wrap"><table><thead><tr><th>Producto</th><th>No suministrado</th></tr></thead><tbody>${rows}</tbody></table></div>${errorBox()}${footer('Crear solicitud')}</form>`);
      return;
    }
    if (action === 'renew') {
      const result = await api('/api/purchases/options?supplier_id=' + encodeURIComponent(current.supplier.id));
      const quotes = (result.quotes || []).filter(quote => quote.currency === current.currency && quote.id !== current.supplier_quote_id);
      open('Cambiar cotización · ' + current.order_number, `<form id="po-confirm-form">${previewHelp()}<p>Solo cotizaciones vigentes del mismo proveedor y moneda. Se conservan productos y cantidades de la solicitud; los importes se recalculan en el servidor. Para otro proveedor o moneda, anule el borrador y tramite una nueva solicitud.</p><label>Cotización vigente<select name="supplier_quote_id" required><option value="">${quotes.length ? 'Seleccione una cotización' : 'No hay otra cotización vigente'}</option>${quotes.map(quote => `<option value="${quote.id}">${escape(quote.label)}</option>`).join('')}</select></label>${errorBox()}${footer('Cambiar cotización')}</form>`);
      return;
    }
    open('Anular orden ' + current.order_number, `<form id="po-confirm-form">${previewHelp()}<p>La orden queda anulada por error y no se modifica. Hace falta un motivo. Solo se permiten órdenes sin recepción.</p><label>Motivo<textarea name="reason" required maxlength="1000"></textarea></label>${errorBox()}${footer('Confirmar anulación')}</form>`);
  }

  async function runAction(action, id, button) {
    if (action !== 'menu') closeMenu();
    if (action === 'menu') {
      const row = context.items.find(order=>order.id === id) || await api('/api/purchases/' + id);
      actions(row, button);
    } else if (action === 'detail') await detail(id);
    else if (action === 'edit') await edit(id);
    else if (action === 'receive') await reception(id);
    else if (['cancel','approve','reject','close_request','close_approve','close_reject','shortage','renew'].includes(action)) await confirmation(id, action);
    else if (action === 'print') window.open(printURL({id}), '_blank', 'noopener');
  }

  document.addEventListener('click', event => {
    const close = event.target.closest('[data-close]');
    if (close) { close.closest('dialog')?.close(); return; }
    const button = event.target.closest('[data-action]');
    if (button) { runAction(button.dataset.action, Number(button.dataset.id), button).catch(error=>toast(error.message)); return; }
    if (!event.target.closest('#po-menu')) closeMenu();
    if (event.target.closest('#po-add-supplier')) supplierDialog.showModal();
    const selected = event.target.closest('[data-product]');
    if (selected) {
      const product = options.products.find(item=>item.id === Number(selected.dataset.product));
      if (product && !lines.some(line=>line.product_id === product.id)) {
        lines.push({product_id:product.id, product_code:product.code, product_name:product.name, quantity:1, unit_cost:product.unit_cost, associated:Boolean(product.associated), minimum_order_quantity:product.minimum_order_quantity || ''});
        renderLines(); showProducts(options.products);
      }
    }
    const remove = event.target.closest('[data-remove]');
    if (remove) { lines.splice(Number(remove.dataset.remove),1); renderLines(); showProducts(options.products); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeMenu();
    if (!menu.hidden && ['ArrowDown','ArrowUp'].includes(event.key)) {
      event.preventDefault(); const items = [...menu.querySelectorAll('button')], index = items.indexOf(document.activeElement);
      items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    }
  });
  window.addEventListener('resize', closeMenu);
  document.addEventListener('scroll', closeMenu, true);
  function clearQuoteProducts() {
    productRequest++;
    clearTimeout(searchTimer);
    lines = [];
    documentCurrency = null;
    const currency = body.querySelector('[name=currency]');
    if (currency) currency.value = '';
    renderLines();
    showProducts([]);
    document.getElementById('po-product-results').textContent = 'Seleccione una cotización vigente para ver sus productos.';
    const search = document.getElementById('po-product-search');
    if (search) { search.value = ''; search.disabled = true; }
  }
  async function reloadQuotes(selectedId = null) {
    const requestId = ++quoteRequest;
    const supplier = body.querySelector('[name=supplier_id]');
    const select = body.querySelector('[name=supplier_quote_id]');
    if (!select) return;
    const supplierId = supplier.value;
    const savedLines = selectedId ? lines : [];
    clearQuoteProducts();
    select.disabled = true;
    select.innerHTML = `<option value="">${supplierId ? 'Cargando cotizaciones…' : 'Seleccione primero un proveedor'}</option>`;
    if (!supplierId) return;
    try {
      const result = await api('/api/purchases/options?supplier_id=' + encodeURIComponent(supplierId));
      if (requestId !== quoteRequest || !select.isConnected || supplier.value !== supplierId) return;
      const quotes = (result.quotes || []).filter(quote => String(quote.supplier_id) === supplierId);
      select.innerHTML = `<option value="">${quotes.length ? 'Seleccione una cotización vigente' : 'No hay cotizaciones vigentes'}</option>` +
        quotes.map(quote => `<option value="${quote.id}" data-currency="${escape(quote.currency)}">${escape(quote.label)}</option>`).join('');
      select.disabled = !quotes.length;
      if (selectedId && quotes.some(quote => String(quote.id) === String(selectedId))) {
        select.value = String(selectedId);
        lines = savedLines;
        syncCurrency();
        renderLines();
        await reloadProducts();
      }
    } catch (error) {
      if (requestId === quoteRequest && select.isConnected) {
        select.innerHTML = '<option value="">No se pudieron cargar las cotizaciones</option>';
        toast(error.message);
      }
    }
  }
  async function reloadProducts(term) {
    const requestId = ++productRequest;
    const supplier = body.querySelector('[name=supplier_id]');
    const quote = body.querySelector('[name=supplier_quote_id]');
    const input = document.getElementById('po-product-search');
    if (!supplier?.value || !quote?.value || !input) return;
    input.disabled = false;
    const supplierId = supplier.value, quoteId = quote.value;
    const search = term === undefined ? input.value : term;
    const params = new URLSearchParams({supplier_id:supplierId, supplier_quote_id:quoteId, search});
    try {
      const result = await api('/api/purchases/options?' + params.toString());
      if (requestId === productRequest && input.isConnected && supplier.value === supplierId && quote.value === quoteId && input.value === search) showProducts(result.products);
    } catch (error) { if (requestId === productRequest && input.isConnected) toast(error.message); }
  }
  function syncCurrency() {
    const quote = body.querySelector('[name=supplier_quote_id]');
    documentCurrency = quote?.selectedOptions[0]?.dataset.currency || null;
    body.querySelector('[name=currency]').value = documentCurrency || '';
  }
  body.addEventListener('change', event => {
    if (event.target.name === 'supplier_id') reloadQuotes();
    if (event.target.name === 'supplier_quote_id') { clearQuoteProducts(); syncCurrency(); renderLines(); reloadProducts(); }
  });
  body.addEventListener('input', event => {
    if (event.target.matches('[data-line]')) {
      const index = Number(event.target.dataset.line); lines[index][event.target.dataset.field] = event.target.value;
      body.querySelector(`[data-line-total="${index}"]`).textContent = money(Number(lines[index].quantity)*Number(lines[index].unit_cost));
      document.getElementById('po-edit-total').textContent = 'Total estimado: ' + money(lines.reduce((sum,line)=>sum+Number(line.quantity)*Number(line.unit_cost),0));
    }
    if (event.target.id === 'po-product-search') {
      clearTimeout(searchTimer); const term = event.target.value;
      searchTimer = setTimeout(() => reloadProducts(term), 250);
    }
    if (event.target.name?.startsWith('line-')) togglePartialClosure();
  });

  async function save(form) {
    const values = Object.fromEntries(new FormData(form));
    let path = '/api/purchases', method = 'POST', data;
    if (form.id === 'po-order-form') {
      data = {estimated_delivery_date:values.estimated_delivery_date, notes:values.notes};
      if (!['approved','partial'].includes(current?.status)) {
        if (!lines.length) throw new Error('Agregue al menos un producto.');
        data.supplier_id = values.supplier_id;
        data.supplier_quote_id = values.supplier_quote_id;
        data.items = lines.map(line=>({product_id:line.product_id, quantity:line.quantity, unit_cost:String(line.unit_cost)}));
      }
      path += '/' + current.id; method = 'PATCH';
    } else if (form.id === 'po-reception-form') {
      data = {received_at:values.received_at, notes:values.notes, items:current.items.filter(line=>Number(values['line-'+line.id]) > 0).map(line=>({line_id:line.id, quantity:values['line-'+line.id]}))};
      if (!data.items.length) throw new Error('Indique al menos una cantidad recibida mayor a cero.');
      if (receivingPartial(form)) {
        if (!form.elements.confirm_partial_closure.checked) throw new Error('Confirme el cierre definitivo de la orden con el faltante.');
        data.confirm_partial_closure = true;
        data.closure_reason = values.closure_reason || '';
      }
      path += '/' + current.id + '/receptions';
    } else if (form.id === 'po-confirm-form' && mode === 'shortage') {
      path = '/api/replenishment-requests/from-order/' + current.id; data = {};
    } else if (form.id === 'po-confirm-form' && mode === 'renew') {
      path += '/' + current.id; method = 'PATCH'; data = {supplier_quote_id:values.supplier_quote_id};
    } else if (form.id === 'po-confirm-form') {
      if (mode === 'close_request') path += '/' + current.id + '/closure-requests';
      else if (mode === 'close_approve') path += '/' + current.id + '/closure-requests/approve';
      else if (mode === 'close_reject') path += '/' + current.id + '/closure-requests/reject';
      else path += '/' + current.id + '/' + mode;
      data = ['reject','cancel','close_request','close_reject'].includes(mode) ? {reason:values.reason, kind:values.kind} : {};
    }
    if (context.reference) { dialog.close(); toast('Vista previa: validación completada. No se guardó ningún cambio.'); return; }
    const result = await api(path, method, data);
    if (mode === 'shortage') { window.location.assign('/purchases/requests/' + Number(result.id)); return; }
    const url = new URL(window.location.href); url.searchParams.set('detail', result.id); url.searchParams.delete('page');
    window.location.assign(url);
  }
  body.addEventListener('submit', async event => {
    event.preventDefault(); const form = event.target, submit = form.querySelector('[type=submit]');
    const error = form.querySelector('.po-form-error'); error.textContent = ''; submit.disabled = true;
    try { await save(form); } catch (failure) { error.textContent = failure.message; } finally { submit.disabled = false; }
  });
  document.getElementById('po-supplier-form').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.target, button = form.querySelector('button:not([type=button])'), error = form.querySelector('.po-form-error');
    error.textContent = ''; button.disabled = true;
    try {
      if (context.reference) { supplierDialog.close(); toast('Proveedor de referencia: no se guardan cambios.'); return; }
      const supplier = await api('/api/purchases/suppliers','POST',Object.fromEntries(new FormData(form)));
      const select = body.querySelector('[name=supplier_id]');
      if (select) select.add(new Option(supplier.name + ' · ' + supplier.tax_id, supplier.id, true, true));
      supplierDialog.close(); form.reset();
    } catch(failure) { error.textContent = failure.message; } finally { button.disabled = false; }
  });
  document.getElementById('po-toggle-filters').addEventListener('click', event => {
    const filters = document.getElementById('po-filters'); filters.hidden = !filters.hidden;
    event.currentTarget.setAttribute('aria-expanded', String(!filters.hidden));
    if (!filters.hidden) filters.querySelector('[name=search]').focus();
  });
  const filters = document.getElementById('po-filters');
  filters.addEventListener('change', event=>{if (!event.target.matches('[type=search]')) filters.requestSubmit();});
  filters.querySelector('[name=search]').addEventListener('search',()=>filters.requestSubmit());
  document.getElementById('po-per-page').addEventListener('change', event=>{const url = new URL(location.href);url.searchParams.set('per_page',event.target.value);url.searchParams.delete('page');location.assign(url);});
  if (context.reference) {
    document.querySelector('.ix-bell')?.addEventListener('click',()=>toast('Referencia: 3 notificaciones de ejemplo. Sin acceso a datos reales.'));
    document.querySelector('.ix-search input')?.addEventListener('input',event=>{if(event.target.value.length > 1) toast('Búsqueda de referencia: utilice los filtros de órdenes para explorar los datos de ejemplo.');});
  }
  const initial = new URL(location.href).searchParams.get('detail');
  if (initial && /^\d+$/.test(initial)) detail(Number(initial)).catch(error=>toast(error.message));
  else if (context.canWrite && new URL(location.href).searchParams.get('new') === '1') location.replace('/purchases/requests' + (context.reference ? '?ref=1' : ''));
})();
