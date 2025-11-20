# botapp/orders.py

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

from .ozon_client import (
    OzonClient,
    fmt_int,
    fmt_rub0,
    get_client,
    msk_today_range,
    msk_yesterday_range,
    s_num,
)


CANCELLED_STATUSES = {"cancelled"}
RETURN_STATUSES = {"returned", "returned_to_seller", "client_refund"}


def _extract_amounts(posting: Dict[str, Any]) -> Tuple[float, float]:
    products = posting.get("products") or []
    base_amount = 0.0
    for prod in products:
        qty = max(int(s_num(prod.get("quantity") or 1)), 1)
        price = s_num(
            prod.get("price")
            or prod.get("offer_price")
            or prod.get("price_without_discount")
            or 0
        )
        base_amount += price * qty

    payout = 0.0
    fin = posting.get("financial_data") or {}
    for fprod in fin.get("products") or []:
        payout += s_num(
            fprod.get("payout")
            or fprod.get("client_price")
            or fprod.get("price")
            or 0
        )

    return base_amount, payout


def _summarize_postings(postings: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(postings)
    cancelled_orders = 0
    returns = 0
    orders_without_cancel = 0
    amount_ordered = 0.0
    amount_without_cancel = 0.0

    product_counter: Counter[str] = Counter()
    product_names: Dict[str, str] = {}

    for p in postings:
        status = (p.get("status") or "").lower()
        base_amount, payout = _extract_amounts(p)
        amount_ordered += base_amount

        products = p.get("products") or []
        for prod in products:
            qty = int(s_num(prod.get("quantity"))) or 0
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

        if status in CANCELLED_STATUSES:
            cancelled_orders += 1
        else:
            orders_without_cancel += 1
            amount_without_cancel += payout or base_amount

        if status in RETURN_STATUSES:
            returns += 1

    avg_check = amount_without_cancel / orders_without_cancel if orders_without_cancel else 0

    top3 = []
    for idx, (offer, qty) in enumerate(product_counter.most_common(3), start=1):
        name = product_names.get(offer, offer)
        top3.append(f"{idx}) {name} — {fmt_int(qty)} шт")

    return {
        "total": total,
        "cancelled": cancelled_orders,
        "returns": returns,
        "orders_without_cancel": orders_without_cancel,
        "amount_ordered": amount_ordered,
        "amount_without_cancel": amount_without_cancel,
        "avg_check": avg_check,
        "top3": top3,
    }


def _fmt_delta(value: float) -> str:
    if value == 0:
        return "0"
    sign = "+" if value > 0 else "-"
    return f"{sign}{fmt_int(abs(value))}"


async def get_orders_today_text(client: OzonClient | None = None) -> str:
    """Формирует расширенную сводку FBO за сегодня с дельтой к вчера."""

    client = client or get_client()

    try:
        since, to, pretty_today = msk_today_range()
        yesterday_since, yesterday_to, _ = msk_yesterday_range()
        today_postings = await client.get_fbo_postings(since, to)
        yesterday_postings = await client.get_fbo_postings(yesterday_since, yesterday_to)
    except Exception as e:
        return "⚠️ Не удалось получить сводку по FBO. Ошибка: %s" % e

    safe_today = [p for p in today_postings if isinstance(p, dict)]
    safe_yesterday = [p for p in yesterday_postings if isinstance(p, dict)]

    today = _summarize_postings(safe_today)
    yesterday = _summarize_postings(safe_yesterday)

    if not safe_today:
        return f"📦 FBO • Сводка\n{pretty_today}\n\nЗаказов за сегодня нет."

    delta_orders = today["total"] - yesterday.get("total", 0)
    delta_revenue = today["amount_without_cancel"] - yesterday.get(
        "amount_without_cancel", 0
    )

    lines = [
        "📦 FBO • Сводка",
        pretty_today,
        "",
        "Сегодня",
        f"📦 Заказано: {fmt_int(today['total'])} / {fmt_rub0(today['amount_ordered'])}",
        f"✅ Без отмен: {fmt_int(today['orders_without_cancel'])} / {fmt_rub0(today['amount_without_cancel'])}",
        f"❌ Отмен: {fmt_int(today['cancelled'])} / Ср. чек: {fmt_rub0(today['avg_check'])}",
        f"🔁 Возвраты: {fmt_int(today['returns'])} шт",
        "",
        "Δ к вчера",
        f"• Заказы: {_fmt_delta(delta_orders)}",
        f"• Выручка (без отмен): {_fmt_delta(delta_revenue)} ₽",
    ]

    if today.get("top3"):
        lines.append("")
        lines.append("Топ-3 товаров:")
        lines.extend(today["top3"])

    return "\n".join(lines)
