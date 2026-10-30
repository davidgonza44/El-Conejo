/* Live adapter for the approved Movimientos markup. No fixtures or stock rules. */
(() => {
    'use strict';
    const root = document.querySelector('[data-movements-live]');
    if (!root || document.documentElement.dataset.referenceMode !== '0') return;
    const $ = selector => document.querySelector(selector);
    const labels = {entrada:'Entrada', salida:'Salida', ajuste:'Ajuste'};
    const tones = {entrada:'green', salida:'red', ajuste:'purple'};
    const state = {page:1, per_page:8, sort:'-created_at', search:'', movement_type:'', user_id:'', date_from:'', date_to:''};
    const tbody = $('.iv-history-table tbody');
    const footer = $('.iv-history .px-pagination');
    let requestId = 0;
    let timer;
    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }
    async function api(path, options = {}) {
        const response = await fetch('/api/inventory/' + path, {
            credentials:'same-origin', ...options,
            headers:{'Content-Type':'application/json', ...options.headers},
        });
        let data;
        try { data = await response.json(); } catch { throw new Error('No se pudo procesar la respuesta.'); }
        if (!response.ok) throw new Error(data.error || 'No se pudo completar la operación.');
        return data;
    }
    function date(value, timeOnly = false) {
        if (!value) return 'No registrado';
        // Existing naive timestamps are UTC; never rewrite stored values.
        const instant = new Date(value + (/[zZ]|[+-]\d\d:\d\d$/.test(value) ? '' : 'Z'));
        return new Intl.DateTimeFormat('es-VE', {timeZone:'America/Caracas', ...(timeOnly ? {hour:'2-digit',minute:'2-digit'} : {day:'2-digit',month:'short',year:'numeric'})}).format(instant);
    }
    const signed = row => {
        const quantity=row.movement_type==='entrada'?row.quantity:row.movement_type==='salida'?-row.quantity:row.delta;
        return `${quantity > 0 ? '+' : ''}${quantity}`;
    };
    function badge(row) {
        const value = node('span', 'px-badge is-' + (tones[row.movement_type] || 'gray'), labels[row.movement_type] || 'Tipo no soportado');
        value.title = value.textContent; return value;
    }
    function product(row) {
        const wrapper = node('div', 'px-product iv-product');
        const thumb = node('span', 'ix-thumb px-thumb');
        if (row.product_image) {
            const img = node('img'); img.alt = '';
            const url = new URL(row.product_image, location.origin);
            if (url.origin === location.origin && ['http:', 'https:'].includes(url.protocol)) img.src = url.href;
            img.addEventListener('error', () => img.remove(), {once:true}); thumb.append(img);
        }
        const copy = node('span','px-product-text');
        copy.append(node('span','px-product-name',row.product || 'Producto no disponible'),node('span','px-product-sku',row.product_code || ''));
        for (const value of copy.children) value.title = value.textContent;
        wrapper.append(thumb,copy); return wrapper;
    }
    function referenceNode(row, inDetail=false) {
        const navigable = row.source_url && row.source_url.startsWith('/') && !row.source_url.startsWith('//');
        const link = node(navigable && !(inDetail && row.source_type==='manual') ? 'a' : 'span', 'mv-reference-text', row.display_reference || row.movement_number);
        if(link.tagName==='A') {
            link.href=row.source_url;
            link.setAttribute('aria-label',`${row.display_reference} · ${row.source_label}`);
            if(row.source_type==='manual') link.addEventListener('click',event=>{
                event.preventDefault();document.dispatchEvent(new CustomEvent('mv:detail',{detail:{id:row.id,trigger:link}}));
            });
        }
        return link;
    }
    function renderRow(row) {
        const tr = node('tr'); tr.dataset.movementId = row.id;
        const dateCell = node('td'); dateCell.append(node('span','iv-date',date(row.created_at)),node('span','iv-time',date(row.created_at,true)));
        const typeCell = node('td'); typeCell.append(badge(row));
        const productCell = node('td'); productCell.append(product(row));
        const quantity = node('td','is-center'); quantity.append(node('strong','iv-num is-' + (row.delta < 0 ? 'red':'green'),signed(row)));
        const after = node('td','is-center'); after.append(node('strong','iv-num is-' + (row.delta < 0 ? 'red':'green'),row.new_stock));
        const reference = node('td','px-cell-muted');
        reference.append(referenceNode(row)); reference.title=row.source_label || reference.textContent;
        if(row.source_type==='inconsistent' || row.source_type===null) reference.append(node('small','',row.source_label));
        const status = node('td','px-cell-muted',row.display_status || '—'); status.title=status.textContent;
        const actions = node('td','is-center');
        const button = node('button','iv-icon-btn'); button.type='button'; button.title='Ver detalle'; button.setAttribute('aria-label','Ver detalle');
        button.innerHTML='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>';
        button.addEventListener('click', () => document.dispatchEvent(new CustomEvent('mv:detail',{detail:{id:row.id,trigger:button}})));
        const actionWrap=node('div','iv-row-actions'); actionWrap.append(button); actions.append(actionWrap);
        const responsible=node('td','px-cell-muted',row.user || 'No registrado'); responsible.title=responsible.textContent;
        tr.append(dateCell,typeCell,reference,productCell,quantity,node('td','is-center',row.previous_stock),after,responsible,status,actions);
        return tr;
    }
    function tableMessage(message) {
        const row=node('tr','mv-inline-empty'); const cell=node('td','',message); cell.colSpan=10; row.append(cell); tbody.replaceChildren(row);
    }
    function renderPagination(data) {
        footer.querySelector('.px-pagination-summary').textContent = data.count ? `Mostrando ${(data.page-1)*data.per_page+1} a ${(data.page-1)*data.per_page+data.count} de ${data.total} movimientos` : `Mostrando 0 de ${data.total} movimientos`;
        const pages=footer.querySelector('.px-pages'); pages.replaceChildren();
        const pageButton=(text,page,disabled,label) => {
            const button=node('button','px-pg'+(page===data.page && !label?' is-active':''),text); button.type='button'; button.disabled=disabled;
            if(label) button.setAttribute('aria-label',label); else if(page===data.page) button.setAttribute('aria-current','page');
            button.addEventListener('click',()=>{state.page=page; refresh();}); return button;
        };
        pages.append(pageButton('‹',data.page-1,data.page<=1,'Página anterior'));
        const start=Math.max(1,data.page-2), end=Math.min(data.pages,start+4);
        for(let page=start;page<=end;page++) pages.append(pageButton(String(page),page,false));
        pages.append(pageButton('›',data.page+1,data.page>=data.pages,'Página siguiente'));
    }
    function renderSummary(summary, historyTotal) {
        ['entrada','salida','ajuste'].forEach((kind,index)=>{
            const card=document.querySelectorAll('.iv-kpis .ix-kpi')[index];
            card.querySelector('.ix-kpi-value').textContent=summary.daily[kind];
            card.title=`${summary.commercial_day} · America/Caracas · Cantidad de movimientos`;
        });
        const historyCard=document.querySelectorAll('.iv-kpis .ix-kpi')[3];
        const historyValue=new Intl.NumberFormat('es-VE').format(historyTotal);
        historyCard.querySelector('.ix-kpi-value').textContent=historyValue;
        historyCard.title=`Movimientos registrados: ${historyValue}`;
        const values=['entrada','salida','ajuste'].map(kind=>summary.distribution[kind] || 0);
        const total=values.reduce((sum,value)=>sum+value,0);
        $('.iv-donut .ix-donut-value').textContent=total;
        document.querySelectorAll('.iv-type-legend li').forEach((li,index)=>{
            const value=li.querySelector('.iv-type-value');
            value.replaceChildren(node('strong','',values[index]),document.createTextNode(` (${total?(100*values[index]/total).toFixed(1):'0.0'}%)`));
        });
        const canvas=$('.iv-donut canvas');
        const chart=window.Chart?.getChart(canvas);
        if(chart){chart.data.datasets[0].data=values; chart.update();}
        canvas.setAttribute('aria-label',`Movimientos por tipo: ${values.join(', ')}. Total ${total}`);
        const activity=$('.iv-mov-activity .iv-activity-list'); activity.replaceChildren();
        summary.recent.forEach(row=>{
            const item=node('li','ix-row');
            const icon=node('span','ix-row-icon tone-'+tones[row.movement_type]);
            const source=document.querySelectorAll('.iv-kpis .ix-kpi-icon')[['entrada','salida','ajuste'].indexOf(row.movement_type)];
            if(source) icon.append(source.querySelector('svg').cloneNode(true));
            const copy=node('span','ix-row-text'); copy.append(node('span','ix-row-title',`${labels[row.movement_type] || 'Tipo no soportado'} de inventario · ${row.display_reference}`),node('span','ix-row-desc',row.product));
            const side=node('span','ix-row-side'); side.append(node('span','ix-row-time',date(row.created_at)+' '+date(row.created_at,true)),node('span','ix-row-amount is-'+(row.delta<0?'down':'up'),signed(row)+' '+(row.product_unit || '')));
            item.append(icon,copy,side); activity.append(item);
        });
        if(!summary.recent.length) activity.append(node('li','ix-row','Sin movimientos para los filtros seleccionados.'));
    }
    async function refresh() {
        const id=++requestId;
        tbody.setAttribute('aria-busy','true');
        const params=new URLSearchParams(Object.entries(state).filter(([,v])=>v!==''));
        tableMessage('Cargando movimientos…');
        try {
            const filtered=['search','movement_type','user_id','date_from','date_to'].some(key=>state[key]!=='');
            const [data,summary,history]=await Promise.all([api('movements?'+params),api('movements/summary?'+params),
                filtered ? api('movements?page=1&per_page=1') : Promise.resolve(null)]);
            if(id!==requestId) return;
            if(!data.count && data.total && state.page>data.pages){state.page=data.pages; return refresh();}
            if(data.count) tbody.replaceChildren(...data.items.map(renderRow)); else tableMessage('No hay movimientos con estos filtros.');
            renderPagination(data); renderSummary(summary,history ? history.total : data.total);
        } catch(error) {
            if(id!==requestId) return;
            tableMessage(error.message);
            footer.querySelector('.px-pagination-summary').textContent='No se pudo cargar el historial';
            footer.querySelectorAll('button').forEach(button=>{button.disabled=true;});
            document.querySelectorAll('.iv-kpis .ix-kpi-value,.iv-donut .ix-donut-value').forEach(el=>{el.textContent='—';});
            document.querySelectorAll('.iv-type-value').forEach((el,index)=>{if(index<3)el.replaceChildren(node('strong','','—'));});
            $('.iv-mov-activity .iv-activity-list').replaceChildren(node('li','ix-row','No se pudo cargar la actividad.'));
            const chart=window.Chart?.getChart($('.iv-donut canvas'));
            if(chart){chart.data.datasets[0].data=[];chart.update();}
        } finally {if(id===requestId) tbody.setAttribute('aria-busy','false');}
    }
    function select(box, key, options) {
        const control=node('select'); control.setAttribute('aria-label',key==='user_id'?'Responsable':key==='movement_type'?'Tipo':'Por página');
        options.forEach(([value,label])=>control.append(new Option(label,value)));
        control.value=state[key]; box.replaceChildren(control);
        control.addEventListener('change',()=>{state[key]=control.value;state.page=1;refresh();}); return control;
    }
    const users=[];
    api('movements/users').then(data=>{users.push(...data.items);document.dispatchEvent(new Event('mv:filters-sync'));}).catch(()=>{
        const control=document.querySelectorAll('.iv-mov-filters .px-select-box')[1];control.title='No se pudieron cargar los responsables.';
    });
    select(footer.querySelector('.px-per-page .px-select-box'),'per_page',[[8,'8 por página'],[16,'16 por página'],[32,'32 por página'],[100,'100 por página']]);
    footer.querySelector('.px-pagination-summary').setAttribute('aria-live','polite');
    const search=$('.iv-toolbar-search input');
    search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{state.search=search.value;state.page=1;refresh();},250);});
    $('.iv-mov-filters').addEventListener('reset',()=>{
        Object.assign(state,{page:1,search:'',movement_type:'',user_id:'',date_from:'',date_to:''});search.value='';
        setTimeout(refresh,0);
    });
    const sorts=['created_at','movement_type',null,'product','quantity','previous_stock','new_stock','user'];
    document.querySelectorAll('.iv-history-table th').forEach((th,index)=>{
        if(!sorts[index]) return;
        const button=node('button','mv-sort',th.textContent);button.type='button';button.setAttribute('aria-label','Ordenar por '+th.textContent);
        button.addEventListener('click',()=>{state.sort=state.sort===sorts[index]?'-'+sorts[index]:sorts[index];state.page=1;
            document.querySelectorAll('.iv-history-table th').forEach(el=>el.removeAttribute('aria-sort'));th.setAttribute('aria-sort',state.sort.startsWith('-')?'descending':'ascending');refresh();});th.replaceChildren(button);
    });
    const actions=document.querySelectorAll('.iv-toolbar-actions button');
    actions[2].disabled=root.dataset.canMove!=='1';
    if(actions[2].disabled) actions[2].title='Requiere permiso inventory:move.';
    document.addEventListener('mv:refresh',refresh);
    window.MovementsLive={api,node,date,signed,badge,product,referenceNode,state,refresh,root,labels,users};
    refresh();
})();
