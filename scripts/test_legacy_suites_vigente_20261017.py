"""Aceptación vigente (posterior al 5 oct 2026) de las suites de compras anteriores al cambio de contrato.

Las suites originales no se modifican y sus evidencias históricas se conservan. Este ejecutor corre su
código tal cual con un único adaptador de fixtures, declarado aquí:

  Antes, las pruebas creaban órdenes con POST /api/purchases sin solicitud. Ahora toda orden nace de una
  solicitud de reposición en proceso. El adaptador convierte ese cuerpo directo en una solicitud interna
  ya en proceso, con los mismos productos y cantidades, y luego llama a la creación real. Exige el mismo
  permiso antes de escribir y revierte la solicitud si la orden falla.

Los casos que verifican el contrato anterior (duplicar órdenes, crear sin solicitud, etiquetas antiguas)
se declaran SUPERSEDED con el motivo y la prueba vigente que los reemplaza. Cualquier otro fallo es real.
Los fallos que ya existían en la línea base sin el parche se declaran PREEXISTING con su evidencia.

SQLite en memoria o temporal, como cada suite original. No abre MySQL, el puerto 5001 ni la base de tesis.
Uso: python -B scripts/test_legacy_suites_vigente_20261017.py [modulo ...]
"""
import contextlib
import io
import runpy
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import approval_test_support  # noqa: E402,F401  (fija sys.path y desactiva dotenv)

from flask_login import current_user  # noqa: E402

from app.extensions import db  # noqa: E402
from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine  # noqa: E402
from app.services import purchase_service  # noqa: E402

REAL_CREATE = purchase_service.create_order
ADAPTER_NOTE = ("Adaptador de pruebas posterior al 5 oct 2026: solicitud en proceso creada para un cuerpo "
                "directo del contrato anterior.")

MODULES = ("test_purchase_approval", "test_purchase_csrf", "test_purchase_currency", "test_purchase_quote_selection",
           "test_operational_separation", "test_tutor_cycle", "test_replenishment_filters", "test_purchase_unavailable",
           "test_replenishment_supplier_coverage")
SCRIPTS = ("test_cost_visibility",)

PARTIAL = ("Recibe una parte sin confirmar el cierre ni indicar el motivo; ahora la recepción parcial exige ambos. "
           "Prueba vigente: test_purchase_tutor_20261005.ReceptionTests.test_partial_requires_reason")
DUPLICATE = ("Espera que duplicar una orden devuelva 201; las órdenes ya no se duplican. "
             "Prueba vigente: test_purchase_tutor_20261005.DirectPathTests.test_direct_order_and_duplicate_denied")
LINKED_EDIT = ("Cambia productos o cotización con PATCH en un borrador que, por el adaptador, nace de una solicitud: "
               "ahora es 409 y no 400; la moneda y los importes se conservan igual. Pruebas vigentes: "
               "test_purchase_tutor_20261005.QuoteRevalidationTests (renovación, moneda y cotización inválida)")
SUPERSEDED = {
    "test_purchase_approval.ApprovalTests.test_approval_stock_supply_and_receipt": PARTIAL,
    "test_purchase_approval.ApprovalTests.test_edit_cancel_duplicate_and_kpis": DUPLICATE,
    "test_purchase_csrf.PurchaseCsrf.test_valid_csrf_quote_cancel_patch_duplicate_and_decisions": DUPLICATE,
    "test_purchase_csrf.PurchaseCsrf.test_valid_csrf_closure_reject_approve_and_receive": PARTIAL,
    "test_purchase_currency.PurchaseCurrency.test_pending_edit_cannot_relabel_existing_prices_using_another_currency":
        LINKED_EDIT,
    "test_purchase_quote_selection.PurchaseQuoteTests.test_invalidated_after_selection_rejected_on_create_and_edit":
        LINKED_EDIT,
    "test_tutor_cycle.TutorCycleTests.test_partial_receipt_closes_and_a_new_need_creates_another_order": PARTIAL,
    "test_purchase_unavailable.UnavailableTests.test_partial_receipt_is_never_unavailable": PARTIAL,
    "test_purchase_unavailable.UnavailableTests.test_receipt_winner_leaves_no_undecidable_pending_closure": PARTIAL,
    "test_replenishment_filters.ReplenishmentFilterTests.test_fulfilled_badge_and_linked_order_current_status":
        ("Busca la etiqueta anterior «Pendiente de aprobación»; ahora es «Borrador pendiente de aprobación». "
         "Prueba vigente: test_purchase_tutor_20261005.DraftAndImmutabilityTests.test_draft_print_is_not_final"),
}
SCRIPT_SUPERSEDED = {"test_cost_visibility": {"una recepción cierra y la segunda se rechaza": PARTIAL}}
_PRODUCT_CURRENCY = ("Falla igual en la línea base sin el parche (17 oct 2026, registro baseline_tests): la API de "
                     "productos exige moneda de precio que la fixture no envía. Ajeno a compras.")
_UTC_DAY = ("Ventana horaria: la fixture fecha la cotización con datetime.utcnow().date(); entre las 20:00 y las 24:00 "
            "de Caracas esa fecha es «mañana» y la cotización no está vigente. Falla igual sin el parche (17 oct 2026, "
            "00:41 UTC); fuera de esa ventana pasa y aparece en «Declarados que ahora pasan».")
PREEXISTING = {
    "test_tutor_cycle.TutorCycleTests.test_product_and_supplier_need_approval_and_oc_has_no_inline_supplier":
        _PRODUCT_CURRENCY,
    "test_tutor_cycle.TutorCycleTests.test_price_change_and_unavailability_need_admin_and_then_a_new_order": _UTC_DAY,
    **{f"test_replenishment_supplier_coverage.SupplierCoverageTests.{name}": _PRODUCT_CURRENCY for name in (
        "test_manual_supplier_id_is_rejected_and_cannot_use_the_direct_api",
        "test_multiple_products_require_the_intersection_and_preserve_quantities",
        "test_no_association_for_the_single_product_cannot_be_submitted",
        "test_omitting_the_unassociated_product_does_not_bypass_sr_coverage",
        "test_removed_association_after_render_is_revalidated_and_empty_state_is_clear",
        "test_single_product_uses_active_association_without_requiring_preferred",
        "test_supplier_invalidated_after_render_is_revalidated")},
}


def _positive(value):
    return not isinstance(value, bool) and isinstance(value, int) and value > 0 or (
        isinstance(value, str) and value.isdigit() and int(value) > 0)


def _request_items(items):
    """(product, quantity) when the old body is well formed; None lets the real path answer."""
    if not isinstance(items, list) or not items:
        return None
    from app.models import Product

    result, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not _positive(item.get("product_id")) or not _positive(item.get("quantity")):
            return None
        product = db.session.get(Product, int(item["product_id"]))
        if product is None or product.id in seen:
            return None
        seen.add(product.id)
        result.append((product, int(item["quantity"])))
    return result


def create_through_request(data, commit=True):
    if not isinstance(data, dict) or data.get("replenishment_request_id"):
        return REAL_CREATE(data, commit=commit)
    purchase_service.require_operational_permission("purchases:write")
    items = _request_items(data.get("items"))
    if items is None:
        return REAL_CREATE(data, commit=commit)
    row = ReplenishmentRequest(created_by_user_id=current_user.id, status="processing", origin="manual",
                               processed_by_user_id=current_user.id, processed_at=datetime.utcnow(), notes=ADAPTER_NOTE)
    row.lines = [ReplenishmentRequestLine(product_id=product.id, product_code=product.code, product_name=product.name,
                                          unit=product.unit, quantity=quantity, stock_on_hand=int(product.current_stock))
                 for product, quantity in items]
    db.session.add(row)
    db.session.flush()
    row.request_number = f"SR-{row.id:06d}"
    try:
        return REAL_CREATE({**data, "replenishment_request_id": row.id}, commit=commit)
    except Exception:
        db.session.rollback()
        raise


class Result(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.declared = []

    def _declared(self, test):
        key = test.id().split(" ")[0]
        for table, kind in ((SUPERSEDED, "SUPERSEDED"), (PREEXISTING, "PREEXISTING")):
            if key in table:
                return kind, table[key]
        return None

    def addFailure(self, test, err):
        self._route(test, err, super().addFailure)

    def addError(self, test, err):
        self._route(test, err, super().addError)

    def addSubTest(self, test, subtest, err):
        if err is not None and self._declared(test):
            self.declared.append((test.id(), *self._declared(test)))
            return
        super().addSubTest(test, subtest, err)

    def _route(self, test, err, fallback):
        declared = self._declared(test)
        if declared:
            self.declared.append((test.id(), *declared))
        else:
            fallback(test, err)


def run_scripts(names, declared):
    """Suites de estilo script (check + RESULTADO): solo se aceptan los [FALLO] declarados por nombre."""
    failed = []
    for name in names:
        print(f"\n== {name} (script, con el mismo adaptador)")
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                runpy.run_path(str(Path(__file__).resolve().parent / f"{name}.py"), run_name="__main__")
        except SystemExit:
            pass
        print(output.getvalue(), end="")
        allowed = SCRIPT_SUPERSEDED.get(name, {})
        for line in output.getvalue().splitlines():
            if not line.startswith("[FALLO] "):
                continue
            label = line.removeprefix("[FALLO] ").split(" — ")[0]
            if label in allowed:
                declared.append((f"{name}: {label}", "SUPERSEDED", allowed[label]))
            elif name not in failed:
                failed.append(name)
        if "RESULTADO:" not in output.getvalue() and name not in failed:
            failed.append(name)
    return failed


def run(modules, scripts=SCRIPTS):
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite(loader.loadTestsFromName(name) for name in modules)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=Result)
    with patch.object(purchase_service, "create_order", create_through_request):
        result = runner.run(suite)
        failed_scripts = run_scripts(scripts, result.declared)
    print("\nCasos declarados (no cuentan como aprobados ni como fallos nuevos):")
    for test_id, kind, reason in sorted(set(result.declared)):
        print(f"  {kind}: {test_id}\n    {reason}")
    unexpected = [test for test in SUPERSEDED.keys() | PREEXISTING.keys() if test.split(".")[0] in modules
                  and not any(test == seen.split(" ")[0] for seen, _kind, _reason in result.declared)]
    if unexpected:
        print("\nDeclarados que ahora pasan (revisar la declaración):", *sorted(unexpected), sep="\n  ")
    print(f"\nRESULTADO: {result.testsRun} casos; fallos reales {len(result.failures)}, errores reales "
          f"{len(result.errors)}; declarados {len(set(result.declared))}; scripts con fallos: "
          f"{', '.join(failed_scripts) or 'ninguno'}.")
    return result.wasSuccessful() and not failed_scripts


if __name__ == "__main__":
    selected = sys.argv[1:]
    sys.exit(0 if run([name for name in selected if name not in SCRIPTS] if selected else MODULES,
                      [name for name in selected if name in SCRIPTS] if selected else SCRIPTS) else 1)
