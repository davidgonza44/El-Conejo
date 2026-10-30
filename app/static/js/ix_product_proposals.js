/* Comparación y decisión de propuestas. Los permisos efectivos vienen del servidor. */
(function () {
    'use strict';
    const labels = {
        code: 'Código', name: 'Nombre', category_id: 'Categoría', unit: 'Unidad',
        description: 'Descripción', brand: 'Marca', sale_price: 'Precio de venta',
        purchase_price: 'Costo de referencia', minimum_stock: 'Stock mínimo',
        sale_price_currency: 'Moneda de venta', purchase_price_currency: 'Moneda de referencia',
        current_stock: 'Stock inicial', is_active: 'Estado del catálogo', image_url: 'Imagen'
    };
    window.canProposeProductChange = (product, canWrite) => Boolean(
        canWrite && product.approval_status === 'approved' && !product.pending_change && !product.has_pending_change);
    window.createProductProposalReview = function ({ request, reviewed, canApprove, categoryName }) {
        const $ = id => document.getElementById(`pl-${id}`);
        let product = null, busy = false;
        function node(tag, text) {
            const element = document.createElement(tag);
            if (text !== undefined) element.textContent = text;
            return element;
        }
        function value(field, raw, values) {
            if (['sale_price', 'purchase_price'].includes(field) && raw != null) {
                const symbol = {USD: '$', VES: 'Bs.'}[values[`${field}_currency`]] || 'Moneda no registrada ·';
                return `${symbol} ${Number(raw).toFixed(2)}`;
            }
            if (field === 'is_active') return raw ? 'Activo' : 'Inactivo';
            if (field === 'category_id') return categoryName(raw) || `Categoría #${raw}`;
            return raw === null || raw === '' ? '—' : String(raw);
        }
        function comparison(proposal, creation) {
            $('review-comparison').replaceChildren();
            if (!proposal && !creation) return;
            const table = node('table');
            table.className = 'pl-table';
            const head = node('thead'), headings = node('tr'), body = node('tbody');
            ['Campo', 'Valor actual', 'Valor propuesto'].forEach(label => {
                const th = node('th', label);
                th.scope = 'col';
                headings.append(th);
            });
            const changes = proposal ? proposal.changes : Object.fromEntries(
                Object.keys(labels).filter(field => Object.hasOwn(product, field)).map(field => [field, product[field]]));
            Object.entries(changes).forEach(([field, proposed]) => {
                const row = node('tr'), label = node('th', labels[field] || field);
                label.scope = 'row';
                row.append(label, node('td', creation ? 'Sin alta aprobada' : value(field, product[field], product)),
                    node('td', value(field, proposed, changes)));
                body.append(row);
            });
            head.append(headings);
            table.append(head, body);
            $('review-comparison').append(table);
        }
        function metadata(proposal) {
            const authorId = proposal ? proposal.proposed_by_user_id : product.proposed_by_user_id;
            const date = proposal ? proposal.created_at : product.created_at;
            const when = date ? new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(date) ? date : `${date}Z`) : null;
            $('review-metadata').replaceChildren(
                node('dt', 'Proponente'), node('dd', product.proposal_author || (authorId ? `Usuario #${authorId}` : 'No registrado')),
                node('dt', 'Fecha de propuesta'), node('dd', when && !Number.isNaN(when.getTime())
                    ? when.toLocaleString('es-VE', { timeZone: 'America/Caracas' }) : 'No registrada'));
        }
        function render(selected) {
            product = selected;
            const proposal = product.pending_change || product.last_change;
            const creation = product.approval_status === 'pending';
            const pending = Boolean(product.pending_change);
            $('review').hidden = !proposal && !creation && product.approval_status !== 'rejected';
            $('review-title').textContent = proposal ? 'Propuesta de modificación' : 'Propuesta de producto nuevo';
            const states = { pending: 'Pendiente de revisión', approved: 'Aprobada', rejected: 'Rechazada' };
            const state = proposal ? proposal.status : product.approval_status;
            const reason = proposal?.rejection_reason || product.rejection_reason;
            $('review-note').textContent = `${states[state] || ''}${pending ? '. Los datos vigentes se conservan hasta la aprobación.' : ''}${reason ? `. Motivo: ${reason}` : ''}`;
            metadata(proposal);
            $('review-actions').hidden = !canApprove || !(pending ? product.can_review_change : creation && product.can_review_creation);
            $('review-feedback').hidden = true;
            $('rejection-reason').value = '';
            comparison(proposal, creation);
        }
        async function decide(approve) {
            if (busy || !product || !canApprove || $('review-actions').hidden) return;
            const reason = $('rejection-reason');
            reason.required = !approve;
            if (!approve && (!reason.reportValidity() || !reason.value.trim())) return;
            const body = approve ? {} : { reason: reason.value.trim() };
            if (product.pending_change) body.proposal_id = product.pending_change.id;
            busy = true;
            $('approve').disabled = $('reject').disabled = true;
            try {
                const result = await request(`/api/products/${product.id}/${approve ? 'approve' : 'reject'}`, {
                    method: 'POST', body: JSON.stringify(body)
                });
                render(result);
                await reviewed(result);
            } catch (error) {
                $('review-feedback').textContent = error.message;
                $('review-feedback').hidden = false;
            } finally {
                busy = false;
                $('approve').disabled = $('reject').disabled = false;
            }
        }
        $('approve').addEventListener('click', () => decide(true));
        $('reject').addEventListener('click', () => decide(false));
        return { render };
    };
})();
