/* Approved movement layers, populated only from live APIs. */
(() => {
    'use strict';
    const live=window.MovementsLive;
    if(!live || document.documentElement.dataset.referenceMode!=='0') return;
    const {api,node,date,signed,badge,product,root}=live;
    const drawer=document.getElementById('mv-drawer'), overlay=document.getElementById('mv-overlay');
    const content=document.getElementById('mv-content'), shell=document.getElementById('ix-shell');
    const status=document.getElementById('mv-status');
    [drawer,overlay,status].forEach(element=>document.body.append(element));
    let trigger, previousOverflow, previousInert, generation=0;
    function announce(message) { status.textContent=message;status.hidden=false; }
    function close() {
        generation++;
        drawer.hidden=true;overlay.hidden=true;shell.inert=previousInert;
        document.body.style.overflow=previousOverflow;
        trigger?.focus({preventScroll:true});
    }
    function open(state,title,subtitle,button) {
        document.dispatchEvent(new Event('mv:close-inline'));
        closeMenu();
        generation++;
        if(drawer.hidden){previousOverflow=document.body.style.overflow;previousInert=shell.inert;}
        trigger=button || trigger;drawer.dataset.state=state;overlay.dataset.state=state;
        document.getElementById('mv-title').textContent=title;
        document.getElementById('mv-subtitle').textContent=subtitle;
        shell.inert=true;document.body.style.overflow='hidden';
        content.onscroll=null;content.replaceChildren();drawer.hidden=false;overlay.hidden=false;
        document.getElementById('mv-title').focus({preventScroll:true});
    }
    function fieldList(values) {
        const dl=node('dl');values.forEach(([label,value])=>{
            const row=node('div'), description=node('dd');
            if(value && typeof value==='object') description.append(value);
            else description.textContent=value ?? 'No registrado';
            row.append(node('dt','',label),description);dl.append(row);
        });return dl;
    }
    function footer(correction) {
        const foot=node('footer','mv-footer');const cancel=node('button','mv-button','Cerrar');cancel.type='button';cancel.addEventListener('click',close);foot.append(cancel);
        if(correction && root.dataset.canRequestAdjustment==='1'){
            const button=node('button','mv-button mv-correction','Crear corrección');button.type='button';button.addEventListener('click',()=>newMovement(trigger,correction));foot.append(button);
        }return foot;
    }
    function details(row) {
        const wrapper=node('div','mv-detail');
        const summary=node('section','mv-detail-summary mv-card');const title=node('div');
        const icon=node('span','mv-detail-icon');
        const glyph=document.querySelectorAll('.iv-kpis .ix-kpi-icon')[['entrada','salida','ajuste'].indexOf(row.movement_type)]?.querySelector('svg');
        if(glyph)icon.append(glyph.cloneNode(true));summary.append(icon);
        title.append(badge(row),node('h3','',row.movement_number),node('p','',date(row.created_at)+' '+date(row.created_at,true)));summary.append(title);
        const card=node('section','mv-card mv-product-card');
        const info=node('div','mv-product-info');const productView=product(row);productView.className='mv-product-info';
        const copy=productView.querySelector('.px-product-text');copy.append(node('p','',row.product_category || 'Categoría no disponible'),node('p','','Subcategoría: no disponible'),node('p','','Código de barras: no disponible'));
        info.append(productView);card.append(node('h3','','Información del producto'),info);
        const main=node('section','mv-card mv-detail-main');main.append(node('h3','','Detalles del movimiento'));
        const columns=node('div','mv-detail-columns');
        columns.append(fieldList([['Tipo de movimiento',live.labels[row.movement_type] || 'Tipo no soportado'],['Referencia',live.referenceNode(row,true)],['Origen',row.source_label],['Referencia externa',row.external_reference || 'No registrada'],['Fecha y hora',date(row.created_at)+' '+date(row.created_at,true)],['Responsable',row.user]]),fieldList([['Cantidad',signed(row)+' '+(row.product_unit || '')],['Stock anterior',row.previous_stock],['Stock actual',row.new_stock],['Estado',row.display_status || '—'],['Motivo',row.reason || 'No especificado']]));
        main.append(columns);
        const history=node('div','mv-history');history.append(node('h3','','Historial del movimiento'),node('p','',`Movimiento registrado el ${date(row.created_at)}, ${date(row.created_at,true)}. Responsable: ${row.user || 'No registrado'}.`));main.append(history);
        wrapper.append(summary,card,main,node('p','mv-info','Este movimiento ya fue aplicado al inventario y no puede editarse. Una corrección crea un nuevo ajuste.'));
        content.replaceChildren(wrapper,footer(row));
    }
    async function detail(id,button) {
        open('detail','Detalle del movimiento','Información del movimiento aplicado',button);
        const token=generation;content.append(node('p','mv-fields','Cargando detalle…'));
        try {const row=await api('movements/'+id);if(token===generation) details(row);}
        catch(error){if(token===generation) content.replaceChildren(node('p','mv-info',error.message),footer());}
    }
    async function productRequest(query) {
        const response=await fetch('/api/products?'+new URLSearchParams({search:query,page:1,per_page:50}),{credentials:'same-origin'});
        if(!response.ok) throw new Error('No se pudieron cargar los productos.');
        return response.json();
    }
    async function setupProducts(form,correction) {
        const control=form.querySelector('#mv-product');
        const search=node('input');search.type='search';search.placeholder='Buscar producto por nombre o código…';search.setAttribute('aria-label','Buscar producto');
        control.closest('label').before(search);
        let products=[],sequence=0,timer;
        function showStock(){const selected=products.find(item=>String(item.id)===control.value);form.querySelector('#mv-stock').value=selected?.current_stock ?? '';}
        async function load() {
            const id=++sequence;control.disabled=true;
            try {
                const data=await productRequest(search.value);
                if(id!==sequence || !form.isConnected) return;
                products=data.items.filter(item=>item.is_active);
                control.replaceChildren(new Option('Selecciona un producto',''),...products.map(item=>new Option(`${item.name} (${item.code})`,item.id)));
                if(correction && !search.value){
                    let selected=products.find(item=>item.id===correction.product_id);
                    if(!selected){
                        const response=await fetch('/api/products/'+correction.product_id,{credentials:'same-origin'});
                        if(!response.ok) throw new Error('Producto no disponible.');
                        selected=await response.json();products.push(selected);control.append(new Option(`${selected.name} (${selected.code})`,selected.id));
                    }
                    control.value=String(correction.product_id);
                }
                showStock();
            } catch(error){form.querySelector('[data-live-error]').textContent=error.message;}
            finally{if(id===sequence)control.disabled=false;}
        }
        control.addEventListener('change',showStock);
        search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(load,250);});
        await load();
    }
    function setupForm(form,correction) {
        form.querySelectorAll('[data-edit-only]').forEach(el=>{el.hidden=true;el.querySelectorAll('input,select').forEach(input=>{input.disabled=true;input.required=false;});});
        const kind=form.querySelector('#mv-kind');
        kind.replaceChildren(new Option('Selecciona un tipo',''),new Option('Entrada','entrada'),new Option('Salida','salida'),...(root.dataset.canRequestAdjustment==='1'?[new Option('Solicitar ajuste','ajuste')]:[]));
        for(const id of ['mv-reference','mv-user','mv-date']){
            const input=form.querySelector('#'+id);input.disabled=true;input.required=false;
            if(input.tagName==='SELECT')input.replaceChildren(new Option(root.dataset.currentUser,''));
            else input.value=id==='mv-date'?'Asignada al guardar':'Se asignará al guardar';
        }
        const quantity=form.querySelector('#mv-quantity'),notes=form.querySelector('#mv-notes');
        quantity.step='1';notes.maxLength=255;
        const text=quantity.closest('label').querySelector('span');
        function updateKind(){const adjustment=kind.value==='ajuste';text.textContent=adjustment?'Stock objetivo *':'Cantidad *';quantity.min=adjustment?'0':'1';notes.required=adjustment;form.querySelector('[data-save-label]').textContent=adjustment?'Solicitar ajuste (requiere aprobación)':'Guardar movimiento';}
        kind.addEventListener('change',updateKind);
        if(correction)kind.value='ajuste';
        updateKind();
        notes.addEventListener('input',()=>{form.querySelector('.mv-counter').textContent=notes.value.length+'/255';});
        const error=node('p','mv-info');error.dataset.liveError='';error.setAttribute('role','alert');form.querySelector('.mv-fields').prepend(error);
        form.querySelector('button[data-mv-close]').addEventListener('click',close);
        form.addEventListener('submit',async event=>{
            event.preventDefault();if(!form.reportValidity())return;
            const value=Number(quantity.value);
            if(!Number.isSafeInteger(value) || value<Number(quantity.min)){error.textContent='Introduce un entero válido.';return;}
            const button=form.querySelector('[type="submit"]');if(button.disabled)return;button.disabled=true;error.textContent='';
            const body={product_id:Number(form.querySelector('#mv-product').value),reason:notes.value.trim()};
            body.external_reference=form.querySelector('#mv-external-reference').value.trim();
            body[kind.value==='ajuste'?'new_stock':'quantity']=value;
            try{
                const result=await api({entrada:'entry',salida:'exit',ajuste:'adjustment'}[kind.value],{method:'POST',body:JSON.stringify(body)});
                close();announce(kind.value==='ajuste'?`Solicitud #${result.id} pendiente de aprobación. El stock permanece intacto.`:`${result.movement.movement_number} registrado. El stock se actualizó correctamente.`);document.dispatchEvent(new Event('mv:refresh'));
            }catch(failure){error.textContent=failure.message;}finally{button.disabled=false;}
        });
        setupProducts(form,correction);
    }
    function newMovement(button,correction) {
        if(root.dataset.canMove!=='1')return;
        open('new',correction?'Nuevo ajuste de corrección':'Nuevo movimiento','Registra una entrada, salida o ajuste',button);
        content.append(document.getElementById('mv-form-template').content.cloneNode(true));setupForm(content.querySelector('form'),correction);
    }
    async function adjustmentRequests(button,page=1) {
        open('detail','Solicitudes de ajuste','El stock cambia únicamente al aprobar una solicitud vigente.',button);
        const token=generation;
        content.append(node('p','mv-fields','Cargando solicitudes…'));
        try {
            const data=await api('adjustments?'+new URLSearchParams({page,per_page:10}));
            if(token!==generation)return;
            const list=node('div','mv-fields');
            if(!data.items.length)list.append(node('p','','No hay solicitudes.'));
            data.items.forEach(row=>list.append(adjustmentCard(row,button,page)));
            const navigation=node('div','mv-footer');
            [[page-1,'Anterior',page>1],[page+1,'Siguiente',page*data.per_page<data.total]].forEach(([target,label,enabled])=>{
                const control=node('button','mv-button',label);control.type='button';control.disabled=!enabled;
                control.addEventListener('click',()=>adjustmentRequests(button,target));navigation.append(control);
            });
            content.replaceChildren(list,navigation,footer());
        }catch(error){if(token===generation)content.replaceChildren(node('p','mv-info',error.message),footer());}
    }
    function adjustmentCard(row,button,page) {
        const card=node('section','mv-card');
        const labels={pending:'Pendiente',approved:'Aprobada',rejected:'Rechazada'};
        card.append(node('h3','',`Solicitud #${row.id} · ${labels[row.status]}`),fieldList([
            ['Producto',row.product],['Stock anterior / objetivo',`${row.previous_stock} / ${row.new_stock}`],
            ['Motivo',row.reason],['Solicitante',`Usuario #${row.requester_user_id}`],['Fecha',date(row.requested_at)],
            ['Revisor',row.reviewer_user_id?`Usuario #${row.reviewer_user_id}`:'Pendiente'],
            ['Comentario',row.review_comment || 'Sin comentario'],['Movimiento aplicado',row.stock_movement_id || 'Ninguno']
        ]));
        if(row.status!=='pending' || root.dataset.canReviewAdjustment!=='1')return card;
        const form=node('form','mv-fields'),label=node('label','mv-field','Comentario de revisión (opcional)');
        const comment=node('textarea');comment.maxLength=255;label.append(comment);
        const error=node('p','mv-info');error.setAttribute('role','alert');
        form.append(label,error);
        ['approve','reject'].forEach(action=>{
            const control=node('button','mv-button',action==='approve'?'Aprobar':'Rechazar');control.type='submit';control.value=action;form.append(control);
        });
        form.addEventListener('submit',async event=>{
            event.preventDefault();const action=event.submitter?.value;if(!action)return;
            const controls=[...form.querySelectorAll('button')];controls.forEach(control=>{control.disabled=true;});
            try {
                await api(`adjustments/${row.id}/${action}`,{method:'POST',body:JSON.stringify({review_comment:comment.value})});
                document.dispatchEvent(new Event('mv:refresh'));await adjustmentRequests(button,page);
            }catch(failure){error.textContent=failure.message;}
            finally{controls.forEach(control=>{control.disabled=false;});}
        });
        card.append(form);return card;
    }
    const requestsButton=document.getElementById('mv-adjustment-requests');
    requestsButton?.addEventListener('click',()=>adjustmentRequests(requestsButton));
    function filters(button) {
        open('filters','Filtros de movimientos','Aplica filtros al historial y su distribución',button);
        content.append(document.getElementById('mv-filters-template').content.cloneNode(true));
        const form=content.querySelector('form');
        const mapping={'mv-filter-kind':'movement_type','mv-filter-user':'user_id','mv-filter-search':'search'};
        for(const [id,key] of Object.entries(mapping)){
            const input=form.querySelector('#'+id);
            if(key==='movement_type')input.replaceChildren(new Option('Todos',''),...Object.entries(live.labels).map(([value,label])=>new Option(label,value)));
            if(key==='user_id')input.replaceChildren(new Option('Todos',''),...live.users.map(user=>new Option(user.name,user.id)));
            input.value=live.state[key];
        }
        form.querySelector('#mv-filter-search').placeholder='Buscar por producto, código o motivo…';
        form.querySelector('#mv-filter-search').closest('label').querySelector('span').textContent='Producto, código o motivo';
        for(const id of ['mv-filter-state','mv-filter-category']){const input=form.querySelector('#'+id);input.disabled=true;input.replaceChildren(new Option('No disponible',''));}
        form.querySelectorAll('.mv-advanced input').forEach(input=>{input.disabled=true;input.title='Filtro no implementado en esta fase.';});
        const dateField=form.querySelector('#mv-filter-date').closest('label');const dateWrap=node('div','mv-grid');
        ['date_from','date_to'].forEach(key=>{const label=node('label','mv-field',key==='date_from'?'Desde':'Hasta');const input=node('input');input.type='date';input.name=key;input.value=live.state[key];label.append(input);dateWrap.append(label);});dateField.replaceWith(dateWrap);
        form.addEventListener('submit',event=>{event.preventDefault();for(const [id,key]of Object.entries(mapping))live.state[key]=form.querySelector('#'+id).value;
            ['date_from','date_to'].forEach(key=>{live.state[key]=form.elements[key].value;});
            document.querySelector('.iv-toolbar-search input').value=live.state.search;
            document.dispatchEvent(new Event('mv:filters-sync'));
            live.state.page=1;close();live.refresh();
        });
    }
    function activityRow(row) {
        const template=document.getElementById('mv-activity-row-'+row.movement_type);
        const item=(template || document.getElementById('mv-activity-row-ajuste')).content.firstElementChild.cloneNode(true);
        if(!template)item.querySelector('.mv-activity-icon').remove();
        item.dataset.movementId=row.id;
        const copy=item.querySelector('.mv-activity-copy');
        const title={entrada:'Entrada de inventario',salida:'Salida de inventario',ajuste:'Ajuste de stock'};
        copy.querySelector('strong').textContent=`${title[row.movement_type] || 'Tipo no soportado'} · ${row.display_reference}`;
        const action=copy.querySelector('strong');
        action.tabIndex=0;action.setAttribute('role','button');
        action.addEventListener('click',()=>detail(row.id,trigger));
        action.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();detail(row.id,trigger);}});
        copy.querySelector('span').textContent=`${row.product || 'Producto no disponible'}${row.product_code ? ' ('+row.product_code+')' : ''}`;
        copy.querySelector('small').textContent=`${date(row.created_at)}, ${date(row.created_at,true)} · ${row.user || 'No registrado'}`;
        const quantity=item.querySelector('.mv-activity-side strong');
        quantity.textContent=signed(row)+' uds.';
        quantity.className='mv-text-'+(row.delta<0?'red':'green');
        return item;
    }
    function activityDates(form,params,reload) {
        const control=form.querySelector('#mv-activity-date');
        control.readOnly=true;control.setAttribute('role','button');control.setAttribute('aria-expanded','false');
        control.setAttribute('aria-controls','mv-activity-date-options');
        const popup=node('div','mv-activity-date-options');popup.id='mv-activity-date-options';popup.hidden=true;
        popup.setAttribute('role','group');popup.setAttribute('aria-label','Seleccionar rango de fechas');
        const fields={};
        for(const [key,label] of [['date_from','Desde'],['date_to','Hasta']]){
            const field=node('label','mv-field',label),input=node('input');
            input.type='date';input.name=key;input.value=params[key];fields[key]=input;field.append(input);popup.append(field);
        }
        const format=value=>new Intl.DateTimeFormat('es-VE',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(value+'T00:00:00Z'));
        const update=()=>{control.value=params.date_from || params.date_to ? `${params.date_from?format(params.date_from):'Inicio'} - ${params.date_to?format(params.date_to):'Actualidad'}`:'Todas las fechas';};
        const hide=()=>{popup.hidden=true;control.setAttribute('aria-expanded','false');};
        const apply=node('button','mv-inline-apply','Aplicar');apply.type='button';
        apply.addEventListener('click',()=>{
            fields.date_to.setCustomValidity(fields.date_from.value && fields.date_to.value && fields.date_from.value>fields.date_to.value?'La fecha final debe ser igual o posterior a la inicial.':'');
            if(!fields.date_from.reportValidity() || !fields.date_to.reportValidity())return;
            for(const key of Object.keys(fields))params[key]=fields[key].value;
            update();hide();control.focus();reload();
        });
        const clear=node('button','mv-inline-cancel','Todas las fechas');clear.type='button';
        clear.addEventListener('click',()=>{for(const key of Object.keys(fields)){params[key]='';fields[key].value='';fields[key].setCustomValidity('');}update();hide();control.focus();reload();});
        for(const field of Object.values(fields))field.addEventListener('input',()=>fields.date_to.setCustomValidity(''));
        popup.append(clear,apply);form.append(popup);
        const toggle=()=>{popup.hidden=!popup.hidden;control.setAttribute('aria-expanded',String(!popup.hidden));if(!popup.hidden)fields.date_from.focus();};
        control.addEventListener('click',toggle);
        control.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();toggle();}});
        popup.addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();hide();control.focus();}});
        form.addEventListener('focusout',event=>{if(!popup.contains(event.relatedTarget) && event.relatedTarget!==control)hide();});
        update();
    }
    function activities(button) {
        open('activities','Todas las actividades','Registro completo de movimientos de inventario',button);
        content.append(document.getElementById('mv-activities-template').content.cloneNode(true));
        content.scrollTop=0;
        const token=generation,form=content.querySelector('form'),list=content.querySelector('.mv-activity-list');
        const count=content.querySelector('[data-activity-count]'),order=content.querySelector('[data-activity-order]');
        const params={search:'',movement_type:'',date_from:'',date_to:'',sort:'-created_at',per_page:25};
        const message=node('p','mv-activity-message');message.setAttribute('role','status');list.after(message);
        count.setAttribute('aria-live','polite');
        let sequence=0,loading=false,nextPage=1,pages=1,shown=0,timer;
        async function load(reset=false) {
            if(token!==generation || (!reset && (loading || nextPage>pages)))return;
            const request=++sequence;loading=true;
            if(reset){nextPage=1;shown=0;list.replaceChildren();content.scrollTop=0;count.textContent='Cargando actividades…';}
            const page=nextPage;
            message.textContent='Cargando actividades…';list.setAttribute('aria-busy','true');
            try {
                const data=await api('movements?'+new URLSearchParams({...params,page}));
                if(token!==generation || request!==sequence)return;
                list.append(...data.items.map(activityRow));shown+=data.count;pages=data.pages;nextPage=data.page+1;
                count.textContent=`Mostrando ${shown} de ${data.total} actividades`;
                message.textContent=shown?'':'No hay actividades con estos filtros.';
            } catch(error){
                if(token===generation && request===sequence){message.textContent=error.message;if(reset)count.textContent='No se pudieron cargar las actividades';}
            } finally {
                if(token===generation && request===sequence){
                    loading=false;list.setAttribute('aria-busy','false');
                    if(!message.textContent && nextPage<=pages && content.scrollHeight<=content.clientHeight)load();
                }
            }
        }
        const kind=form.querySelector('#mv-activity-kind');
        kind.replaceChildren(new Option('Todos',''),...Object.entries(live.labels).map(([value,label])=>new Option(label,value)));
        for(const [id,title] of [
            ['mv-activity-state','No disponible: los movimientos aplicados no tienen un estado persistido.'],
        ]){
            const control=form.querySelector('#'+id);control.disabled=true;control.title=title;
            control.closest('label').title=title;control.replaceChildren(new Option('No disponible',''));
        }
        form.addEventListener('submit',event=>event.preventDefault());
        form.querySelector('#mv-activity-search').addEventListener('input',event=>{
            params.search=event.target.value;clearTimeout(timer);sequence++;loading=true;
            timer=setTimeout(()=>load(true),250);
        });
        kind.addEventListener('change',()=>{params.movement_type=kind.value;clearTimeout(timer);load(true);});
        order.addEventListener('change',()=>{params.sort=order.value==='asc'?'created_at':'-created_at';clearTimeout(timer);load(true);});
        activityDates(form,params,()=>{clearTimeout(timer);load(true);});
        content.onscroll=()=>{if(content.scrollTop+content.clientHeight>=content.scrollHeight-80)load();};
        load(true);
    }
    /* Export menu: same approved geometry as the reference; files come from the filtered live query. */
    const menu=document.getElementById('mv-export');document.body.append(menu);
    const FORMATS={'Exportar a Excel':'xlsx','Exportar a CSV':'csv','Exportar a PDF':'pdf','Imprimir':'print'};
    let exporting=false;
    function exportParams(){
        const {page,per_page,...filters}=live.state;
        return new URLSearchParams(Object.entries(filters).filter(([,value])=>value!==''));
    }
    function closeMenu(restore=false){menu.hidden=true;exportButton.setAttribute('aria-expanded','false');if(restore)exportButton.focus();}
    async function download(format){
        if(exporting)return;exporting=true;
        const params=exportParams();params.set('format',format);
        try{
            const response=await fetch('/api/inventory/movements/export?'+params,{credentials:'same-origin'});
            if(!response.ok){const data=await response.json().catch(()=>({}));throw new Error(data.error || 'No se pudo exportar el historial.');}
            const url=URL.createObjectURL(await response.blob());
            const link=node('a');link.href=url;
            link.download=response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] || `movimientos_inventario.${format}`;
            document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
            announce('Archivo generado. Descarga iniciada.');
        }catch(error){announce(error.message);}
        finally{exporting=false;}
    }
    function exportMovements(name){
        const format=FORMATS[name];closeMenu(true);
        if(!format)return;
        if(format==='print'){window.open('/inventory/print?'+exportParams(),'_blank','noopener');return;}
        download(format);
    }
    const buttons=document.querySelectorAll('.iv-toolbar-actions button');
    const exportButton=buttons[1];
    exportButton.setAttribute('aria-controls','mv-export');exportButton.setAttribute('aria-haspopup','menu');exportButton.setAttribute('aria-expanded','false');
    exportButton.addEventListener('click',()=>{
        document.dispatchEvent(new Event('mv:close-inline'));
        if(!menu.hidden){closeMenu(true);return;}
        const bounds=exportButton.getBoundingClientRect();
        menu.style.left=`${Math.max(8,Math.min(bounds.left,window.innerWidth-238))}px`;
        menu.style.top=`${bounds.bottom+1}px`;
        menu.hidden=false;exportButton.setAttribute('aria-expanded','true');
        menu.querySelector('button').focus();
    });
    menu.addEventListener('click',event=>{const option=event.target.closest('[data-mv-export]');if(option)exportMovements(option.dataset.mvExport);});
    menu.addEventListener('keydown',event=>{
        const options=[...menu.querySelectorAll('button')];const index=options.indexOf(document.activeElement);
        const moves={ArrowDown:(index+1)%options.length,ArrowUp:(index+options.length-1)%options.length,Home:0,End:options.length-1};
        if(event.key in moves){event.preventDefault();options[moves[event.key]].focus();}
        if(event.key==='Tab')closeMenu();
        if(event.key==='Escape'){event.preventDefault();closeMenu(true);}
    });
    document.addEventListener('click',event=>{if(!menu.contains(event.target) && !exportButton.contains(event.target))closeMenu();});
    window.addEventListener('resize',()=>closeMenu());window.addEventListener('scroll',()=>closeMenu(),true);
    buttons[0].addEventListener('click',()=>filters(buttons[0]));buttons[2].addEventListener('click',()=>newMovement(buttons[2]));
    const activity=document.querySelector('[data-mv-open="activities"]');activity.addEventListener('click',()=>activities(activity));
    document.addEventListener('mv:detail',event=>detail(event.detail.id,event.detail.trigger));
    const initialDetail=new URL(location.href).searchParams.get('detail');
    if(/^[1-9][0-9]{0,9}$/.test(initialDetail || '') && Number(initialDetail)<=2147483647) detail(Number(initialDetail));
    drawer.querySelector('.mv-close').addEventListener('click',close);overlay.addEventListener('click',close);
    document.addEventListener('keydown',event=>{
        if(drawer.hidden)return;
        if(event.key==='Escape'){event.preventDefault();close();}
        if(event.key!=='Tab')return;
        const controls=[...drawer.querySelectorAll('button,input,select,textarea,summary,[tabindex="0"]')].filter(el=>!el.disabled && el.getClientRects().length);
        const first=controls[0],last=controls.at(-1);
        if(event.shiftKey && (document.activeElement===first || document.activeElement.id==='mv-title')){event.preventDefault();last?.focus();}
        else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();}
    });
})();
