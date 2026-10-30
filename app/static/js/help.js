/* Guía de uso: búsqueda dentro de la guía y sección activa en el índice. */
(() => {
    'use strict';
    const sections = [...document.querySelectorAll('[data-hp-section]')];
    const links = new Map([...document.querySelectorAll('[data-hp-link]')].map((link) => [link.dataset.hpLink, link]));
    const empty = document.getElementById('hp-empty');
    const normalize = (text) => text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

    document.getElementById('hp-filter')?.addEventListener('input', (event) => {
        const query = normalize(event.target.value.trim());
        let visible = 0;
        sections.forEach((section) => {
            const match = !query || normalize(section.textContent).includes(query);
            section.hidden = !match;
            links.get(section.id)?.closest('li').toggleAttribute('hidden', !match);
            if (match) visible += 1;
        });
        empty.hidden = visible > 0;
    });

    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver((entries) => {
            entries.filter((entry) => entry.isIntersecting).forEach((entry) => {
                links.forEach((link) => link.removeAttribute('aria-current'));
                links.get(entry.target.id)?.setAttribute('aria-current', 'true');
            });
        }, { rootMargin: '-15% 0px -70% 0px' });
        sections.forEach((section) => observer.observe(section));
    }
})();
