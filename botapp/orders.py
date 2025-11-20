# botapp/orders.py

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .ozon_client import (
    OzonClient,
    fmt_int,
    fmt_rub0,
    get_client,
    msk_today_range,
    s_num,
)


async def get_orders_today_text(client: OzonClient | None = None) -> str:
    """Формирует текст для раздела «Заказы за сегодня» через SellerAPI."""

    client = client or get_client()

    try:
        since, to, pretty = msk_today_range()
        postings = await client.get_fbo_postings(since, to)
    except Exception as e:
        return (
            "⚠️ Не удалось получить заказы за сегодня.\n"
            f"Ошибка: {e}"
        )

    if not postings:
        return f"📦 За {datetime.now().strftime('%d.%m.%Y')} заказов нет."

    safe_postings = [p for p in postings if isinstance(p, dict)]

    total = len(safe_postings)
    delivered = sum(1 for p in safe_postings if p.get("status") == "delivered")
    cancelled = sum(1 for p in safe_postings if p.get("status") == "cancelled")
    in_work = total - delivered - cancelled

    revenue = 0.0
    product_counter: Counter[str] = Counter()
    product_names: dict[str, str] = {}

    for p in safe_postings:
        products = p.get("products") or []
        for prod in products:
            qty = int(s_num(prod.get("quantity")))
            if qty <= 0:
                continue
            offer = (
                prod.get("offer_id")
                or prod.get("sku")
                or prod.get("product_id")
                or prod.get("name")
                or "?"
            )
            name = (
                prod.get("name")
                or prod.get("product_name")
                or product_names.get(str(offer))
                or ""
            )
            product_counter[str(offer)] += qty
            if name:
                product_names.setdefault(str(offer), str(name))

        if p.get("status") == "delivered":
            fin = p.get("financial_data") or {}
            fin_products = fin.get("products") or []
            for fprod in fin_products:
                revenue += s_num(
                    fprod.get("payout")
                    or fprod.get("client_price")
                    or fprod.get("price")
                    or 0
                )

    avg_check = revenue / delivered if delivered else 0
    unique_items = len(product_counter)

    top3_lines: list[str] = []
    if product_counter:
        top3 = product_counter.most_common(3)
        for idx, (offer, qty) in enumerate(top3, start=1):
            name = product_names.get(offer, offer)
            top3_lines.append(f"{idx}) {name} — {fmt_int(qty)} шт")

    lines = [
        "📦 <b>Заказы за сегодня</b>",
        pretty,
        "",
        f"Всего заказов: <b>{fmt_int(total)}</b>",
        f"✅ Доставлено: <b>{fmt_int(delivered)}</b>",
        f"🚚 В обработке: <b>{fmt_int(in_work)}</b>",
        f"❌ Отменено: <b>{fmt_int(cancelled)}</b>",
        "",
        f"💰 Выручка по доставленным: <b>{fmt_rub0(revenue)}</b>",
        f"🧾 Средний чек: <b>{fmt_rub0(avg_check)}</b>",
        f"🎯 Уникальных товаров: <b>{fmt_int(unique_items)}</b>",
    ]

    if top3_lines:
        lines.append("")
        lines.append("Топ-3 товаров:")
        lines.extend(top3_lines)

    return "\n".join(lines)
