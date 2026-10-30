"""Persistencia histórica 2026 con paquetes sintéticos y SQLite efímero."""
import copy
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from flask_login import login_user
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from approval_test_support import IsolatedCase
from app.extensions import db
from app.models import HistoricalDemandRecord, HistoricalImport, Product, StockMovement
from app.models.real_2026 import Real2026Daily, Real2026Import, Real2026Product
from app.services import real_2026_import_service as service
from app.services import real_2026_source_service as source
from app.services.exceptions import ConflictError, ValidationError
from migrate_real_2026_history import _isolated_target, apply_changes, main


def package(tag="first"):
    files = {name: ("synthetic " + name).encode() for name in source.REQUIRED_FILES}
    files[source.SERIES_FILES["STRICT"]] = ("synthetic " + tag).encode()
    return files


def parsed(files, *, allow_revision=False):
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    calendar = [date(2026, 1, 5), date(2026, 1, 6)]
    products = [dict(code="0007", name="Artículo sintético", unit="unidad", cohort="STRICT",
                     identity_status="CONTINUIDAD_PLAUSIBLE", unit_status="UNIDAD_SIMPLE",
                     is_service=False, included=True, reason="", points=[
                         (calendar[0], Decimal("1.250000")), (calendar[1], Decimal("0"))]),
                dict(code="0008", name="Identidad pendiente", unit="unidad", cohort="QUARANTINE",
                     identity_status="REVISAR_IDENTIDAD", unit_status="MULTIUNIDAD_NO_RESUELTA",
                     is_service=False, included=False, reason="Revisión pendiente", points=[])]
    return dict(package_hash=digest, source_hashes=hashes, coverage_start=calendar[0], coverage_end=calendar[-1],
                calendar=calendar, products=copy.deepcopy(products), summary=dict(products=2, total_points=2),
                provenance=dict(reference_match=not allow_revision, source="synthetic_fixture"))


class Real2026ImportTests(IsolatedCase):
    def setUp(self):
        super().setUp()
        self.parser = patch.object(source, "parse_package", side_effect=parsed)
        self.parser.start()
        self.addCleanup(self.parser.stop)

    def act(self, function, *args, actor="admin", **kwargs):
        with self.app.test_request_context():
            login_user(self.users[actor])
            return function(*args, **kwargs)

    def preview(self, files=None, **kwargs):
        return self.act(service.preview, files or package(), self.users["admin"].id, **kwargs)

    def confirm(self, batch, token):
        return self.act(service.confirm, batch.public_id, token, self.users["admin"].id)

    def test_preview_confirm_and_replay_preserve_operation_and_2025(self):
        original = (Product.query.count(), self.stock(), StockMovement.query.count(),
                    HistoricalImport.query.count(), HistoricalDemandRecord.query.count())
        batch, token, created = self.preview()
        self.assertTrue(created)
        self.assertEqual(batch.status, "preview")
        self.assertEqual(Real2026Product.query.count(), 2)
        self.assertEqual(Real2026Daily.query.count(), 2)
        self.assertEqual(Real2026Product.query.filter_by(code="0007").one().code, "0007")
        quantities = [row.quantity for row in Real2026Daily.query.order_by(Real2026Daily.event_date)]
        self.assertEqual(quantities, [Decimal("1.250000"), Decimal("0.000000")])
        self.assertEqual(self.confirm(batch, token)[1], True)
        self.assertEqual(self.confirm(batch, token)[1], False)
        self.assertEqual((Product.query.count(), self.stock(), StockMovement.query.count(),
                          HistoricalImport.query.count(), HistoricalDemandRecord.query.count()), original)
        serialized = service.serialize(batch)
        self.assertNotIn("preview_token_hash", serialized)
        self.assertNotIn(self.app.instance_path, json.dumps(serialized))
        folder = Path(self.app.instance_path) / "real_2026" / batch.public_id
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, package())

    def test_same_preview_rotates_token_without_duplicating_rows(self):
        batch, old_token, _ = self.preview()
        same, new_token, created = self.preview()
        self.assertEqual(batch.id, same.id)
        self.assertFalse(created)
        self.assertNotEqual(old_token, new_token)
        with self.assertRaises(ConflictError):
            self.confirm(batch, old_token)
        self.confirm(batch, new_token)
        existing, token, created = self.preview()
        self.assertEqual(existing.status, "confirmed")
        self.assertIsNone(token)
        self.assertFalse(created)
        self.assertEqual(Real2026Import.query.count(), 1)
        self.assertEqual(Real2026Daily.query.count(), 2)

    def test_revision_preserves_old_rows_and_rejects_sibling_confirmation(self):
        parent, token, _ = self.preview()
        self.confirm(parent, token)
        children = [self.preview(package(tag), supersedes_public_id=parent.public_id,
                                 revision_reason="Corrección sintética") for tag in ("second", "third")]
        child, child_token, _ = children[0]
        self.confirm(child, child_token)
        db.session.refresh(parent)
        self.assertEqual(parent.status, "superseded")
        self.assertIsNone(parent.confirmed_slot)
        self.assertEqual(child.supersedes_import_id, parent.id)
        self.assertEqual(Real2026Daily.query.count(), 6)
        with self.assertRaises(ConflictError):
            self.confirm(children[1][0], children[1][1])
        self.assertEqual(Real2026Import.query.filter_by(status="confirmed").count(), 1)

    def test_anchor_revision_reason_and_first_dataset_are_required(self):
        parent, token, _ = self.preview()
        self.confirm(parent, token)
        with self.assertRaises(ValidationError):
            self.preview(package("second"), supersedes_public_id=parent.public_id)
        changed = package("second")
        changed[source.ANCHOR_FILES[0]] = b"different catalog"
        with self.assertRaises(ConflictError):
            self.preview(changed, supersedes_public_id=parent.public_id, revision_reason="Motivo")
        second, second_token, _ = self.preview(package("other initial"))
        with self.assertRaises(ConflictError):
            self.confirm(second, second_token)
        second.status, second.confirmed_slot = "confirmed", 1
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_expiry_and_private_integrity_block_confirmation(self):
        batch, token, _ = self.preview()
        batch.preview_token_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        with self.assertRaises(ConflictError):
            self.confirm(batch, token)
        batch, token, _ = self.preview()
        folder = Path(self.app.instance_path) / "real_2026" / batch.public_id
        (folder / source.REQUIRED_FILES[0]).write_bytes(b"changed synthetic evidence")
        with self.assertRaises(ConflictError):
            self.confirm(batch, token)
        self.assertEqual(batch.status, "preview")

    def test_guards_actor_and_parser_failure_happen_before_archive(self):
        with self.assertRaises(ValidationError) as error:
            self.act(service.preview, package(), self.users["inventario"].id, actor="inventario")
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(ValidationError):
            self.act(service.preview, package(), self.users["vendedor"].id)
        with patch.object(source, "parse_package", side_effect=ValueError("Paquete inválido")):
            with self.assertRaises(ValidationError):
                self.preview()
        self.assertFalse((Path(self.app.instance_path) / "real_2026").exists())
        self.assertEqual(Real2026Import.query.count(), 0)

    def test_quarantine_has_metadata_without_daily_records(self):
        batch, _, _ = self.preview()
        quarantined = Real2026Product.query.filter_by(batch_id=batch.id, cohort="QUARANTINE").one()
        self.assertFalse(quarantined.included)
        self.assertEqual(Real2026Daily.query.filter_by(product_id=quarantined.id).count(), 0)
        self.assertEqual(len(self.act(service.listing, actor="inventario")), 1)


class Real2026MigrationTests(unittest.TestCase):
    def test_new_tables_only_idempotence_and_fail_closed_schema(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users(id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE products(id INTEGER PRIMARY KEY, current_stock INTEGER)"))
            connection.execute(text("INSERT INTO products VALUES(1,19)"))
            apply_changes(connection)
            apply_changes(connection)
            self.assertEqual(set(inspect(connection).get_table_names()), {
                "users", "products", "historical_2026_imports", "historical_2026_products", "historical_2026_daily"})
            self.assertEqual(connection.execute(text("SELECT current_stock FROM products")).scalar_one(), 19)
        other = create_engine("sqlite:///:memory:")
        self.addCleanup(other.dispose)
        with other.begin() as connection:
            connection.execute(text("CREATE TABLE users(id INTEGER PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE historical_2026_imports(id INTEGER PRIMARY KEY)"))
            with self.assertRaises(RuntimeError):
                apply_changes(connection)

    def test_default_plan_does_not_connect_and_target_is_restricted(self):
        with patch("sys.argv", ["migrate_real_2026_history.py"]), patch("migrate_real_2026_history.create_engine") as engine:
            main()
            engine.assert_not_called()
        with self.assertRaises(RuntimeError):
            _isolated_target("existing.sqlite", ".")


if __name__ == "__main__":
    unittest.main(verbosity=2)
