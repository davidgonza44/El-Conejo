"""DDL idempotente en SQLite desechable; sin importar configuración privada."""
import unittest

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import ProductChange
from migrate_product_changes import apply_changes
from sqlalchemy import inspect


class ProductChangeMigrationTests(IsolatedCase):
    def test_additive_and_idempotent(self):
        before = self.product.to_dict()
        with db.engine.begin() as connection:
            ProductChange.__table__.drop(connection)
            apply_changes(connection)
            apply_changes(connection)
            foreign = inspect(connection).get_foreign_keys('product_changes')
            self.assertEqual(len(foreign), 3)
            self.assertIn('product_changes', inspect(connection).get_table_names())
        db.session.expire_all()
        self.assertEqual(self.product.to_dict(), before)
        self.assertEqual(ProductChange.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
