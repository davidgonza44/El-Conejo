/* Product detail presents one server DTO. No predictive or purchasing arithmetic. */
(() => {
    "use strict";
    const dialog = document.getElementById("pd-drawer");
    if (!dialog) return;
    const body = document.getElementById("pd-body"), footer = document.getElementById("pd-footer");
    const tabs = [...dialog.querySelectorAll("[data-pd-tab]")];
    const esc = (v) => String(v ?? "N/D").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const number = (v) => v == null ? "N/D" : Number(v).toLocaleString("es-VE", {maximumFractionDigits: 1});
    const date = (v) => v ? v.split("-").reverse().join("/") : "N/D";
    const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${({box:'<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12m-4.5-7.5 9 5"/>',chart:'<path d="M3 3v18h18M7 14v3m5-7v7m5-12v12"/>',calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18m-13 5h4"/>',cart:'<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2 2h2l3 14h13l2-10H5"/>',info:'<circle cx="12" cy="12" r="10"/><path d="M12 16v-4m0-4h.01"/>',alert:'<path d="m10.3 3.9-8.6 15a2 2 0 0 0 1.7 3h17.2a2 2 0 0 0 1.7-3l-8.6-15a2 2 0 0 0-3.4 0ZM12 9v4m0 4h.01"/>'})[name] || ''}</svg>`;
    let vm = null, opener = null, controller = null, charts = [], active = "summary", scrollBefore = "";
    let stopFollowing = null;
    const computationPending = () => vm && !vm.reference && vm.computation_status && vm.computation_status !== "ready";
    const computationMessage = () => vm.computation_message || (["error", "unavailable", "expired"].includes(vm.computation_status) ? "Pronóstico no disponible. Cierre y vuelva a abrir el detalle en unos momentos para reintentar." : "Calculando pronóstico… El resultado aparecerá automáticamente. El inventario y la demanda observada ya están disponibles.");
    const qty = (v) => v == null ? "N/D" : `${number(v)} ${esc(vm.product.unit)}`;
    const forecastQty = (v) => v == null ? "Sin pronóstico disponible" : `${v > 0 && v < 0.01 ? "< 0,01" : Number(v).toLocaleString("es-VE", {maximumFractionDigits: 2})} ${esc(vm.product.unit)}`;
    const coverText = () => esc(vm.risk.cover_label || (vm.risk.cover != null ? `${number(vm.risk.cover)} días` : vm.periods[1].total === 0 ? "Sin demanda prevista" : "No calculable"));
    const card = (label, value, note = "", glyph = "box") => `<article class="pd-card"><span>${label}</span><strong>${icon(glyph)}<span>${value}</span></strong><small>${note}</small></article>`;
    const link = (href, label) => href ? `<a href="${esc(href)}">${label}</a>` : "";
    const notice = (text, tone = "blue") => `<div class="pd-notice is-${tone}">${icon(tone === "red" ? "alert" : "info")}<div>${text}</div></div>`;
    const empty = (text) => `<p class="pd-empty">${text}</p>`;
    const table = (headers, rows, message = "Sin registros en esta ventana.") => `<div class="pd-table-wrap"><table><thead><tr>${headers.map(h => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headers.length}">${message}</td></tr>`}</tbody></table></div>`;
    const section = (title, content) => `<section class="pd-section"><h3>${title}</h3>${content}</section>`;
    function historyMetrics() {
        const h = vm.history;
        return card("Total vendido", qty(h.total)) + card("Promedio diario", qty(h.average)) + card("Demanda máxima", qty(h.maximum)) + card("Demanda mínima", qty(h.minimum));
    }
    function stockCards(inventory = false) {
        return `<div class="pd-cards">${card("Stock actual", qty(vm.product.stock), vm.product.stock <= 0 ? "Sin stock" : "Disponible")}${card("Stock mínimo", qty(vm.product.minimum), "Umbral mínimo")}${inventory ? card("Stock en camino", qty(vm.pending), "Pendiente de recepción", "cart") : card("Demanda (30 días)", forecastQty(vm.periods[1].total), "Estimada", "chart")}${card("Días de cobertura", coverText(), vm.risk.cover === 0 ? "Riesgo de quiebre" : "Stock actual / demanda diaria", "calendar")}</div>`;
    }
    function historyChart() { return vm.history.values.length ? '<div class="pd-chart"><canvas data-pd-chart="history" role="img" aria-label="Demanda diaria observada de los últimos 90 días"></canvas></div>' : empty("Sin demanda observada disponible en los últimos 90 días."); }
    function forecastChart() { return vm.periods[1].path.length ? '<div class="pd-chart"><canvas data-pd-chart="forecast" role="img" aria-label="Demanda histórica y pronóstico real; marcador Hoy"></canvas></div>' : empty(esc(vm.periods[1].forecast_reason || (computationPending() ? computationMessage() : vm.eligibility))); }
    function purchaseExplanation() {
        if (computationPending()) return computationMessage();
        const r = vm.recommendation;
        if (!r) return "Sin recomendación válida: historial insuficiente o producto inactivo.";
        if (r.suggested_quantity === 0) return vm.pending > 0 ? "No requiere compra: el supply pendiente cubre el punto de reorden." : "No requiere compra: el stock actual cubre el punto de reorden.";
        return "Se recomienda generar una orden de compra. Requiere confirmación; no se genera automáticamente.";
    }
    function summary() {
        const critical = ["critical", "high"].includes(vm.risk.level);
        const alert = critical ? notice(`<strong>Producto en riesgo ${vm.risk.level === "critical" ? "crítico" : "alto"}</strong><br>${vm.product.stock <= 0 ? "No hay stock disponible. " : "Revise la cobertura disponible. "}${purchaseExplanation()}`, "red") : "";
        return section("Estado actual", stockCards() + alert) + section("Demanda histórica (últimos 90 días)", `<div class="pd-summary-chart">${historyChart()}<div class="pd-metric-list">${historyMetrics()}</div></div>`) + section("Pronóstico de demanda", `<div class="pd-summary-chart">${forecastChart()}<div class="pd-metric-list">${card("Demanda estimada (30 días)", forecastQty(vm.periods[1].total))}${card("Tendencia", esc(vm.trend?.label))}${card("Error del modelo (WAPE)", vm.wape == null ? "N/D" : number(vm.wape) + "%")}</div></div>`);
    }
    function demand() {
        const sales = vm.sales.map(r => [date(r.date), esc(r.document), esc(r.customer), qty(r.quantity)]);
        return section("Demanda histórica (últimos 90 días)", historyChart() + `<div class="pd-cards">${historyMetrics()}</div>`) + section("Ventas recientes", table(["Fecha", "Documento", "Cliente", "Cantidad"], sales, vm.permissions.sales ? "Sin ventas completadas recientes." : "No dispone de permiso para consultar ventas.") + link(vm.links.sales, "Ver historial completo →"));
    }
    function experimentalForecast() {
        if (vm.reference || !vm.demand_profile) return "";
        const profile = vm.demand_profile;
        const segments = {regular: "Relativamente regular", intermittent: "Intermitente", extremely_intermittent: "Extremadamente intermitente", unavailable: "No disponible"};
        const classes = {smooth: "Suave", erratic: "Errática", intermittent: "Intermitente", lumpy: "Irregular", unavailable: "No disponible"};
        const states = {available: "Disponible (experimental)", limited: "Limitado", abstain: "Abstención"};
        const observationUnits = {calendar_day: "días calendario", operational_day: "días operativos", calendar_week: "semanas calendario"};
        const observationUnit = Object.hasOwn(observationUnits, profile.observation_unit) ? observationUnits[profile.observation_unit] : "N/D";
        const reasons = {insufficient_history: "Historial insuficiente", insufficient_positive_events: "Pocas observaciones positivas", insufficient_independent_calibration: "Calibración insuficiente", extreme_intermittency: "Intermitencia extrema", inactive_product: "Producto inactivo", invalid_history: "Historial inválido", incomplete_calendar: "Calendario incompleto", unsupported_observation_unit_or_horizon: "Horizonte o unidad no admitidos"};
        const rows = vm.periods.filter(p => [7, 30].includes(p.days) && p.two_part).map(p => {
            const two = p.two_part, interval = p.uncertainty, policy = p.prediction_policy;
            const reason = policy.reason.map(code => reasons[code] || code).map(esc).join("; ");
            return [`${p.days} días`, `${esc(states[policy.status])}<small>${reason}</small>`,
                two.available ? number(two.occurrence_probability * 100) + "%" : "N/D",
                qty(two.positive_demand_expectation), qty(two.unconditional_expected_quantity),
                interval.available ? `${qty(interval.lower_bound)}–${qty(interval.upper_bound)}<small>Cobertura nominal ${number(interval.nominal_coverage * 100)}%; ${number(interval.calibration_count)} bloques</small>` : `No disponible<small>${esc(reasons[interval.unavailable_reason] || interval.unavailable_reason)}</small>`];
        });
        const profileText = `<p>Segmento por ceros: <strong>${esc(segments[profile.zero_rate_segment])}</strong> (${number(profile.zero_rate == null ? null : profile.zero_rate * 100)}%); ${number(profile.positive_count)} observaciones positivas. ADI: ${number(profile.adi)}; CV²: ${number(profile.cv2)}; clasificación ADI/CV²: ${esc(classes[profile.adi_cv2_classification])}. Unidad: ${esc(observationUnit)}. Son dos clasificaciones distintas.</p>`;
        return section("Demanda e incertidumbre experimental", profileText + notice("Estimación empírica separada del pronóstico anterior y del cálculo de compras. Responde si habrá demanda en el horizonte y cuánto se observaría si la hay. Una abstención no significa demanda cero. No se ha demostrado mejora predictiva con datos reales.") + table(["Horizonte", "Política", "P(demanda > 0)", "Cantidad si hay demanda", "Cantidad esperada", "Intervalo experimental"], rows) + notice("Ajuste y calibración usan bloques cronológicos completos sin solapamiento; esto no prueba independencia temporal. La cobertura nominal no es una garantía de cobertura real. No se convierten estos resultados a tasas diarias ni a órdenes de compra."));
    }
    function forecast() {
        const estimates = vm.periods.map(p => `<div><strong>${p.days} días</strong><span><b>${forecastQty(p.total)}</b><small>${p.interval == null ? "Intervalo N/D" : "± " + qty(p.interval)}</small></span></div>`).join("");
        const notes = [computationPending() ? computationMessage() : vm.reference ? "Ejemplo visual determinista, no corresponde a datos LIVE." : vm.forecast_kind === "descriptive" ? "Estimación descriptiva de tasa media, no un modelo validado por backtest." : vm.wape != null ? "Modelo seleccionado automáticamente mediante WAPE en backtest temporal del producto." : "Sin backtest evaluable: modelo baseline cuando la elegibilidad lo permite.",
            "Demanda: históricos confirmados, ventas completadas y notas emitidas. Excluye cotizaciones y ventas canceladas.",
            vm.periods.some(p => p.interval != null) ? (vm.reference ? "Banda ilustrativa exclusiva de referencia; no representa incertidumbre LIVE." : "El ± del pronóstico anterior es una aproximación normal de residuos, no un intervalo calibrado ni una banda diaria validada. No corresponde al intervalo experimental separado.") : "Sin intervalo válido disponible; no se representa una banda de incertidumbre.",
            "La demanda intermitente limita la evaluación.", "Este pronóstico no genera órdenes de compra automáticamente."];
        return experimentalForecast() + section("Pronóstico anterior (modelo o tasa descriptiva)", `<div class="pd-forecast-top">${forecastChart()}<aside class="pd-estimates"><h4>Pronóstico estimado</h4>${estimates}</aside></div><div class="pd-cards">${card("Demanda promedio (30 días)", forecastQty(vm.periods[1].total == null ? null : vm.periods[1].daily_rate), "Por día", "chart")}${card("Tendencia", esc(vm.trend?.label), vm.trend ? `Variación: ${number(vm.trend.variation)}%` : "Sin historial suficiente", "chart")}${card("Error del modelo (WAPE)", vm.wape == null ? "N/D" : number(vm.wape) + "%", vm.wape == null ? "Sin backtest válido" : "Producto seleccionado", "info")}${card("Horizonte de análisis", "30 días", esc(vm.periods[1].model), "calendar")}</div><div class="pd-forecast-bottom"><section><h3>Pronóstico por período</h3>${table(["Período", "Fecha inicio", "Fecha fin", "Demanda estimada", "Intervalo (±)"], vm.periods.map(p => [p.days + " días", date(p.start), date(p.end), forecastQty(p.total), p.interval == null ? "N/D" : "± " + qty(p.interval)]))}${link(vm.links.model, "Ver detalles del modelo →")}</section><aside class="pd-notes"><h3>${icon("info")}Notas del pronóstico</h3><ul>${notes.map(n => `<li>${n}</li>`).join("")}</ul></aside></div>`);
    }
    function inventory() {
        return section("Estado de inventario", stockCards(true)) + section("Movimientos de inventario (últimos 30 días)", table(["Fecha", "Tipo", "Documento", "Entrada", "Salida", "Stock"], vm.movements.map(r => [date(r.date), esc(r.type), esc(r.document), number(r.entry) || "—", number(r.exit) || "—", number(r.stock)]), vm.permissions.inventory ? "Sin movimientos en esta ventana." : "No dispone de permiso para consultar movimientos.") + link(vm.links.movements, "Ver más movimientos →")) + notice("El stock en camino incluye órdenes de compra pendientes y recepciones parciales. Las órdenes canceladas no se consideran.");
    }
    function replenishment() {
        const r = vm.recommendation;
        const list = (rows) => `<dl class="pd-calculation">${rows.map(([k,v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
        return section("Cálculo de reabastecimiento", notice("El punto de reorden (ROP) se calcula como la demanda durante el lead time más el umbral mínimo utilizado actualmente. No es un safety stock estadístico.") + (r ? list([["Demanda promedio diaria", qty(vm.periods[1].daily_rate)], ["Lead time (días)", `${number(r.lead_time_days)} días${r.lead_time_source === "assumed" ? " (asumido)" : " (proporcionado)"}`], ["Demanda durante lead time", qty(r.lead_time_demand)], ["Stock mínimo", qty(r.safety_stock)], ["Punto de reorden (ROP)", qty(r.reorder_point)]]) : empty(esc(computationPending() ? computationMessage() : vm.eligibility)))) + section("Posición actual de inventario", list([["Stock actual", qty(vm.product.stock)], ["Órdenes en camino (pendientes)", qty(vm.pending)], ["Posición total", qty(r?.inventory_position)]])) + `<div class="pd-suggestion">${icon("cart")}<div><strong>Cantidad sugerida</strong><small>Basada en ROP − posición actual</small></div><strong>${qty(r?.suggested_quantity)}</strong></div><p class="pd-explanation">${purchaseExplanation()}</p>` + section("Órdenes de compra en camino", table(["Orden", "Proveedor", "Estado", "Cantidad pendiente", "F. esperada"], vm.orders.map(o => [esc(o.number), esc(o.supplier), `<span class="pd-badge">${esc(o.status_label)}</span>`, qty(o.pending), date(o.expected)]), vm.permissions.purchases ? "Sin órdenes pendientes o parciales." : "No dispone de permiso para consultar órdenes."));
    }
    function drawCharts() {
        if (typeof Chart === "undefined") return;
        body.querySelectorAll("[data-pd-chart]").forEach(canvas => {
            const historical = canvas.dataset.pdChart === "history";
            const chartFontSize = active === "forecast" ? 12 : 10;
            const visual = vm.reference && !historical ? vm.visual_forecast : null;
            const h = visual || (vm.reference && historical ? vm.visual_demand : null) || vm.history;
            const path = historical ? [] : visual ? visual.path : vm.periods[1].path;
            const labels = [...h.dates, ...path.map((_, i) => { const d = new Date(vm.today + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0,10); })];
            const datasets = [{label: "Histórico", data: [...h.values, ...path.map(() => null)], borderColor: "#0873ff", backgroundColor: historical ? "#78b8ff" : "#e8f2ff", pointRadius: 0, borderWidth: historical ? 0 : 1.7, borderRadius: historical ? 2 : 0, barPercentage: .8, categoryPercentage: .9, fill: !historical}];
            if (!historical) datasets.push({label: "Pronóstico (30 días)", data: [...h.values.map(() => null), ...path], borderColor: "#ff8a00", borderDash: [5,4], pointRadius: 0, borderWidth: 2});
            if (visual && active === "forecast") {
                datasets[0].fill = false;
                datasets.push({label:"Intervalo inferior (referencia)",data:[...h.values.map(()=>null),...visual.lower],borderWidth:1,borderColor:"#d9eaff",pointRadius:0,backgroundColor:"#d9eaff",fill:false});
                datasets.push({label:"Intervalo superior (referencia)",data:[...h.values.map(()=>null),...visual.upper],borderWidth:1,borderColor:"#d9eaff",pointRadius:0,backgroundColor:"#d9eaff",fill:"-1"});
            }
            const todayMarker = {id:"todayMarker", afterDraw(chart) { if (historical || !h.values.length) return; const x = chart.scales.x.getPixelForValue(h.values.length); const ctx = chart.ctx; ctx.save(); ctx.strokeStyle="#8798b0";ctx.beginPath();ctx.moveTo(x,chart.chartArea.top);ctx.lineTo(x,chart.chartArea.bottom);ctx.stroke();ctx.fillStyle="#526581";ctx.font="11px sans-serif";ctx.fillText("Hoy",x-10,chart.chartArea.top-5);ctx.restore(); }};
            charts.push(new Chart(canvas, {type: historical ? "bar" : "line", data:{labels,datasets},plugins:[todayMarker], options:{responsive:true,maintainAspectRatio:false,animation:false,layout:{padding:{top:14,right:5}}, plugins:{legend:{display:!historical,labels:{usePointStyle:true,pointStyle:"line",boxWidth:20,padding:14,color:"#4c6080",font:{size:chartFontSize}}}},scales:{x:{grid:{display:false},border:{color:"#dce5f1"},ticks:{maxTicksLimit:7,maxRotation:0,color:"#546b90",font:{size:chartFontSize},callback(v){return labels[v] ? new Intl.DateTimeFormat("es-VE",{day:"numeric",month:"short",timeZone:"America/Caracas"}).format(new Date(labels[v]+"T12:00:00Z")).replace(".","") : "";}}},y:{beginAtZero:true,title:{display:true,text:vm.product.unit,color:"#607393",font:{size:chartFontSize}},border:{display:false},grid:{color:"#edf2f8"},ticks:{maxTicksLimit:6,color:"#546b90",font:{size:chartFontSize}}}}}}));
        });
    }
    function select(key, focus = false) {
        active = key;
        dialog.classList.toggle("is-forecast", key === "forecast");
        tabs.forEach(t => {const selected=t.dataset.pdTab===key;t.setAttribute("aria-selected",String(selected));t.tabIndex=selected?0:-1;if(selected&&focus)t.focus();});
        charts.forEach(c => c.destroy()); charts=[];
        if (!vm) return;
        const render = {summary,demand,forecast,inventory,replenishment};
        body.innerHTML = tabs.map(t => { const name=t.dataset.pdTab; return `<div id="pd-panel-${name}" role="tabpanel" aria-labelledby="pd-tab-${name}" tabindex="0" ${name === key ? "" : "hidden"}>${name === key ? render[key]() : ""}</div>`; }).join("");
        body.scrollTop=0; drawCharts();
    }
    function render() {
        const p = vm.product, risks={critical:"Crítico",high:"Alto",medium:"Medio",low:"Bajo"};
        document.getElementById("pd-product").innerHTML=`<div class="pd-image">${p.image ? `${icon("box")}<img src="${esc(p.image)}" alt="" onerror="this.remove()">` : icon("box")}</div><div><h3>${esc(p.name)}</h3><p>SKU: ${esc(p.sku)}</p><p>Categoría: ${esc(p.category)}</p><p>Proveedor: ${p.suppliers.length ? p.suppliers.map(esc).join(", ") : "N/D"}</p></div><span class="pd-risk is-${esc(vm.risk.level)}">${esc(risks[vm.risk.level] || vm.risk.level)}</span>`;
        footer.innerHTML=`<div class="pd-footer-actions">${link(vm.links.inventory, icon("box")+"Ver en inventario")}${link(vm.links.sales, icon("calendar")+"Ver historial de ventas")}<button type="button" id="pd-buy" ${vm.can_order && !vm.reference ? "" : "disabled"}>${icon("cart")}${vm.recommendation?.suggested_quantity === 0 ? "No requiere compra" : "Generar solicitud de reposición"}</button></div><p>${vm.reference ? "Referencia visual: cero escrituras." : purchaseExplanation()}</p>`;
        document.getElementById("pd-buy").addEventListener("click", buy);
        select(active);
    }
    function buy() {
        if (!vm || vm.reference || !vm.can_order || !vm.recommendation?.suggested_quantity) return;
        const row = document.createElement("div");
        const prioLabels = {high: "Alta", medium: "Media", low: "Baja"};
        const reasons = vm.recommendation.reasons || [];
        Object.assign(row.dataset, {productId: vm.product.id, name: vm.product.name, sku: vm.product.sku,
            src: vm.product.image || "", quantity: vm.recommendation.suggested_quantity,
            qtyText: qty(vm.recommendation.suggested_quantity), stockText: qty(vm.product.stock),
            prioLabel: prioLabels[vm.recommendation.priority] || vm.recommendation.priority || "N/D",
            reason: reasons.length ? reasons[0] : "N/D"});
        close();
        document.dispatchEvent(new CustomEvent("predictive:order-detail", {detail: {row}}));
    }
    function close() {
        stopFollowing?.();stopFollowing=null;
        controller?.abort();dialog.close();document.body.style.overflow=scrollBefore;charts.forEach(c=>c.destroy());charts=[];if(opener?.isConnected)opener.focus();
    }
    function showComputationStatus(state, message) {
        if (!vm || (vm.computation_status === state && vm.computation_message === message)) return;
        vm.computation_status = state || vm.computation_status;
        vm.computation_message = message;
        const scroll = body.scrollTop;
        render();
        body.scrollTop = scroll;
    }
    function followDetail(url, requestController) {
        if (vm.reference || !vm.computation_status_url ||
            !["pending", "running", "busy"].includes(vm.computation_status)) return;
        stopFollowing = window.PredictiveFollowStatus(vm.computation_status_url, showComputationStatus,
            () => loadDetail(url, requestController),
            () => dialog.open && controller === requestController && !requestController.signal.aborted);
    }
    async function loadDetail(url, requestController) {
        const timeout = window.setTimeout(() => requestController.abort(), 15000);
        try {
            const response = await fetch(url, {signal: requestController.signal, credentials: "same-origin", cache: "no-store"});
            if (!response.ok || response.redirected) throw new Error("Detail unavailable");
            const loaded = await response.json();
            if (requestController.signal.aborted || controller !== requestController || !dialog.open) return;
            vm = loaded;
            render();
            followDetail(url, requestController);
        } catch (_error) {
            if (controller !== requestController || !dialog.open) return;
            const message = "No se pudo cargar el producto. Compruebe su conexión y sesión o vuelva a abrir el detalle.";
            if (vm) showComputationStatus("unavailable", message);
            else body.innerHTML = notice(message, "red");
        } finally {
            window.clearTimeout(timeout);
        }
    }
    async function open(button) {
        stopFollowing?.();stopFollowing=null;
        opener=button; vm=null; controller?.abort();controller=new AbortController();const requestController=controller;
        active=({demand_forecast:"forecast",replenishment:"replenishment",trends:"demand"})[dialog.dataset.page]||"summary";
        document.getElementById("pd-product").replaceChildren();footer.replaceChildren();body.innerHTML=empty("Cargando detalle del producto…");
        scrollBefore=document.body.style.overflow;document.body.style.overflow="hidden";dialog.showModal();document.getElementById("pd-title").focus();select(active);
        const url=new URL(dialog.dataset.detailUrl.replace("/0/",`/${button.dataset.productDetail}/`),location.origin);
        if(dialog.dataset.reference==="1"){url.searchParams.set("ref","1");url.searchParams.set("sku",button.dataset.productSku);}
        await loadDetail(url, requestController);
    }
    document.querySelectorAll("[data-product-detail]").forEach(b=>b.addEventListener("click",()=>open(b)));
    dialog.querySelector("[data-pd-close]").addEventListener("click",close);
    dialog.addEventListener("cancel",e=>{e.preventDefault();close();});
    tabs.forEach((tab,i)=>{tab.addEventListener("click",()=>select(tab.dataset.pdTab));tab.addEventListener("keydown",e=>{let next;if(e.key==="ArrowRight")next=(i+1)%tabs.length;else if(e.key==="ArrowLeft")next=(i+tabs.length-1)%tabs.length;else if(e.key==="Home")next=0;else if(e.key==="End")next=tabs.length-1;if(next!==undefined){e.preventDefault();select(tabs[next].dataset.pdTab,true);}});});
    document.addEventListener("predictive:order-created",()=>{vm=null;});
    window.addEventListener("pagehide", () => { stopFollowing?.(); controller?.abort(); }, {once: true});
})();
