"""Versiones aisladas del histórico real 2026; sin vínculos con el stock operativo."""
from datetime import datetime
from uuid import uuid4

from app.extensions import db


class Real2026Import(db.Model):
    __tablename__ = "historical_2026_imports"
    __table_args__ = (
        db.CheckConstraint("status IN ('preview','confirmed','superseded','reverted')", name="ck_real2026_status"),
        db.CheckConstraint("coverage_start <= coverage_end", name="ck_real2026_coverage"),
        db.CheckConstraint(
            "(status = 'confirmed' AND confirmed_slot IS NOT NULL AND confirmed_slot = 1) OR "
            "(status <> 'confirmed' AND confirmed_slot IS NULL)", name="ck_real2026_confirmed_slot"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid4()))
    package_hash = db.Column(db.String(64), nullable=False, unique=True)
    status = db.Column(db.String(16), nullable=False, default="preview")
    coverage_start = db.Column(db.Date, nullable=False)
    coverage_end = db.Column(db.Date, nullable=False)
    calendar = db.Column(db.JSON, nullable=False)
    summary = db.Column(db.JSON, nullable=False)
    source_hashes = db.Column(db.JSON, nullable=False)
    provenance = db.Column(db.JSON, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    confirmed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    confirmed_at = db.Column(db.DateTime)
    reverted_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reverted_at = db.Column(db.DateTime)
    reversion_reason = db.Column(db.Text)
    supersedes_import_id = db.Column(db.Integer, db.ForeignKey("historical_2026_imports.id"))
    revision_reason = db.Column(db.Text)
    preview_token_hash = db.Column(db.String(64), nullable=False)
    preview_token_expires_at = db.Column(db.DateTime, nullable=False)
    # A single current dataset, enforced even when two first confirmations race.
    confirmed_slot = db.Column(db.Integer, unique=True, nullable=True)
    lock_version = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    __mapper_args__ = {"version_id_col": lock_version}


class Real2026Product(db.Model):
    __tablename__ = "historical_2026_products"
    __table_args__ = (db.UniqueConstraint("batch_id", "code", name="uq_real2026_batch_code"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("historical_2026_imports.id"), nullable=False, index=True)
    code = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    unit = db.Column(db.String(100), nullable=False)
    cohort = db.Column(db.String(32), nullable=False)
    identity_status = db.Column(db.String(100), nullable=False)
    unit_status = db.Column(db.String(100), nullable=False)
    is_service = db.Column(db.Boolean, nullable=False, default=False)
    included = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.Text, nullable=False)


class Real2026Daily(db.Model):
    __tablename__ = "historical_2026_daily"
    __table_args__ = (db.CheckConstraint("quantity >= 0", name="ck_real2026_daily_quantity"),
                      {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"})

    product_id = db.Column(db.Integer, db.ForeignKey("historical_2026_products.id"), primary_key=True)
    event_date = db.Column(db.Date, primary_key=True)
    quantity = db.Column(db.Numeric(24, 6), nullable=False)
