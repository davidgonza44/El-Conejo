from datetime import datetime

from sqlalchemy import event

from app.extensions import db
from app.utils.category_names import category_name_key

# Identificadores semánticos de la paleta aprobada (nunca CSS arbitrario).
CATEGORY_COLORS = ("blue", "green", "orange", "purple", "teal", "red", "pink", "yellow", "gray", "brown", "navy")
DEFAULT_CATEGORY_COLOR = "blue"
CATEGORY_ICONS = ("tag", "package", "zap", "wrench", "hammer", "paintbrush", "droplet", "shield", "hard-hat")
DEFAULT_CATEGORY_ICON = "tag"


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    normalized_name = db.Column(db.String(200), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    color = db.Column(
        db.String(20), nullable=False, default=DEFAULT_CATEGORY_COLOR, server_default=DEFAULT_CATEGORY_COLOR
    )
    icon = db.Column(
        db.String(30), nullable=False, default=DEFAULT_CATEGORY_ICON, server_default=DEFAULT_CATEGORY_ICON
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    approval_status = db.Column(db.String(20), nullable=False, default="approved", server_default="approved")
    proposed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.String(1000))

    __table_args__ = (
        db.CheckConstraint("approval_status IN ('pending', 'approved', 'rejected')", name="ck_category_approval"),
        db.CheckConstraint("approval_status = 'approved' OR is_active = 0", name="ck_category_usable"),
    )

    products = db.relationship("Product", back_populates="category")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "is_active": self.is_active,
            "approval_status": self.approval_status,
            "proposed_by_user_id": self.proposed_by_user_id,
            "approved_by_user_id": self.approved_by_user_id,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "color": self.color if self.color in CATEGORY_COLORS else DEFAULT_CATEGORY_COLOR,
            "icon": self.icon if self.icon in CATEGORY_ICONS else DEFAULT_CATEGORY_ICON,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Category {self.name}>"


@event.listens_for(Category, "before_insert")
@event.listens_for(Category, "before_update")
def _store_name_key(_mapper, _connection, category):
    category.normalized_name = category_name_key(category.name)
