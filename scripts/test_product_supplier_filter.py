"""Product/supplier filtering: disposable SQLite only; no MySQL, dotenv or seed changes."""
import csv
import unittest
from datetime import datetime
from io import StringIO
from unittest.mock import patch

from approval_test_support import IsolatedCase, db
from app.models import Category, Product
from app.models.purchase import ProductSupplier, Supplier


class ProductSupplierFilterCase(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.other_category = Category(name="Segunda categoría")
        self.supplier_b = Supplier(name="Proveedor B", tax_id="FILTER-B")
        self.empty_supplier = Supplier(name="Sin productos", tax_id="FILTER-EMPTY")
        db.session.add_all([self.other_category, self.supplier_b, self.empty_supplier])
        db.session.flush()
        self.product.name = "Compartido A y B"
        self.product.updated_at = datetime(2026, 9, 1, 12)
        self.only_a = self.make_product("FILTER-A", "Solo A", self.product.category_id, 0, 8)
        self.only_b = self.make_product("FILTER-B", "Solo B", self.other_category.id, 8, 9)
        self.inactive_a = self.make_product("FILTER-INACTIVE", "Inactivo A", self.other_category.id, 5, 4, False)
        self.unlinked = self.make_product("FILTER-NONE", "Sin proveedor", self.other_category.id, 6, 4)
        db.session.add_all([
            self.link(self.product, self.supplier, preferred=True),
            self.link(self.product, self.supplier_b),
            self.link(self.only_a, self.supplier),
            self.link(self.only_b, self.supplier_b),
            self.link(self.inactive_a, self.supplier, active=False),
        ])
        db.session.commit()

    def make_product(self, code, name, category_id, stock, price, active=True):
        product = Product(code=code, name=name, category_id=category_id, current_stock=stock,
                          minimum_stock=1, sale_price=price, purchase_price=1, is_active=active,
                          updated_at=datetime(2026, 9, 2, 12))
        db.session.add(product)
        db.session.flush()
        return product

    @staticmethod
    def link(product, supplier, preferred=False, active=True):
        return ProductSupplier(product_id=product.id, supplier_id=supplier.id,
                               purchase_price=1, is_preferred=preferred, is_active=active)

    def listing(self, role, **filters):
        response = self.clients[role].get('/api/products', query_string=filters)
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        ids = [item['id'] for item in data['items']]
        self.assertEqual(len(ids), len(set(ids)), 'No duplicate products')
        return data

    def assert_ids(self, role, expected, **filters):
        data = self.listing(role, **filters)
        self.assertEqual({p['id'] for p in data['items']}, {p.id for p in expected})
        self.assertEqual(data['count'], len(expected))

    def test_membership_multisupplier_and_empty_for_both_roles(self):
        for role in ('admin', 'inventario'):
            with self.subTest(role=role):
                self.assert_ids(role, [self.product, self.only_a], supplier_id=self.supplier.id)
                self.assert_ids(role, [self.product, self.only_b], supplier_id=self.supplier_b.id)
                data = self.listing(role, supplier_id=self.empty_supplier.id, page=1, per_page=8)
                self.assertEqual(data['items'], [])
                self.assertEqual(data['pagination']['total'], 0)
                self.assertEqual(data['pagination']['pages'], 0)
                self.assertFalse(data['pagination']['has_next'])

    def test_intersections_and_clear_for_both_roles(self):
        for role in ('admin', 'inventario'):
            with self.subTest(role=role):
                filters = dict(supplier_id=self.supplier.id, include_inactive=1)
                self.assert_ids(role, [self.inactive_a], **filters, category_id=self.other_category.id)
                self.assert_ids(role, [self.only_a], **filters, status='agotado')
                self.assert_ids(role, [self.inactive_a], **filters, status='inactivo')
                self.assert_ids(role, [self.product], **filters, search=self.product.code)
                self.assert_ids(role, [], **filters, search=self.only_b.code)
                self.assert_ids(role, [self.only_a], **filters, price_min=5, price_max=10, stock_max=0)
                self.assert_ids(role, [self.product], **filters, updated_from='2026-09-01', updated_to='2026-09-01')
                self.assert_ids(role, [], **filters, category_id=self.other_category.id, status='agotado')
                all_products = [self.product, self.only_a, self.only_b, self.inactive_a, self.unlinked]
                self.assert_ids(role, all_products, supplier_id='', include_inactive=1)
                self.assert_ids(role, all_products, include_inactive=1)

    def test_pagination_and_sort_for_both_roles(self):
        for role in ('admin', 'inventario'):
            with self.subTest(role=role):
                filters = dict(supplier_id=self.supplier.id, include_inactive=1, per_page=1, sort='price-desc')
                pages = [self.listing(role, **filters, page=page) for page in (1, 2, 3)]
                self.assertEqual([p['items'][0]['id'] for p in pages],
                                 [self.only_a.id, self.inactive_a.id, self.product.id])
                for page in pages:
                    self.assertEqual(page['count'], 3)
                    self.assertEqual(page['pagination']['pages'], 3)
                self.assertFalse(pages[2]['pagination']['has_next'])

    def test_existing_permissions_and_real_supplier_options(self):
        for role in ('admin', 'inventario'):
            with self.subTest(role=role):
                html = self.clients[role].get('/products')
                self.assertEqual(html.status_code, 200)
                for control in ('ix-filter-supplier', 'pl-filter-supplier', 'pl-advanced-supplier'):
                    self.assertIn(f'id="{control}"'.encode(), html.data)
                suppliers = self.clients[role].get('/api/suppliers?per_page=24')
                self.assertEqual(suppliers.status_code, 200)
                self.assertEqual({s['id'] for s in suppliers.json['items']},
                                 {self.supplier.id, self.supplier_b.id, self.empty_supplier.id})
        seller = self.clients['vendedor']
        html = seller.get('/products')
        self.assertEqual(html.status_code, 200)
        for control in ('ix-filter-supplier', 'pl-filter-supplier', 'pl-advanced-supplier'):
            self.assertNotIn(f'id="{control}"'.encode(), html.data)
        for url in ('/suppliers', '/api/suppliers', f'/api/products?supplier_id={self.supplier.id}'):
            self.assertEqual(seller.get(url).status_code, 403, url)
        self.assertEqual(seller.get('/api/products').status_code, 200)
        with patch('app.services.product_service.role_has_permission', return_value=False):
            self.assertEqual(self.clients['admin'].get('/api/products',
                             query_string={'supplier_id': self.supplier.id}).status_code, 403)

    def test_supplier_validation(self):
        for value in ('abc', '1.5', '0', '-1', '2147483648'):
            with self.subTest(value=value):
                response = self.clients['admin'].get('/api/products', query_string={'supplier_id': value})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.clients['admin'].get('/api/products?supplier_id=99999').status_code, 404)

    def test_export_uses_same_filter_and_authorization(self):
        body = {'format': 'csv', 'filters': {'supplier_id': self.supplier_b.id}}
        for role in ('admin', 'inventario'):
            with self.subTest(role=role):
                response = self.clients[role].post('/api/products/export', json=body)
                self.assertEqual(response.status_code, 200, response.get_json())
                rows = list(csv.DictReader(StringIO(response.data.decode('utf-8-sig'))))
                self.assertEqual({r['Código'] for r in rows}, {self.product.code, self.only_b.code})
        self.assertEqual(self.clients['vendedor'].post('/api/products/export', json=body).status_code, 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
