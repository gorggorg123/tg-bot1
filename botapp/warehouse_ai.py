"""AI helpers for warehouse flows."""

from __future__ import annotations

import json
import logging
from typing import Iterable, Tuple

from botapp import ai_client
from botapp.products_service import CatalogProduct

logger = logging.getLogger(__name__)


def _build_catalog_snippet(catalog: Iterable[CatalogProduct], limit: int = 150) -> str:
    lines = []
    for idx, item in enumerate(catalog):
        if idx >= limit:
            break
        lines.append(f"- {item.name} (SKU: {item.sku})")
    return "\n".join(lines)


async def parse_production_text_to_items(
    text: str, catalog: list[CatalogProduct]
) -> list[Tuple[CatalogProduct, int]]:
    """Use GPT-4o-mini to map free-form production text to catalog items."""

    cleaned = text.strip()
    if not cleaned:
        return []

    catalog_snippet = _build_catalog_snippet(catalog)
    system_prompt = (
        "Ты — помощник по учёту производства мебели ITOM. Тебе дают текст "
        "о том, сколько и каких изделий произвели. У тебя есть список товаров "
        "(SKU + название). Верни JSON-массив объектов вида {\"sku\": \"...\", "
        "\"quantity\": N}, используя только SKU из списка. Если не уверен, "
        "выбери ближайший по смыслу товар."
    )

    user_message = (
        "Список товаров:\n"
        f"{catalog_snippet}\n\n"
        "Текст пользователя:\n"
        f"{cleaned}\n\n"
        "Верни только JSON без комментариев."
    )

    raw = await ai_client._call_openai(system_prompt, user_message, max_tokens=400)  # type: ignore[attr-defined]
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to parse AI production output: %s", exc)
        return []

    results: list[Tuple[CatalogProduct, int]] = []
    if isinstance(parsed, list):
        catalog_by_sku = {item.sku: item for item in catalog}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            sku = str(entry.get("sku") or "").strip()
            qty_raw = entry.get("quantity")
            try:
                qty = int(qty_raw)
            except Exception:
                continue
            if qty <= 0 or not sku:
                continue
            product = catalog_by_sku.get(sku)
            if product:
                results.append((product, qty))
    return results


__all__ = ["parse_production_text_to_items"]
