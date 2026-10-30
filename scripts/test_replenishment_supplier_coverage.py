"""Cobertura producto–proveedor al cotizar una SR; SQLite desechable, sin MySQL."""
import re
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import Product, StockMovement
from app.models.purchase import ProductSupplier, PurchaseOrder, Supplier
from app.models.supplier_quote import SupplierQuote

NOTICE = "No existe un proveedor aprobado y activo asociado a todos los productos de esta solicitud."


def supplier_options(html):
    select = re.search(r'<select\b[^>]*name="supplier_id"[^>]*>(.*?)</select>', html, re.S)
    return {int(value) for value in re.findall(r'<option value="(\d+)"', select.group(1))}


class SupplierCoverageTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.today = datetime.now(ZoneInfo("America/Caracas")).date().isoformat()
        self.extra = Product(code="TEST-02", name="Segundo producto", category_id=self.product.category_id,
                             current_stock=4, minimum_stock=0, purchase_price=2, sale_price=3,
                             is_active=True, approval_status="approved")
        self.providers = {key: Supplier(name=key, tax_id="COVERAGE-" + key)
                          for key in ("partial", "inactive_link", "pending", "rejected", "inactive", "unlinked")}
        db.session.add_all([self.extra, *self.providers.values()])
        db.session.commit()
        # El flujo existente crea los vínculos; no se crean al guardar la cotización.
        for supplier in [self.supplier, *self.providers.values()]:
            if supplier is self.providers["unlinked"]:
                continue
            for product in [self.product, self.extra]:
                if supplier is self.providers["partial"] and product is self.extra:
                    continue
                response = self.clients["asistente"].post(f"/api/products/{product.id}/suppliers", json={
                    "supplier_id": supplier.id, "purchase_price": "2.00", "is_preferred": False})
                self.assertEqual(response.status_code, 201, response.get_json())
        ProductSupplier.query.filter_by(supplier_id=self.providers["inactive_link"].id,
                                        product_id=self.extra.id).one().is_active = False
        self.providers["pending"].approval_status = "pending"
        self.providers["rejected"].approval_status = "rejected"
        self.providers["inactive"].is_active = False
        db.session.commit()

    def request(self, products):
        response = self.clients["inventario"].post("/api/replenishment-requests", json={
            "items": [{"product_id": product.id, "quantity": 3} for product in products]})
        self.assertEqual(response.status_code, 201, response.get_json())
        request_id = response.get_json()["id"]
        response = self.clients["asistente"].post(f"/api/replenishment-requests/{request_id}/start", json={})
        self.assertEqual(response.status_code, 200, response.get_json())
        return request_id

    def page(self, request_id):
        response = self.clients["asistente"].get(f"/purchases/requests/{request_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def payload(self, supplier, products):
        return {"supplier_id": supplier.id, "currency": "VES", "issued_on": self.today,
                "valid_until": "2099-01-01",
                "items": [{"product_id": product.id, "unit_price": "3.50"} for product in products]}

    def snapshot(self):
        return (SupplierQuote.query.count(), ProductSupplier.query.count(), PurchaseOrder.query.count(),
                StockMovement.query.count(), self.product.current_stock, self.extra.current_stock)

    def test_single_product_uses_active_association_without_requiring_preferred(self):
        request_id = self.request([self.product])
        self.assertEqual(supplier_options(self.page(request_id)), {
            self.supplier.id, self.providers["partial"].id, self.providers["inactive_link"].id})

    def test_multiple_products_require_the_intersection_and_preserve_quantities(self):
        request_id = self.request([self.product, self.extra])
        self.assertEqual(supplier_options(self.page(request_id)), {self.supplier.id})
        before = self.snapshot()
        result = self.clients["asistente"].post(f"/api/replenishment-requests/{request_id}/quotes",
            json=self.payload(self.supplier, [self.product, self.extra]))
        self.assertEqual(result.status_code, 201, result.get_json())
        self.assertEqual({item["product_id"] for item in result.get_json()["items"]},
                         {self.product.id, self.extra.id})
        self.assertEqual([item["quantity"] for item in result.get_json()["items"]], [3, 3])
        self.assertEqual(self.snapshot(), (before[0] + 1, *before[1:]))

    def test_manual_supplier_id_is_rejected_and_cannot_use_the_direct_api(self):
        products = [self.product, self.extra]
        request_id = self.request(products)
        before = self.snapshot()
        for key, supplier in self.providers.items():
            for direct in (False, True):
                with self.subTest(supplier=key, direct=direct):
                    data = self.payload(supplier, products)
                    path = f"/api/replenishment-requests/{request_id}/quotes"
                    if direct:
                        path = "/api/supplier-quotes"
                        data["items"] = [{**item, "quantity": 3} for item in data["items"]]
                    result = self.clients["asistente"].post(path, json=data)
                    self.assertEqual(result.status_code, 400 if key in ("pending", "rejected", "inactive") else 409,
                                     result.get_json())
                    self.assertEqual(self.snapshot(), before)

    def test_removed_association_after_render_is_revalidated_and_empty_state_is_clear(self):
        products = [self.product, self.extra]
        request_id = self.request(products)
        self.assertEqual(supplier_options(self.page(request_id)), {self.supplier.id})
        link = ProductSupplier.query.filter_by(product_id=self.extra.id, supplier_id=self.supplier.id).one()
        response = self.clients["asistente"].patch(f"/api/products/{self.extra.id}/suppliers/{link.id}",
                                                  json={"is_active": False})
        self.assertEqual(response.status_code, 200, response.get_json())
        before = self.snapshot()
        result = self.clients["asistente"].post(f"/api/replenishment-requests/{request_id}/quotes",
            json=self.payload(self.supplier, products))
        self.assertEqual(result.status_code, 409, result.get_json())
        html = self.page(request_id)
        self.assertEqual(supplier_options(html), set())
        self.assertIn(NOTICE, html)
        self.assertRegex(html, r'<fieldset[^>]* disabled aria-describedby="sr-supplier-notice"')
        self.assertEqual(self.snapshot(), before)

    def test_no_association_for_the_single_product_cannot_be_submitted(self):
        request_id = self.request([self.extra])
        supplier = self.providers["partial"]
        before = self.snapshot()
        result = self.clients["asistente"].post(f"/api/replenishment-requests/{request_id}/quotes",
            json=self.payload(supplier, [self.extra]))
        self.assertEqual(result.status_code, 409, result.get_json())
        self.assertNotIn(supplier.id, supplier_options(self.page(request_id)))
        self.assertEqual(self.snapshot(), before)

    def test_omitting_the_unassociated_product_does_not_bypass_sr_coverage(self):
        request_id = self.request([self.product, self.extra])
        before = self.snapshot()
        result = self.clients["asistente"].post(f"/api/replenishment-requests/{request_id}/quotes",
            json=self.payload(self.providers["partial"], [self.product]))
        self.assertEqual(result.status_code, 400, result.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_supplier_invalidated_after_render_is_revalidated(self):
        request_id = self.request([self.product, self.extra])
        self.assertIn(self.supplier.id, supplier_options(self.page(request_id)))
        before = self.snapshot()
        for changes in ({"approval_status": "pending"}, {"is_active": False}):
            with self.subTest(changes=changes):
                self.supplier.approval_status = "approved"
                self.supplier.is_active = True
                for key, value in changes.items():
                    setattr(self.supplier, key, value)
                db.session.commit()
                result = self.clients["asistente"].post(f"/api/replenishment-requests/{request_id}/quotes",
                    json=self.payload(self.supplier, [self.product, self.extra]))
                self.assertEqual(result.status_code, 400, result.get_json())
                self.assertNotIn(self.supplier.id, supplier_options(self.page(request_id)))
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
