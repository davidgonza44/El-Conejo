/* Módulo Análisis predictivo: dibujo de gráficos a partir de los data-* del
   marcado (donuts, sparklines, evolución por categoría, precisión y medidor) e
   interacción mínima LIVE (filtros GET auto-enviados, filas por página, "Ver detalle").
   Sin lógica de negocio: solo representa los valores ya renderizados por el servidor.
   Las series sintéticas (LCG) se usan únicamente cuando el marcado no trae datos reales
   (modo referencia); en LIVE el backend entrega data-points / series.values. */
(function () {
    "use strict";

    /* Seguimiento LIVE: consulta un token ya creado; no inicia cálculos nuevos. */
    function followStatus(statusUrl, onStatus, onReady, isCurrent = () => true) {
        const activeStates = ["pending", "running", "busy"];
        let stopped = false;
        let timer;
        let deadline;
        let controller;
        let failures = 0;

        function stop() {
            stopped = true;
            window.clearTimeout(timer);
            window.clearTimeout(deadline);
            if (controller) controller.abort();
        }

        function unavailable(message) {
            onStatus("unavailable", message);
            stop();
        }

        function acceptStatus(payload) {
            const state = payload.status;
            if (state === "ready") {
                stop();
                onReady();
                return;
            }
            if (["error", "unavailable", "expired"].includes(state)) {
                onStatus(state, payload.message || "No se pudo completar el pronóstico. Puede reintentar con Actualizar modelo.");
                stop();
                return;
            }
            if (!activeStates.includes(state)) throw new Error("Invalid predictive status");
            onStatus(state, payload.message || "El resultado aparecerá automáticamente cuando termine el cálculo.");
            const delay = Math.min(10, Math.max(1, Number(payload.retry_after) || 2));
            timer = window.setTimeout(poll, delay * 1000);
        }

        async function poll() {
            if (stopped) return;
            if (!isCurrent()) { stop(); return; }
            controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 15000);
            try {
                const response = await fetch(statusUrl, {
                    headers: { Accept: "application/json" }, cache: "no-store",
                    credentials: "same-origin", signal: controller.signal,
                });
                if (stopped) return;
                if (response.redirected || response.status === 401 || response.status === 403) {
                    unavailable("No se puede consultar el pronóstico con la sesión actual. Vuelva a iniciar sesión o compruebe sus permisos.");
                    return;
                }
                if (!response.ok) throw new Error("Predictive status unavailable");
                const payload = await response.json();
                if (stopped) return;
                if (!isCurrent()) { stop(); return; }
                acceptStatus(payload);
                failures = 0;
            } catch (_error) {
                if (stopped) return;
                failures += 1;
                if (failures >= 3) {
                    unavailable("No se pudo consultar el estado del pronóstico. Compruebe la conexión y use Actualizar modelo para reintentar.");
                    return;
                }
                onStatus(null, "No se pudo consultar el estado. Se volverá a intentar automáticamente.");
                timer = window.setTimeout(poll, 5000);
            } finally {
                window.clearTimeout(timeout);
                controller = null;
            }
        }

        deadline = window.setTimeout(() => unavailable(
            "El seguimiento alcanzó su tiempo máximo. El cálculo puede continuar; use Actualizar modelo para volver a consultar."
        ), 30 * 60 * 1000);
        timer = window.setTimeout(poll, 2000);
        return stop;
    }
    window.PredictiveFollowStatus = followStatus;

    function followForecast() {
        const marker = document.querySelector("[data-predictive-status-url]");
        if (!marker) return;
        const activeStates = ["pending", "running", "busy"];
        const initialState = marker.getAttribute("data-predictive-state");
        if (!activeStates.includes(initialState)) return;
        const panel = marker.closest(".px-page") || marker;
        const banner = document.querySelector("[data-predictive-status]");
        const pageUrl = window.location.href;
        function show(state, message) {
            state = state || marker.getAttribute("data-predictive-state");
            marker.setAttribute("data-predictive-state", state);
            if (!banner) return;
            banner.setAttribute("data-predictive-status", state);
            const title = banner.querySelector("h2");
            const text = banner.querySelector("p");
            if (title) title.textContent = state === "running" ? "Calculando pronóstico" :
                activeStates.includes(state) ? "Pronóstico pendiente" : "Pronóstico no disponible";
            if (text) text.textContent = message;
            const emptyTitle = panel.querySelector(".px-empty strong");
            const emptyText = panel.querySelector(".px-empty p");
            if (emptyTitle && title) emptyTitle.textContent = title.textContent;
            if (emptyText) emptyText.textContent = message;
            const topEmpty = panel.querySelector(".px-top-empty");
            if (topEmpty && !activeStates.includes(state)) topEmpty.textContent = "Pronóstico no disponible.";
            if (!activeStates.includes(state)) {
                panel.querySelectorAll(".px-kpi-line .px-seg").forEach(label => {
                    if (/pronóstico pendiente|cobertura predictiva pendiente/i.test(label.textContent)) {
                        label.textContent = label.textContent.replace(/pendiente/g, "no disponible");
                    }
                });
                const donutLabel = panel.querySelector(".px-donut-label");
                if (donutLabel?.textContent === "Pendiente") donutLabel.textContent = "No disponible";
            }
        }
        const stop = followStatus(marker.getAttribute("data-predictive-status-url"), show,
            () => window.location.reload(), () => window.location.href === pageUrl);
        // Cancelar antes de enviar nuevos filtros descarta respuestas antiguas.
        document.addEventListener("submit", stop, true);
        document.addEventListener("change", (event) => {
            if (event.target.closest("form[data-autosubmit], select[data-navigate]")) stop();
        }, true);
        window.addEventListener("pagehide", stop, { once: true });
        show(initialState, "El resultado aparecerá automáticamente cuando termine el cálculo.");
    }
    followForecast();

    /* ---- LIVE: filtros reales, paginación y detalle por fila ---- */
    document.querySelectorAll("form[data-autosubmit]").forEach((form) => {
        form.querySelectorAll("select").forEach((select) => {
            select.addEventListener("change", () => form.requestSubmit());
        });
    });
    document.querySelectorAll("select[data-navigate]").forEach((select) => {
        select.addEventListener("change", () => { window.location.assign(select.value); });
    });
    const TONES = {
        red: "#ef4444",
        orange: "#c98c00",
        yellow: "#c98c00",
        green: "#22c55e",
        blue: "#2f7fc1",
        purple: "#a855f7",
        gray: "#626a65",
    };

    /* Ruido determinista (LCG) para que las series de referencia sean estables entre capturas. */
    function seeded(seed) {
        let s = (seed * 9301 + 49297) % 233280;
        return () => {
            s = (s * 9301 + 49297) % 233280;
            return s / 233280;
        };
    }

    function parseJSON(el, attr, fallback) {
        const raw = el.getAttribute(attr);
        if (!raw) return fallback;
        return JSON.parse(raw);
    }

    /* ---- Donuts (distribución de riesgo / prioridad / criticidad) ---- */
    document.querySelectorAll(".px-donut").forEach((box) => {
        const canvas = box.querySelector("canvas");
        if (!canvas || typeof Chart === "undefined") return;
        let values = parseJSON(box, "data-donut", []);
        let tones = parseJSON(box, "data-tones", []);
        if (!values.some((v) => v > 0)) { values = [1]; tones = ["gray"]; } // anillo vacío honesto
        new Chart(canvas, {
            type: "doughnut",
            data: {
                datasets: [{
                    data: values,
                    backgroundColor: tones.map((t) => (t === "gray" ? "#e2e4df" : TONES[t] || TONES.gray)),
                    borderWidth: 2,
                    borderColor: "#fff",
                    hoverOffset: 0,
                }],
            },
            options: {
                cutout: "66%",
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                events: [],
            },
        });
    });

    /* ---- Sparklines (columna Tendencia últimos 30 días) ---- */
    function sparkPoints(direction, seed, width, height) {
        const rnd = seeded(seed);
        const n = 18;
        const pts = [];
        for (let i = 0; i < n; i++) {
            const t = i / (n - 1);
            let base;
            if (direction === "up") base = 0.82 - t * 0.66;
            else if (direction === "down") base = 0.18 + t * 0.66;
            else base = 0.5;
            const amp = direction === "flat" ? 0.34 : 0.4;
            const y = Math.min(0.97, Math.max(0.03, base + (rnd() - 0.5) * amp));
            pts.push([2 + t * (width - 4), 2 + y * (height - 4)]);
        }
        return pts;
    }

    /* LIVE: data-points trae la ventana observada real (valores por período). */
    function realPoints(values, width, height) {
        const max = Math.max(...values, 1);
        const n = values.length;
        return values.map((v, i) => [2 + (n > 1 ? i / (n - 1) : 0.5) * (width - 4), 2 + (1 - v / max) * (height - 4)]);
    }

    document.querySelectorAll(".px-spark").forEach((svg, index) => {
        const direction = svg.getAttribute("data-spark") || "flat";
        const color = svg.getAttribute("data-color") || TONES.blue;
        const width = 94;
        const height = 22;
        svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
        const real = parseJSON(svg, "data-points", null);
        const pts = real && real.length ? realPoints(real, width, height) : sparkPoints(direction, index + 7, width, height);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        line.setAttribute("points", pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" "));
        line.setAttribute("fill", "none");
        line.setAttribute("stroke", color);
        line.setAttribute("stroke-width", "1.6");
        line.setAttribute("stroke-linejoin", "round");
        line.setAttribute("stroke-linecap", "round");
        svg.appendChild(line);
    });

    /* ---- Evolución de ventas por categoría (Tendencias) ---- */
    function seriesData(from, to, seed, count) {
        const rnd = seeded(seed);
        const out = [];
        for (let i = 0; i < count; i++) {
            const t = i / (count - 1);
            const noise = (rnd() - 0.5) * Math.max(40, Math.abs(to - from) * 0.12 + 30);
            out.push(Math.max(0, Math.round(from + (to - from) * t + noise)));
        }
        return out;
    }

    document.querySelectorAll("[data-category-chart]").forEach((box) => {
        const canvas = box.querySelector("canvas");
        if (!canvas || typeof Chart === "undefined") return;
        const cfg = parseJSON(box, "data-category-chart", null);
        if (!cfg || !cfg.series.length) return;
        const live = Array.isArray(cfg.series[0].values);
        const count = live ? cfg.series[0].values.length : 30;
        const labels = [];
        if (live) {
            // Etiquetas reales (una por período); se muestran ~5 repartidas para no solaparse.
            const every = Math.max(1, Math.round((count - 1) / 4));
            cfg.labels.forEach((l, i) => labels.push(i % every === 0 || i === count - 1 ? l : ""));
        } else {
            for (let i = 0; i < count; i++) {
                const tick = Math.round((i / (count - 1)) * (cfg.labels.length - 1));
                const exact = Math.round((tick / (cfg.labels.length - 1)) * (count - 1)) === i;
                labels.push(exact ? cfg.labels[tick] : "");
            }
        }
        new Chart(canvas, {
            type: "line",
            data: {
                labels,
                datasets: cfg.series.map((s, i) => ({
                    data: live ? s.values : seriesData(s.from, s.to, 11 + i * 3, count),
                    borderColor: TONES[s.tone] || TONES.blue,
                    borderWidth: 1.8,
                    pointRadius: 0,
                    tension: 0.25,
                    fill: false,
                })),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                events: [],
                layout: { padding: { top: 4, right: 6 } },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { color: "#e2e4df" },
                        ticks: {
                            autoSkip: false, maxRotation: 0, font: { size: 9 }, color: "#626a65",
                            callback: (v, i) => labels[i] || null, padding: 4,
                        },
                    },
                    y: {
                        min: 0, max: cfg.y_max,
                        grid: { color: "#f7f8f5" },
                        border: { display: false, dash: [3, 3] },
                        ticks: {
                            stepSize: cfg.y_step, font: { size: 9 }, color: "#626a65", padding: 6,
                            callback: (v) => v.toLocaleString("en-US"),
                        },
                    },
                },
            },
        });
    });

    /* ---- Predicción vs. Real (Precisión del modelo) ---- */
    document.querySelectorAll("[data-accuracy-chart]").forEach((box) => {
        const canvas = box.querySelector("canvas");
        if (!canvas || typeof Chart === "undefined") return;
        const cfg = parseJSON(box, "data-accuracy-chart", null);
        if (!cfg) return;
        new Chart(canvas, {
            type: "line",
            data: {
                labels: cfg.labels,
                datasets: [
                    {
                        data: cfg.real, borderColor: "#e07b10", backgroundColor: "#e07b10",
                        borderWidth: 1.8, pointRadius: 2.4, pointHoverRadius: 2.4, tension: 0,
                    },
                    {
                        data: cfg.pred, borderColor: "#22c55e", backgroundColor: "#22c55e",
                        borderWidth: 1.8, borderDash: [5, 4], pointRadius: 2.4, pointHoverRadius: 2.4, tension: 0,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                events: [],
                layout: { padding: { top: 6, right: 10 } },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { color: "#e2e4df" },
                        ticks: { autoSkip: false, maxRotation: 0, font: { size: 10 }, color: "#3a403c", padding: 6 },
                    },
                    y: {
                        min: 0, max: cfg.y_max,
                        grid: { color: "#f7f8f5" },
                        border: { display: false },
                        ticks: { stepSize: cfg.y_step, font: { size: 10 }, color: "#3a403c", padding: 8 },
                    },
                },
            },
        });
    });

    /* ---- Medidor semicircular (Desempeño del modelo) ---- */
    document.querySelectorAll("[data-gauge]").forEach((box) => {
        const canvas = box.querySelector("canvas");
        if (!canvas || typeof Chart === "undefined") return;
        const raw = box.getAttribute("data-gauge");
        const value = raw === "" ? 0 : Number(raw) || 0; // "" = N/D: solo la pista gris
        new Chart(canvas, {
            type: "doughnut",
            data: {
                datasets: [{
                    data: [value, Math.max(0, 100 - value)],
                    backgroundColor: [value >= 80 ? "#16a34a" : value >= 70 ? "#c98c00" : "#ef4444", "#e2e4df"],
                    borderWidth: 0,
                    borderRadius: 6,
                }],
            },
            options: {
                rotation: -90,
                circumference: 180,
                cutout: "74%",
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                events: [],
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
            },
        });
    });
})();
