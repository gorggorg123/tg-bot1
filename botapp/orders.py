from __future__ import annotations

from typing import Any, Dict, List

from .ozon_client import ozon_post, msk_day_range


def _snum(x: Any) -> float:
    try:
        return float(str(x).replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except Exception:
        return 0.0


def _fmt_int(n: float | int) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def _rub0(n: float | int) -> str:
    return f"{_fmt_int(n)} ₽"


def _is_cancelled(posting: Dict[str, Any]) -> bool:
    status = str(posting.get("status") or "").lower()
    return "cancel" in status


def _posting_total_price(posting: Dict[str, Any]) -> float:
    """
    Аналог postingTotalPrice из твоего JS:
    - сначала пытаемся взять analytics_data.total_price
    - если нет, считаем по products: quantity * price
    """
    ad = posting.get("analytics_data") or posting.get("analyticsData") or {}
    ad_price = _snum(ad.get("total_price") or ad.get("price"))
    if ad_price > 0:
        return ad_price

    products = posting.get("products") or []
    total = 0.0
    for p in products:
        qty = _snum(p.get("quantity") or p.get("offer_quantity") or p.get("items_count"))
        price = _snum(p.get("price") or p.get("client_price") or p.get("original_price"))
        total += qty * price
    return total


async def get_orders_today_text() -> str:
    """
    Готовый текст для Telegram: FBO-заказы за текущие сутки (по МСК).
    """
    rng = msk_day_range()

    body = {
        "dir": "DESC",
        "filter": {
            "since": rng["since"],
            "to": rng["to"],
        },
        "limit": 1000,
        "offset": 0,
        "with": {
            "products": True,
            "financial_data": False,
            "analytics_data": True,
        },
    }

    data = await ozon_post("/v2/posting/fbo/list", body)

    postings: List[Dict[str, Any]]
    if isinstance(data, dict):
        result = data.get("result") or {}
        postings = (
            result.get("postings")
            or data.get("postings")
            or result
            or []
        )
        if isinstance(postings, dict):
            # На всякий случай, если result — это просто список
            postings = postings.get("postings", [])
    else:
        postings = []

    if not isinstance(postings, list):
        postings = []

    total_orders = len(postings)
    ok_orders = 0
    cancelled_orders = 0
    sum_all = 0.0
    sum_ok = 0.0

    for p in postings:
        price = _posting_total_price(p)
        sum_all += price
        if _is_cancelled(p):
            cancelled_orders += 1
        else:
            ok_orders += 1
            sum_ok += price

    avg_check = sum_ok / ok_orders if ok_orders > 0 else 0.0

    text = (
        "<b>📦 FBO — заказы за сегодня</b>\n"
        f"{rng['pretty']}\n\n"
        f"🧾 Всего заказов: {_fmt_int(total_orders)} / {_rub0(sum_all)}\n"
        f"✅ Без отмен: {_fmt_int(ok_orders)} / {_rub0(sum_ok)}\n"
        f"❌ Отменено: {_fmt_int(cancelled_orders)}\n"
        f"🧮 Средний чек (по успешным): {_rub0(avg_check)}"
    )

    return text
