"""Modelos del sistema. Importarlos aquí los registra en SQLAlchemy."""
from app.models.user import User
from app.models.auth_identity import AuthIdentity
from app.models.passwordless_token import PasswordlessToken
from app.models.password_reset_token import PasswordResetToken
from app.models.category import Category
from app.models.category_change import CategoryChange
from app.models.supplier_change import SupplierChange
from app.models.product import Product
from app.models.product_change import ProductChange
from app.models.product_state_history import ProductStateHistory
from app.models.stock_movement import StockMovement
from app.models.inventory_adjustment import InventoryAdjustmentRequest
from app.models.inventory_snapshot import InventorySnapshot
from app.models.delivery_note import DeliveryNote
from app.models.delivery_note_item import DeliveryNoteItem
from app.models.historical_import import HistoricalImport
from app.models.historical_demand_record import HistoricalDemandRecord
from app.models.historical_import_error import HistoricalImportError
from app.models.customer import Customer
from app.models.sale import Sale
from app.models.sale_line import SaleLine
from app.models.quote import Quote, QuoteLine
from app.models.order import Order, OrderLine
from app.models.app_setting import AppSetting
from app.models.backup import Backup, RestoreRun
from app.models.role import Permission, Role
from app.models.security import LoginEvent, MfaRecoveryCode, PasswordHistory, TrustedDevice, UserMfa, UserSession
from app.models.notification import Notification, NotificationDelivery
from app.models.integration_connection import IntegrationConnection

__all__ = [
    "AppSetting",
    "Backup",
    "RestoreRun",
    "Permission",
    "Role",
    "LoginEvent",
    "MfaRecoveryCode",
    "PasswordHistory",
    "TrustedDevice",
    "UserMfa",
    "UserSession",
    "Notification",
    "NotificationDelivery",
    "IntegrationConnection",
    "Customer",
    "Sale",
    "SaleLine",
    "Quote",
    "QuoteLine",
    "Order",
    "OrderLine",
    "User",
    "AuthIdentity",
    "PasswordlessToken",
    "PasswordResetToken",
    "Category",
    "CategoryChange",
    "SupplierChange",
    "Product",
    "ProductChange",
    "ProductStateHistory",
    "StockMovement",
    "InventoryAdjustmentRequest",
    "InventorySnapshot",
    "DeliveryNote",
    "DeliveryNoteItem",
    "HistoricalImport",
    "HistoricalDemandRecord",
    "HistoricalImportError",
]

from app.models.replenishment_request import ReplenishmentRequest, ReplenishmentRequestLine
from app.models.supplier_quote import SupplierQuote, SupplierQuoteLine
from app.models.purchase_closure import PurchaseClosureRequest
from app.models.purchase import (
    ProductSupplier,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReception,
    PurchaseReceptionLine,
    Supplier,
)

from app.models.reconciliation import ReconciliationRun, ReconciliationLine, ReconciliationCut, ReconciliationCutStock
from app.models.real_2026 import Real2026Import, Real2026Product, Real2026Daily
