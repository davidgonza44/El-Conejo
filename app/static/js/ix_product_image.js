/* Shared edit-only image state. Reference mode never reaches an API. */
(function () {
    'use strict';
    window.createProductImageEditor = function ({ request, changed, canWrite = true } = {}) {
        const $ = id => document.getElementById(`pl-${id}`);
        const reference = new URLSearchParams(location.search).get('ref') === '1';
        let productId, currentUrl = '', objectUrl = '', pendingFile = null;
        let busy = false, generation = 0;
        function message(text = '', error = false) {
            $('image-message').textContent = text;
            $('image-message').hidden = !text;
            $('image-message').classList.toggle('is-error', error);
        }
        function render(url) {
            $('edit-image').hidden = !url;
            $('edit-placeholder').hidden = Boolean(url);
            if (url) $('edit-image').src = url;
            else $('edit-image').removeAttribute('src');
            $('image-delete').disabled = busy || !canWrite || !currentUrl;
        }
        function clearPending() {
            generation++;
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            objectUrl = '';
            pendingFile = null;
            $('image-file').value = '';
        }
        function setBusy(value) {
            busy = value;
            $('image-editor').setAttribute('aria-busy', String(value));
            $('image-change').disabled = value || !canWrite;
            $('image-file').disabled = value || !canWrite;
            $('image-delete').disabled = value || !canWrite || !currentUrl;
            $('action').disabled = value;
            $('dialog').querySelectorAll('[data-pl-close]').forEach(button => { button.disabled = value; });
        }
        async function selectFile() {
            const file = $('image-file').files[0];
            if (!file || busy || !canWrite) return;
            $('image-file').value = '';
            if (!/\.(jpe?g|png|webp)$/i.test(file.name) || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
                message('Selecciona una imagen JPG, PNG o WebP. No se permite SVG.', true);
                return;
            }
            if (!file.size || file.size > 2 * 1024 * 1024) {
                message('La imagen debe tener contenido y no superar 2 MB.', true);
                return;
            }
            const token = ++generation;
            const url = URL.createObjectURL(file);
            const probe = new Image();
            probe.src = url;
            try {
                await probe.decode();
                if (token !== generation) { URL.revokeObjectURL(url); return; }
                clearPending();
                pendingFile = file;
                objectUrl = url;
                render(url);
                message(reference ? 'Vista previa local. El guardado será una simulación.' : 'Vista previa local. La imagen se incluirá en la propuesta pendiente de aprobación.');
            } catch {
                URL.revokeObjectURL(url);
                if (token === generation) message('No se pudo leer la imagen. Selecciona otro archivo.', true);
            }
        }
        function acceptProduct(product) {
            currentUrl = product.image_url || '';
            clearPending();
            render(currentUrl);
            if (currentUrl) $('image').src = currentUrl;
            else $('image').removeAttribute('src');
            $('image').hidden = !currentUrl;
            changed?.(product);
        }
        async function upload(changes) {
            if (!pendingFile || reference || !canWrite) return;
            const body = new FormData();
            body.append('image', pendingFile);
            if (changes) body.append('changes', JSON.stringify(changes));
            try {
                const result = await request(`/api/products/${productId}/image`, { method: 'POST', body });
                acceptProduct(result.product);
                return result;
            } catch (error) {
                message(error.message || 'No se pudo enviar la propuesta. La imagen vigente se conserva.', true);
                throw error;
            }
        }
        async function remove() {
            if (busy || !canWrite || !currentUrl) return;
            if (!window.confirm(reference ? '¿Simular la eliminación de la imagen? No se guardarán cambios.' : '¿Proponer retirar la imagen? La imagen vigente se conserva hasta su aprobación.')) return;
            if (reference) {
                currentUrl = '';
                clearPending();
                render('');
                message('Eliminación simulada. No se modificó el producto.');
                $('image-change').focus();
                return;
            }
            setBusy(true);
            message('Eliminando imagen…');
            try {
                const result = await request(`/api/products/${productId}/image`, { method: 'DELETE' });
                acceptProduct(result.product);
                message(result.message || 'Propuesta enviada para revisión.');
            } catch {
                message('No se pudo eliminar la imagen. Se conserva la imagen actual; inténtalo de nuevo.', true);
            } finally {
                setBusy(false);
                $('image-change').focus();
            }
        }
        $('image-change').addEventListener('click', () => $('image-file').click());
        $('image-file').addEventListener('change', selectFile);
        $('image-delete').addEventListener('click', remove);
        $('edit-image').addEventListener('error', () => {
            render('');
            message('No se pudo mostrar la imagen. Puedes seleccionar otra.', true);
        });
        $('dialog').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
        $('dialog').addEventListener('close', clearPending);
        return {
            get busy() { return busy; },
            get hasPendingFile() { return Boolean(pendingFile); },
            setBusy, message, upload,
            reset(product) {
                productId = product.id;
                currentUrl = product.image_url || '';
                clearPending();
                setBusy(false);
                render(currentUrl);
                message();
            }
        };
    };
})();
