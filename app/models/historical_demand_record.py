"""Renglón histórico validado y auditable.

Los hechos de origen (fecha, código, cantidad y JSON allowlist) no se cambian
después de confirmar. Los campos de estado efectivo permiten superseder un
hecho sin borrarlo ni perder la trazabilidad.
"""
from datetime import datetime

from sqlalchemy import event, inspect as sa_inspect, select
from sqlalchemy.orm import synonym

from app.extensions import db

RECORD_TYPE_SALE = "sale"
RECORD_TYPE_RETURN = "return"
RECORD_TYPE_CANCELLATION = "cancellation"
RECORD_TYPE_CORRECTION = "correction"

HISTORICAL_RECORD_TYPES = (
    RECORD_TYPE_SALE,
    RECORD_TYPE_RETURN,
    RECORD_TYPE_CANCELLATION,
    RECORD_TYPE_CORRECTION,
)

RECORD_STATUS_ISSUED = "issued"
RECORD_STATUS_ACTIVE = "active"
RECORD_STATUS_CANCELLED = "cancelled"
RECORD_STATUS_VOIDED = "voided"
RECORD_STATUS_SUPERSEDED = "superseded"

HISTORICAL_RECORD_STATUSES = (
    RECORD_STATUS_ISSUED,
    RECORD_STATUS_ACTIVE,
    RECORD_STATUS_CANCELLED,
    RECORD_STATUS_VOIDED,
    RECORD_STATUS_SUPERSEDED,
)

# Una vez confirmado o revertido el lote, el registro completo es inmutable
# salvo su estado lógico: include_in_demand, effective_status, superseded_by_*,
# superseded_at, updated_at y lock_version. Esta lista protege tanto los hechos
# fuente como la resolución administrativa que quedó confirmada.
IMMUTABLE_CONFIRMED_ATTRIBUTE_NAMES = frozenset(
    {
        "id",
        "historical_import_id",
        "source_row_number",
        "source_record_id_original",
        "source_record_id_normalized",
        "source_line_id_original",
        "source_line_id_normalized",
        "document_type",
        "document_number_original",
        "document_number_normalized",
        "event_date",
        "product_code_original",
        "product_code_normalized",
        "product_name_original",
        "product_name_normalized",
        "product_id",
        "suggested_product_id",
        "quantity",
        "unit_price",
        "record_type",
        "record_status",
        "related_source_record_id",
        "related_document_number_normalized",
        "related_record_id",
        "fingerprint",
        "fingerprint_strength",
        "dedupe_key",
        "match_status",
        "match_method",
        "review_flags_json",
        "reviewed_by_user_id",
        "reviewed_at",
        "raw_row_json",
        "created_at",
    }
)

IMMUTABLE_CONFIRMED_COLUMN_NAMES = (
    "id",
    "import_id",
    "source_row_number",
    "source_record_id",
    "source_record_id_normalized",
    "source_line_id",
    "source_line_id_normalized",
    "document_type",
    "document_number",
    "document_number_normalized",
    "event_date",
    "original_product_code",
    "normalized_product_code",
    "original_product_name",
    "normalized_product_name",
    "product_id",
    "suggested_product_id",
    "quantity",
    "unit_price",
    "record_type",
    "record_status",
    "related_source_record_id",
    "related_document_number_normalized",
    "related_record_id",
    "fingerprint",
    "fingerprint_strength",
    "dedupe_key",
    "match_status",
    "match_method",
    "review_flags_json",
    "reviewed_by_user_id",
    "reviewed_at",
    "raw_row_json",
    "created_at",
)

FINGERPRINT_STRONG = "strong"
FINGERPRINT_WEAK = "weak"


class HistoricalDemandRecord(db.Model):
    """Registro staging/confirmado; jamás genera movimientos de inventario."""

    __tablename__ = "historical_demand_records"
    __table_args__ = (
        db.UniqueConstraint(
            "import_id",
            "source_row_number",
            name="uq_hist_records_import_id_source_row",
        ),
        db.UniqueConstraint("dedupe_key", name="uq_hist_records_dedupe_key"),
        db.CheckConstraint("quantity > 0", name="ck_hist_records_quantity_positive"),
        db.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name="ck_hist_records_unit_price_nonnegative",
        ),
        db.CheckConstraint(
            "event_date >= '2025-01-01' AND event_date <= '2025-12-31'",
            name="ck_hist_records_event_date_2025",
        ),
        db.CheckConstraint(
            "record_type IN ('sale', 'return', 'cancellation', 'correction')",
            name="ck_hist_records_record_type",
        ),
        db.CheckConstraint(
            "record_status IN ('issued', 'active', 'cancelled', 'voided', "
            "'superseded')",
            name="ck_hist_records_record_status",
        ),
        db.CheckConstraint(
            "effective_status IN ('issued', 'active', 'cancelled', 'voided', "
            "'superseded')",
            name="ck_hist_records_effective_status",
        ),
        db.CheckConstraint(
            "fingerprint_strength IN ('strong', 'weak')",
            name="ck_hist_records_fingerprint_strength",
        ),
        db.Index("ix_hist_records_import_id", "import_id"),
        db.Index("ix_hist_records_event_date", "event_date"),
        db.Index("ix_hist_records_product", "product_id"),
        db.Index(
            "ix_hist_records_normalized_product_code",
            "normalized_product_code",
        ),
        db.Index("ix_hist_records_document", "document_number_normalized"),
        db.Index("ix_hist_records_fingerprint", "fingerprint"),
        db.Index("ix_hist_records_match_status", "match_status"),
        db.Index(
            "ix_hist_records_demand_effective",
            "include_in_demand",
            "effective_status",
            "record_type",
        ),
        db.Index("ix_hist_records_related", "related_record_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )

    _historical_id_type = db.BigInteger().with_variant(db.Integer, "sqlite")

    id = db.Column(_historical_id_type, primary_key=True, autoincrement=True)
    historical_import_id = db.Column(
        "import_id",
        _historical_id_type,
        db.ForeignKey(
            "historical_imports.id",
            ondelete="RESTRICT",
            name="fk_hist_records_import_id",
        ),
        nullable=False,
    )
    import_id = synonym("historical_import_id")
    source_row_number = db.Column(db.Integer, nullable=False)

    source_record_id_original = db.Column(
        "source_record_id", db.String(255), nullable=True
    )
    source_record_id = synonym("source_record_id_original")
    source_record_id_normalized = db.Column(db.String(255), nullable=True)
    source_line_id_original = db.Column(
        "source_line_id", db.String(255), nullable=True
    )
    source_line_id = synonym("source_line_id_original")
    source_line_id_normalized = db.Column(db.String(255), nullable=True)

    document_type = db.Column(
        db.String(50), nullable=False, default="historical_demand"
    )
    document_number_original = db.Column(
        "document_number", db.String(255), nullable=True
    )
    document_number = synonym("document_number_original")
    document_number_normalized = db.Column(db.String(255), nullable=True)
    event_date = db.Column(db.Date, nullable=False)

    product_code_original = db.Column(
        "original_product_code", db.String(255), nullable=False
    )
    product_code_normalized = db.Column(
        "normalized_product_code", db.String(255), nullable=False
    )
    product_name_original = db.Column(
        "original_product_name", db.String(255), nullable=True
    )
    product_name_normalized = db.Column(
        "normalized_product_name", db.String(255), nullable=True
    )
    original_product_code = synonym("product_code_original")
    normalized_product_code = synonym("product_code_normalized")
    original_product_name = synonym("product_name_original")
    normalized_product_name = synonym("product_name_normalized")
    product_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "products.id", ondelete="RESTRICT", name="fk_hist_records_product_id"
        ),
        nullable=True,
    )
    suggested_product_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "products.id",
            ondelete="RESTRICT",
            name="fk_hist_records_suggested_product_id",
        ),
        nullable=True,
    )

    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=True)
    record_type = db.Column(db.String(20), nullable=False)
    # Estado declarado por la fuente; se conserva como hecho inmutable.
    record_status = db.Column(db.String(20), nullable=False)
    # Estado lógico vigente, modificable solo por una corrección auditada.
    effective_status = db.Column(db.String(20), nullable=False)

    related_source_record_id = db.Column(db.String(255), nullable=True)
    related_document_number_normalized = db.Column(db.String(255), nullable=True)
    related_record_id = db.Column(
        _historical_id_type,
        db.ForeignKey(
            "historical_demand_records.id",
            ondelete="RESTRICT",
            name="fk_hist_records_related_record_id",
        ),
        nullable=True,
    )
    superseded_by_record_id = db.Column(
        _historical_id_type,
        db.ForeignKey(
            "historical_demand_records.id",
            ondelete="RESTRICT",
            name="fk_hist_records_superseded_by_record_id",
        ),
        nullable=True,
    )
    superseded_by_import_id = db.Column(
        _historical_id_type,
        db.ForeignKey(
            "historical_imports.id",
            ondelete="RESTRICT",
            name="fk_hist_records_superseded_by_import_id",
        ),
        nullable=True,
    )
    superseded_at = db.Column(db.DateTime, nullable=True)

    fingerprint = db.Column(db.String(64), nullable=False)
    fingerprint_strength = db.Column(db.String(10), nullable=False)
    # Solo se rellena en confirmación fuerte; NULL permite filas débiles.
    dedupe_key = db.Column(db.String(64), nullable=True)

    match_status = db.Column(db.String(32), nullable=False, default="pending")
    match_method = db.Column(db.String(32), nullable=True)
    include_in_demand = db.Column(db.Boolean, nullable=False, default=False)

    # Allowlist de decisiones booleanas; no admite texto libre ni datos cliente.
    review_flags_json = db.Column(db.JSON, nullable=True)
    reviewed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_hist_records_reviewed_by_user_id",
        ),
        nullable=True,
    )
    reviewed_at = db.Column(db.DateTime, nullable=True)

    # Solo las diez columnas canónicas del CSV, nunca columnas desconocidas.
    raw_row_json = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    lock_version = db.Column(db.Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": lock_version}

    historical_import = db.relationship(
        "HistoricalImport",
        back_populates="records",
        foreign_keys=[historical_import_id],
    )
    product = db.relationship("Product", foreign_keys=[product_id])
    suggested_product = db.relationship(
        "Product", foreign_keys=[suggested_product_id]
    )
    related_record = db.relationship(
        "HistoricalDemandRecord",
        remote_side=[id],
        foreign_keys=[related_record_id],
        post_update=True,
    )
    superseded_by_record = db.relationship(
        "HistoricalDemandRecord",
        remote_side=[id],
        foreign_keys=[superseded_by_record_id],
        post_update=True,
    )
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_user_id])

    def __repr__(self) -> str:
        return (
            f"<HistoricalDemandRecord import={self.historical_import_id} "
            f"row={self.source_row_number}>"
        )


def _persisted_import_status(connection, target: HistoricalDemandRecord) -> str | None:
    loaded_import = target.__dict__.get("historical_import")
    if loaded_import is not None and loaded_import.status in {"confirmed", "reverted"}:
        return loaded_import.status

    # Import local para evitar el ciclo de importación declarativa.
    from app.models.historical_import import HistoricalImport

    # Si se intenta mover la fila a otro lote, también se consulta el import_id
    # anterior. De lo contrario bastaría cambiar import_id y un hecho fuente en
    # el mismo flush para eludir la comprobación ORM.
    import_id_history = sa_inspect(target).attrs.historical_import_id.history
    candidate_import_ids = {
        import_id
        for import_id in (
            target.historical_import_id,
            *import_id_history.deleted,
        )
        if import_id is not None
    }
    if not candidate_import_ids:
        return None

    protected_statuses = connection.execute(
        select(HistoricalImport.status).where(
            HistoricalImport.id.in_(candidate_import_ids),
            HistoricalImport.status.in_(("confirmed", "reverted")),
        )
    ).scalars()
    return next(iter(protected_statuses), None)


@event.listens_for(HistoricalDemandRecord, "before_update")
def _protect_confirmed_source_fields(_mapper, connection, target) -> None:
    state = sa_inspect(target)
    changed_source_fields = {
        name
        for name in IMMUTABLE_CONFIRMED_ATTRIBUTE_NAMES
        if state.attrs[name].history.has_changes()
    }
    if not changed_source_fields:
        return
    if _persisted_import_status(connection, target) in {"confirmed", "reverted"}:
        raise ValueError(
            "Un registro histórico confirmado es inmutable salvo su estado lógico."
        )


@event.listens_for(HistoricalDemandRecord, "before_delete")
def _protect_confirmed_record_delete(_mapper, connection, target) -> None:
    if _persisted_import_status(connection, target) in {"confirmed", "reverted"}:
        raise ValueError(
            "Un registro histórico confirmado no puede eliminarse físicamente."
        )
