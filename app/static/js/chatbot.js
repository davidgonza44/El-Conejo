/* Interfaz del asistente interno de inventario.
   La conversación vive únicamente en el DOM mientras la página está abierta. */
(function () {
    "use strict";

    const ENDPOINT = "/api/chatbot/message";
    const MAX_MESSAGE_LENGTH = 500;
    const SAFE_IMAGE_RE =
        /^\/media\/products\/[A-Za-z0-9][A-Za-z0-9_-]{0,199}\.(?:jpg|jpeg|png|webp)$/i;
    const $ = (id) => document.getElementById(id);

    const page = $("chatbot-page");
    if (!page) return;

    const elements = {
        form: $("chatbot-form"),
        input: $("chatbot-input"),
        inputError: $("chatbot-input-error"),
        counter: $("chatbot-counter"),
        counterAlert: $("chatbot-counter-alert"),
        send: $("chatbot-send"),
        sendLabel: $("chatbot-send-label"),
        sendLoading: $("chatbot-send-loading"),
        log: $("chatbot-log"),
        typing: $("chatbot-typing"),
    };

    if (Object.values(elements).some((element) => !element)) return;

    const canViewStock = page.dataset.canViewStock === "true";
    const quickActions = Array.from(
        document.querySelectorAll("[data-chat-prompt][data-chat-mode]")
    );
    const state = {
        sending: false,
        reviewingHistory: false,
        counterThreshold: "normal",
        userScrollIntent: false,
        scrollIntentTimer: null,
    };

    const priceFormat = new Intl.NumberFormat("es-VE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
    const quantityFormat = new Intl.NumberFormat("es-VE", {
        maximumFractionDigits: 2,
    });

    const STATUS_DETAILS = {
        empty: {
            label: "Sin resultados",
            icon: "ti-inbox",
            className: "chatbot-response-state--empty",
        },
        not_found: {
            label: "No encontrado",
            icon: "ti-search-off",
            className: "chatbot-response-state--not-found",
        },
        unknown: {
            label: "Consulta no reconocida",
            icon: "ti-help",
            className: "chatbot-response-state--unknown",
        },
        needs_clarification: {
            label: "Elige una opción",
            icon: "ti-list-search",
            className: "chatbot-response-state--needs-clarification",
        },
    };

    const AVAILABILITY_DETAILS = {
        available: {
            label: "Disponible",
            icon: "ti-circle-check",
            className: "chatbot-availability--available",
        },
        low_stock: {
            label: "Bajo stock",
            icon: "ti-alert-triangle",
            className: "chatbot-availability--low",
        },
        out_of_stock: {
            label: "Sin existencia",
            icon: "ti-package-off",
            className: "chatbot-availability--out",
        },
    };

    class ChatbotRequestError extends Error {
        constructor(status, message) {
            super(message);
            this.name = "ChatbotRequestError";
            this.status = status;
        }
    }

    function hasField(object, field) {
        return (
            object !== null &&
            typeof object === "object" &&
            !Array.isArray(object) &&
            Object.prototype.hasOwnProperty.call(object, field)
        );
    }

    function isRecord(value) {
        return value !== null && typeof value === "object" && !Array.isArray(value);
    }

    function displayText(value) {
        if (typeof value === "string") return value.trim();
        if (typeof value === "number" && Number.isFinite(value)) return String(value);
        return "";
    }

    function numericValue(value) {
        if (typeof value === "number" && Number.isFinite(value)) return value;
        if (typeof value === "string" && value.trim() !== "") {
            const parsed = Number(value);
            return Number.isFinite(parsed) ? parsed : null;
        }
        return null;
    }

    function createIcon(iconName, extraClass = "") {
        const icon = document.createElement("i");
        icon.className = `ti ${iconName}${extraClass ? ` ${extraClass}` : ""}`;
        icon.setAttribute("aria-hidden", "true");
        return icon;
    }

    function createTextElement(tagName, className, text) {
        const element = document.createElement(tagName);
        if (className) element.className = className;
        element.textContent = text;
        return element;
    }

    function prefersReducedMotion() {
        return (
            typeof window.matchMedia === "function" &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches
        );
    }

    function isNearConversationEnd() {
        const remaining =
            elements.log.scrollHeight -
            elements.log.scrollTop -
            elements.log.clientHeight;
        return remaining <= 72;
    }

    function revealLogNode(node, alignment = "start") {
        window.requestAnimationFrame(() => {
            if (!node.isConnected || node.hidden) return;

            const padding = 12;
            const start = Math.max(0, node.offsetTop - padding);
            const end = Math.max(
                0,
                node.offsetTop +
                    node.offsetHeight -
                    elements.log.clientHeight +
                    padding
            );
            elements.log.scrollTo({
                top: alignment === "end" ? end : start,
                behavior: prefersReducedMotion() ? "auto" : "smooth",
            });
        });
    }

    function focusInput() {
        try {
            elements.input.focus({ preventScroll: true });
        } catch (_error) {
            elements.input.focus();
        }
    }

    function insertBeforeTyping(node, options = {}) {
        const { reveal = true, alignment = "start" } = options;
        elements.log.insertBefore(node, elements.typing);
        if (reveal) revealLogNode(node, alignment);
    }

    function createMessage(kind, text) {
        const message = document.createElement("article");
        message.className = `chatbot-message chatbot-message--${kind}`;
        message.setAttribute(
            "aria-label",
            kind === "user"
                ? "Mensaje del usuario"
                : kind === "error"
                  ? "Error del asistente"
                  : "Mensaje del asistente"
        );
        const avatar = document.createElement("div");
        avatar.className = "chatbot-message__avatar";
        avatar.setAttribute("aria-hidden", "true");
        avatar.appendChild(
            createIcon(
                kind === "user"
                    ? "ti-user"
                    : kind === "error"
                      ? "ti-alert-triangle"
                      : "ti-message-chatbot"
            )
        );

        const content = document.createElement("div");
        content.className = "chatbot-message__content";

        const author = createTextElement(
            "span",
            "chatbot-message__author",
            kind === "user" ? "Tú" : kind === "error" ? "Error" : "Asistente"
        );

        const bubble = document.createElement("div");
        bubble.className = "chatbot-message__bubble";
        bubble.appendChild(createTextElement("p", "", text));

        content.append(author, bubble);
        message.append(avatar, content);
        return { message, bubble };
    }

    function appendUserMessage(text) {
        state.reviewingHistory = false;
        state.userScrollIntent = false;
        window.clearTimeout(state.scrollIntentTimer);
        insertBeforeTyping(createMessage("user", text).message, {
            alignment: "end",
        });
    }

    function createResponseState(status) {
        const details = STATUS_DETAILS[status];
        if (!details) return null;

        const badge = document.createElement("span");
        badge.className = `chatbot-response-state ${details.className}`;
        badge.append(createIcon(details.icon), document.createTextNode(details.label));
        return badge;
    }

    function createImagePlaceholder() {
        const placeholder = document.createElement("div");
        placeholder.className = "chatbot-product-card__placeholder";
        placeholder.append(
            createIcon("ti-photo-off"),
            createTextElement("span", "", "Sin imagen")
        );
        return placeholder;
    }

    function safeImagePath(value) {
        if (typeof value !== "string") return null;
        const candidate = value.trim();
        return SAFE_IMAGE_RE.test(candidate) ? candidate : null;
    }

    function createProductImage(item) {
        const holder = document.createElement("div");
        holder.className = "chatbot-product-card__image";

        const imagePath = safeImagePath(item.image_url);
        if (!imagePath) {
            holder.appendChild(createImagePlaceholder());
            return holder;
        }

        const image = document.createElement("img");
        const productName = displayText(item.name);
        image.src = imagePath;
        image.alt = productName ? `Imagen de ${productName}` : "Imagen del producto";
        image.loading = "lazy";
        image.decoding = "async";
        image.addEventListener(
            "error",
            () => holder.replaceChildren(createImagePlaceholder()),
            { once: true }
        );
        holder.appendChild(image);
        return holder;
    }

    function createFact(label, value) {
        const fact = document.createElement("div");
        fact.className = "chatbot-product-card__fact";
        fact.append(
            createTextElement("span", "", label),
            createTextElement("strong", "", value)
        );
        return fact;
    }

    function appendNumericFact(facts, item, field, label, formatter) {
        if (!hasField(item, field)) return;
        const value = numericValue(item[field]);
        if (value === null) return;
        facts.appendChild(createFact(label, formatter.format(value)));
    }

    function formattedPrice(value) {
        if (typeof value === "number" && Number.isFinite(value)) {
            return priceFormat.format(value);
        }
        if (typeof value !== "string") return "";

        const supplied = value.trim();
        const simpleNumber = supplied.replace(/\s/g, "");
        if (/^-?\d+(?:[.,]\d+)?$/.test(simpleNumber)) {
            const parsed = Number(simpleNumber.replace(",", "."));
            if (Number.isFinite(parsed)) return priceFormat.format(parsed);
        }
        return supplied;
    }

    function appendPriceFact(facts, item) {
        if (!hasField(item, "sale_price")) return;
        const value = formattedPrice(item.sale_price);
        if (!value) return;
        facts.appendChild(createFact("Precio de venta", value));
    }

    function createAvailability(value) {
        const details = AVAILABILITY_DETAILS[value];
        if (!details) return null;

        const badge = document.createElement("span");
        badge.className = `chatbot-availability ${details.className}`;
        badge.append(createIcon(details.icon), document.createTextNode(details.label));
        return badge;
    }

    function createResultCard(item) {
        const card = document.createElement("article");
        card.className = "chatbot-product-card";

        const name = displayText(item.name);
        card.setAttribute(
            "aria-label",
            name ? `Resultado: ${name}` : "Resultado de la consulta"
        );
        card.appendChild(createProductImage(item));

        const body = document.createElement("div");
        body.className = "chatbot-product-card__body";

        const code = displayText(item.code);
        if (code) {
            const topLine = document.createElement("div");
            topLine.className = "chatbot-product-card__topline";
            topLine.appendChild(
                createTextElement("span", "chatbot-product-card__code", code)
            );
            body.appendChild(topLine);
        }

        body.appendChild(
            createTextElement(
                "h3",
                "chatbot-product-card__name",
                name || "Resultado"
            )
        );

        const category = displayText(item.category);
        if (category) {
            body.appendChild(
                createTextElement(
                    "p",
                    "chatbot-product-card__category",
                    category
                )
            );
        }

        const description = displayText(item.description);
        if (description) {
            body.appendChild(
                createTextElement(
                    "p",
                    "chatbot-product-card__description",
                    description
                )
            );
        }

        if (hasField(item, "availability")) {
            const availability = createAvailability(item.availability);
            if (availability) body.appendChild(availability);
        }

        const facts = document.createElement("div");
        facts.className = "chatbot-product-card__facts";
        appendPriceFact(facts, item);
        appendNumericFact(facts, item, "current_stock", "Stock actual", quantityFormat);
        appendNumericFact(facts, item, "minimum_stock", "Stock mínimo", quantityFormat);
        if (facts.childElementCount > 0) body.appendChild(facts);

        card.appendChild(body);
        return card;
    }

    function isCategoryClarification(response) {
        return (
            response.intent === "products_by_category" &&
            response.status === "needs_clarification"
        );
    }

    function responseCategories(response) {
        if (!isCategoryClarification(response)) return [];

        const data = isRecord(response.data) ? response.data : {};
        const source = Array.isArray(data.categories)
            ? data.categories
            : Array.isArray(data.items)
              ? data.items
              : [];
        return source
            .filter(isRecord)
            .filter((category) => Boolean(displayText(category.name)))
            .slice(0, 5);
    }

    function createCategoryOptions(categories) {
        if (categories.length === 0) return null;

        const section = document.createElement("section");
        section.className = "chatbot-category-options";
        section.setAttribute("aria-label", "Categorías posibles");
        section.appendChild(
            createTextElement(
                "h3",
                "chatbot-category-options__title",
                "Categorías posibles"
            )
        );

        const list = document.createElement("ul");
        list.className = "chatbot-category-options__list";

        categories.forEach((category) => {
            const name = displayText(category.name);
            const description = displayText(category.description);
            const item = document.createElement("li");
            const button = document.createElement("button");
            button.type = "button";
            button.className = "chatbot-category-option";

            const icon = document.createElement("span");
            icon.className = "chatbot-category-option__icon";
            icon.setAttribute("aria-hidden", "true");
            icon.appendChild(createIcon("ti-category"));

            const content = document.createElement("span");
            content.className = "chatbot-category-option__content";
            content.appendChild(
                createTextElement(
                    "strong",
                    "chatbot-category-option__name",
                    name
                )
            );
            if (description) {
                content.appendChild(
                    createTextElement(
                        "span",
                        "chatbot-category-option__description",
                        description
                    )
                );
            }

            button.append(
                icon,
                content,
                createIcon("ti-chevron-right", "chatbot-category-option__arrow")
            );
            button.addEventListener("click", () =>
                runAction({
                    label: name,
                    query: `Productos de la categoría ${name}`,
                    mode: "send",
                })
            );
            item.appendChild(button);
            list.appendChild(item);
        });

        section.appendChild(list);
        return section;
    }

    function responseItems(response) {
        if (isCategoryClarification(response)) return [];

        const data = isRecord(response.data) ? response.data : {};
        if (isRecord(data.product)) return [data.product];
        if (Array.isArray(data.items)) return data.items.filter(isRecord);
        if (Array.isArray(data.candidates)) return data.candidates.filter(isRecord);
        return [];
    }

    function createResults(response, items) {
        if (items.length === 0 || isCategoryClarification(response)) return null;

        const data = isRecord(response.data) ? response.data : {};
        const totalValue = numericValue(data.count);
        const total =
            totalValue !== null && totalValue >= 0
                ? Math.floor(totalValue)
                : items.length;

        const section = document.createElement("section");
        section.className = "chatbot-results";
        section.setAttribute("aria-label", "Resultados de la consulta");

        const header = document.createElement("div");
        header.className = "chatbot-results__header";
        header.appendChild(
            createTextElement(
                "span",
                "",
                items.length === 1 ? "Producto encontrado" : "Productos encontrados"
            )
        );

        const summary = createTextElement(
            "span",
            "chatbot-results__summary",
            total === 1 ? "1 resultado" : `${quantityFormat.format(total)} resultados`
        );
        header.appendChild(summary);

        const grid = document.createElement("div");
        grid.className = "chatbot-results__grid";
        grid.replaceChildren(...items.map(createResultCard));
        section.append(header, grid);

        if (data.truncated === true && total > items.length) {
            section.appendChild(
                createTextElement(
                    "p",
                    "chatbot-results__summary mt-2 mb-0",
                    `Se muestran ${items.length} de ${total} resultados.`
                )
            );
        }
        return section;
    }

    function normalizedText(value) {
        return value
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLocaleLowerCase("es");
    }

    function sellerCanSeeSuggestion(label) {
        if (canViewStock) return true;
        const normalized = normalizedText(label);
        return !(
            normalized.includes("bajo stock") ||
            normalized.includes("stock bajo") ||
            normalized.includes("stock exacto") ||
            normalized.includes("stock actual") ||
            normalized.includes("stock minimo") ||
            normalized.includes("stock de") ||
            normalized.includes("reposicion")
        );
    }

    function actionFromSuggestion(label, response) {
        const normalized = normalizedText(label);

        if (response.status === "not_found") {
            if (normalized.includes("categoria")) {
                return {
                    label,
                    query: "Productos de la categoría",
                    mode: "compose",
                };
            }
            if (normalized.includes("codigo")) {
                return {
                    label,
                    query: "Dame información del producto con código",
                    mode: "compose",
                };
            }
            if (normalized.includes("nombre")) {
                return { label, query: "Buscar producto", mode: "compose" };
            }
        }

        if (
            response.status === "needs_clarification" &&
            response.intent === "products_by_category"
        ) {
            return {
                label,
                query: `Productos de la categoría ${label}`,
                mode: "send",
            };
        }

        const codeMatch = label.match(/^c[oó]digo\s+(.+)$/i);
        if (response.status === "needs_clarification" && codeMatch) {
            const code = codeMatch[1].trim();
            return {
                label: `Consultar ${code}`,
                query: `Dame información del producto con código ${code}`,
                mode: "send",
            };
        }

        return { label, query: label, mode: "send" };
    }

    function clarificationActions(items) {
        const byCode = items
            .map((item) => displayText(item.code))
            .filter(Boolean)
            .slice(0, 5)
            .map((code) => ({
                label: `Consultar ${code}`,
                query: `Dame información del producto con código ${code}`,
                mode: "send",
            }));
        if (byCode.length > 0) return byCode;
        return [];
    }

    function composeQuery(prompt) {
        if (state.sending) return;
        elements.input.value = `${prompt.trim()} `;
        updateCounter();
        clearInputError();
        focusInput();
    }

    function runAction(action) {
        if (state.sending) return;
        if (action.mode === "compose") {
            composeQuery(action.query);
            return;
        }
        sendMessage(action.query);
    }

    function createActionsBlock(actions, title) {
        if (actions.length === 0) return null;

        const section = document.createElement("div");
        section.className = "chatbot-response-actions";
        section.appendChild(
            createTextElement("span", "chatbot-response-actions__title", title)
        );

        const buttons = document.createElement("div");
        buttons.className = "chatbot-response-actions__buttons";
        actions.forEach((action) => {
            const button = createTextElement(
                "button",
                "chatbot-suggestion",
                action.label
            );
            button.type = "button";
            button.addEventListener("click", () => runAction(action));
            buttons.appendChild(button);
        });
        section.appendChild(buttons);
        return section;
    }

    function responseActions(response, items) {
        if (response.status === "needs_clarification") {
            const actions = clarificationActions(items);
            if (actions.length > 0) {
                return {
                    actions,
                    title: "Selecciona una coincidencia",
                };
            }
        }

        const suggestions = Array.isArray(response.suggestions)
            ? response.suggestions
                  .map(displayText)
                  .filter(Boolean)
                  .filter(sellerCanSeeSuggestion)
                  .slice(0, response.status === "needs_clarification" ? 5 : 6)
            : [];
        return {
            actions: suggestions.map((label) =>
                actionFromSuggestion(label, response)
            ),
            title: "Puedes preguntar",
        };
    }

    function appendAssistantResponse(response) {
        if (!isRecord(response) || typeof response.message !== "string") {
            throw new ChatbotRequestError(
                500,
                "El servidor devolvió una respuesta no válida."
            );
        }

        const text = response.message.trim() || "La consulta no produjo un mensaje.";
        const rendered = createMessage("assistant", text);
        const statusBadge = createResponseState(response.status);
        if (statusBadge) rendered.bubble.appendChild(statusBadge);

        const categories = responseCategories(response);
        const categoryOptions = createCategoryOptions(categories);
        if (categoryOptions) {
            rendered.bubble.appendChild(categoryOptions);
        } else {
            const items = responseItems(response);
            const results = createResults(response, items);
            if (results) rendered.bubble.appendChild(results);

            const actionGroup = responseActions(response, items);
            const actions = createActionsBlock(
                actionGroup.actions,
                actionGroup.title
            );
            if (actions) rendered.bubble.appendChild(actions);
        }

        insertBeforeTyping(rendered.message, {
            reveal: !state.reviewingHistory,
        });
    }

    function requestErrorMessage(error) {
        if (!(error instanceof ChatbotRequestError)) {
            return "Ocurrió un error inesperado al procesar la consulta.";
        }

        switch (error.status) {
            case 0:
                return "No fue posible conectar con el servidor. Revisa tu conexión e intenta nuevamente.";
            case 400:
                return error.message || "La consulta no es válida. Revísala e intenta nuevamente.";
            case 401:
                return "Tu sesión expiró o no está autenticada. Vuelve a iniciar sesión.";
            case 403:
                return error.message || "No tienes permiso para realizar esta consulta.";
            case 404:
                return error.message || "No se encontró el servicio solicitado.";
            case 409:
                return error.message || "La consulta no pudo completarse por un conflicto.";
            case 500:
                return "El servidor no pudo procesar la consulta. Intenta nuevamente más tarde.";
            default:
                return error.message || `No se pudo completar la consulta (${error.status}).`;
        }
    }

    function appendRequestError(error) {
        const rendered = createMessage("error", requestErrorMessage(error));

        if (error instanceof ChatbotRequestError && error.status === 401) {
            const loginLink = document.createElement("a");
            loginLink.className = "chatbot-login-link";
            loginLink.href = "/login";
            loginLink.append(
                createIcon("ti-login"),
                document.createTextNode("Volver al inicio de sesión")
            );
            rendered.bubble.appendChild(loginLink);
        }

        insertBeforeTyping(rendered.message, {
            reveal: !state.reviewingHistory,
        });
    }

    async function postMessage(message) {
        let response;
        try {
            response = await fetch(ENDPOINT, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    "Content-Type": "application/json",
                    Accept: "application/json",
                },
                body: JSON.stringify({ message }),
            });
        } catch (_error) {
            throw new ChatbotRequestError(
                0,
                "No fue posible conectar con el servidor."
            );
        }

        let payload = null;
        try {
            payload = await response.json();
        } catch (_error) {
            if (response.ok) {
                throw new ChatbotRequestError(
                    500,
                    "El servidor devolvió una respuesta no válida."
                );
            }
        }

        if (!response.ok) {
            const apiMessage =
                isRecord(payload) && typeof payload.error === "string"
                    ? payload.error.trim()
                    : "";
            throw new ChatbotRequestError(response.status, apiMessage);
        }

        if (!isRecord(payload)) {
            throw new ChatbotRequestError(
                500,
                "El servidor devolvió una respuesta no válida."
            );
        }
        return payload;
    }

    function setSending(sending) {
        state.sending = sending;
        elements.input.disabled = sending;
        elements.send.disabled = sending;
        elements.sendLabel.hidden = sending;
        elements.sendLoading.hidden = !sending;
        elements.typing.hidden = !sending;

        quickActions.forEach((button) => {
            button.disabled = sending;
        });
        document
            .querySelectorAll(
                ".chatbot-suggestion, .chatbot-category-option"
            )
            .forEach((button) => {
                button.disabled = sending;
            });

        if (sending) revealLogNode(elements.typing, "end");
    }

    function showInputError(message) {
        elements.inputError.textContent = message;
        elements.inputError.hidden = false;
        elements.input.setAttribute("aria-invalid", "true");
    }

    function clearInputError() {
        elements.inputError.textContent = "";
        elements.inputError.hidden = true;
        elements.input.removeAttribute("aria-invalid");
    }

    function updateCounter() {
        const length = elements.input.value.length;
        const threshold =
            length >= MAX_MESSAGE_LENGTH
                ? "limit"
                : length >= 450
                  ? "warning"
                  : "normal";

        elements.counter.textContent = `${length}/${MAX_MESSAGE_LENGTH}`;
        elements.counter.classList.toggle(
            "chatbot-counter--warning",
            length >= 450 && length < MAX_MESSAGE_LENGTH
        );
        elements.counter.classList.toggle(
            "chatbot-counter--limit",
            length >= MAX_MESSAGE_LENGTH
        );

        if (threshold === state.counterThreshold) return;
        state.counterThreshold = threshold;
        if (threshold === "warning") {
            elements.counterAlert.textContent =
                `Quedan ${MAX_MESSAGE_LENGTH - length} caracteres disponibles.`;
        } else if (threshold === "limit") {
            elements.counterAlert.textContent =
                "Has alcanzado el límite de 500 caracteres.";
        } else {
            elements.counterAlert.textContent = "";
        }
    }

    function validatedInput() {
        const rawMessage = elements.input.value;
        const message = rawMessage.trim();

        if (!message) {
            showInputError("Escribe una consulta antes de enviarla.");
            focusInput();
            return null;
        }
        if (rawMessage.length > MAX_MESSAGE_LENGTH) {
            showInputError(
                `La consulta no puede superar ${MAX_MESSAGE_LENGTH} caracteres.`
            );
            focusInput();
            return null;
        }
        clearInputError();
        return message;
    }

    async function sendMessage(message) {
        const normalizedMessage =
            typeof message === "string" ? message.trim() : "";
        if (
            state.sending ||
            !normalizedMessage ||
            normalizedMessage.length > MAX_MESSAGE_LENGTH
        ) {
            return;
        }

        appendUserMessage(normalizedMessage);
        elements.input.value = "";
        updateCounter();
        clearInputError();
        setSending(true);

        try {
            const response = await postMessage(normalizedMessage);
            appendAssistantResponse(response);
        } catch (error) {
            appendRequestError(error);
        } finally {
            setSending(false);
            focusInput();
        }
    }

    elements.form.addEventListener("submit", (event) => {
        event.preventDefault();
        const message = validatedInput();
        if (message) sendMessage(message);
    });

    elements.input.addEventListener("input", () => {
        updateCounter();
        if (elements.input.hasAttribute("aria-invalid")) clearInputError();
    });

    elements.input.addEventListener("keydown", (event) => {
        if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.isComposing
        ) {
            event.preventDefault();
            if (!state.sending) elements.form.requestSubmit();
        }
    });

    quickActions.forEach((button) => {
        button.addEventListener("click", () => {
            const prompt = button.dataset.chatPrompt || "";
            const mode = button.dataset.chatMode;
            if (!prompt || (mode !== "compose" && mode !== "send")) return;
            runAction({ label: prompt, query: prompt, mode });
        });
    });

    function markUserScrollIntent() {
        state.userScrollIntent = true;
        window.clearTimeout(state.scrollIntentTimer);
        state.scrollIntentTimer = window.setTimeout(() => {
            state.userScrollIntent = false;
        }, 220);
    }

    ["wheel", "touchmove", "pointerdown"].forEach((eventName) => {
        elements.log.addEventListener(eventName, markUserScrollIntent, {
            passive: true,
        });
    });

    elements.log.addEventListener("keydown", (event) => {
        if (
            [
                "ArrowUp",
                "ArrowDown",
                "PageUp",
                "PageDown",
                "Home",
                "End",
                " ",
            ].includes(event.key)
        ) {
            markUserScrollIntent();
        }
    });

    elements.log.addEventListener(
        "scroll",
        () => {
            if (!state.userScrollIntent) return;
            state.reviewingHistory = !isNearConversationEnd();
            markUserScrollIntent();
        },
        { passive: true }
    );

    updateCounter();
})();
