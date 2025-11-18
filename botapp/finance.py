# botapp/finance.py
from __future__ import annotations

from typing import Dict, Any

from .ozon_client import (
    OzonClient,
    fmt_int,
    fmt_rub0,
    get_client,
    msk_current_month_range,
    msk_today_range,
    s_num,
)


def _sales_from_totals(t: Dict[str, Any]) -> float:
    # как в JS: продажи = начислено за продажи – возвраты/отмены
    return s_num(t.get("accruals_for_sale")) - s_num(
        t.get("refunds_and_cancellations")
    )


def _build_expenses(t: Dict[str, Any]) -> float:
    sc = s_num(t.get("sale_commission"))
    pad = s_num(t.get("processing_and_delivery"))
    rfc = s_num(t.get("refunds_and_cancellations"))
    sa = s_num(t.get("services_amount"))
    oa = s_num(t.get("others_amount"))

    commission = abs(sc)
    delivery = abs(pad)
    returns = -rfc if rfc < 0 else 0
    other = abs(sa) + abs(oa)
    return commission + delivery + returns + other


def _accrued_from_totals(t: Dict[str, Any]) -> float:
    return (
        s_num(t.get("accruals_for_sale"))
        + s_num(t.get("sale_commission"))
        + s_num(t.get("processing_and_delivery"))
        + s_num(t.get("refunds_and_cancellations"))
        + s_num(t.get("services_amount"))
        + s_num(t.get("others_amount"))
        + s_num(t.get("compensation_amount"))
    )


async def get_finance_today_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_today_range()
    totals = await client.get_finance_totals(since, to)

    accrued = _accrued_from_totals(totals)
    sales = _sales_from_totals(totals)
    expenses = _build_expenses(totals)
    profit = sales - expenses

    return (
        "<b>🏦 Финансы за сегодня</b>\n"
        f"{pretty}\n\n"
        f"💰 Начислено: {fmt_rub0(accrued)}\n"
        f"🛒 Продажи:   {fmt_rub0(sales)}\n"
        f"💸 Расходы:   {fmt_rub0(expenses)}\n"
        f"📈 Прибыль до себестоимости: {fmt_rub0(profit)}"
    )


async def get_finance_month_summary_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_current_month_range()
    totals = await client.get_finance_totals(since, to)

    accrued = _accrued_from_totals(totals)
    sales = _sales_from_totals(totals)
    expenses = _build_expenses(totals)
    profit = sales - expenses

    return (
        "<b>🏦 Финансы • текущий месяц</b>\n"
        f"{pretty}\n\n"
        f"💰 Начислено: {fmt_rub0(accrued)}\n"
        f"🛒 Продажи:   {fmt_rub0(sales)}\n"
        f"💸 Расходы:   {fmt_rub0(expenses)}\n"
        f"📈 Прибыль до себестоимости: {fmt_rub0(profit)}"
    )
