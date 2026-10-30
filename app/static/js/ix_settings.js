/* Non-persistent preview interactions. No network, storage, or training calls. */
(() => {
    "use strict";
    const toast = document.querySelector(".st-toast");
    const userSearch = document.querySelector('.st-user-search input');
    const roleFilter = document.querySelector('.st-user-filter select');
    if (userSearch && roleFilter) {
        const rows = [...document.querySelectorAll('[data-user-row]')];
        [...new Set(rows.map(row => row.dataset.userRole))].forEach(role => {
            roleFilter.add(new Option(role, role));
        });
        const filterUsers = () => {
            const query = userSearch.value.toLocaleLowerCase('es');
            let count = 0;
            rows.forEach(row => {
                row.hidden = !row.textContent.toLocaleLowerCase('es').includes(query)
                    || (roleFilter.selectedIndex > 0 && row.dataset.userRole !== roleFilter.value);
                if (!row.hidden) count += 1;
            });
            document.querySelector('[data-user-count]').textContent = count
                ? `Mostrando 1 a ${count} de ${rows.length} usuarios`
                : 'No se encontraron usuarios';
        };
        userSearch.addEventListener('input', filterUsers);
        roleFilter.addEventListener('change', filterUsers);
    }
    const selectAll = document.querySelector('[data-permissions-all]');
    if (selectAll) {
        const permissions = [...document.querySelectorAll('[data-role-permission]')];
        const updateCount = () => {
            const count = permissions.filter(input => input.checked).length;
            selectAll.checked = count === permissions.length;
            selectAll.indeterminate = count > 0 && count < permissions.length;
            document.querySelector('[data-permission-count]').textContent = `${count} de ${permissions.length} permisos asignados`;
        };
        selectAll.addEventListener('change', () => {
            permissions.forEach(input => { input.checked = selectAll.checked; });
            updateCount();
        });
        permissions.forEach(input => input.addEventListener('change', updateCount));
    }
    document.querySelectorAll("[data-preview-action]").forEach(button => {
        button.addEventListener("click", () => {
            toast.hidden = false;
            toast.textContent = document.documentElement.dataset.referenceMode === "1"
                ? "Vista previa: esta acción no guarda datos ni ejecuta procesos."
                : "Acción no disponible: no existe una configuración conectada para guardar o modificar.";
        });
    });
    if (document.documentElement.dataset.referenceMode !== "1") {
        document.querySelectorAll(".st-actions button").forEach(button => {
            button.disabled = true;
            button.title = "Configuración no conectada: acción no disponible";
        });
    }
})();
