/* Native dialogs provide focus containment, Escape and return focus. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const context = JSON.parse($('sp-context').textContent);
  const editor = $('sp-editor'), form = $('sp-editor-form'), menu = $('sp-menu');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const suffix = context.reference ? '&ref=1' : '';
  let editing = null, pending = null, menuTrigger = null, toastTimer, reviewing = null, deciding = false;
  let detailTrigger = null;
  const approvalLabel = row => ({pending:'Pendiente',approved:'Aprobado',rejected:'Rechazado'}[row.approval_status] || '—');
  // Alta pendiente o modificación propuesta de un proveedor aprobado: decide quien no la propuso.
  const canDecide = row => !context.reference && context.canApprove && (row.approval_status === 'pending' && row.proposed_by_user_id !== context.currentUserId
    || row.pending_change && row.pending_change.proposed_by_user_id !== context.currentUserId);
  const CHANGE_LABELS = {name:'Nombre / razón social',tax_id:'RIF',contact_name:'Contacto principal',contact_title:'Cargo',phone:'Teléfono',email:'Correo',address:'Dirección',city:'Ciudad',state_province:'Estado/Provincia',category:'Categoría',notes:'Notas',is_active:'Estado'};
  const changeSummary = change => `<section class="sp-change" aria-label="Modificación propuesta"><h3>Modificación propuesta por ${esc(change.proposed_by_name || 'Asistente')}</h3><p>Los datos oficiales no cambian hasta que un administrador apruebe la propuesta.</p><p>${esc(change.reason || '')}</p>${fields(Object.keys(change.changes).map(key => [CHANGE_LABELS[key] || key, `${typeof change.previous_values[key] === 'boolean' ? (change.previous_values[key] ? 'Activo' : 'Inactivo') : change.previous_values[key] || '—'} → ${typeof change.changes[key] === 'boolean' ? (change.changes[key] ? 'Activo' : 'Inactivo') : change.changes[key] || '—'}`]))}</section>`;
  const canEdit = row => context.canWrite && (context.reference || row.approval_status !== 'pending' || row.proposed_by_user_id === context.currentUserId);
  function toast(message) {
    $('sp-message').textContent = message; $('sp-message').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => {$('sp-message').hidden = true;}, 6000);
  }
  async function api(path, method = 'GET', data) {
    if (context.reference && method !== 'GET') throw new Error('La referencia no admite escrituras.');
    if (context.reference) path += (path.includes('?') ? '&' : '?') + 'ref=1';
    const response = await fetch(path, {method, credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json'},body:data === undefined ? undefined : JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
    return result;
  }
  function closeMenu() { menu.hidden = true; menuTrigger?.setAttribute('aria-expanded', 'false'); }
  function ordersURL(id) {return `/purchases?supplier_id=${id}&start=&end=${suffix}`;}
  function newOrderURL(id) {return `/purchases?new=1&supplier_id=${id}${suffix}`;}
  async function edit(id = null) {
    editing = id; form.reset(); form.querySelector('.sp-error').textContent = '';
    $('sp-editor-title').textContent = id ? 'Proponer modificación de proveedor' : 'Nuevo proveedor';
    $('sp-editor-subtitle').textContent = context.reference ? 'Registra o actualiza el proveedor' : 'La propuesta quedará pendiente de aprobación del Administrador.';
    if (id) {
      const row = await api('/api/suppliers/' + id);
      if (!canEdit(row)) throw new Error('No puede editar esta propuesta.');
      for (const input of form.elements) if (input.name && Object.hasOwn(row,input.name)) input.value = String(row[input.name] ?? '');
    }
    $('sp-notes-count').textContent = form.elements.notes.value.length + '/300';
    editor.showModal();
  }
  const fields = pairs => `<dl class="sp-detail-grid">${pairs.map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value || '—')}</dd></div>`).join('')}</dl>`;
  const dateLabel = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value.split('-').reverse().join('/') : 'Fecha no registrada';
  const emptyState = message => `<p class="sp-purchase-empty">${esc(message)}</p>`;
  const purchaseSection = (id, title, body) => `<section class="sp-purchase-section" aria-labelledby="${id}"><h3 id="${id}">${title}</h3>${body}</section>`;
  function purchaseSummary(row) {
    const metrics = [];
    if (context.canViewCost) {
      for (const currency of ['USD', 'VES']) {
        const total = (row.totals_by_currency || []).find(item => item.currency === currency);
        metrics.push([`Total solicitado ${currency}`, total ? PurchaseMoney.format(total.amount, currency) : 'Sin compras en ' + currency, currency]);
      }
    }
    if (context.canViewCost && row.stage_totals) {
      for (const [key,label] of [['approved','Aprobado'],['received','Recibido neto'],['not_supplied','No suministrado']]) {
        const totals = row.stage_totals[key] || [];
        metrics.push([label, totals.length ? totals.map(item=>PurchaseMoney.format(item.amount,item.currency)).join(' / ') : 'Sin importes']);
      }
    }
    metrics.push(['Órdenes totales' , row.orders ?? 'No disponible'],
      ['Pendientes o parciales', row.pending ?? 'No disponible'],
      ['Órdenes completadas', row.completed ?? 'No disponible'],
      ['Última compra', row.last ? dateLabel(row.last) : 'Sin compras']);
    const grid = `<dl class="sp-purchase-summary">${metrics.map(([label, value, currency]) => `<div${currency ? ` data-currency="${currency}"` : ''}><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`;
    const note = row.unknown_currency_orders ? `<p class="sp-purchase-note">${esc(PurchaseMoney.count(row.unknown_currency_orders, 'orden sin moneda', 'órdenes sin moneda'))}. Excluidas de los totales.</p>` : '';
    return purchaseSection('sp-purchase-summary-title', 'Resumen de compras', grid + note);
  }
  function productCost(row) {
    if (row.purchase_price == null || row.purchase_price === '') return 'No registrado';
    return esc(PurchaseMoney.format(row.purchase_price, row.purchase_price_currency));
  }
  function suppliedProducts(rows) {
    let body = emptyState('La información de productos suministrados no está disponible.');
    if (Array.isArray(rows)) {
      body = rows.length ? `<ul class="sp-purchase-list sp-products">${rows.map(row => {
        const delivery = row.lead_time_days == null ? 'No registrado' : PurchaseMoney.count(row.lead_time_days, 'día', 'días');
        const preferred = row.operational_preferred ? '<span class="sp-purchase-badge is-positive">Preferido</span>' : '';
        return `<li class="sp-product-item"><div class="sp-product-heading"><h4>${esc(row.product_name || 'Nombre no registrado')}</h4>${preferred}</div><dl class="sp-product-meta"><div><dt>Código del producto</dt><dd>${esc(row.product_code || 'No registrado')}</dd></div><div><dt>Código del proveedor</dt><dd>${esc(row.supplier_product_code || 'No registrado')}</dd></div>${context.canViewCost ? `<div><dt>Costo referencial</dt><dd>${productCost(row)}</dd></div>` : ''}<div><dt>Entrega estimada</dt><dd>${esc(delivery)}</dd></div></dl>${row.is_active === false ? '<p class="sp-product-inactive">Asociación inactiva</p>' : ''}</li>`;
      }).join('')}</ul>` : emptyState('Este proveedor no tiene productos asociados.');
    }
    return purchaseSection('sp-supplied-products-title', 'Productos suministrados', body);
  }
  function orderLinks(orders) {
    const tones = {pending_approval:'warning',pending:'warning',approved:'info',partial:'warning',completed:'positive',closed_complete:'positive',closed_partial:'warning',closed_unavailable:'warning',closed_price_change:'warning',rejected:'critical',cancelled:'neutral'};
    return `<ul class="sp-purchase-list sp-orders">${orders.map(order => {
      const label = context.purchaseStatusLabels[order.status] || 'Estado no disponible';
      const amount = context.canViewCost ? `<span class="sp-order-amount">${esc(PurchaseMoney.format(order.total, order.currency))}${order.currency ? ` <small>${esc(order.currency)}</small>` : ''}</span>` : '';
      return `<li><a class="sp-order-item" href="/purchases?detail=${order.id}${suffix}"><div class="sp-order-heading"><strong>${esc(order.number)}</strong>${amount}</div><div class="sp-order-meta"><time datetime="${esc(order.date)}">${esc(dateLabel(order.date))}</time><span class="sp-purchase-badge is-${tones[order.status] || 'neutral'}">${esc(label)}</span></div></a></li>`;
    }).join('')}</ul>`;
  }
  function historicalOrders(row) {
    const orders = row.unknown_currency_order_details || [];
    if (!orders.length) return '';
    return purchaseSection('sp-unknown-orders-title', `Órdenes históricas sin moneda (${orders.length})`, `<p class="sp-purchase-note">Excluidas de los totales. Cada importe se conserva individualmente, sin asignar una moneda.</p>${orderLinks(orders)}`);
  }
  function purchases(row, id) {
    const orders = row.recent_orders || [];
    return `<div class="sp-purchases">${purchaseSummary(row)}${suppliedProducts(row.supplied_products)}${purchaseSection('sp-recent-orders-title', 'Órdenes recientes', orders.length ? orderLinks(orders) : emptyState('Este proveedor no tiene órdenes de compra recientes.'))}${historicalOrders(row)}<a class="sp-btn sp-purchase-all" href="${ordersURL(id)}">Ver órdenes de compra</a></div>`;
  }
  async function detail(id, trigger = document.activeElement) {
    const opener = trigger?.closest('#sp-menu') ? menuTrigger : trigger;
    const row = await api('/api/suppliers/' + id);
    detailTrigger = opener;
    reviewing = row;
    $('sp-detail-title').textContent = row.name;
    $('sp-detail-state').textContent = (row.is_active ? 'Activo' : 'Inactivo') + (row.is_preferred ? ' · ★ Proveedor preferido' : ' · No preferido');
    if (!context.reference) $('sp-detail-state').textContent += ' · ' + approvalLabel(row);
    const sections = {
      information:fields([['Nombre / razón social',row.name],['RIF',row.tax_id],['Estado',row.is_active?'Activo':'Inactivo'],['Categoría',row.category],['Teléfono',row.phone],['Correo',row.email],['Dirección',row.address],['Ciudad',row.city],['Estado/Provincia',row.state_province],['Contacto principal',row.contact_name],['Cargo',row.contact_title],['Notas',row.notes],['Proveedor preferido',row.is_preferred?'Sí':'No']]),
      purchases:purchases(row, id),
      contacts:fields([['Contacto principal',row.contact_name],['Cargo',row.contact_title],['Teléfono',row.phone],['Correo',row.email]]),
      history:fields([['Fecha de registro',row.created_at.replace('T',' ')+' UTC'],['Última actualización',row.updated_at.replace('T',' ')+' UTC']]) + (row.changes_history || []).map(change=>`<article><h3>Propuesta ${change.id} · ${esc(change.status)}</h3>${changeSummary(change)}${fields([['Decisión',change.reviewed_at],['Motivo de rechazo',change.rejection_reason]])}</article>`).join('')
    };
    $('sp-detail-body').innerHTML = `<div class="sp-detail-tabs" role="tablist" aria-label="Información del proveedor">${[['information','Información'],['purchases','Compras'],['contacts','Contactos'],['history','Historial']].map(([key,label],i)=>`<button type="button" role="tab" id="sp-tab-${key}" data-tab="${key}" aria-controls="sp-panel-${key}" aria-selected="${i===0}" tabindex="${i===0?0:-1}">${label}</button>`).join('')}</div>${Object.entries(sections).map(([key,html],i)=>`<section id="sp-panel-${key}" role="tabpanel" tabindex="0" aria-labelledby="sp-tab-${key}"${i?' hidden':''}>${html}</section>`).join('')}`;
    if (!context.reference) {
      $('sp-detail-body').querySelector('#sp-panel-information').insertAdjacentHTML('afterbegin', fields([['Estado de aprobación',approvalLabel(row)],['Fecha de decisión',row.approved_at],['Motivo de rechazo',row.rejection_reason]]));
      if (row.pending_change) $('sp-detail-body').querySelector('#sp-panel-information').insertAdjacentHTML('afterbegin', changeSummary(row.pending_change));
      $('sp-review').hidden = !canDecide(row);
      $('sp-review-reason').value = '';
      $('sp-review-error').textContent = '';
    }
    $('sp-detail-body').scrollTop = 0;
    $('sp-detail').showModal();
  }
  function actions(id, trigger) {
    const row = context.items.find(item=>item.id === id);
    const choices = [['detail','Ver detalle']];
    if (canEdit(row)) choices.push(['edit',context.reference?'Editar proveedor':'Proponer modificación']);
    if (!context.reference && context.canWrite && row.approval_status === 'approved' && !row.pending_change) choices.push(['state_proposal',row.is_active?'Solicitar desactivación':'Solicitar reactivación']);
    let html = choices.map(([action,label])=>`<button role="menuitem" data-action="${action}" data-id="${id}">${label}</button>`).join('');
    html += `<a role="menuitem" href="${ordersURL(id)}">Ver órdenes de compra</a>`;
    if (context.canOrder && row.is_active && (!context.reference || id <= 8)) html += `<a role="menuitem" href="${newOrderURL(id)}">Crear orden de compra</a>`;
    if (context.reference && context.canWrite) {
      html += [['preferred',row.is_preferred?'Quitar de preferidos':'Marcar como preferido'],['status',row.is_active?'Desactivar':'Activar'],['delete','Eliminar proveedor']].map(([action,label])=>`<button role="menuitem" data-action="${action}" data-id="${id}" class="${action==='delete'?'is-danger':''}">${label}</button>`).join('');
    }
    closeMenu(); menuTrigger = trigger; menu.innerHTML = html; menu.hidden = false; trigger.setAttribute('aria-expanded','true');
    const rect = trigger.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(rect.right-menu.offsetWidth,innerWidth-menu.offsetWidth-8))+'px';
    menu.style.top = Math.max(8, Math.min(rect.bottom+5,innerHeight-menu.offsetHeight-8))+'px';
    menu.querySelector('[role=menuitem]').focus();
  }
  function confirmation(id, action) {
    pending = {id,action};
    const row = context.items.find(item=>item.id===id);
    $('sp-confirm-title').textContent = action==='delete'?'Eliminar proveedor':action==='status'?(row.is_active?'Desactivar proveedor':'Activar proveedor'):(row.is_preferred?'Quitar de preferidos':'Marcar como preferido');
    $('sp-confirm-text').textContent = action==='delete'?'Solo se puede eliminar un proveedor sin registros relacionados. Si tiene órdenes de compra, la eliminación se bloqueará.':'Se actualizará el proveedor '+row.name+'.';
    $('sp-confirm-form').querySelector('.sp-error').textContent = '';
    $('sp-state-reason-box').hidden = action !== 'state_proposal';
    $('sp-confirm-form').elements.reason.required = action === 'state_proposal';
    $('sp-confirm-form').elements.reason.value = '';
    if (action === 'state_proposal') { $('sp-confirm-title').textContent = row.is_active ? 'Solicitar desactivación' : 'Solicitar reactivación'; $('sp-confirm-text').textContent = 'El estado vigente se conserva hasta la aprobación del Administrador.'; }
    $('sp-confirm').showModal();
  }
  async function action(button) {
    const {action:kind,id} = button.dataset;
    if (kind==='menu') return actions(Number(id),button);
    closeMenu();
    if (kind==='detail') return detail(Number(id), button);
    if (kind==='edit') return edit(Number(id));
    confirmation(Number(id),kind);
  }
  function selectTab(button) {
    document.querySelectorAll('#sp-detail [role=tab]').forEach(tab=>{tab.setAttribute('aria-selected',String(tab===button));tab.tabIndex=tab===button?0:-1;});
    document.querySelectorAll('#sp-detail [role=tabpanel]').forEach(panel=>{panel.hidden=panel.id!==button.getAttribute('aria-controls');});
    button.focus();
  }
  function containDetailFocus(event) {
    const dialog = $('sp-detail');
    if (!dialog.open || event.key !== 'Tab') return;
    const controls = [...dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex], summary, [contenteditable="true"]')]
      .filter(node => node.tabIndex >= 0 && !node.matches(':disabled, [inert], [inert] *') && node.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (!first) { event.preventDefault(); return; }
    const active = document.activeElement;
    if (!dialog.contains(active) || (event.shiftKey ? active === first : active === last)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    }
  }
  $('sp-detail').addEventListener('close', () => {
    if (detailTrigger?.isConnected) detailTrigger.focus({preventScroll:true});
    detailTrigger = null;
  });
  async function decide(approve) {
    if (deciding || !reviewing || !canDecide(reviewing)) return;
    const reason = $('sp-review-reason');
    reason.required = !approve;
    if (!approve && (!reason.reportValidity() || !reason.value.trim())) return;
    deciding = true;
    $('sp-approve').disabled = $('sp-reject').disabled = true;
    try {
      const result = await api(`/api/suppliers/${reviewing.id}/${approve?'approve':'reject'}`, 'POST', approve?{}:{reason:reason.value.trim()});
      const url = new URL(location.href);
      url.searchParams.set('detail', result.id);
      location.assign(url);
    } catch (error) {
      $('sp-review-error').textContent = error.message;
    } finally {
      deciding = false;
      $('sp-approve').disabled = $('sp-reject').disabled = false;
    }
  }
  if (!context.reference) {
    $('sp-approve').addEventListener('click', () => decide(true));
    $('sp-reject').addEventListener('click', () => decide(false));
  }
  document.addEventListener('click',event=>{
    const close = event.target.closest('[data-close]');
    if (close) return close.closest('dialog').close();
    const open = event.target.closest('[data-open]');
    if (open) return $(open.dataset.open).showModal();
    const button = event.target.closest('[data-action]');
    if (button) { action(button).catch(error=>toast(error.message)); return; }
    if (!event.target.closest('#sp-menu')) closeMenu();
    if (event.target.closest('#sp-new')) edit().catch(error=>toast(error.message));
    const tab = event.target.closest('[data-tab]');
    if (tab) selectTab(tab);
  });
  document.addEventListener('keydown',event=>{
    containDetailFocus(event);
    if (event.key==='Escape' && !menu.hidden) {closeMenu();menuTrigger?.focus();}
    const tabList = event.target.closest('[role=tablist]');
    const list = tabList ? [...tabList.querySelectorAll('[role=tab]')] : !menu.hidden ? [...menu.querySelectorAll('[role=menuitem]')] : [];
    if (list.length && ['ArrowDown','ArrowUp','ArrowLeft','ArrowRight'].includes(event.key)) {
      event.preventDefault(); const delta=['ArrowDown','ArrowRight'].includes(event.key)?1:-1;
      const next=list[(list.indexOf(document.activeElement)+delta+list.length)%list.length];
      if (tabList) selectTab(next); else next.focus();
    }
  });
  window.addEventListener('resize',closeMenu);document.addEventListener('scroll',closeMenu,true);
  form.elements.notes.addEventListener('input',()=>{$('sp-notes-count').textContent=form.elements.notes.value.length+'/300';});
  async function submit(formNode, task) {
    const button=formNode.querySelector('[type=submit]') || formNode.querySelector('footer button:last-child');
    const error=formNode.querySelector('.sp-error');error.textContent='';button.disabled=true;
    try { await task(); } catch(failure) {error.textContent=failure.message;} finally {button.disabled=false;}
  }
  form.addEventListener('submit',event=>{
    event.preventDefault();submit(form,async()=>{
      const data=Object.fromEntries(new FormData(form));data.is_active=data.is_active==='true';
      const saved=await api('/api/suppliers'+(editing?'/'+editing:''),editing?'PATCH':'POST',data);
      const url=new URL(location.href);url.searchParams.set('detail',saved.id);location.assign(url);
    });
  });
  $('sp-confirm-form').addEventListener('submit',event=>{
    event.preventDefault();submit(event.target,async()=>{
      const row=context.items.find(item=>item.id===pending.id);
      const state=pending.action==='state_proposal';
      await api('/api/suppliers/'+pending.id+(state?'/status-proposals':pending.action==='delete'?'':'/'+pending.action),pending.action==='delete'?'DELETE':'POST',state?{is_active:!row.is_active,reason:event.target.elements.reason.value}:{});
      location.reload();
    });
  });
  $('sp-export-form').addEventListener('submit',event=>{
    event.preventDefault();submit(event.target,async()=>{
      const params=new URLSearchParams(location.search);params.delete('page');params.delete('detail');params.set('format',new FormData(event.target).get('format'));
      for(const input of event.target.querySelectorAll('[type=checkbox]')) params.set(input.name,input.checked?'1':'0');
      const response=await fetch('/api/suppliers/export?'+params, {credentials:'same-origin'});
      if(!response.ok) {const result=await response.json();throw new Error(result.error||'No se pudo exportar.');}
      const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download='proveedores.'+params.get('format');link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('sp-export-dialog').close();
    });
  });
  $('sp-filters').addEventListener('change',event=>{if(event.target.tagName==='SELECT') event.currentTarget.requestSubmit();});
  const advanced = $('sp-filters-dialog').querySelector('form');
  function requireCurrency() {
    const controls = advanced.elements;
    if (!controls.currency) return;
    controls.currency.required = Boolean(controls.min_total.value || controls.max_total.value || controls.sort.value.startsWith('total'));
  }
  advanced.addEventListener('input', requireCurrency);
  advanced.addEventListener('change', requireCurrency);
  requireCurrency();
  $('sp-filters').querySelector('[name=search]').addEventListener('search',()=>$('sp-filters').requestSubmit());
  $('sp-per-page').addEventListener('change',event=>{const url=new URL(location.href);url.searchParams.set('per_page',event.target.value);url.searchParams.delete('page');location.assign(url);});
  const initial=new URL(location.href).searchParams.get('detail');
  if(initial && /^\d+$/.test(initial)) detail(Number(initial)).catch(error=>toast(error.message));
})();
