"""Helpers for connecting local warehouse models with Ozon stocks."""

from __future__ import annotations

import logging
from typing import Dict

from pydantic import BaseModel, Field

from botapp.ozon_client import get_client
from botapp.warehouse_models import Product

logger = logging.getLogger(__name__)


class OzonStock(BaseModel):
    ozon_total: int
    ozon_reserved: int
    by_warehouse: Dict[str, int] = Field(default_factory=dict)


async def get_ozon_stock_for_product(product: Product) -> OzonStock:
    """Fetch stock info for given product from Ozon Seller API."""

    client = get_client()
    offer_id = product.ozon_offer_id
    sku = product.ozon_sku
    product_id = product.ozon_product_id

    stock_info = None
    try:
        stock_info = await client.get_product_stocks(
            offer_id=offer_id, sku=sku, product_id=product_id
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch Ozon stocks for product %s: %s", product.sku, exc
        )

    if not stock_info:
        return OzonStock(ozon_total=0, ozon_reserved=0, by_warehouse={})

    total_present = 0
    total_reserved = 0
    by_warehouse: Dict[str, int] = {}
    for item in stock_info.stocks:
        present = max(0, item.present or 0)
        reserved = max(0, item.reserved or 0)
        total_present += present
        total_reserved += reserved
        if item.warehouse_id:
            by_warehouse[item.warehouse_id] = by_warehouse.get(item.warehouse_id, 0) + present

    if not by_warehouse and offer_id:
        try:
            fbs_stocks = await client.get_product_stocks_by_warehouse_fbs(
                offer_id=offer_id, sku=sku
            )
            for wh in fbs_stocks:
                present = max(0, wh.present or 0)
                by_warehouse[wh.warehouse_id or "unknown"] = present
        except Exception as exc:
            logger.info(
                "FBS by-warehouse stock fetch failed for %s: %s", offer_id, exc
            )

    return OzonStock(
        ozon_total=total_present,
        ozon_reserved=total_reserved,
        by_warehouse=by_warehouse,
    )


__all__ = ["OzonStock", "get_ozon_stock_for_product"]
