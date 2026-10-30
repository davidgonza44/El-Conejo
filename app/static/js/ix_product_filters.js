/* Estado de los filtros del catálogo compartido por la URL y el listado. */
(function () {
    'use strict';
    const keys = {
        search: 'search', categoryId: 'category_id', supplierId: 'supplier_id',
        status: 'status', approvalStatus: 'approval_status', priceMin: 'price_min',
        priceMax: 'price_max', stockMin: 'stock_min', stockMax: 'stock_max',
        dateMin: 'updated_from', dateMax: 'updated_to', sort: 'sort',
        page: 'page', perPage: 'per_page'
    };
    const defaults = { sort: 'name-asc', page: 1, perPage: 8 };
    function restore(state, search) {
        const params = new URLSearchParams(search);
        Object.entries(keys).forEach(([key, param]) => {
            if (!params.has(param)) return;
            const raw = params.get(param).trim();
            if (key === 'page' || key === 'perPage') {
                const number = Number(raw);
                state[key] = Number.isSafeInteger(number) && number > 0 ? number : defaults[key];
            } else state[key] = raw;
        });
        if (!['', 'all', 'pending', 'approved', 'rejected'].includes(state.approvalStatus)) state.approvalStatus = '';
        if (state.approvalStatus === 'all') state.approvalStatus = '';
    }
    function url(state, current) {
        const target = new URL(current);
        Object.entries(keys).forEach(([key, param]) => {
            const value = state[key];
            if (value === '' || value == null || value === defaults[key]) target.searchParams.delete(param);
            else target.searchParams.set(param, String(value));
        });
        return target.pathname + target.search + target.hash;
    }
    window.productFilters = { restore, url };
})();
