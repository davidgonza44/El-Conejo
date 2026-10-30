"""Script inicial de base de datos.

Crea la base de datos MySQL (si no existe), las tablas y los datos semilla.
Es idempotente: puede ejecutarse varias veces sin duplicar datos.

Uso:
    python scripts/init_db.py
"""
import os
import sys

# Permite ejecutar el script desde la raíz del proyecto o desde scripts/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pymysql

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import AuthIdentity, Category, Product, User
from app.models.auth_identity import PROVIDER_LOCAL
from app.models.user import ROLE_ADMIN


def create_database_if_not_exists() -> None:
    """Crea la base de datos con conexión directa a MySQL (sin seleccionar BD)."""
    connection = pymysql.connect(
        host=Config.DB_HOST,
        port=int(Config.DB_PORT),
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{Config.DB_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()
        print(f"[OK] Base de datos '{Config.DB_NAME}' verificada/creada.")
    finally:
        connection.close()


def seed_admin_user() -> None:
    if User.query.first() is not None:
        print("[--] Usuarios ya existen, se omite la siembra.")
        return

    admin = User(
        name="Administrador",
        email=os.getenv("ADMIN_EMAIL", "admin@elconejo.com"),
        username="admin",
        role=ROLE_ADMIN,
        email_verified=True,
    )
    admin.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
    db.session.add(admin)
    db.session.flush()
    db.session.add(
        AuthIdentity(
            user_id=admin.id,
            provider=PROVIDER_LOCAL,
            provider_user_id=str(admin.id),
            email=admin.email,
        )
    )
    print(f"[OK] Usuario administrador creado: {admin.email}")


def seed_categories() -> None:
    if Category.query.first() is not None:
        print("[--] Categorías ya existen, se omite la siembra.")
        return

    categories = [
        Category(name="Construcción", description="Cemento, arena, bloques y materiales de obra"),
        Category(name="Electricidad", description="Cables, breakers, tomas e iluminación"),
        Category(name="Plomería y grifería", description="Tubos, conexiones, llaves y grifos"),
        Category(name="Pinturas", description="Pinturas, fondos, brochas y rodillos"),
        Category(name="Herramientas", description="Herramientas manuales y eléctricas"),
        Category(name="Ferretería en general", description="Tornillería, candados, adhesivos y misceláneos"),
    ]
    db.session.add_all(categories)
    print(f"[OK] {len(categories)} categorías creadas.")


def seed_products() -> None:
    if Product.query.first() is not None:
        print("[--] Productos ya existen, se omite la siembra.")
        return

    def category_id(name: str) -> int:
        return Category.query.filter_by(name=name).one().id

    products = [
        Product(
            code="CON-001",
            name="Cemento gris 42.5 kg",
            description="Saco de cemento Portland gris de 42.5 kg",
            category_id=category_id("Construcción"),
            unit="saco",
            current_stock=120,
            minimum_stock=30,
            purchase_price=6.50,
            sale_price=8.00,
        ),
        Product(
            code="ELE-001",
            name="Cable THW 12 AWG (metro)",
            description="Cable de cobre THW calibre 12, venta por metro",
            category_id=category_id("Electricidad"),
            unit="metro",
            current_stock=350,
            minimum_stock=100,
            purchase_price=0.80,
            sale_price=1.20,
        ),
        Product(
            code="PLO-001",
            name="Tubo PVC 1/2\" x 3 m",
            description="Tubo PVC para agua fría de 1/2 pulgada por 3 metros",
            category_id=category_id("Plomería y grifería"),
            unit="unidad",
            current_stock=45,
            minimum_stock=20,
            purchase_price=2.10,
            sale_price=3.00,
        ),
        Product(
            code="HER-001",
            name="Martillo de uña 16 oz",
            description="Martillo de uña con mango de fibra de vidrio",
            category_id=category_id("Herramientas"),
            unit="unidad",
            current_stock=8,
            minimum_stock=10,
            purchase_price=4.75,
            sale_price=7.50,
        ),
    ]
    db.session.add_all(products)
    print(f"[OK] {len(products)} productos de ejemplo creados (uno en bajo stock).")


def main() -> None:
    create_database_if_not_exists()

    app = create_app()
    with app.app_context():
        db.create_all()
        print("[OK] Tablas creadas/verificadas.")

        seed_admin_user()
        seed_categories()
        db.session.commit()

        seed_products()
        db.session.commit()

    print("[OK] Inicialización completada.")


if __name__ == "__main__":
    main()
