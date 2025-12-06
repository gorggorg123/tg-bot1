"""Lightweight product catalog cache sourced from Ozon Seller API."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List

from pydantic import BaseModel

from botapp.ozon_client import (
    OzonAPIError,
    ProductInfoItem,
    ProductListItem,
    get_client,
)

logger = logging.getLogger(__name__)


class CatalogProduct(BaseModel):
    sku: str
    name: str
    ozon_product_id: int | None = None
    ozon_sku: int | None = None
    barcode: str | None = None


_catalog_cache: list[CatalogProduct] = []
_catalog_updated_at: datetime | None = None
_CATALOG_TTL = timedelta(minutes=15)


async def _fetch_product_list() -> list[ProductListItem]:
    client = get_client()
    last_id: str | None = None
    items: list[ProductListItem] = []

    while True:
        page = await client.list_products(limit=100, last_id=last_id)
        items.extend(page.items)
        if not page.last_id:
            break
        last_id = page.last_id

    return items


async def _fetch_product_info_map(ids: list[int]) -> Dict[int, ProductInfoItem]:
    """Load product info items keyed by product_id.

    Errors from Ozon API are logged and result in an empty map so that catalog
    refresh continues without barcodes/details rather than breaking flows.
    """

    client = get_client()
    info_map: Dict[int, ProductInfoItem] = {}

    chunk_size = 1000
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        try:
            info_items = await client.get_product_info_list(product_ids=chunk)
        except OzonAPIError as exc:
            logger.warning("Failed to fetch product info list: %s", exc)
            return {}

        for info in info_items:
            try:
                pid = int(info.product_id) if info.product_id is not None else None
            except (TypeError, ValueError):
                pid = None

            if pid is None:
                continue
            info_map[pid] = info

    return info_map


def _to_catalog_product(item: ProductListItem, info_map: Dict[int, ProductInfoItem]) -> CatalogProduct:
    info = info_map.get(item.product_id) if item.product_id is not None else None
    name = (info.name if info else None) or item.name or item.offer_id or str(item.sku or "")
    barcode = None
    if info:
        barcode = info.barcode or next((b for b in info.barcodes if b), None)

    return CatalogProduct(
        sku=(item.offer_id or str(item.sku or item.product_id or "")).strip(),
        name=name,
        ozon_product_id=item.product_id,
        ozon_sku=item.sku,
        barcode=barcode,
    )


async def refresh_catalog_from_ozon(force: bool = False) -> list[CatalogProduct]:
    """
    Load catalog items from Ozon Seller API and update local cache.

    Uses /v3/product/list for identifiers and /v3/product/info/list for details.
    Reuses cached data if it is younger than ``_CATALOG_TTL`` unless ``force``
    is set to ``True``.
    """

    global _catalog_cache, _catalog_updated_at

    now = datetime.utcnow()
    if (
        not force
        and _catalog_cache
        and _catalog_updated_at
        and now - _catalog_updated_at < _CATALOG_TTL
    ):
        return _catalog_cache

    items = await _fetch_product_list()
    filtered = [item for item in items if (item.visibility or "").upper() != "ARCHIVED"]
    product_ids = [item.product_id for item in filtered if item.product_id]
    info_map = await _fetch_product_info_map(product_ids) if product_ids else {}

    _catalog_cache = [_to_catalog_product(item, info_map) for item in filtered]
    _catalog_updated_at = now
    return _catalog_cache


async def get_catalog(force_refresh: bool = False) -> list[CatalogProduct]:
    if not _catalog_cache or force_refresh:
        return await refresh_catalog_from_ozon(force=force_refresh)
    return _catalog_cache


async def find_by_sku(sku: str) -> CatalogProduct | None:
    catalog = await get_catalog()
    for item in catalog:
        if item.sku == sku:
            return item
    return None


async def search_by_name(query: str, limit: int = 10) -> list[CatalogProduct]:
    catalog = await get_catalog()
    if not query:
        return []
    q = query.lower()
    results: list[CatalogProduct] = []
    for item in catalog:
        if q in item.name.lower():
            results.append(item)
            if len(results) >= limit:
                break
    return results


async def update_barcode_in_cache(sku: str, barcode: str) -> None:
    """Persist a freshly received barcode in the local cache."""

    for item in _catalog_cache:
        if item.sku == sku:
            item.barcode = barcode
            break


__all__ = [
    "CatalogProduct",
    "find_by_sku",
    "get_catalog",
    "refresh_catalog_from_ozon",
    "search_by_name",
    "update_barcode_in_cache",
]
