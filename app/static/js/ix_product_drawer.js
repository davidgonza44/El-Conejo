/* Reference-only interaction: no requests, persistence, stock or uploads. */
(function () {
    'use strict';
    const drawer = document.getElementById('np-drawer');
    const trigger = [...document.querySelectorAll('.iv-prod-toolbar button')]
        .find(button => button.textContent.trim() === 'Nuevo producto');
    if (!drawer || !trigger || new URLSearchParams(location.search).get('ref') !== '1') return;
    const form = document.getElementById('np-form');
    const feedback = document.getElementById('np-feedback');
    const image = document.getElementById('np-preview-image');
    const placeholder = document.getElementById('np-image-placeholder');
    const fileInput = document.getElementById('np-image');
    const imageError = document.getElementById('np-image-error');
    let objectUrl = null;
    let oldOverflow = '';
    let oldPadding = '';
    let feedbackTimer;
    trigger.setAttribute('aria-haspopup', 'dialog');
    trigger.setAttribute('aria-controls', drawer.id);

    function clearPreview() {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = null;
        image.removeAttribute('src');
        image.hidden = true;
        placeholder.hidden = false;
    }
    function validate(field) {
        const invalid = !field.validity.valid || (field.required && !field.value.trim());
        field.setAttribute('aria-invalid', String(invalid));
        const error = document.getElementById(`${field.id}-error`);
        error.textContent = field.validity.rangeUnderflow || field.validity.stepMismatch
            ? 'Introduce un número válido igual o mayor que 0.' : 'Completa este campo.';
        error.hidden = !invalid;
        return !invalid;
    }
    trigger.addEventListener('click', () => {
        if (drawer.open) return;
        document.dispatchEvent(new CustomEvent('product:before-layer-open', {detail: 'np'}));
        document.querySelectorAll('dialog[open]').forEach(layer => layer.close());
        document.querySelectorAll('[popover]:popover-open').forEach(layer => layer.hidePopover());
        document.querySelectorAll('.dropdown-toggle[aria-expanded="true"]').forEach(button => button.click());
        feedback.hidden = true;
        form.reset();
        clearPreview();
        imageError.hidden = true;
        form.querySelectorAll('[aria-invalid]').forEach(field => field.removeAttribute('aria-invalid'));
        form.querySelectorAll('.np-error').forEach(error => { error.hidden = true; });
        document.getElementById('np-counter').textContent = '0/500';
        document.querySelector('.np-status').dataset.status = 'Disponible';
        oldOverflow = document.body.style.overflow;
        oldPadding = document.body.style.paddingRight;
        const gutter = innerWidth - document.documentElement.clientWidth;
        if (gutter) document.body.style.paddingRight = `${parseFloat(getComputedStyle(document.body).paddingRight) + gutter}px`;
        document.body.style.overflow = 'hidden';
        drawer.showModal();
    });
    drawer.querySelectorAll('[data-np-close]').forEach(button => button.addEventListener('click', () => drawer.close()));
    document.addEventListener('product:before-layer-open', event => {
        if (event.detail === 'np' || !drawer.open) return;
        drawer.close();
        document.body.style.overflow = oldOverflow;
        document.body.style.paddingRight = oldPadding;
    });
    drawer.addEventListener('close', () => {
        clearPreview();
        fileInput.value = '';
        if (document.querySelector('dialog[open]')) return;
        document.body.style.overflow = oldOverflow;
        document.body.style.paddingRight = oldPadding;
        trigger.focus({preventScroll: true});
    });
    form.addEventListener('submit', event => event.preventDefault());
    form.querySelectorAll('[required]').forEach(field => {
        field.addEventListener('input', () => {
            if (field.hasAttribute('aria-invalid')) validate(field);
        });
        field.addEventListener('change', () => {
            if (field.hasAttribute('aria-invalid')) validate(field);
        });
    });
    document.getElementById('np-status').addEventListener('change', event => {
        event.target.parentElement.dataset.status = event.target.value;
    });
    document.getElementById('np-description').addEventListener('input', event => {
        document.getElementById('np-counter').textContent = `${event.target.value.length}/500`;
    });
    fileInput.addEventListener('change', () => {
        clearPreview();
        imageError.hidden = true;
        const file = fileInput.files[0];
        if (!file) return;
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
            imageError.textContent = 'Selecciona PNG, JPG o WEBP de hasta 5 MB.';
            imageError.hidden = false;
            fileInput.value = '';
            return;
        }
        objectUrl = URL.createObjectURL(file);
        image.src = objectUrl;
        image.hidden = false;
        placeholder.hidden = true;
    });
    image.addEventListener('error', () => {
        clearPreview();
        fileInput.value = '';
        imageError.textContent = 'No se pudo visualizar esta imagen. Selecciona otra.';
        imageError.hidden = false;
    });
    document.getElementById('np-save').addEventListener('click', () => {
        const results = [...form.querySelectorAll('[required]')].map(validate);
        if (results.includes(false)) {
            form.querySelector('[aria-invalid="true"]').focus();
            return;
        }
        drawer.close();
        feedback.textContent = 'Simulación completada. No se creó ningún producto ni se guardaron datos.';
        feedback.hidden = false;
        clearTimeout(feedbackTimer);
        feedbackTimer = setTimeout(() => { feedback.hidden = true; }, 7000);
    });
})();
