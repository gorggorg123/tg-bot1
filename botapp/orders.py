# botapp/orders.py

from __future__ import annotations

from datetime import datetime

from .ozon_client import OzonClient, get_client, msk_today_range


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

    total = len(postings)
    delivered = sum(1 for p in postings if p.get("status") == "delivered")
    cancelled = sum(1 for p in postings if p.get("status") == "cancelled")
    in_work = total - delivered - cancelled

    lines = [
        "📦 <b>Заказы за сегодня</b>",
        pretty,
        "",
        f"Всего заказов: <b>{total}</b>",
        f"✅ Доставлено: <b>{delivered}</b>",
        f"🚚 В обработке: <b>{in_work}</b>",
        f"❌ Отменено: <b>{cancelled}</b>",
    ]

    return "\n".join(lines)
