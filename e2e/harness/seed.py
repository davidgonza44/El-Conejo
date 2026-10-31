"""Dataset sintético mínimo para E2E, creado con los servicios de dominio y el rol correcto.

Respeta la separación de funciones vigente: el asistente propone categorías, proveedores y
productos; el administrador los aprueba; inventario mueve stock y el vendedor emite notas.
Así el stock solo cambia por los servicios y cada cambio deja su movimiento.
"""
from flask_login import login_user

from app.services import (category_service, customer_service, delivery_note_service, inventory_service,
                          product_service, product_supplier_service, supplier_service)

CATEGORIES = (("E2E Herramientas", "hammer", "blue"), ("E2E Construcción", "package", "orange"),
              ("E2E Electricidad", "zap", "teal"))
# código, nombre, categoría, stock inicial, mínimo, costo, precio
PRODUCTS = (
    ("E2E-001", "Martillo de uña 16 oz", 0, 40, 10, "6.50", "11.90"),
    ("E2E-002", "Destornillador plano 1/4", 0, 25, 8, "2.10", "4.25"),
    ("E2E-003", "Cemento gris 42.5 kg", 1, 6, 12, "8.90", "12.40"),
    ("E2E-004", "Arena lavada m3", 1, 0, 3, "18.00", "29.00"),
    ("E2E-005", "Cable THW 12 AWG rollo", 2, 15, 5, "38.00", "54.00"),
    ("E2E-006", "Interruptor sencillo", 2, 60, 20, "1.20", "2.60"),
    ("E2E-007", "Cinta aislante negra", 2, 4, 10, "0.80", "1.75"),
    ("E2E-008", "Nivel de burbuja 24 in", 0, 12, 4, "9.40", "15.80"),
)


def _approve(admin, decide, rows):
    login_user(admin)
    for row in rows:
        decide(row.id, approve=True)


def seed(users):
    admin, asistente = users["admin"], users["asistente"]

    login_user(asistente)
    categories = [category_service.create_category({"name": name, "icon": icon, "color": color,
                                                   "description": "Categoría sintética de pruebas E2E"})
                  for name, icon, color in CATEGORIES]
    suppliers = [supplier_service.save({"name": f"E2E Proveedor {n}", "tax_id": f"J-9000000{n}-0",
                                        "contact_name": f"Contacto E2E {n}", "phone": "0241-0000000",
                                        "email": f"proveedor{n}@example.invalid", "category": "Ferretería",
                                        "address": "Zona industrial ficticia", "city": "Valencia",
                                        "notes": "Proveedor sintético de pruebas", "is_active": True})
                 for n in (1, 2)]
    _approve(admin, category_service.decide_category, categories)
    _approve(admin, supplier_service.decide, suppliers)

    login_user(asistente)
    products = [product_service.create_product({
        "code": code, "name": name, "category_id": categories[cat].id, "current_stock": stock,
        "minimum_stock": minimum, "purchase_price": cost, "purchase_price_currency": "USD",
        "sale_price": price, "sale_price_currency": "USD", "unit": "unidad",
        "description": "Producto sintético de pruebas E2E"})
        for code, name, cat, stock, minimum, cost, price in PRODUCTS]
    _approve(admin, product_service.decide_product, products)

    # Asociación explícita producto-proveedor: requisito para cotizar y comprar.
    login_user(asistente)
    for product in products:
        product_supplier_service.create_link(product.id, {
            "supplier_id": suppliers[0].id, "purchase_price": str(product.purchase_price),
            "purchase_price_currency": "USD", "is_preferred": True})

    login_user(users["inventario"])
    inventory_service.register_exit({"product_id": products[0].id, "quantity": 3, "reason": "Salida E2E de mostrador"})
    inventory_service.register_entry({"product_id": products[5].id, "quantity": 10, "reason": "Entrada E2E de reposición"})

    login_user(users["vendedor"])
    customer = customer_service.create_customer({"name": "E2E Constructora Prueba", "phone": "0414-0000000",
                                                 "email": "cliente@example.invalid"})
    delivery_note_service.create_note({"customer_name": customer.name, "customer_document": "J-90000099-1",
                                       "items": [{"product_id": products[1].id, "quantity": 2,
                                                  "expected_unit_price": str(products[1].sale_price)}]})
    return {"categories": len(categories), "suppliers": len(suppliers), "products": len(products)}
