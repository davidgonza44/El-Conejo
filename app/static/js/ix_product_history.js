/* Period bar state. Never filters the catalog; publishes `ix-product-history-change` for the live KPI loader. */
(function () {
    'use strict';
    const bar = document.getElementById('ix-product-history');
    if (!bar) return;
    const get = (name) => document.getElementById(`ix-history-${name}`);
    const presets = [...bar.querySelectorAll('[data-history-preset]')];
    const form = get('custom');
    const startInput = get('start');
    const endInput = get('end');
    const calendarDate = (value) => new Date(`${value}T12:00:00`);
    const iso = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
    const format = (value) => calendarDate(value).toLocaleDateString('es', {
        day: '2-digit', month: 'short', year: 'numeric'
    }).replaceAll(' de ', ' ').replace(/sept\.?/, 'sep.').replace(/\b(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)\b\.?/g, '$1.');
    let selected = '30days';
    let range;

    function presetRange(key) {
        // Reference mode has a fixed clock; live uses the server's America/Caracas commercial day.
        const fixed = bar.dataset.referenceDate || bar.dataset.commercialToday;
        const today = fixed ? calendarDate(fixed) : new Date();
        const end = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 12);
        const start = new Date(end);
        if (key === '7days' || key === '30days') start.setDate(start.getDate() - (key === '7days' ? 6 : 29));
        if (key === 'month' || key === 'previous-month') {
            start.setDate(1);
            if (key === 'previous-month') start.setMonth(start.getMonth() - 1);
            end.setFullYear(start.getFullYear(), start.getMonth() + 1, 0);
        }
        return [iso(start), iso(end)];
    }

    function render() {
        get('range-text').textContent = `${format(range[0])} — ${format(range[1])}`;
        presets.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.historyPreset === selected)));
        bar.dataset.preset = selected;
        [bar.dataset.start, bar.dataset.end] = range;
        publish();
    }

    function publish() {
        bar.dispatchEvent(new CustomEvent('ix-product-history-change', {
            bubbles: true,
            detail: { start: range[0], end: range[1], compare: get('compare').value, preset: selected }
        }));
    }

    function closeCustom() {
        form.hidden = true;
        get('range').setAttribute('aria-expanded', 'false');
        presets.at(-1).setAttribute('aria-expanded', 'false');
    }

    function openCustom() {
        [startInput.value, endInput.value] = range;
        get('error').hidden = true;
        endInput.setCustomValidity('');
        startInput.removeAttribute('aria-invalid');
        endInput.removeAttribute('aria-invalid');
        form.hidden = false;
        get('range').setAttribute('aria-expanded', 'true');
        presets.at(-1).setAttribute('aria-expanded', 'true');
        startInput.focus();
    }

    function validate() {
        const invalid = Boolean(startInput.value && endInput.value && startInput.value > endInput.value);
        get('error').hidden = !invalid;
        endInput.setCustomValidity(invalid ? get('error').textContent : '');
        [startInput, endInput].forEach((input) => input.setAttribute('aria-invalid', String(invalid)));
        return !invalid;
    }

    presets.forEach((button) => button.addEventListener('click', () => {
        const key = button.dataset.historyPreset;
        if (key === 'custom') { openCustom(); return; }
        selected = key;
        range = presetRange(key);
        closeCustom();
        render();
    }));
    get('range').addEventListener('click', openCustom);
    get('cancel').addEventListener('click', () => { closeCustom(); get('range').focus(); });
    [startInput, endInput].forEach((input) => input.addEventListener('input', validate));
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (!validate() || !form.reportValidity()) return;
        range = [startInput.value, endInput.value];
        selected = 'custom';
        render();
        closeCustom();
        presets.at(-1).focus();
    });
    bar.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !form.hidden) { closeCustom(); get('range').focus(); }
    });
    get('compare').addEventListener('change', () => { bar.dataset.comparison = get('compare').value; publish(); });
    bar.dataset.comparison = get('compare').value;
    range = presetRange(selected);
    render();
})();
