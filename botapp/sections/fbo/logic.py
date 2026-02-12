# botapp/sections/fbo/logic.py
"""
Логика для работы с отправками FBO и FBS.

Поддерживает:
- FBO (Fulfillment by Ozon) — товары на складе Ozon
- FBS (Fulfillment by Seller) — товары на вашем складе

Объединённая статистика показывает суммарные данные по обоим типам отправок.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

from botapp.api.ozon_client import (
    OzonClient,
    fmt_int,
    fmt_rub0,
    get_client,
    msk_current_month_range,
    msk_today_range,
    msk_yesterday_range,
    s_num,
)


# Статусы отмены (общие для FBO и FBS)
CANCELLED_STATUSES = {"cancelled", "canceled"}

# Статусы возврата
RETURN_STATUSES = {"returned", "returned_to_seller", "client_refund"}

# Статусы ожидания обработки (FBS)
FBS_AWAITING_STATUSES = {
    "awaiting_registration",
    "acceptance_in_progress", 
    "awaiting_approve",
    "awaiting_packaging",
    "awaiting_deliver",
}

# Статусы в доставке
DELIVERING_STATUSES = {"delivering", "driver_pickup", "sendfromtaker"}


def _extract_amounts(posting: Dict[str, Any]) -> Tuple[float, float]:
    """Извлекает суммы из отправки: (базовая_сумма, выплата)."""
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
    """Подсчитывает статистику по списку отправок (FBO и/или FBS)."""
    total = len(postings)
    cancelled_orders = 0
    returns = 0
    orders_without_cancel = 0
    amount_ordered = 0.0
    amount_without_cancel = 0.0
    amount_cancelled = 0.0
    
    # Счётчики по типам отправок
    fbo_count = 0
    fbs_count = 0
    fbs_awaiting = 0  # FBS ожидающие обработки
    delivering_count = 0  # В доставке

    product_counter: Counter[str] = Counter()
    product_names: Dict[str, str] = {}

    for p in postings:
        status = (p.get("status") or "").lower()
        fulfillment_type = p.get("_fulfillment_type", "").lower()
        base_amount, payout = _extract_amounts(p)
        amount_ordered += base_amount
        
        # Считаем по типам
        if fulfillment_type == "fbo":
            fbo_count += 1
        elif fulfillment_type == "fbs":
            fbs_count += 1
            if status in FBS_AWAITING_STATUSES:
                fbs_awaiting += 1
        
        if status in DELIVERING_STATUSES:
            delivering_count += 1

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
            amount_cancelled += base_amount
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
        "amount_cancelled": amount_cancelled,
        "avg_check": avg_check,
        "top3": top3,
        # Новые поля для FBO/FBS
        "fbo_count": fbo_count,
        "fbs_count": fbs_count,
        "fbs_awaiting": fbs_awaiting,
        "delivering": delivering_count,
    }


def _fmt_delta(value: float) -> str:
    """Форматирует дельту со знаком."""
    if value == 0:
        return "0"
    sign = "+" if value > 0 else "-"
    return f"{sign}{fmt_int(abs(value))}"


def _format_fulfillment_breakdown(summary: Dict[str, Any]) -> List[str]:
    """Форматирует разбивку по типам отправок (FBO/FBS)."""
    lines = []
    fbo = summary.get("fbo_count", 0)
    fbs = summary.get("fbs_count", 0)
    fbs_awaiting = summary.get("fbs_awaiting", 0)
    delivering = summary.get("delivering", 0)
    
    if fbo > 0 or fbs > 0:
        parts = []
        if fbo > 0:
            parts.append(f"FBO: {fmt_int(fbo)}")
        if fbs > 0:
            fbs_str = f"FBS: {fmt_int(fbs)}"
            if fbs_awaiting > 0:
                fbs_str += f" (⏳ {fbs_awaiting} ожидают)"
            parts.append(fbs_str)
        lines.append("📍 " + " | ".join(parts))
    
    if delivering > 0:
        lines.append(f"🚚 В доставке: {fmt_int(delivering)}")
    
    return lines


async def get_orders_today_text(client: OzonClient | None = None, *, mode: str = "all") -> str:
    """Формирует расширенную сводку по отправкам за сегодня с дельтой к вчера.
    
    Args:
        client: Ozon API клиент
        mode: Режим отображения:
              - "all" — FBO + FBS (по умолчанию)
              - "fbo" — только FBO
              - "fbs" — только FBS
    
    Returns:
        Форматированный текст сводки
    """
    client = client or get_client()
    
    include_fbo = mode in ("all", "fbo")
    include_fbs = mode in ("all", "fbs")
    
    # Заголовок в зависимости от режима
    if mode == "fbo":
        title = "📦 FBO • Сводка"
    elif mode == "fbs":
        title = "🏭 FBS • Сводка"
    else:
        title = "📦 Отправки (FBO+FBS) • Сводка"

    try:
        since, to, pretty_today = msk_today_range()
        yesterday_since, yesterday_to, _ = msk_yesterday_range()
        
        today_postings = await client.get_all_postings(
            since, to, include_fbo=include_fbo, include_fbs=include_fbs
        )
        yesterday_postings = await client.get_all_postings(
            yesterday_since, yesterday_to, include_fbo=include_fbo, include_fbs=include_fbs
        )
    except Exception as e:
        return f"⚠️ Не удалось получить сводку. Ошибка: {e}"

    safe_today = [p for p in today_postings if isinstance(p, dict)]
    safe_yesterday = [p for p in yesterday_postings if isinstance(p, dict)]

    today = _summarize_postings(safe_today)
    yesterday = _summarize_postings(safe_yesterday)

    if not safe_today:
        return f"{title}\n{pretty_today}\n\nЗаказов за сегодня нет."

    delta_orders = today["total"] - yesterday.get("total", 0)
    delta_revenue = today["amount_without_cancel"] - yesterday.get(
        "amount_without_cancel", 0
    )
    delta_avg = today.get("avg_check", 0) - yesterday.get("avg_check", 0)

    lines = [
        title,
        pretty_today,
        "",
        "<b>Сегодня</b>",
        f"📊 Заказано: {fmt_int(today['total'])} / {fmt_rub0(today['amount_ordered'])}",
        f"✅ Без отмен: {fmt_int(today['orders_without_cancel'])} / {fmt_rub0(today['amount_without_cancel'])}",
        f"❌ Отмен: {fmt_int(today['cancelled'])} / {fmt_rub0(today['amount_cancelled'])}",
        f"🔁 Возвраты: {fmt_int(today['returns'])} шт",
    ]
    
    # Разбивка по FBO/FBS
    breakdown = _format_fulfillment_breakdown(today)
    if breakdown:
        lines.extend(breakdown)

    if today.get("orders_without_cancel"):
        lines.append(f"🧾 Средний чек (без отмен): {fmt_rub0(today['avg_check'])}")

    lines.extend(
        [
            "",
            "<b>Δ к вчера</b>",
            f"• Заказы: {_fmt_delta(delta_orders)}",
            f"• Выручка (без отмен): {_fmt_delta(delta_revenue)} ₽",
        ]
    )

    if today.get("orders_without_cancel"):
        if yesterday.get("orders_without_cancel"):
            lines.append(
                f"• Средний чек: {_fmt_delta(delta_avg)} ₽"
            )
        else:
            lines.append(
                f"• Средний чек: {fmt_rub0(today['avg_check'])} (вчера заказов не было)"
            )

    if today.get("top3"):
        lines.append("")
        lines.append("<b>Топ-3 товаров:</b>")
        lines.extend(today["top3"])

    return "\n".join(lines)


async def get_orders_month_text(client: OzonClient | None = None, *, mode: str = "all") -> str:
    """Сводка по отправкам за текущий месяц.
    
    Args:
        client: Ozon API клиент
        mode: Режим отображения ("all", "fbo", "fbs")
    
    Returns:
        Форматированный текст сводки
    """
    client = client or get_client()
    
    include_fbo = mode in ("all", "fbo")
    include_fbs = mode in ("all", "fbs")
    
    # Заголовок в зависимости от режима
    if mode == "fbo":
        title = "📦 FBO • Текущий месяц"
    elif mode == "fbs":
        title = "🏭 FBS • Текущий месяц"
    else:
        title = "📦 Отправки (FBO+FBS) • Текущий месяц"

    try:
        since, to, pretty_period = msk_current_month_range()
        postings = await client.get_all_postings(
            since, to, include_fbo=include_fbo, include_fbs=include_fbs
        )
    except Exception as e:
        return f"⚠️ Не удалось получить сводку за месяц. Ошибка: {e}"

    safe_postings = [p for p in postings if isinstance(p, dict)]
    if not safe_postings:
        return f"{title}\n{pretty_period}\n\nЗаказов за период нет."

    summary = _summarize_postings(safe_postings)
    lines = [
        title,
        pretty_period,
        "",
        f"📊 Заказано: {fmt_int(summary['total'])} / {fmt_rub0(summary['amount_ordered'])}",
        f"✅ Без отмен: {fmt_int(summary['orders_without_cancel'])} / {fmt_rub0(summary['amount_without_cancel'])}",
        f"❌ Отмен: {fmt_int(summary['cancelled'])} / {fmt_rub0(summary['amount_cancelled'])}",
        f"🔁 Возвраты: {fmt_int(summary['returns'])} шт",
    ]
    
    # Разбивка по FBO/FBS
    breakdown = _format_fulfillment_breakdown(summary)
    if breakdown:
        lines.extend(breakdown)

    if summary.get("orders_without_cancel"):
        lines.append(
            f"🧾 Средний чек (без отмен): {fmt_rub0(summary['avg_check'])}"
        )

    if summary.get("top3"):
        lines.append("")
        lines.append("<b>Топ-3 товаров:</b>")
        lines.extend(summary["top3"])

    return "\n".join(lines)


# ---------- Функции для отдельных типов отправок ----------

async def get_fbo_today_text(client: OzonClient | None = None) -> str:
    """Сводка только по FBO за сегодня."""
    return await get_orders_today_text(client, mode="fbo")


async def get_fbs_today_text(client: OzonClient | None = None) -> str:
    """Сводка только по FBS за сегодня."""
    return await get_orders_today_text(client, mode="fbs")


async def get_fbo_month_text(client: OzonClient | None = None) -> str:
    """Сводка только по FBO за месяц."""
    return await get_orders_month_text(client, mode="fbo")


async def get_fbs_month_text(client: OzonClient | None = None) -> str:
    """Сводка только по FBS за месяц."""
    return await get_orders_month_text(client, mode="fbs")
