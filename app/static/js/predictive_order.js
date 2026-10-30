/* Reabastecimiento LIVE: selección explícita de recomendaciones y modal
   "Generar solicitud de reposición". Crea UNA solicitud sin precios
   (POST JSON con CSRF) únicamente cuando el usuario confirma en el modal.
   Inventario ve solo producto, stock, cantidad sugerida, prioridad y motivo.
   Sin cálculos predictivos aquí: solo confirma lo que ya mostró el servidor. */
(() => {
    "use strict";
    const dialog = document.getElementById("rl-order");
    const openButton = document.getElementById("px-order-open");
    if (!dialog || document.documentElement.dataset.referenceMode === "1") return;

    const $ = (id) => document.getElementById(id);
    const picks = [...document.querySelectorAll("[data-order-pick]")];
    let opener = null;
    let detailRows = null;
    let inFlight = false;
    let retryKey = null;
    let retryPayload = null;

    function selectedRows() {
        return detailRows || picks.filter((p) => p.checked).map((p) => p.closest("[data-order-row]"));
    }

    function syncSelection() {
        const count = selectedRows().length;
        if (openButton) openButton.disabled = count === 0;
        const label = $("px-selection");
        if (label) label.textContent = count ? `${count} producto(s) seleccionado(s)` : "Seleccione productos para generar una solicitud";
    }

    function renderLines() {
        const rows = selectedRows();
        const body = $("rl-order-lines");
        body.replaceChildren();
        rows.forEach((row) => {
            const tr = document.createElement("tr");
            const product = document.createElement("td");
            const box = document.createElement("div");
            box.className = "rl-product";
            if (row.dataset.src) {
                const img = document.createElement("img");
                img.src = row.dataset.src;
                img.alt = "";
                box.append(img);
            }
            const text = document.createElement("span");
            const name = document.createElement("strong");
            name.textContent = row.dataset.name || "N/D";
            const sku = document.createElement("small");
            sku.textContent = "SKU: " + (row.dataset.sku || "N/D");
            text.append(name, sku);
            box.append(text);
            product.append(box);
            const stock = document.createElement("td");
            stock.textContent = row.dataset.stockText || "N/D";
            const qty = document.createElement("td");
            qty.textContent = row.dataset.qtyText || "N/D";
            const prio = document.createElement("td");
            prio.textContent = row.dataset.prioLabel || "N/D";
            const reason = document.createElement("td");
            reason.textContent = row.dataset.reason || "N/D";
            tr.append(product, stock, qty, prio, reason);
            body.append(tr);
        });
        $("rl-order-count").textContent = String(rows.length);
    }

    function showError(message) {
        const box = $("rl-order-error");
        box.textContent = message || "";
        box.hidden = !message;
    }

    function open() {
        opener = document.activeElement;
        showError("");
        renderLines();
        dialog.showModal();
        document.body.style.overflow = "hidden";
        dialog.querySelector("h2").focus();
    }

    function close() {
        if (!dialog.open) return;
        dialog.close();
        document.body.style.overflow = "";
        if (opener && opener.isConnected) opener.focus();
    }

    function notify(message, href) {
        const box = $("rl-feedback");
        box.replaceChildren(document.createTextNode(message + " "));
        if (href) {
            const link = document.createElement("a");
            link.href = href;
            link.textContent = "Ver la solicitud";
            box.append(link);
        }
        box.hidden = false;
    }

    picks.forEach((p) => p.addEventListener("change", syncSelection));
    syncSelection();
    if (openButton) openButton.addEventListener("click", () => { detailRows = null; open(); });
    document.addEventListener("predictive:order-detail", (event) => {
        detailRows = [event.detail.row];
        open();
    });
    dialog.querySelectorAll("[data-order-close]").forEach((b) => b.addEventListener("click", close));
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    $("rl-notes").addEventListener("input", () => { $("rl-note-count").textContent = $("rl-notes").value.length + "/500"; });

    $("rl-order-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        if (inFlight) return;
        const form = event.currentTarget;
        if (!form.reportValidity()) return;
        const rows = selectedRows();
        if (!rows.length) { showError("Seleccione al menos un producto para generar la solicitud."); return; }
        const items = [];
        for (const row of rows) {
            const productId = Number(row.dataset.productId);
            const quantity = Number(row.dataset.quantity);
            if (!Number.isInteger(productId) || productId <= 0) { showError("Producto inválido en la selección."); return; }
            if (!Number.isInteger(quantity) || quantity <= 0) { showError("La cantidad debe ser mayor que cero."); return; }
            items.push({ product_id: productId, quantity });
        }
        const payload = { confirm: true, notes: $("rl-notes").value, items };
        const signature = JSON.stringify(payload);
        if (signature !== retryPayload) {
            retryPayload = signature;
            retryKey = crypto.randomUUID();
        }
        payload.idempotency_key = retryKey;
        const submit = $("rl-order-submit");
        inFlight = true;
        submit.disabled = true;
        if (openButton) openButton.disabled = true;
        showError("");
        try {
            const response = await fetch(dialog.dataset.orderUrl, {
                method: "POST",
                credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": dialog.dataset.csrf },
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) { showError(data.error || `No se pudo crear la solicitud (HTTP ${response.status}).`); return; }
            const number = data.request_number || "";
            retryKey = null;
            retryPayload = null;
            close();
            picks.forEach((p) => { p.checked = false; });
            detailRows = null;
            syncSelection();
            document.dispatchEvent(new Event("predictive:order-created"));
            const inbox = dialog.dataset.purchasesUrl || "/purchases/requests";
            notify(`Solicitud ${number} creada correctamente. El asistente prepara la orden de compra.`,
                number ? `${inbox}?search=${encodeURIComponent(number)}` : inbox);
        } catch (_error) {
            showError("No se pudo confirmar la respuesta. Puede reintentar la misma selección sin duplicarla o consultar Mis solicitudes.");
        } finally {
            inFlight = false;
            submit.disabled = false;
            syncSelection();
        }
    });
})();
