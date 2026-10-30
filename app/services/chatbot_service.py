"""Servicio determinista y de solo lectura para el chatbot interno.

El servicio reconoce un conjunto cerrado de intenciones en español y consulta
únicamente productos y categorías activos mediante SQLAlchemy. No persiste
conversaciones, no ejecuta código y no usa servicios externos.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.models import Category, Product
from app.models.user import ROLE_ADMIN, ROLE_ASISTENTE, ROLE_SOPORTE_TI, ROLE_VENDEDOR
from app.services.exceptions import ApiError
from app.utils.permissions import INVENTORY_READ, PRODUCTS_READ, PRODUCTS_VIEW_PRICE, role_has_permission


def assistant_title(role: str) -> str:
    """Nombre del asistente, independiente del cargo del usuario."""
    return "Asistente de Inventario"


INTENT_GREETING = "greeting"
INTENT_HELP = "help"
INTENT_PRODUCT_BY_NAME = "product_by_name"
INTENT_PRODUCT_BY_CODE = "product_by_code"
INTENT_PRODUCT_STOCK = "product_stock"
INTENT_PRODUCT_PRICE = "product_price"
INTENT_PRODUCT_CATEGORY = "product_category"
INTENT_PRODUCTS_BY_CATEGORY = "products_by_category"
INTENT_LOW_STOCK = "low_stock_products"
INTENT_OUT_OF_STOCK = "out_of_stock_products"
INTENT_PRODUCT_STATUS = "product_status"
INTENT_UNKNOWN = "unknown"

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_NOT_FOUND = "not_found"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_UNKNOWN = "unknown"

MAX_LIST_RESULTS = 20
MAX_AMBIGUOUS_CANDIDATES = 5
FUZZY_MIN_LENGTH = 4
FUZZY_THRESHOLD = 0.86
FUZZY_CLEAR_GAP = 0.08

_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[a-z0-9ñ]+")
_TRAILING_COURTESY_RE = re.compile(
    r"\s+(?:por favor|porfa|gracias|por favor gracias)\s*$"
)
_LEADING_ENTITY_WORD_RE = re.compile(
    r"^(?:(?:de|del|para|sobre|el|la|los|las|un|una|al)\s+"
    r"|(?:producto|articulo|llamado|denominado|tiene)\s+)+"
)

_CODE_RE = re.compile(
    r"\b(?:c[oó]digo|c[oó]d\.?)\b\s*"
    r"(?:(?:de|del|para\s+el)\s+(?:producto|art[ií]culo)\s+)?"
    r"(?:es\s+)?(?:[:#=-]\s*)?"
    r"([A-Za-z0-9][A-Za-z0-9._/-]{0,49})",
    re.IGNORECASE,
)

_PRODUCTS_BY_CATEGORY_PATTERNS = (
    re.compile(
        r"\b(?:que|cuales|lista|listar|mostrar|muestra|dime)?\s*"
        r"(?:productos?|articulos?)\s+(?:hay\s+)?"
        r"(?:de|en|por)\s+(?:la\s+)?categoria\s+(.+)$"
    ),
    re.compile(
        r"\b(?:productos?|articulos?)\s+categoria\s+(.+)$"
    ),
)

_LOW_STOCK_LIST_RE = re.compile(
    r"(?:\b(?:productos?|articulos?)\b.*\b"
    r"(?:bajo stock|stock bajo|stock critico|por debajo del minimo|reposicion)\b"
    r"|\b(?:que|cuales|lista|listar|mostrar|muestra|dime)\b.*\b"
    r"(?:bajo stock|stock bajo|stock critico|reposicion)\b"
    r"|^(?:bajo stock|stock bajo|reposicion)[?!.]*$)"
)
_OUT_OF_STOCK_LIST_RE = re.compile(
    r"(?:\b(?:productos?|articulos?)\b.*\b"
    r"(?:sin existencia|sin stock|agotados?)\b"
    r"|\b(?:que|cuales|lista|listar|mostrar|muestra|dime)\b.*\b"
    r"(?:sin existencia|sin stock|agotados?)\b"
    r"|^(?:sin existencia|sin stock|agotados?)[?!.]*$)"
)

_PRICE_RE = re.compile(
    r"\b(?:precio|precio de venta|valor|cuanto cuesta|cuanto vale)\b"
)
_STOCK_RE = re.compile(
    r"\b(?:stock|existencia|cantidad disponible|cuanto hay|cuantos hay|"
    r"cuanto queda|cuantos quedan)\b"
)
_CATEGORY_RE = re.compile(
    r"\b(?:categoria|rubro|a que categoria pertenece)\b"
)
_STATUS_RE = re.compile(
    r"\b(?:estado general|estado|disponibilidad|esta disponible|hay disponible)\b"
)
_HELP_RE = re.compile(
    r"\b(?:ayuda|opciones|comandos|que puedes hacer|como puedes ayudar)\b"
)
_GREETING_RE = re.compile(
    r"\b(?:hola|buenas|buenos dias|buen dia|buenas tardes|"
    r"buenas noches|saludos?|hey)\b"
)

_PRICE_ENTITY_PATTERNS = (
    re.compile(
        r"\b(?:precio(?: de venta)?|valor)\s+"
        r"(?:del|de|para)?\s*(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
    re.compile(
        r"\bcuanto\s+(?:cuesta|vale)\s+(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
)
_STOCK_ENTITY_PATTERNS = (
    re.compile(
        r"\bstock(?:\s+(?:actual|minimo|exacto))?\s+"
        r"(?:del|de|para)?\s*(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
    re.compile(
        r"\b(?:existencia|cantidad disponible)\s+"
        r"(?:del|de|para)?\s*(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
    re.compile(
        r"\bcuantos?\s+(?:hay|queda|quedan)\s+(?:del|de)?\s*"
        r"(?:el|la)?\s*(?:producto|articulo)?\s*(.+)$"
    ),
)
_CATEGORY_ENTITY_PATTERNS = (
    re.compile(
        r"\b(?:categoria|rubro)\s+(?:del|de)?\s*(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
    re.compile(
        r"\ba\s+que\s+categoria\s+pertenece\s+(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
)
_STATUS_ENTITY_PATTERNS = (
    re.compile(
        r"\b(?:estado general|estado|disponibilidad)\s+"
        r"(?:del|de)?\s*(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
    re.compile(
        r"\b(?:esta disponible|hay disponible)\s+(?:el|la)?\s*"
        r"(?:producto|articulo)?\s*(.+)$"
    ),
)
_NAME_ENTITY_PATTERNS = (
    re.compile(
        r"\b(?:producto|articulo)\s+(?:llamado|denominado)?\s*(.+)$"
    ),
    re.compile(
        r"\b(?:buscar|busca|consultar|consulta|mostrar|muestra|ver)\s+"
        r"(?:el|la)?\s*(?:producto|articulo)?\s*(.+)$"
    ),
    re.compile(
        r"\b(?:informacion|datos|detalle)\s+(?:del|de|sobre)?\s*"
        r"(?:el|la)?\s*(?:producto|articulo)?\s*(.+)$"
    ),
)

_SAFE_PRODUCT_IMAGE_RE = re.compile(
    r"^/media/products/"
    r"([A-Za-z0-9][A-Za-z0-9_-]{0,199}\.(?:jpg|jpeg|png|webp))$",
    re.IGNORECASE,
)

_ACCENT_TRANSLATION = str.maketrans(
    {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "Á": "a",
        "É": "e",
        "Í": "i",
        "Ó": "o",
        "Ú": "u",
        "Ü": "u",
    }
)

_ALL_INTENTS = {
    INTENT_GREETING,
    INTENT_HELP,
    INTENT_PRODUCT_BY_NAME,
    INTENT_PRODUCT_BY_CODE,
    INTENT_PRODUCT_STOCK,
    INTENT_PRODUCT_PRICE,
    INTENT_PRODUCT_CATEGORY,
    INTENT_PRODUCTS_BY_CATEGORY,
    INTENT_LOW_STOCK,
    INTENT_OUT_OF_STOCK,
    INTENT_PRODUCT_STATUS,
    INTENT_UNKNOWN,
}


@dataclass(frozen=True)
class DetectedIntent:
    """Intención detectada junto con la entidad o código extraído."""

    name: str
    entity: str | None = None
    code: str | None = None


@dataclass(frozen=True)
class ProductLookup:
    """Resultado de buscar un producto por código o nombre."""

    product: Product | None = None
    candidates: tuple[Product, ...] = ()


def normalize_text(value: str) -> str:
    """Normaliza español sin convertir la ñ en n."""
    if not isinstance(value, str):
        return ""
    normalized = value.translate(_ACCENT_TRANSLATION).lower().strip()
    return _SPACE_RE.sub(" ", normalized)


def _clean_entity(value: str | None) -> str | None:
    if not value:
        return None
    entity = value.strip().strip("¿?¡!.,;:")
    entity = _TRAILING_COURTESY_RE.sub("", entity).strip()
    previous = None
    while entity and entity != previous:
        previous = entity
        entity = _LEADING_ENTITY_WORD_RE.sub("", entity).strip()
    return entity.strip("¿?¡!.,;:") or None


def _extract_with_patterns(
    normalized_message: str, patterns: tuple[re.Pattern[str], ...]
) -> str | None:
    for pattern in patterns:
        match = pattern.search(normalized_message)
        if match:
            return _clean_entity(match.group(1))
    return None


def _extract_code(message: str) -> str | None:
    match = _CODE_RE.search(message)
    if not match:
        return None
    code = match.group(1).rstrip(".,;:!?")
    if normalize_text(code) in {"de", "del", "el", "la", "producto", "articulo", "es"}:
        return None
    return code or None


def detect_intent(message: str) -> DetectedIntent:
    """Detecta una intención usando únicamente reglas y regex precompiladas."""
    normalized = normalize_text(message)

    code = _extract_code(message)
    if code:
        return DetectedIntent(INTENT_PRODUCT_BY_CODE, code=code)

    for pattern in _PRODUCTS_BY_CATEGORY_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return DetectedIntent(
                INTENT_PRODUCTS_BY_CATEGORY,
                entity=_clean_entity(match.group(1)),
            )

    if _LOW_STOCK_LIST_RE.search(normalized):
        return DetectedIntent(INTENT_LOW_STOCK)

    if _OUT_OF_STOCK_LIST_RE.search(normalized):
        return DetectedIntent(INTENT_OUT_OF_STOCK)

    if _PRICE_RE.search(normalized):
        return DetectedIntent(
            INTENT_PRODUCT_PRICE,
            entity=_extract_with_patterns(normalized, _PRICE_ENTITY_PATTERNS),
        )

    if _STOCK_RE.search(normalized):
        return DetectedIntent(
            INTENT_PRODUCT_STOCK,
            entity=_extract_with_patterns(normalized, _STOCK_ENTITY_PATTERNS),
        )

    if _CATEGORY_RE.search(normalized):
        return DetectedIntent(
            INTENT_PRODUCT_CATEGORY,
            entity=_extract_with_patterns(normalized, _CATEGORY_ENTITY_PATTERNS),
        )

    if _STATUS_RE.search(normalized):
        return DetectedIntent(
            INTENT_PRODUCT_STATUS,
            entity=_extract_with_patterns(normalized, _STATUS_ENTITY_PATTERNS),
        )

    name = _extract_with_patterns(normalized, _NAME_ENTITY_PATTERNS)
    if name:
        return DetectedIntent(INTENT_PRODUCT_BY_NAME, entity=name)

    if _HELP_RE.search(normalized):
        return DetectedIntent(INTENT_HELP)

    if _GREETING_RE.search(normalized):
        return DetectedIntent(INTENT_GREETING)

    return DetectedIntent(INTENT_UNKNOWN)


def safe_product_image_url(value: str | None) -> str | None:
    """Permite solo imágenes locales con nombre simple y extensión conocida."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or "\\" in candidate or "%" in candidate or ".." in candidate:
        return None
    match = _SAFE_PRODUCT_IMAGE_RE.fullmatch(candidate)
    if not match:
        return None
    return candidate


def _active_products_query():
    return (
        Product.query.options(joinedload(Product.category))
        .filter(Product.is_active.is_(True))
    )


def _active_products() -> list[Product]:
    return _active_products_query().order_by(Product.name.asc(), Product.id.asc()).all()


def _product_words(value: str) -> set[str]:
    return set(_WORD_RE.findall(normalize_text(value)))


def _limited_candidates(products: list[Product]) -> tuple[Product, ...]:
    ordered = sorted(
        products,
        key=lambda product: (normalize_text(product.name), product.code.lower()),
    )
    return tuple(ordered[:MAX_AMBIGUOUS_CANDIDATES])


def _single_or_candidates(matches) -> ProductLookup | None:
    """Un resultado: el producto. Varios: candidatos ordenados. Ninguno: None."""
    if len(matches) == 1:
        return ProductLookup(product=matches[0])
    if len(matches) > 1:
        return ProductLookup(candidates=_limited_candidates(matches))
    return None


def _active_by_code(query: str):
    return _active_products_query().filter(func.lower(Product.code) == query.lower()).first()


def _fuzzy_lookup(normalized_query: str, products) -> ProductLookup:
    scored = sorted(
        (
            (
                SequenceMatcher(
                    None,
                    normalized_query,
                    normalize_text(product.name),
                    autojunk=False,
                ).ratio(),
                product,
            )
            for product in products
        ),
        key=lambda item: (
            -item[0],
            normalize_text(item[1].name),
            item[1].code.lower(),
        ),
    )
    matches = [item for item in scored if item[0] >= FUZZY_THRESHOLD]
    if not matches:
        return ProductLookup()
    if len(matches) == 1 or matches[0][0] - matches[1][0] >= FUZZY_CLEAR_GAP:
        return ProductLookup(product=matches[0][1])
    return ProductLookup(
        candidates=tuple(
            product for _, product in matches[:MAX_AMBIGUOUS_CANDIDATES]
        )
    )


def find_product(query: str, *, by_code: bool = False) -> ProductLookup:
    """Busca solo productos activos; los códigos nunca usan fuzzy matching."""
    query = (query or "").strip()
    if not query:
        return ProductLookup()
    if by_code:
        return ProductLookup(product=_active_by_code(query))

    # Una entidad de una sola palabra puede ser el código ("stock del producto HER-001"):
    # se prueba la coincidencia exacta de código antes que el nombre.
    if " " not in query:
        product = _active_by_code(query)
        if product is not None:
            return ProductLookup(product=product)

    normalized_query = normalize_text(query)
    products = _active_products()

    exact = [
        product
        for product in products
        if normalize_text(product.name) == normalized_query
    ]
    query_words = _product_words(normalized_query)
    whole_word = [
        product
        for product in products
        if query_words and query_words.issubset(_product_words(product.name))
    ]
    inclusion = [
        product
        for product in products
        if len(normalized_query) >= 3
        and (
            normalized_query in normalize_text(product.name)
            or normalize_text(product.name) in normalized_query
        )
    ]
    for matches in (exact, whole_word, inclusion):
        found = _single_or_candidates(matches)
        if found is not None:
            return found

    if len(normalized_query) < FUZZY_MIN_LENGTH:
        return ProductLookup()
    return _fuzzy_lookup(normalized_query, products)


def _sees_sale_price(role: str) -> bool:
    return role_has_permission(role, PRODUCTS_VIEW_PRICE)


def _sees_stock(role: str) -> bool:
    """Cantidades y bajo stock siguen inventory:read, no el nombre del rol."""
    return role_has_permission(role, INVENTORY_READ)


def _availability(product: Product, role: str) -> str:
    if product.current_stock <= 0:
        return "out_of_stock"
    if _sees_stock(role) and product.is_low_stock:
        return "low_stock"
    return "available"


def _base_product(product: Product) -> dict:
    return {
        "id": product.id,
        "code": product.code,
        "name": product.name,
        "category": product.category.name if product.category else None,
        "image_url": safe_product_image_url(product.image_url),
    }


def _serialize_candidate(product: Product) -> dict:
    return _base_product(product)


def _serialize_product(product: Product, role: str, intent: str) -> dict:
    """Serializa por lista blanca según rol e intención."""
    data = _base_product(product)

    if intent == INTENT_PRODUCT_CATEGORY:
        return data

    if intent == INTENT_PRODUCT_PRICE:
        if _sees_sale_price(role):
            data["sale_price"] = float(product.sale_price)
        return data

    if intent == INTENT_PRODUCT_STOCK:
        data["availability"] = _availability(product, role)
        if _sees_stock(role):
            data["current_stock"] = product.current_stock
            data["minimum_stock"] = product.minimum_stock
        return data

    if intent in {INTENT_PRODUCT_STATUS, INTENT_PRODUCT_BY_NAME, INTENT_PRODUCT_BY_CODE}:
        if role in {ROLE_ADMIN, ROLE_VENDEDOR} or intent == INTENT_PRODUCT_STATUS:
            data["description"] = product.description
        if _sees_sale_price(role):
            data["sale_price"] = float(product.sale_price)
        data["availability"] = _availability(product, role)
        if _sees_stock(role):
            data["current_stock"] = product.current_stock
            data["minimum_stock"] = product.minimum_stock
        return data

    if intent in {
        INTENT_PRODUCTS_BY_CATEGORY,
        INTENT_LOW_STOCK,
        INTENT_OUT_OF_STOCK,
    }:
        if role in {ROLE_ADMIN, ROLE_VENDEDOR, ROLE_ASISTENTE}:
            data["description"] = product.description
        if _sees_sale_price(role):
            data["sale_price"] = float(product.sale_price)
        data["availability"] = _availability(product, role)
        if _sees_stock(role):
            data["current_stock"] = product.current_stock
            data["minimum_stock"] = product.minimum_stock
        return data

    return data


def _response(
    intent: str,
    status: str,
    message: str,
    *,
    data: dict | None = None,
    suggestions: list[str] | None = None,
) -> dict:
    return {
        "intent": intent,
        "status": status,
        "message": message,
        "data": data or {},
        "suggestions": suggestions or [],
    }


def _availability_text(value: str) -> str:
    return {
        "available": "disponible",
        "low_stock": "disponible, pero bajo stock",
        "out_of_stock": "sin existencia",
    }[value]


def _format_price_message(value) -> str:
    """Formatea dos decimales en español solo para el texto conversacional."""
    return f"{float(value):.2f}".replace(".", ",")


def build_product_response(product: Product, intent: str, role: str) -> dict:
    """Construye una respuesta de producto sin exponer campos no autorizados."""
    serialized = _serialize_product(product, role, intent)
    name = product.name

    if intent == INTENT_PRODUCT_STOCK:
        availability = _availability(product, role)
        if not _sees_stock(role):
            message = f"El producto '{name}' está {_availability_text(availability)}."
        else:
            message = (
                f"El stock actual de '{name}' es {product.current_stock} "
                f"y su stock mínimo es {product.minimum_stock}."
            )
    elif intent == INTENT_PRODUCT_PRICE:
        message = (
            f"El precio de venta de '{name}' es "
            f"{_format_price_message(product.sale_price)}."
        )
    elif intent == INTENT_PRODUCT_CATEGORY:
        category = product.category.name if product.category else None
        message = (
            f"'{name}' pertenece a la categoría '{category}'."
            if category
            else f"'{name}' no tiene una categoría registrada."
        )
    elif intent == INTENT_PRODUCT_STATUS:
        availability = _availability(product, role)
        message = f"El producto '{name}' está {_availability_text(availability)}."
    else:
        message = f"Encontré el producto activo '{name}' ({product.code})."

    return _response(
        intent,
        STATUS_OK,
        message,
        data={"product": serialized},
    )


def _help_suggestions(role: str) -> list[str]:
    product = (
        _active_products_query()
        .order_by(Product.name.asc(), Product.id.asc())
        .first()
    )
    suggestions = ["Productos agotados"]
    if product is not None:
        suggestions.extend(
            [
                f"Buscar producto {product.name}",
                f"Código {product.code}",
                *([f"Precio de {product.name}"] if _sees_sale_price(role) else []),
                f"Categoría de {product.name}",
            ]
        )
        if product.category is not None:
            suggestions.append(
                f"Productos de la categoría {product.category.name}"
            )
    if _sees_stock(role):
        if product is not None:
            suggestions.append(f"Stock de {product.name}")
        suggestions.extend(
            [
                "Productos bajo stock",
            ]
        )
    elif product is not None:
        suggestions.append(f"¿Está disponible {product.name}?")
    return suggestions


def build_help_response(role: str) -> dict:
    """Devuelve ayuda ajustada a las consultas permitidas para el rol."""
    capabilities = "Puedo consultar productos activos por nombre o código, categoría, disponibilidad, productos por categoría y productos agotados."
    if _sees_sale_price(role):
        capabilities += " También puedo consultar precios de venta."
    if _sees_stock(role):
        capabilities += " Puedo mostrar el stock actual y mínimo, y los productos bajo stock."
    return _response(
        INTENT_HELP,
        STATUS_OK,
        capabilities,
        suggestions=_help_suggestions(role),
    )


def _not_found_response(intent: str, query: str | None) -> dict:
    if query:
        message = f"No encontré un producto activo para '{query}'."
    else:
        message = "No pude identificar el producto. Indica su nombre o código."
    return _response(
        intent,
        STATUS_NOT_FOUND,
        message,
        data={"query": query},
        suggestions=["Indica el nombre completo", "Indica el código exacto"],
    )


def _ambiguous_response(
    intent: str, candidates: tuple[Product, ...]
) -> dict:
    items = [_serialize_candidate(product) for product in candidates]
    return _response(
        intent,
        STATUS_NEEDS_CLARIFICATION,
        "Encontré varios productos similares. Indica el código exacto.",
        data={"count": len(items), "items": items},
        suggestions=[f"Código {product.code}" for product in candidates],
    )


def _find_category(name: str | None) -> tuple[Category | None, list[Category]]:
    if not name:
        return None, []
    normalized = normalize_text(name)
    categories = Category.query.order_by(Category.name.asc(), Category.id.asc()).all()

    exact = [
        category
        for category in categories
        if normalize_text(category.name) == normalized
    ]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact[:MAX_AMBIGUOUS_CANDIDATES]

    included = [
        category
        for category in categories
        if len(normalized) >= 3
        and (
            normalized in normalize_text(category.name)
            or normalize_text(category.name) in normalized
        )
    ]
    if len(included) == 1:
        return included[0], []
    return None, included[:MAX_AMBIGUOUS_CANDIDATES]


def _category_products_response(category_name: str | None, role: str) -> dict:
    category, ambiguous = _find_category(category_name)
    if ambiguous:
        categories = [
            {
                "id": category.id,
                "name": category.name,
                "description": category.description,
            }
            for category in ambiguous[:MAX_AMBIGUOUS_CANDIDATES]
        ]
        return _response(
            INTENT_PRODUCTS_BY_CATEGORY,
            STATUS_NEEDS_CLARIFICATION,
            "Encontré varias categorías posibles. Indica el nombre exacto.",
            data={"count": len(categories), "categories": categories},
            suggestions=[
                f"Productos de la categoría {category.name}"
                for category in ambiguous[:MAX_AMBIGUOUS_CANDIDATES]
            ],
        )
    if category is None:
        return _response(
            INTENT_PRODUCTS_BY_CATEGORY,
            STATUS_NOT_FOUND,
            "No encontré la categoría solicitada.",
            data={"query": category_name},
            suggestions=["Indica el nombre exacto de la categoría"],
        )

    query = (
        _active_products_query()
        .filter(Product.category_id == category.id)
        .order_by(Product.name.asc(), Product.id.asc())
    )
    products = query.all()
    return _product_list_response(
        INTENT_PRODUCTS_BY_CATEGORY,
        products,
        role,
        empty_message=f"No hay productos activos en la categoría '{category.name}'.",
        success_message=(
            f"Encontré {len(products)} producto(s) activo(s) "
            f"en la categoría '{category.name}'."
        ),
        extra_data={"category": {"id": category.id, "name": category.name}},
    )


def _product_list_response(
    intent: str,
    products: list[Product],
    role: str,
    *,
    empty_message: str,
    success_message: str,
    extra_data: dict | None = None,
) -> dict:
    total = len(products)
    visible = products[:MAX_LIST_RESULTS]
    data = {
        "count": total,
        "items": [
            _serialize_product(product, role, intent) for product in visible
        ],
        "truncated": total > MAX_LIST_RESULTS,
    }
    if extra_data:
        data.update(extra_data)
    return _response(
        intent,
        STATUS_EMPTY if total == 0 else STATUS_OK,
        empty_message if total == 0 else success_message,
        data=data,
    )


def _list_low_stock(role: str) -> dict:
    products = (
        _active_products_query()
        .filter(Product.current_stock <= Product.minimum_stock)
        .order_by(
            (Product.current_stock - Product.minimum_stock).asc(),
            Product.name.asc(),
            Product.id.asc(),
        )
        .all()
    )
    return _product_list_response(
        INTENT_LOW_STOCK,
        products,
        role,
        empty_message="No hay productos activos bajo stock.",
        success_message=f"Encontré {len(products)} producto(s) bajo stock.",
    )


def _list_out_of_stock(role: str) -> dict:
    products = (
        _active_products_query()
        .filter(Product.current_stock <= 0)
        .order_by(Product.name.asc(), Product.id.asc())
        .all()
    )
    return _product_list_response(
        INTENT_OUT_OF_STOCK,
        products,
        role,
        empty_message="No hay productos activos sin existencia.",
        success_message=f"Encontré {len(products)} producto(s) sin existencia.",
    )


def _ensure_intent_allowed(intent: str, role: str) -> None:
    if not role_has_permission(role, PRODUCTS_READ):
        raise ApiError("Acceso denegado: falta el permiso 'products:read'.", status_code=403)
    if intent not in _ALL_INTENTS:
        raise ApiError("Intención no permitida.", status_code=403)
    if intent == INTENT_PRODUCT_PRICE and not _sees_sale_price(role):
        raise ApiError("Acceso denegado: el precio no está disponible para su rol.", status_code=403)
    if intent == INTENT_LOW_STOCK and not _sees_stock(role):
        raise ApiError(
            "Acceso denegado: la información de bajo stock y reposición "
            "está restringida.",
            status_code=403,
        )


def process_message(message: str, role: str) -> dict:
    """Procesa un mensaje validado y devuelve el contrato conversacional."""
    if role == ROLE_SOPORTE_TI:
        return _response(
            INTENT_HELP, STATUS_OK,
            "Soy el Asistente de Inventario. Para su rol, este chat muestra únicamente orientación general "
            "sobre configuración, seguridad y respaldos. No ejecuta acciones; utilice las secciones habilitadas del menú. "
            "No consulto precios, costos, proveedores ni operaciones comerciales.",
            suggestions=[],
        )
    detected = detect_intent(message)
    _ensure_intent_allowed(detected.name, role)

    if detected.name == INTENT_GREETING:
        return _response(
            INTENT_GREETING,
            STATUS_OK,
            f"Hola. Soy el {assistant_title(role)}. ¿Qué deseas consultar?",
            suggestions=_help_suggestions(role),
        )

    if detected.name == INTENT_HELP:
        return build_help_response(role)

    if detected.name == INTENT_UNKNOWN:
        help_response = build_help_response(role)
        return _response(
            INTENT_UNKNOWN,
            STATUS_UNKNOWN,
            "No reconocí la consulta. " + help_response["message"],
            suggestions=help_response["suggestions"],
        )

    if detected.name == INTENT_PRODUCTS_BY_CATEGORY:
        return _category_products_response(detected.entity, role)

    if detected.name == INTENT_LOW_STOCK:
        return _list_low_stock(role)

    if detected.name == INTENT_OUT_OF_STOCK:
        return _list_out_of_stock(role)

    if detected.name == INTENT_PRODUCT_BY_CODE:
        lookup = find_product(detected.code or "", by_code=True)
        if lookup.product is None:
            return _not_found_response(detected.name, detected.code)
        return build_product_response(lookup.product, detected.name, role)

    lookup = find_product(detected.entity or "")
    if lookup.candidates:
        return _ambiguous_response(detected.name, lookup.candidates)
    if lookup.product is None:
        return _not_found_response(detected.name, detected.entity)
    return build_product_response(lookup.product, detected.name, role)
