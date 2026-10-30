(() => {
  "use strict";
  const filters = document.getElementById("sr-filters");
  if (filters) {
    const startDate = filters.elements.start;
    const endDate = filters.elements.end;
    const message = document.getElementById("sr-filter-error");

    function validateDates() {
      const invalid = startDate.value && endDate.value && startDate.value > endDate.value;
      const text = invalid ? "La fecha inicial no puede superar la final." : "";
      endDate.setCustomValidity(text);
      endDate.setAttribute("aria-invalid", invalid ? "true" : "false");
      message.textContent = text;
      message.hidden = !invalid;
      return !invalid;
    }

    filters.addEventListener("submit", (event) => {
      if (!validateDates()) {
        event.preventDefault();
        endDate.reportValidity();
      }
    });
    [startDate, endDate].forEach((input) => input.addEventListener("input", validateDates));
    filters.querySelectorAll("select, input[type=date]").forEach((input) => {
      input.addEventListener("change", () => {
        if (validateDates()) filters.requestSubmit();
      });
    });
    validateDates();
  }
  const root = document.getElementById("sr-actions");
  if (!root) return;
  const error = document.getElementById("sr-error");
  const id = root.dataset.id;
  let busy = false;
  window.addEventListener("pageshow", (event) => { if (event.persisted) window.location.reload(); });

  function show(message) {
    error.textContent = message;
    error.hidden = !message;
  }

  async function post(path, body) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": root.dataset.csrf },
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) throw new Error("La sesión expiró. Inicie sesión y recargue esta solicitud.");
    if (!response.ok) throw new Error(payload.error || "No se pudo completar la operación.");
    return payload;
  }

  async function run(button, path, body) {
    if (busy) return;
    busy = true;
    const buttons = [...root.querySelectorAll("button")].filter((node) => !node.disabled && !node.closest("fieldset[disabled]"));
    buttons.forEach((node) => { node.disabled = true; });
    root.setAttribute("aria-busy", "true");
    const label = button.textContent;
    button.textContent = "Procesando…";
    show("");
    try {
      await post(path, body);
      window.location.reload();
    } catch (err) {
      show(err.message);
      busy = false;
      root.removeAttribute("aria-busy");
      button.textContent = label;
      buttons.forEach((node) => { node.disabled = false; });
    }
  }

  const start = document.getElementById("sr-start");
  if (start) start.addEventListener("click", () => run(start, `/api/replenishment-requests/${id}/start`, {}));

  const prepare = document.getElementById("sr-prepare");
  if (prepare) {
    prepare.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!prepare.reportValidity()) return;
      const button = prepare.querySelector("button[type=submit]");
      run(button, `/api/replenishment-requests/${id}/prepare`, {
        supplier_quote_id: Number(new FormData(prepare).get("supplier_quote_id")),
      });
    });
  }

  const quote = document.getElementById("sr-quote");
  if (quote) {
    const currency = quote.elements.currency;
    const updateSymbols = () => quote.querySelectorAll("[data-quote-symbol]").forEach((node) => {
      node.textContent = {USD: "$", VES: "Bs."}[currency.value] || "Moneda no registrada";
    });
    currency.addEventListener("change", updateSymbols);
    updateSymbols();
    quote.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!quote.reportValidity()) return;
      const button = quote.querySelector("button[type=submit]");
      const data = new FormData(quote);
      const items = [...quote.querySelectorAll("[data-product-id]")].map((input) => ({
        product_id: Number(input.dataset.productId),
        unit_price: input.value,
      }));
      run(button, `/api/replenishment-requests/${id}/quotes`, {
        supplier_id: Number(data.get("supplier_id")),
        currency: data.get("currency"),
        issued_on: data.get("issued_on"),
        valid_until: data.get("valid_until"),
        items,
      });
    });
  }

  const cancel = document.getElementById("sr-cancel");
  if (cancel) {
    cancel.addEventListener("click", () => {
      if (!window.confirm("¿Anular esta solicitud? No se borra y no genera una orden.")) return;
      run(cancel, `/api/replenishment-requests/${id}/cancel`, {});
    });
  }
})();
