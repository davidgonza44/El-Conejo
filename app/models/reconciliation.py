"""A comparison snapshot never changes stock; explicit resolutions link movements."""
from datetime import datetime

from app.extensions import db


class ReconciliationRun(db.Model):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (db.CheckConstraint("status IN ('preview','confirmed','discarded')", name="ck_reconciliation_status"),)
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True)
    cutoff_at = db.Column(db.DateTime, nullable=False)
    imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    filename = db.Column(db.String(255), nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    source_type = db.Column(db.String(12))  # Historical rows retain unknown provenance.
    status = db.Column(db.String(20), nullable=False, default="preview")
    received_count = db.Column(db.Integer, nullable=False, default=0)
    valid_count = db.Column(db.Integer, nullable=False, default=0)
    invalid_count = db.Column(db.Integer, nullable=False, default=0)
    match_count = db.Column(db.Integer, nullable=False, default=0)
    discrepancy_count = db.Column(db.Integer, nullable=False, default=0)
    unmatched_count = db.Column(db.Integer, nullable=False, default=0)
    creator = db.relationship("User")
    lines = db.relationship("ReconciliationLine", order_by="ReconciliationLine.row_number", back_populates="run")

    def to_dict(self, detail=True):
        result = {key: getattr(self, key) for key in (
            "id", "number", "filename", "sha256", "status", "created_by_user_id", "source_type",
            "received_count", "valid_count", "invalid_count", "match_count", "discrepancy_count", "unmatched_count")}
        result.update(cutoff_at=self.cutoff_at.isoformat(), imported_at=self.imported_at.isoformat(),
                      creator=self.creator.name if self.creator else ("Proceso automático" if self.source_type == "automatic" else None),
                      cut=self.cut.to_dict(False) if self.cut else None)
        if detail:
            result["items"] = [line.to_dict() for line in self.lines]
        return result


class ReconciliationLine(db.Model):
    __tablename__ = "reconciliation_lines"
    __table_args__ = (
        db.UniqueConstraint("run_id", "row_number", name="uq_reconciliation_row"),
        db.CheckConstraint("result IN ('invalid','unmatched','match','discrepancy')", name="ck_reconciliation_result"),
        db.CheckConstraint("review_status IN ('pending','reviewed','resolved')", name="ck_reconciliation_review"),
        db.CheckConstraint("external_quantity IS NULL OR external_quantity >= 0", name="ck_reconciliation_quantity"),
        db.CheckConstraint("resolution_action IS NULL OR resolution_action IN ('acknowledge','adjustment')", name="ck_reconciliation_action"),
    )
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("reconciliation_runs.id"), nullable=False, index=True)
    row_number = db.Column(db.Integer, nullable=False)
    product_code = db.Column(db.String(50), nullable=False)
    product_name = db.Column(db.String(150))
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), index=True)
    external_quantity = db.Column(db.Integer)
    system_quantity = db.Column(db.Integer)
    difference = db.Column(db.Integer)
    result = db.Column(db.String(20), nullable=False)
    error = db.Column(db.String(255))
    review_status = db.Column(db.String(20), nullable=False, default="pending")
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(1000))
    resolved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)
    resolution_note = db.Column(db.String(1000))
    resolution_action = db.Column(db.String(20))
    stock_movement_id = db.Column(db.Integer, db.ForeignKey("stock_movements.id"), unique=True)
    run = db.relationship("ReconciliationRun", back_populates="lines")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by_user_id])
    resolver = db.relationship("User", foreign_keys=[resolved_by_user_id])

    def to_dict(self):
        result = {column.name: getattr(self, column.name) for column in self.__table__.columns}
        for key in ("reviewed_at", "resolved_at"):
            result[key] = result[key].isoformat() if result[key] else None
        result.update(reviewed_by=self.reviewer.name if self.reviewer else None,
                      resolved_by=self.resolver.name if self.resolver else None)
        latest = self.adjustment_requests[0] if self.adjustment_requests else None
        result["adjustment_request"] = None if latest is None else {
            key: getattr(latest, key) for key in ("id", "status", "new_stock", "review_comment")}
        return result


class ReconciliationCut(db.Model):
    __tablename__ = "reconciliation_cuts"
    __table_args__ = (
        db.UniqueConstraint("source", "scheduled_for", name="uq_reconciliation_cut_source_time"),
        db.UniqueConstraint("run_id", name="uq_reconciliation_cut_run"),
        db.CheckConstraint("status IN ('waiting_source','completed','failed')", name="ck_reconciliation_cut_status"),
        db.CheckConstraint("snapshot_status IN ('pending','captured','unavailable')", name="ck_reconciliation_snapshot_status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(50), nullable=False)
    scheduled_for = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="waiting_source")
    snapshot_status = db.Column(db.String(20), nullable=False, default="pending")
    local_snapshot_at = db.Column(db.DateTime)
    source_received_at = db.Column(db.DateTime)
    run_id = db.Column(db.Integer, db.ForeignKey("reconciliation_runs.id"))
    last_attempt_at = db.Column(db.DateTime)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    failure_reason = db.Column(db.String(255))
    last_source_hash = db.Column(db.String(64))
    archived_at = db.Column(db.DateTime)
    run = db.relationship("ReconciliationRun", backref=db.backref("cut", uselist=False))
    stock = db.relationship("ReconciliationCutStock", back_populates="cut", order_by="ReconciliationCutStock.product_id")

    def to_dict(self, include_run=True):
        data = {name: getattr(self, name) for name in (
            "id", "source", "status", "snapshot_status", "run_id", "attempt_count", "failure_reason", "last_source_hash")}
        for name in ("scheduled_for", "local_snapshot_at", "source_received_at", "last_attempt_at", "archived_at"):
            value = getattr(self, name)
            data[name] = value.isoformat() if value else None
        data["snapshot_delay_seconds"] = ((self.local_snapshot_at - self.scheduled_for).total_seconds()
                                          if self.local_snapshot_at else None)
        if include_run:
            data["run"] = self.run.to_dict(False) if self.run else None
        return data


class ReconciliationCutStock(db.Model):
    __tablename__ = "reconciliation_cut_stock"
    cut_id = db.Column(db.Integer, db.ForeignKey("reconciliation_cuts.id"), primary_key=True)
    product_id = db.Column(db.Integer, primary_key=True)  # Historical identity; no cascade to live catalogue.
    product_code_snapshot = db.Column(db.String(50), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False)
    cut = db.relationship("ReconciliationCut", back_populates="stock")
