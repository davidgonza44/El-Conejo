/* Explicit comparison/decision workflow; server remains authoritative. */
(() => {
  'use strict';
  const context = JSON.parse(document.getElementById('rc-context').textContent);
  const node = id => document.getElementById('rc-' + id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {preview:'Previsualización', confirmed:'Confirmada', discarded:'Descartada', invalid:'Inválida', unmatched:'Sin identificar', match:'Coincidencia', discrepancy:'Discrepancia', pending:'Pendiente', reviewed:'Revisada', resolved:'Resuelta', acknowledge:'Sin ajuste', adjustment:'Ajuste de inventario'};
  const CUT_PAGE_SIZE = 5;
  let cutPage = 1, cutPages = 1, cutRequest = 0;
  const simulated = run => run.source_type === 'simulation' || run.cut?.source.startsWith('demo_sim:');
  let current, page = 1, activeLine, resolving, detailRequest = 0, snapshotCut, snapshotPage = 1, selectedCut = null;
  const snapshotPageSize = 5;
  const lineQuery = (number = current?.page || 1) => '?' + new URLSearchParams({page:number,
    per_page:node('line-size').value, result:node('line-result').value, review_status:node('line-review').value});
  function resetLineFilters() {
    node('line-result').value = node('line-review').value = 'all';
  }
  const localTime = value => value ? new Date(value + 'Z').toLocaleString('es-VE', {timeZone:'America/Caracas', dateStyle:'short', timeStyle:'medium'}) : '—';
  // Same Caracas instant as localTime, split into date and time lines for the table.
  const localWhen = value => {
    const instant = new Date(value + 'Z'), zone = {timeZone:'America/Caracas'};
    return `<span class="ds-when"><strong>${escape(instant.toLocaleDateString('es-VE', {...zone, dateStyle:'short'}))}</strong><span>${escape(instant.toLocaleTimeString('es-VE', {...zone, timeStyle:'medium'}))}</span></span>`;
  };
  const missing = (text, hint) => `<span class="ds-na" title="${hint}">${text}</span><span class="ds-sr-only">${hint}</span>`;
  const cutState = cut => cut.run?.status === 'preview' ? ['CSV validado · pendiente de conciliación', 'info'] : cut.snapshot_status === 'unavailable' ? ['No comparable', 'neutral'] : cut.status === 'failed' ? ['Fallido', 'danger'] : cut.status === 'completed' ? ['Completado', 'success'] : cut.snapshot_status === 'pending' ? ['Pendiente de captura', 'neutral'] : ['Pendiente de archivo', 'warning'];
  const cutStatus = cut => cutState(cut)[0];
  function markCut(id) {
    selectedCut = id;
    node('cuts').querySelectorAll('tr[data-cut-row]').forEach(row => {
      const selected = Number(row.dataset.cutRow) === id;
      row.classList.toggle('is-selected', selected);
      if (selected) row.setAttribute('aria-current', 'true'); else row.removeAttribute('aria-current');
    });
  }

  async function api(path, method = 'GET', data) {
    const multipart = data instanceof FormData;
    const response = await fetch('/api/reconciliation' + path, {
      method, credentials:'same-origin', headers:multipart ? {Accept:'application/json', 'X-CSRFToken':context.csrfToken} : {'Content-Type':'application/json', Accept:'application/json', 'X-CSRFToken':context.csrfToken},
      body:data === undefined ? undefined : multipart ? data : JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'No se pudo completar la operación.');
    return result;
  }
  const message = text => { node('message').textContent = text; };
  async function history() {
    const result = await api(`?page=${page}&source=${node('source').value}&result=${node('result').value}`);
    node('history').innerHTML = result.items.map(run => `<tr><td>${escape(run.number)}</td><td>${simulated(run) ? '<span class="rc-badge rc-warning">SIMULACIÓN</span>' : run.source_type === 'automatic' ? 'Automática' : run.source_type === 'manual' ? 'Manual' : 'Origen no determinado'}</td><td>${escape(localTime(run.cutoff_at))}</td><td>${labels[run.status]}</td><td>${run.match_count + run.discrepancy_count}</td><td>${run.discrepancy_count}</td><td>${run.unmatched_count}</td><td><button class="po-btn" data-detail="${run.id}" aria-label="Ver ${escape(run.number)}">Ver detalle</button></td></tr>`).join('') || '<tr><td colspan="8">No hay conciliaciones registradas.</td></tr>';
    node('pagination').textContent = `Página ${result.page} de ${result.pages} · ${result.total} conciliaciones`;
    node('prev').disabled = page <= 1;
    node('next').disabled = page >= result.pages;
  }
  // The API keeps its own page size and newest-first order (id desc = capture order). Each view
  // reads only the server page(s) holding its five rows: nothing is skipped, repeated or reordered.
  async function cutSlice(type, number) {
    const query = serverPage => api('/cuts?' + new URLSearchParams({page:serverPage, type}));
    const first = await query(1);
    const pages = Math.max(1, Math.ceil(first.total / CUT_PAGE_SIZE));
    const pageNumber = Math.min(Math.max(number, 1), pages);
    const start = (pageNumber - 1) * CUT_PAGE_SIZE, wanted = Math.min(CUT_PAGE_SIZE, first.total - start);
    const serverSize = first.pages > 1 ? first.items.length : Math.max(first.total, 1);
    const items = [];
    for (let serverPage = Math.floor(start / serverSize) + 1, offset = start % serverSize; items.length < wanted; serverPage++, offset = 0) {
      const result = serverPage === 1 ? first : await query(serverPage);
      if (result.page !== serverPage || !result.items.length) break;
      items.push(...result.items.slice(offset, offset + wanted - items.length));
    }
    return {items, total:first.total, page:pageNumber, pages, start};
  }
  function cutPager() {
    node('cut-prev').disabled = cutPage <= 1;
    node('cut-next').disabled = cutPage >= cutPages;
  }
  async function cuts() {
    const requestId = ++cutRequest;
    node('cut-prev').disabled = node('cut-next').disabled = true;
    let result;
    try { result = await cutSlice(node('cut-type').value, cutPage); }
    catch (error) { if (requestId === cutRequest) cutPager(); throw error; }
    if (requestId !== cutRequest) return;
    cutPage = result.page; cutPages = result.pages;
    node('cuts').innerHTML = result.items.map(cut => {
      const [state, tone] = cutState(cut);
      const selected = cut.id === selectedCut ? ' class="is-selected" aria-current="true"' : '';
      return `<tr data-cut-row="${cut.id}"${selected}><td data-label="ID"><span class="ds-id">#${cut.id}</span></td><td data-label="Captura real · Caracas">${cut.local_snapshot_at ? localWhen(cut.local_snapshot_at) : missing('—', 'Sin captura')}</td><td data-label="Horario de referencia"><strong class="ds-num">${escape(cut.reference_slot)}</strong>${cut.execution_type === 'simulation' ? '<small>Referencia de prueba</small>' : ''}</td><td data-label="Tipo"><span class="rc-badge ${cut.execution_type === 'simulation' ? 'rc-warning' : 'rc-blue'}">${cut.execution_type === 'simulation' ? 'SIMULACIÓN' : 'Real'}</span></td><td data-label="Estado"><span class="ds-chip is-${tone}">${state}</span></td><td data-label="Productos / discrepancias" class="ds-num"><strong>${cut.product_count}</strong> / ${cut.run ? cut.run.discrepancy_count : missing('—', 'Sin conciliación')}</td><td data-label="Procesamiento · Caracas">${cut.processed_at ? localWhen(cut.processed_at) : missing('Sin procesar', 'Sin procesar')}</td><td class="ds-cell-action"><button class="po-btn ds-btn-sm" data-cut="${cut.id}" aria-label="Ver corte ${cut.id}">Ver detalle</button></td></tr>`;
    }).join('') || '<tr><td colspan="8" class="ds-table-state">No hay cortes de este tipo. En DEMO, use «Simular corte» para iniciar una prueba.</td></tr>';
    node('cut-range').textContent = result.total ? `Mostrando ${result.start + 1}–${result.start + result.items.length} de ${result.total} ${result.total === 1 ? 'corte' : 'cortes'}` : 'Sin cortes para mostrar';
    node('cut-pagination').textContent = `Página ${cutPage} de ${cutPages}`;
    cutPager();
  }
  function audit(line) {
    const parts = [labels[line.review_status]];
    if (line.reviewed_at) parts.push(`Revisó: ${line.reviewed_by} · ${line.reviewed_at} UTC · ${line.review_note}`);
    if (line.resolved_at) parts.push(`Resolvió: ${line.resolved_by} · ${line.resolved_at} UTC · ${labels[line.resolution_action]} · ${line.resolution_note}`);
    if (line.stock_movement_id) parts.push(`Movimiento #${line.stock_movement_id}`);
    const request = line.adjustment_request;
    if (request && request.status !== 'approved') parts.push(`Solicitud de ajuste #${request.id}: ${request.status === 'pending' ? 'pendiente de aprobación' : 'rechazada' + (request.review_comment ? ` · ${request.review_comment}` : '')}`);
    return parts.map(escape).join('<br>');
  }
  function lineActions(line) {
    if (!context.canResolve || current.status !== 'confirmed' || line.result !== 'discrepancy' || line.review_status === 'resolved') return '';
    if (line.adjustment_request?.status === 'pending') return '';
    return (line.review_status === 'pending' ? `<button class="po-btn" data-line="${line.id}" data-mode="review">Revisar</button>` : '') + `<button class="po-btn" data-line="${line.id}" data-mode="resolve">Resolver</button>`;
  }
  function render(run) {
    ++detailRequest;
    current = run;
    markCut(run.cut?.id ?? null);
    node('detail').hidden = false;
    node('snapshot').hidden = true;
    node('line-filters').hidden = node('line-nav').hidden = false;
    node('line-result').value = run.result;
    node('line-review').value = run.review_status;
    node('line-size').value = String(run.per_page);
    const start = (run.page - 1) * run.per_page;
    node('line-range').textContent = `Mostrando ${run.total ? start + 1 : 0}–${start + run.items.length} de ${run.total} líneas`;
    node('line-pagination').textContent = `Página ${run.page} de ${run.pages}`;
    node('line-prev').disabled = run.page <= 1;
    node('line-next').disabled = run.page >= run.pages;
    node('detail-title').textContent = `${simulated(run) ? 'SIMULACIÓN · ' : ''}${run.number} · ${labels[run.status]}`;
    const cut = run.cut;
    const snapshot = cut ? `<p>${simulated(run) ? 'SIMULACIÓN · Horario de referencia ' + escape(cut.source.split(':')[1]?.replace(/(\d{2})(\d{2})/, '$1:$2')) + '. Captura con hora real.' : 'Corte operativo.'}</p><p>Fotografía local: ${escape(localTime(cut.local_snapshot_at))} · Desfase: ${escape(cut.snapshot_delay_seconds)} s · Archivo detectado: ${escape(localTime(cut.source_received_at))}</p><p>Comparación contra la fotografía inmutable del corte. No equivale a conteo físico.</p>` : '';
    node('summary').innerHTML = `<p>${run.source_type === 'automatic' ? 'Automática · Procesada' : 'Cargó: ' + escape(run.creator)} · ${escape(localTime(run.imported_at))} · ${escape(run.filename)}</p>${snapshot}<p>Filas: ${run.received_count} · Válidas: ${run.valid_count} · Inválidas: ${run.invalid_count} · Coincidencias: ${run.match_count} · Discrepancias: ${run.discrepancy_count} · Sin identificar: ${run.unmatched_count}</p><details><summary>Identificador técnico</summary><code>SHA-256: ${escape(run.sha256)}</code></details>`;
    node('actions').innerHTML = run.status === 'preview' && context.canCreate ? (cut ? '<button class="po-btn is-primary" data-transition="confirm">Ejecutar conciliación contra el snapshot</button>' : '<button class="po-btn is-primary" data-transition="confirm">Confirmar comparación actual</button><button class="po-btn" data-transition="discard">Descartar</button>') : '';
    if (cut) node('actions').innerHTML += `<button class="po-btn" data-cut="${cut.id}">Ver fotografía original</button>`;
    node('lines').innerHTML = run.items.map(line => `<tr><td>${line.row_number} · ${escape(line.product_code)}</td><td>${escape(line.product_name)}</td><td>${line.external_quantity ?? '—'}</td><td>${line.system_quantity ?? '—'}</td><td>${line.difference ?? '—'}</td><td>${line.result === 'discrepancy' ? (line.difference < 0 ? 'Faltante' : 'Sobrante') : labels[line.result]}<br>${escape(line.error)}</td><td>${audit(line)}</td><td>${lineActions(line)}</td></tr>`).join('') || '<tr><td colspan="8">No hay líneas que coincidan con estos filtros.</td></tr>';
  }
  async function show(id, number = 1, reset = true) {
    if (reset) resetLineFilters();
    const requestId = ++detailRequest;
    node('line-prev').disabled = node('line-next').disabled = true;
    try {
      const run = await api('/' + id + lineQuery(number));
      if (requestId !== detailRequest) return;
      render(run);
      node('detail-title').focus();
    } catch (error) {
      if (requestId === detailRequest && current) render(current);
      throw error;
    }
  }
  function renderSnapshot(cut, number = 1) {
    const snapshot = cut.snapshot;
    const pages = Math.max(1, Math.ceil(snapshot.total / snapshotPageSize));
    snapshotCut = cut; snapshotPage = Math.max(1, Math.min(number, pages));
    const start = (snapshotPage - 1) * snapshotPageSize;
    const rows = snapshot.items.slice(start, start + snapshotPageSize);
    node('snapshot').hidden = false;
    node('snapshot-summary').textContent = `Corte #${cut.id} · ${localTime(cut.local_snapshot_at)} · ${snapshot.total} productos. Valores históricos; los movimientos posteriores no reemplazan esta fotografía.`;
    node('snapshot-lines').innerHTML = rows.map(row => `<tr><td>${row.product_id}</td><td>${escape(row.product_code)}</td><td>${row.quantity}</td></tr>`).join('') || '<tr><td colspan="3">No hay fotografía disponible.</td></tr>';
    node('snapshot-range').textContent = `Mostrando ${snapshot.total ? start + 1 : 0}–${start + rows.length} de ${snapshot.total} productos`;
    node('snapshot-pagination').textContent = `Página ${snapshotPage} de ${pages}`;
    node('snapshot-prev').disabled = snapshotPage <= 1;
    node('snapshot-next').disabled = snapshotPage >= pages;
  }
  async function loadSnapshot(id, requestId) {
    // The API keeps its bounded pages; load each once before paging locally.
    const cut = await api(`/cuts/${id}?snapshot_page=1`);
    for (let number = 2; number <= cut.snapshot.pages; number++) {
      if (requestId !== detailRequest) return null;
      const result = await api(`/cuts/${id}?snapshot_page=${number}`);
      cut.snapshot.items.push(...result.snapshot.items);
    }
    return requestId === detailRequest ? cut : null;
  }
  async function showCut(id) {
    const requestId = ++detailRequest;
    const cut = await loadSnapshot(id, requestId);
    if (!cut) return;
    current = null;
    markCut(cut.id);
    node('line-filters').hidden = node('line-nav').hidden = true;
    node('detail').hidden = false;
    const simulation = cut.execution_type === 'simulation';
    node('detail-title').textContent = `${simulation ? 'SIMULACIÓN' : 'Corte operativo'} #${cut.id} · ${cutStatus(cut)}`;
    node('summary').innerHTML = `<dl class="rc-facts"><div><dt>Captura real · Caracas</dt><dd>${escape(localTime(cut.local_snapshot_at))}</dd></div><div><dt>Horario de referencia</dt><dd>${escape(cut.reference_slot)} ${simulation ? '· prueba, sin fecha histórica' : ''}</dd></div><div><dt>Productos incluidos</dt><dd>${cut.product_count}</dd></div><div><dt>Responsable</dt><dd>${escape(cut.created_by || 'Planificador operativo')}</dd></div><div><dt>CSV asociado</dt><dd>${escape(cut.run?.filename || 'Pendiente de carga')}</dd></div><div><dt>Archivo recibido</dt><dd>${escape(localTime(cut.source_received_at))}</dd></div><div><dt>Procesamiento</dt><dd>${escape(localTime(cut.processed_at))}</dd></div><div><dt>Intentos de procesamiento</dt><dd>${cut.attempt_count}</dd></div></dl><p class="rc-help">${escape(cut.failure_reason || (cut.run_id ? 'CSV validado contra la fotografía original. Consulte la conciliación para continuar.' : (simulation ? 'En espera de un CSV de demostraci\u00f3n.' : 'En espera del CSV externo de esta fecha y horario.')))}</p><details><summary>Trazabilidad técnica</summary><p>Identificador: ${escape(cut.source)} · ${escape(cut.scheduled_for)} UTC</p><p>SHA-256: ${escape(cut.last_source_hash || 'Sin archivo')}<br>Archivado: ${escape(localTime(cut.archived_at))}</p></details>`;
    let actions = cut.status === 'failed' && cut.snapshot_status === 'captured' && context.canCreate ? `<button class="po-btn" data-retry="${cut.id}">Reintentar archivo</button>` : '';
    if (simulation && cut.snapshot_status === 'captured') actions += `<a class="po-btn" href="/api/reconciliation/cuts/${cut.id}/csv">Descargar CSV de ejemplo · DEMO</a>`;
    if (simulation && context.canSimulate) actions += `<a class="po-btn" href="/api/reconciliation/cuts/${cut.id}/csv?variant=scenario">Descargar escenario sintético</a>`;
    if (cut.run_id) actions += `<button class="po-btn is-primary" data-detail="${cut.run_id}">Ver conciliación</button>${cut.archived_at ? `<a class="po-btn" href="/api/reconciliation/cuts/${cut.id}/csv?variant=uploaded">Descargar CSV asociado</a>` : ''}`;
    node('actions').innerHTML = actions;
    if (!cut.run_id && cut.snapshot_status === 'captured' && cut.status === 'waiting_source' && context.canCreate) node('actions').innerHTML += `<form id="rc-cut-upload" data-cut-id="${cut.id}" class="rc-csv-form"><label>CSV del corte<input name="file" type="file" accept=".csv,text/csv" required></label><button class="po-btn is-primary" type="submit">Validar CSV</button><p class="rc-help">UTF-8 · punto y coma · product_code;quantity. ${simulation ? 'Datos de demostración; no son mediciones externas.' : 'Archivo requerido: <strong>' + escape(cut.expected_filename) + '</strong>. Debe ser la exportación externa de esa fecha y horario.'} Validar y ejecutar no alteran el inventario.</p></form>`;
    node('lines').innerHTML = `<tr><td colspan="8">${cut.run_id ? 'La comparación está disponible en «Ver conciliación».' : 'En espera del CSV. Todavía no hay una conciliación.'}</td></tr>`;
    renderSnapshot(cut);
    window.history.replaceState(null, '', '/reconciliation?cut=' + id);
    node('detail-title').focus();
  }
  function stockField() {
    const adjusting = resolving && node('resolution').elements.action.value === 'adjustment';
    const line = current && current.items.find(item => item.id === activeLine);
    node('stock-label').hidden = !adjusting;
    // El servidor calcula el objetivo al aprobar: stock vigente + diferencia, así no se pierden las ventas posteriores.
    node('stock-label').textContent = line ? `Se solicitará un ajuste de ${line.difference > 0 ? '+' : ''}${line.difference} unidades sobre el stock vigente. Otro administrador debe aprobarlo; hasta entonces las existencias no cambian.` : '';
    node('resolution').elements.note.maxLength = adjusting ? 255 : 1000;
  }
  function openLine(id, mode) {
    activeLine = id; resolving = mode === 'resolve';
    const line = current.items.find(item => item.id === id);
    node('resolution').reset(); node('error').textContent = '';
    node('dialog-title').textContent = resolving ? 'Resolver discrepancia' : 'Marcar como revisada';
    node('line-label').textContent = `${line.product_code} · Externo ${line.external_quantity} · Sistema ${line.system_quantity} ${current.cut ? 'en la fotografía local del corte' : 'al confirmar'}`;
    const adjustment = node('resolution').elements.action.querySelector('[value=adjustment]');
    if (adjustment) adjustment.disabled = simulated(current);
    node('action-label').hidden = !resolving; stockField(); node('dialog').showModal();
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.hasAttribute('data-close')) { node('dialog').close(); return; }
    if (button.id === 'rc-snapshot-prev' || button.id === 'rc-snapshot-next') {
      if (snapshotCut) renderSnapshot(snapshotCut, snapshotPage + (button.id === 'rc-snapshot-prev' ? -1 : 1));
      return;
    }
    try {
      if (button.dataset.detail) await show(Number(button.dataset.detail));
      if (button.dataset.cut) await showCut(Number(button.dataset.cut));
      if (button.dataset.retry) {
        button.disabled = true;
        const cut = await api(`/cuts/${button.dataset.retry}/retry`, 'POST', {});
        if (cut.run_id) await show(cut.run_id); else await showCut(cut.id);
        await cuts(); await history();
      }
      if (button.dataset.line) openLine(Number(button.dataset.line), button.dataset.mode);
      if (button.dataset.transition) {
        button.disabled = true;
        render(await api(`/${current.id}/${button.dataset.transition}${lineQuery()}`, 'POST', {}));
        message('Conciliación ejecutada. Las diferencias quedan pendientes de revisión; las existencias no se modificaron.'); await history(); await cuts();
      }
    } catch (error) { message(error.message); } finally { button.disabled = false; }
  });
  node('upload')?.addEventListener('submit', async event => {
    event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true;
    try {
      const data = new FormData(event.target);
      if (data.get('file').size > context.maxBytes) throw new Error(`El archivo supera ${context.maxBytes / 1048576} MiB.`);
      data.set('cutoff_at', data.get('cutoff_at') + ':00-04:00');
      message('Validando archivo…'); render(await api('', 'POST', data)); await history();
      message('Previsualización lista. Confirmar recalculará la comparación contra el stock actual.'); node('detail-title').focus();
    } catch (error) { message(error.message); } finally { button.disabled = false; }
  });
  node('resolution').elements.action.addEventListener('change', stockField);
  node('resolution').addEventListener('submit', async event => {
    event.preventDefault(); const form = event.target, button = form.querySelector('[type=submit]'); button.disabled = true;
    try {
      const values = Object.fromEntries(new FormData(form)), data = {note:values.note};
      if (resolving) data.action = values.action;
      render(await api(`/${current.id}/lines/${activeLine}/${resolving ? 'resolve' : 'review'}${lineQuery()}`, 'POST', data));
      node('dialog').close();
      message(resolving && values.action === 'adjustment' ? 'Solicitud de ajuste enviada. Las existencias cambian cuando otro administrador la apruebe.' : 'Revisión guardada con su trazabilidad.');
      await history();
    } catch (error) { node('error').textContent = error.message; } finally { button.disabled = false; }
  });
  node('prev').addEventListener('click', () => { page--; history().catch(error => message(error.message)); });
  for (const [id, offset] of [['line-prev', -1], ['line-next', 1]]) {
    node(id).addEventListener('click', () => show(current.id, current.page + offset, false).catch(error => message(error.message)));
  }
  for (const id of ['line-result', 'line-review', 'line-size']) {
    node(id).addEventListener('change', () => { if (current) show(current.id, 1, false).catch(error => message(error.message)); });
  }
  node('next').addEventListener('click', () => { page++; history().catch(error => message(error.message)); });
  for (const id of ['source', 'result']) node(id).addEventListener('change', () => { page = 1; history().catch(error => message(error.message)); });
  function initializeCuts() {
    for (const [id, offset] of [['cut-prev', -1], ['cut-next', 1]]) node(id).addEventListener('click', () => { cutPage += offset; cuts().catch(error => message(error.message)); });
    node('cut-type').addEventListener('change', () => { cutPage = 1; cuts().catch(error => message(error.message)); });
    node('simulate')?.addEventListener('click', () => { node('sim-error').textContent = ''; node('sim-dialog').showModal(); });
    document.querySelectorAll('[data-sim-close]').forEach(button => button.addEventListener('click', () => node('sim-dialog').close()));
    node('sim-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = event.target, button = form.querySelector('[type=submit]');
      if (button.disabled) return;
      button.disabled = true; form.elements.slot.disabled = true; node('sim-error').textContent = '';
      const saved = sessionStorage.getItem('rc-pending-simulation');
      const slot = form.elements.slot.value;
      const pending = saved ? JSON.parse(saved) : {slot, request_key:crypto.randomUUID().replaceAll('-', '')};
      if (pending.slot !== slot) { node('sim-error').textContent = 'Reintente el horario de la solicitud pendiente: ' + pending.slot + '.'; button.disabled = false; form.elements.slot.disabled = false; return; }
      sessionStorage.setItem('rc-pending-simulation', JSON.stringify(pending));
      try {
        const cut = await api('/cuts/simulate', 'POST', pending);
        sessionStorage.removeItem('rc-pending-simulation');
        node('sim-dialog').close(); cutPage = 1; node('cut-type').value = 'simulation';
        await cuts(); await showCut(cut.id);
        message(`SIMULACIÓN #${cut.id} creada con hora real. En espera del CSV; stock intacto.`);
      } catch (error) { node('sim-error').textContent = error.message; }
      finally { button.disabled = false; form.elements.slot.disabled = false; }
    });
    document.addEventListener('submit', async event => {
      if (event.target.id !== 'rc-cut-upload') return;
      event.preventDefault();
      const form = event.target, button = form.querySelector('[type=submit]');
      if (button.disabled) return;
      button.disabled = true;
      try {
        const data = new FormData(form);
        if (data.get('file').size > context.maxBytes) throw new Error('El CSV supera el límite permitido.');
        const run = await api(`/cuts/${form.dataset.cutId}/upload`, 'POST', data);
        resetLineFilters(); render(run); await cuts(); await history();
        message('CSV validado contra el snapshot. Pulse «Ejecutar conciliación contra el snapshot» para confirmar.');
        node('detail-title').focus();
      } catch (error) { message(error.message); }
      finally { button.disabled = false; }
    });
  }
  initializeCuts();
  if (context.available) {
    history().catch(error => { node('history').innerHTML = '<tr><td colspan="8" class="ds-table-state is-error">No se pudo cargar el historial.</td></tr>'; message(error.message); });
    cuts().catch(error => { node('cuts').innerHTML = '<tr><td colspan="8" class="ds-table-state is-error">No se pudieron cargar los cortes.</td></tr>'; message(error.message); });
  } else node('history').innerHTML = node('cuts').innerHTML = '<tr><td colspan="8" class="ds-table-state">Pendiente de migración.</td></tr>';
  if (new URL(location.href).searchParams.get('new') === '1') node('upload')?.elements.file.focus();
  const initialDetail=new URL(location.href).searchParams.get('detail');
  if(context.available && /^[1-9][0-9]{0,9}$/.test(initialDetail || '') && Number(initialDetail)<=2147483647) {
    show(Number(initialDetail)).catch(error=>message(error.message));
  }
  const initialCut = new URL(location.href).searchParams.get('cut');
  if (context.available && /^[1-9][0-9]{0,9}$/.test(initialCut || '') && Number(initialCut) <= 2147483647) {
    showCut(Number(initialCut)).catch(error => message(error.message));
  }
})();
