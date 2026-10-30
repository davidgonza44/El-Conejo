"""Metadatos de respaldos (base de datos, archivos o completos) y de restauraciones ejecutadas."""
from datetime import datetime, timezone

from app.extensions import db

BACKUP_KIND_DATABASE = "database"
BACKUP_KIND_FILES = "files"
BACKUP_KIND_FULL = "full"
BACKUP_KINDS = (BACKUP_KIND_FULL, BACKUP_KIND_DATABASE, BACKUP_KIND_FILES)
BACKUP_KIND_LABELS = {BACKUP_KIND_FULL: "Completo", BACKUP_KIND_DATABASE: "Solo base de datos",
                      BACKUP_KIND_FILES: "Solo archivos"}
BACKUP_STATUS_COMPLETED = "completed"
BACKUP_STATUS_DELETED = "deleted"
BACKUP_STATUS_MISSING = "missing"
BACKUP_STATUS_CORRUPT = "corrupt"


class Backup(db.Model):
    __tablename__ = "backups"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(160), nullable=False, unique=True)
    sha256 = db.Column(db.String(64), nullable=False)
    byte_size = db.Column(db.BigInteger, nullable=False)
    database_name = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default=BACKUP_KIND_DATABASE)
    status = db.Column(db.String(20), nullable=False, default=BACKUP_STATUS_COMPLETED)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    # Copia remota (Google Drive): id del archivo y estado (uploaded | failed | pending). Sin tokens.
    remote_file_id = db.Column(db.String(120), nullable=True)
    remote_status = db.Column(db.String(20), nullable=True)
    file_count = db.Column(db.Integer, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="manual")  # manual | scheduled

    created_by = db.relationship("User", lazy="joined")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "database_name": self.database_name,
            "kind": self.kind,
            "kind_label": BACKUP_KIND_LABELS.get(self.kind, self.kind),
            "status": self.status,
            "source": self.source,
            "file_count": self.file_count,
            "remote_file_id": self.remote_file_id,
            "remote_status": self.remote_status,
            "created_by": self.created_by.name if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RestoreRun(db.Model):
    __tablename__ = "restore_runs"

    id = db.Column(db.Integer, primary_key=True)
    backup_id = db.Column(db.Integer, db.ForeignKey("backups.id", ondelete="SET NULL"), nullable=True)
    backup_filename = db.Column(db.String(160), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    target_database = db.Column(db.String(64), nullable=False)
    safety_backup_id = db.Column(db.Integer, db.ForeignKey("backups.id", ondelete="SET NULL"), nullable=True)
    status = db.Column(db.String(20), nullable=False)  # completed | failed
    detail = db.Column(db.String(255), nullable=True)
    performed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    performed_by = db.relationship("User", lazy="joined", foreign_keys=[performed_by_user_id])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "backup_id": self.backup_id,
            "backup_filename": self.backup_filename,
            "kind": self.kind,
            "kind_label": BACKUP_KIND_LABELS.get(self.kind, self.kind),
            "target_database": self.target_database,
            "safety_backup_id": self.safety_backup_id,
            "status": self.status,
            "detail": self.detail,
            "performed_by": self.performed_by.name if self.performed_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
