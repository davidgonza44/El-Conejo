"""Application factory del sistema de inventario."""
from flask import Flask, jsonify, redirect, request, url_for

from app.config import Config
from app.extensions import db, login_manager, oauth


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)
    from app.services.company_logo_service import get_company_logo_url
    from flask_wtf.csrf import generate_csrf
    app.add_template_global(get_company_logo_url)
    app.add_template_global(generate_csrf, "csrf_token")

    db.init_app(app)
    login_manager.init_app(app)
    oauth.init_app(app)

    from app.services.google_auth_service import register_oauth_client
    from app.services import google_drive_service, session_service

    register_oauth_client(app)
    google_drive_service.register_oauth_client(app)
    # Registro de sesiones en servidor (revocación inmediata) y bloqueo por contraseña expirada.
    from app.utils.profile_request_guard import guard as profile_request_guard
    app.before_request(profile_request_guard)
    session_service.init_app(app)

    from app.utils.permissions import role_has_permission

    @app.template_global("has_permission")
    def _has_permission(permission: str) -> bool:
        from flask_login import current_user

        return current_user.is_authenticated and role_has_permission(current_user.role, permission)

    def _nav_role() -> str:
        from flask_login import current_user

        return current_user.role if current_user.is_authenticated else ""

    @app.template_global("nav_allowed")
    def _nav_allowed(key: str, reference: bool = False) -> bool:
        from app.utils.navigation import nav_allowed

        return nav_allowed(_nav_role(), key, reference is True)

    @app.template_global("visible_nav")
    def _visible_nav(children, reference: bool = False):
        from app.utils.navigation import visible_links

        return visible_links(_nav_role(), children, reference is True)

    @app.template_global("settings_tab_allowed")
    def _settings_tab_allowed(key: str, reference: bool = False) -> bool:
        from app.utils.navigation import settings_tab_allowed

        return settings_tab_allowed(_nav_role(), key, reference is True)

    @app.template_global("home_path")
    def _home_path() -> str:
        from app.utils.navigation import home_path

        role = _nav_role()
        return home_path(role) if role else "/login"

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        # API y archivos media: JSON 401. Páginas web: redirigen al login.
        if request.path.startswith("/api/") or request.path.startswith("/media/"):
            return jsonify({"error": "Autenticación requerida."}), 401
        return redirect(url_for("pages.login"))

    from app import models  # noqa: F401  (registra los modelos en SQLAlchemy)

    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.users import users_bp
    from app.routes.categories import categories_bp
    from app.routes.products import products_bp
    from app.routes.inventory import inventory_bp
    from app.routes.delivery_notes import delivery_notes_bp
    from app.routes.reports import reports_bp
    from app.routes.pages import pages_bp
    from app.routes.media import media_bp
    from app.routes.chatbot import chatbot_bp
    from app.routes.historical_imports import historical_imports_bp
    from app.routes.real_2026 import real_2026_bp
    from app.routes.predictions import predictions_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.customers import customers_bp
    from app.routes.sales import sales_bp
    from app.routes.quotes import quotes_bp
    from app.routes.orders import orders_bp
    from app.routes.reconciliation import reconciliation_bp
    from app.routes.purchases import purchases_bp
    from app.routes.tutor_cycle import cycle_bp
    from app.routes.suppliers import suppliers_bp
    from app.routes.shell import shell_bp
    from app.routes.settings import settings_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(quotes_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(reconciliation_bp)
    app.register_blueprint(cycle_bp)
    app.register_blueprint(purchases_bp)
    app.register_blueprint(suppliers_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(shell_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(delivery_notes_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(chatbot_bp)
    app.register_blueprint(historical_imports_bp)
    app.register_blueprint(real_2026_bp)
    app.register_blueprint(predictions_bp)

    @app.before_request
    def _block_withdrawn_customer_sales():
        from app.services.rbac_service import block_withdrawn_customer_sales_request

        return block_withdrawn_customer_sales_request()

    from app.services.exceptions import ApiError

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return jsonify({"error": error.message}), error.status_code

    @app.errorhandler(404)
    def handle_not_found(_):
        return jsonify({"error": "Ruta no encontrada."}), 404

    @app.errorhandler(405)
    def handle_method_not_allowed(_):
        return jsonify({"error": "Método HTTP no permitido para esta ruta."}), 405

    @app.errorhandler(413)
    def handle_payload_too_large(_):
        return jsonify({"error": "La petición excede el tamaño permitido."}), 413

    return app
