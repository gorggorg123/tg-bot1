"""Warehouse domain models and lightweight utilities.

The module intentionally keeps models simple to stay compatible with future
persistence layers (e.g. SQLAlchemy mappings) while being immediately usable
in memory for bot flows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel


class Product(BaseModel):
    """Represents a sellable product in the ITOM warehouse linked to Ozon catalog."""

    sku: str
    name: str
    ozon_offer_id: str | None = None
    ozon_sku: int | None = None
    ozon_product_id: str | None = None
    barcode: str | None = None
    weight_kg: float | None = None
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None


class Location(BaseModel):
    """Physical warehouse location (rack/shelf/cell)."""

    id: str
    name: str | None = None


class Box(BaseModel):
    """Physical box/bin/pallet on the warehouse that contains items of a single product."""

    id: str
    product: Product
    quantity: int
    location: Location
    created_at: datetime | None = None
    last_inventory_at: datetime | None = None


class Movement(BaseModel):
    """Stock movement record (receipt, move, dispatch, inventory delta)."""

    id: str
    type: Literal["RECEIPT", "MOVE", "DISPATCH", "INVENTORY"]
    product: Product
    quantity: int
    from_box: Box | None = None
    to_box: Box | None = None
    timestamp: datetime
    reference: str | None = None


def sum_local_quantity(product: Product, boxes: list[Box]) -> int:
    """Sum quantity of all boxes for a given product SKU."""

    target_sku = (product.sku or "").strip()
    return sum(box.quantity for box in boxes if box.product.sku == target_sku)


def _next_numeric_id(prefix: str, existing_ids: set[str]) -> str:
    numbers: list[int] = []
    for raw in existing_ids:
        if not isinstance(raw, str):
            continue
        if not raw.startswith(prefix):
            continue
        try:
            numbers.append(int(raw[len(prefix) :]))
        except Exception:
            continue
    next_num = (max(numbers) + 1) if numbers else 1
    return f"{prefix}{next_num:03d}"


def generate_box_id(existing_ids: set[str]) -> str:
    """Generate a sequential box id like ``B001`` based on existing ids."""

    return _next_numeric_id("B", existing_ids)


def generate_movement_id(existing_ids: set[str]) -> str:
    """Generate a sequential movement id like ``M001`` based on existing ids."""

    return _next_numeric_id("M", existing_ids)


class ProductRepository(Protocol):
    """Repository interface for persisting warehouse products."""

    async def get_by_sku(self, sku: str) -> Product | None: ...

    async def find_by_barcode(self, barcode: str) -> Product | None: ...

    async def search_by_name(self, query: str) -> list[Product]: ...

    async def save(self, product: Product) -> Product: ...


class BoxRepository(Protocol):
    """Repository interface for boxes."""

    async def list_by_product(self, sku: str) -> list[Box]: ...

    async def get(self, box_id: str) -> Box | None: ...

    async def save(self, box: Box) -> Box: ...

    async def delete(self, box_id: str) -> None: ...


class MovementRepository(Protocol):
    """Repository interface for stock movements."""

    async def save(self, movement: Movement) -> Movement: ...

    async def list_all(self) -> list[Movement]: ...


__all__ = [
    "Box",
    "Location",
    "Movement",
    "Product",
    "ProductRepository",
    "BoxRepository",
    "MovementRepository",
    "generate_box_id",
    "generate_movement_id",
    "sum_local_quantity",
]
