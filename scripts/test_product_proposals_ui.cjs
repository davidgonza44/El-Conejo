/* DOM unit regression: runs actual proposal UI code without a browser or server. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
    constructor(tag) { this.tag = tag; this.hidden = false; this.children = []; this.handlers = {}; this.value = ''; }
    append(...items) { this.children.push(...items); }
    replaceChildren(...items) { this.children = items; }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    reportValidity() { return !this.required || Boolean(this.value.trim()); }
}
const elements = new Map();
const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); },
    createElement(tag) { return new Element(tag); }
};
const window = {};
vm.runInNewContext(fs.readFileSync('app/static/js/ix_product_proposals.js', 'utf8'), {window, document});
const normal = {id: 1, name: 'Actual', minimum_stock: 3, approval_status: 'approved', pending_change: null};
assert.equal(window.canProposeProductChange(normal, false), false, 'Admin sin permiso: botón ausente');
assert.equal(window.canProposeProductChange(normal, true), true, 'Asistente: puede proponer');
assert.equal(window.canProposeProductChange({...normal, approval_status: 'pending'}, true), false);
assert.equal(window.canProposeProductChange({...normal, has_pending_change: true}, true), false);
const change = {id: 7, status: 'pending', changes: {name: 'Nuevo', minimum_stock: 8}, previous_values: {name: 'Anterior', minimum_stock: 3}, proposed_by_user_id: 2, created_at: '2026-10-07T16:30:00'};
const pending = {...normal, pending_change: change, last_change: change, can_review_change: true};
assert.equal(window.canProposeProductChange(pending, true), false, 'No se sobrescribe una propuesta pendiente');
let calls = [], reviews = [];
const review = window.createProductProposalReview({
    canApprove: true, categoryName: () => undefined,
    request: async (path, options) => {
        calls.push({path, body: JSON.parse(options.body)});
        return {...normal, last_change: {...change, status: path.endsWith('/approve') ? 'approved' : 'rejected', rejection_reason: 'Motivo válido'}};
    },
    reviewed: async product => reviews.push(product)
});
const $ = id => document.getElementById(`pl-${id}`);
(async () => {
    review.render(normal);
    assert.equal($('review').hidden, true);
    assert.equal($('review-actions').hidden, true, 'Admin sin propuesta: no hay decisiones');
    review.render(pending);
    assert.equal($('review-actions').hidden, false);
    const table = $('review-comparison').children[0];
    assert.equal(table.children[1].children.length, 2, 'Compara exactamente los dos campos propuestos');
    assert.equal(table.children[1].children[0].children[1].textContent, 'Actual');
    assert.equal(table.children[1].children[0].children[2].textContent, 'Nuevo');
    assert.equal($('review-metadata').children[1].textContent, 'Usuario #2');
    assert.notEqual($('review-metadata').children[3].textContent, 'No registrada');
    await $('approve').handlers.click();
    assert.deepEqual(calls[0], {path: '/api/products/1/approve', body: {proposal_id: 7}});
    assert.equal(reviews[0].last_change.status, 'approved');
    assert.equal($('review-actions').hidden, true);
    review.render(pending);
    await $('reject').handlers.click();
    assert.equal(calls.length, 1, 'No rechaza sin motivo');
    $('rejection-reason').value = 'Motivo válido';
    await $('reject').handlers.click();
    assert.deepEqual(calls[1], {path: '/api/products/1/reject', body: {proposal_id: 7, reason: 'Motivo válido'}});
    assert.equal(reviews[1].last_change.status, 'rejected');
    review.render({...pending, can_review_change: false});
    assert.equal($('review-actions').hidden, true, 'Proponente: no ve decisiones sobre su propio cambio');
    await $('approve').handlers.click();
    assert.equal(calls.length, 2);
    review.render({...normal, approval_status: 'pending', proposed_by_user_id: 2,
        proposal_author: 'Proponente sintético', created_at: '2026-10-07T16:30:00', can_review_creation: true});
    assert.equal($('review-actions').hidden, false, 'Alta nueva: permite revisión');
    const creation = $('review-comparison').children[0].children[1];
    assert.equal(creation.children[0].children[1].textContent, 'Sin alta aprobada');
    assert.equal(creation.children[0].children[2].textContent, 'Actual');
    assert.equal($('review-metadata').children[1].textContent, 'Proponente sintético');
    const assistant = window.createProductProposalReview({canApprove: false, categoryName: () => undefined});
    assistant.render(pending);
    assert.equal($('review-actions').hidden, true, 'Asistente: solo estado pendiente');
    console.log('PASS UI: permisos, ausencia del botón Admin, comparación exacta, aprobación, rechazo con motivo y autoaprobación bloqueada.');
})().catch(error => { console.error(error); process.exitCode = 1; });
