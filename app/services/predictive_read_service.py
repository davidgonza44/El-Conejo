"""Materialize read-only predictive inputs before releasing a request-owned session."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import SessionTransactionOrigin

from app.extensions import db


@dataclass(frozen=True)
class NamedValue:
    id: int
    name: str


@dataclass(frozen=True)
class ProductInput:
    id: int
    code: str
    name: str
    category_id: int
    category: NamedValue | None
    unit: str
    current_stock: int
    minimum_stock: int
    purchase_price: Decimal
    image_url: str | None
    is_active: bool
    is_service: bool = False
    approval_status: str = "approved"
    created_at: datetime | None = None
    purchase_price_currency: str | None = None


def product_input(product) -> ProductInput:
    category = NamedValue(product.category.id, product.category.name) if product.category else None
    return ProductInput(product.id, product.code, product.name, product.category_id, category,
                        product.unit, product.current_stock, product.minimum_stock,
                        product.purchase_price, product.image_url, product.is_active,
                        bool(getattr(product, "is_service", False)),
                        getattr(product, "approval_status", "approved"), getattr(product, "created_at", None),
                        getattr(product, "purchase_price_currency", None))


def _read_only_transaction(session) -> bool:
    transaction = session.get_transaction()
    return (not (session.new or session.dirty or session.deleted or session.in_nested_transaction())
            and (transaction is None or transaction.origin == SessionTransactionOrigin.AUTOBEGIN))


@contextmanager
def materialized_read(*, release_session: bool = False):
    """Only a read-only request caller may transfer ownership; generic callers retain it.

    Check before reads can autoflush caller changes, and again before closing. Explicit
    transactions/savepoints remain caller-owned. Close directly: rollback first would
    expire the already-loaded authenticated user still needed by the template.
    """
    session = db.session()
    owned = release_session and _read_only_transaction(session)
    try:
        yield
    finally:
        if owned and _read_only_transaction(session):
            db.session.remove()
