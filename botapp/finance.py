from __future__ import annotations

from typing import Any, Dict

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


def _sales_from_totals(t: Dict[str, Any]) -> float:
    return _snum(t.get("accruals_for_sale")) - _snum(t.get("refunds_and_cancellations"))


def _build_expenses(t: Dict[str, Any]) -> float:
    sc = _snum(t.get("sale_commission"))
    pad = _snum(t.get("processing_and_delivery"))
    rfc = _snum(t.get("refunds_and_cancellations"))
    sa = _snum(t.get("services_amount"))
    oa = _snum(t.get("others_amount"))

    commission = abs(sc)
    delivery = abs(pad)
    returns = -rfc if rfc < 0 else 0
    other = abs(sa) + abs(oa)
    return commission + delivery + returns + other


def _accrued_from_totals(t: Dict[str, Any]) -> float:
    return (
        _snum(t.get("accruals_for_sale"))
        + _snum(t.get("sale_commission"))
        + _snum(t.get("processing_and_delivery"))
        + _snum(t.get("refunds_and_cancellations"))
        + _snum(t.get("services_amount"))
        + _snum(t.get("others_amount"))
        + _snum(t.get("compensation_amount"))
    )


async def get_finance_today_text() -> str:
    """
    Готовый текст для Telegram: финансы за текущие сутки (по МСК).
    """
    rng = msk_day_range()

    payload = {
        "date": {
            "from": rng["since"],
            "to": rng["to"],
        },
        "transaction_type": "all",
    }

    data = await ozon_post("/v3/finance/transaction/totals", payload)
    totals = data.get("result") or {}

    accrued = _accrued_from_totals(totals)
    sales = _sales_from_totals(totals)
    expenses = _build_expenses(totals)
    profit_before_cost = sales - expenses

    text = (
        "<b>🏦 Финансы за сегодня</b>\n"
        f"{rng['pretty']}\n\n"
        f"💰 Начислено: {_rub0(accrued)}\n"
        f"🛒 Продажи:   {_rub0(sales)}\n"
        f"💸 Расходы:   {_rub0(expenses)}\n"
        f"📈 Прибыль до себестоимости: {_rub0(profit_before_cost)}"
    )

    return text
