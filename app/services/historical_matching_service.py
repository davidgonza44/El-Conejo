"""Matching puro de productos para importaciones históricas.

Solo el código NFC/uppercase exacto produce match automático. El nombre se
usa únicamente para sugerir una revisión administrativa.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.historical_validation_service import normalize_code, normalize_name


@dataclass(frozen=True)
class ProductView:
    id: int
    code: str
    name: str
    is_active: bool


@dataclass(frozen=True)
class ProductIndexes:
    by_code: dict[str, tuple[ProductView, ...]]
    by_name: dict[str, tuple[ProductView, ...]]
    normalized_code_collisions: frozenset[str]


@dataclass(frozen=True)
class ProductMatch:
    status: str
    product: ProductView | None = None
    suggested_product: ProductView | None = None


def build_product_indexes(products: Iterable[object]) -> ProductIndexes:
    code_lists: dict[str, list[ProductView]] = {}
    name_lists: dict[str, list[ProductView]] = {}

    for product in products:
        view = ProductView(
            id=int(getattr(product, "id")),
            code=str(getattr(product, "code")),
            name=str(getattr(product, "name")),
            is_active=bool(getattr(product, "is_active")),
        )
        code_lists.setdefault(normalize_code(view.code), []).append(view)
        name_lists.setdefault(normalize_name(view.name), []).append(view)

    by_code = {key: tuple(value) for key, value in code_lists.items()}
    by_name = {key: tuple(value) for key, value in name_lists.items()}
    collisions = frozenset(
        key for key, candidates in by_code.items() if len(candidates) > 1
    )
    return ProductIndexes(
        by_code=by_code,
        by_name=by_name,
        normalized_code_collisions=collisions,
    )


def match_product(
    product_code_normalized: str,
    product_name_normalized: str | None,
    indexes: ProductIndexes,
) -> ProductMatch:
    code_candidates = indexes.by_code.get(product_code_normalized, ())
    if len(code_candidates) > 1:
        return ProductMatch(status="code_collision")
    if len(code_candidates) == 1:
        product = code_candidates[0]
        return ProductMatch(
            status="exact_inactive" if not product.is_active else "exact",
            product=product,
        )

    if product_name_normalized:
        name_candidates = indexes.by_name.get(product_name_normalized, ())
        if len(name_candidates) == 1:
            return ProductMatch(
                status="name_suggestion",
                suggested_product=name_candidates[0],
            )

    return ProductMatch(status="unmatched")
