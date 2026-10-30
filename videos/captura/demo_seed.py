"""Datos ficticios para grabar los videos tutoriales. Solo viven en la SQLite temporal del arnés E2E.

Sigue la misma separación de funciones que e2e/harness/seed.py: el asistente propone, el
administrador aprueba, inventario mueve stock y el vendedor emite notas de entrega. Las ventas a
clientes están retiradas del alcance operativo, así que no se crean ventas.
"""
from flask_login import login_user

from app.extensions import db
from app.services import (category_service, customer_service, delivery_note_service, inventory_service,
                          product_service, product_supplier_service, supplier_service)

# Nombre y usuario visibles en el video (mismo mapa en capturar.mjs); las contraseñas de prueba
# siguen en e2e/fixtures/users.json.
PEOPLE = {"admin": ("Ana Rivas", "arivas"), "inventario": ("Luis Mendoza", "lmendoza"),
          "asistente": ("Carla Pérez", "cperez"), "vendedor": ("José Herrera", "jherrera"),
          "soporte_ti": ("Pedro Salas", "psalas")}

CATEGORIES = (("Herramientas", "hammer", "blue"), ("Construcción", "hard-hat", "orange"),
              ("Electricidad", "zap", "teal"), ("Plomería", "droplet", "purple"))
SUPPLIERS = (("Distribuidora Andina", "J-41000001-1"), ("Aceros del Centro", "J-41000002-2"),
             ("Eléctricos Carabobo", "J-41000003-3"))
# código, nombre, categoría, stock inicial, mínimo, costo, precio
PRODUCTS = (
    ("HER-001", "Martillo de uña 16 oz", 0, 42, 10, "6.50", "11.90"),
    ("HER-002", "Destornillador plano 1/4", 0, 35, 8, "2.10", "4.25"),
    ("HER-003", "Nivel de burbuja 24 in", 0, 14, 4, "9.40", "15.80"),
    ("HER-004", "Llave ajustable 10 in", 0, 6, 8, "7.80", "13.50"),
    ("CON-001", "Cemento gris 42,5 kg", 1, 60, 25, "8.90", "12.40"),
    ("CON-002", "Cabilla 3/8 x 6 m", 1, 120, 40, "4.30", "6.90"),
    ("CON-003", "Bloque de arcilla 15 cm", 1, 18, 200, "0.45", "0.80"),
    ("CON-004", "Arena lavada m3", 1, 0, 3, "18.00", "29.00"),
    ("ELE-001", "Cable THW 12 AWG rollo", 2, 22, 5, "38.00", "54.00"),
    ("ELE-002", "Interruptor sencillo", 2, 75, 20, "1.20", "2.60"),
    ("ELE-003", "Cinta aislante negra", 2, 4, 10, "0.80", "1.75"),
    ("ELE-004", "Bombillo LED 12 W", 2, 90, 30, "1.10", "2.40"),
    ("PLO-001", "Tubo PVC 1/2 x 6 m", 3, 48, 15, "2.60", "4.70"),
    ("PLO-002", "Llave de paso 1/2", 3, 16, 6, "3.90", "7.20"),
    ("PLO-003", "Teflón 3/4", 3, 0, 12, "0.30", "0.90"),
)
CUSTOMERS = (("Constructora Los Guayos", "J-30111222-0", "0241-8123344"),
             ("Inversiones Naguanagua", "J-30333444-5", "0241-8456677"),
             ("María Fernández", "V-15123456", "0414-4123456"),
             ("Taller Eléctrico Flor Amarillo", "J-30555666-1", "0412-4987654"))
# (cliente, [(código, cantidad)]): despachos con nota de entrega.
NOTES = (
    (0, [("CON-001", 5), ("CON-002", 10)]), (2, [("HER-002", 2), ("ELE-004", 6)]),
    (1, [("CON-001", 6), ("PLO-001", 8)]), (3, [("ELE-001", 2), ("ELE-002", 12)]),
)


def _approve(admin, decide, rows):
    login_user(admin)
    for row in rows:
        decide(row.id, approve=True)


def _rename(users):
    for role, user in users.items():
        user.name, user.username = PEOPLE[role]
        user.email = f"{user.username}@elconejo.example"
    db.session.commit()


def _catalog(users):
    admin, asistente = users["admin"], users["asistente"]
    login_user(asistente)
    categories = [category_service.create_category({"name": name, "icon": icon, "color": color,
                                                   "description": f"Productos de {name.lower()}"})
                  for name, icon, color in CATEGORIES]
    suppliers = [supplier_service.save({"name": name, "tax_id": rif, "contact_name": "Atención comercial",
                                        "phone": "0241-0000000", "email": f"ventas{n}@proveedor.example",
                                        "category": "Ferretería", "address": "Zona industrial",
                                        "city": "Valencia", "notes": "", "is_active": True})
                 for n, (name, rif) in enumerate(SUPPLIERS, start=1)]
    _approve(admin, category_service.decide_category, categories)
    _approve(admin, supplier_service.decide, suppliers)

    login_user(asistente)
    products = {code: product_service.create_product({
        "code": code, "name": name, "category_id": categories[cat].id, "current_stock": stock,
        "minimum_stock": minimum, "purchase_price": cost, "purchase_price_currency": "USD",
        "sale_price": price, "sale_price_currency": "USD", "unit": "unidad",
        "description": f"{name}. Producto de catálogo."})
        for code, name, cat, stock, minimum, cost, price in PRODUCTS}
    _approve(admin, product_service.decide_product, products.values())

    login_user(asistente)
    for code, product in products.items():
        supplier = suppliers[{"E": 2, "C": 1}.get(code[0], 0)]
        product_supplier_service.create_link(product.id, {
            "supplier_id": supplier.id, "purchase_price": str(product.purchase_price),
            "purchase_price_currency": "USD", "is_preferred": True})
    # Queda pendiente a propósito: Inicio muestra «Propuestas pendientes» al administrador.
    product_service.create_product({
        "code": "HER-005", "name": "Juego de brocas para concreto", "category_id": categories[0].id,
        "current_stock": 10, "minimum_stock": 4, "purchase_price": "8.20", "purchase_price_currency": "USD",
        "sale_price": "14.90", "sale_price_currency": "USD", "unit": "unidad",
        "description": "Juego de 5 brocas."})
    return products


def seed(users):
    _rename(users)
    products = _catalog(users)

    login_user(users["inventario"])
    inventory_service.register_entry({"product_id": products["ELE-002"].id, "quantity": 25,
                                      "reason": "Reposición de proveedor"})
    inventory_service.register_exit({"product_id": products["HER-001"].id, "quantity": 2,
                                     "reason": "Uso interno del taller"})

    login_user(users["vendedor"])
    customers = [customer_service.create_customer({"name": name, "tax_id": rif, "phone": phone,
                                                   "email": f"cliente{n}@cliente.example"})
                 for n, (name, rif, phone) in enumerate(CUSTOMERS, start=1)]
    for customer, lines in NOTES:
        delivery_note_service.create_note({
            "customer_name": customers[customer].name, "customer_document": customers[customer].tax_id,
            "items": [{"product_id": products[code].id, "quantity": qty} for code, qty in lines]})
    return {"categories": len(CATEGORIES), "products": len(products), "notes": len(NOTES)}
