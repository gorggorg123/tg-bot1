from .ozon_client import ozon_post, msk_today_range_iso, parse_num, rub0


def build_fin_today_message() -> str:
    """
    Возвращает HTML-текст для сообщения в Telegram
    по финансам за сегодня.
    """

    date = msk_today_range_iso()
    body = {
        "date": {
            "from": date["from"],
            "to": date["to"],
        },
        "transaction_type": "all",
    }

    data = ozon_post("/v3/finance/transaction/totals", body)
    result = data.get("result") or {}

    accruals_for_sale = parse_num(result.get("accruals_for_sale"))
    sale_commission = parse_num(result.get("sale_commission"))
    processing_and_delivery = parse_num(result.get("processing_and_delivery"))
    refunds_and_cancellations = parse_num(result.get("refunds_and_cancellations"))
    services_amount = parse_num(result.get("services_amount"))
    others_amount = parse_num(result.get("others_amount"))
    compensation_amount = parse_num(result.get("compensation_amount"))

    # Продажи без отмен
    sales = accruals_for_sale - refunds_and_cancellations

    # Расходы (как в JS-версии)
    returns_exp = -refunds_and_cancellations if refunds_and_cancellations < 0 else 0
    expenses = (
        abs(sale_commission)
        + abs(processing_and_delivery)
        + returns_exp
        + abs(services_amount)
        + abs(others_amount)
    )

    # Итого начислено
    total_accrued = (
        accruals_for_sale
        + sale_commission
        + processing_and_delivery
        + refunds_and_cancellations
        + services_amount
        + others_amount
        + compensation_amount
    )

    msg = (
        f"<b>🏦 Финансы за сегодня (МСК)</b>\n"
        f"{date['pretty']}\n\n"
        f"<b>Начислено всего:</b> {rub0(total_accrued)}\n"
        f"Выручка (продажи без отмен): {rub0(sales)}\n"
        f"Расходы: {rub0(expenses)}\n\n"
        f"<b>Детализация:</b>\n"
        f"• Начисления за продажи: {rub0(accruals_for_sale)}\n"
        f"• Комиссии: {rub0(sale_commission)}\n"
        f"• Обработка и доставка: {rub0(processing_and_delivery)}\n"
        f"• Возвраты и отмены: {rub0(refunds_and_cancellations)}\n"
        f"• Услуги: {rub0(services_amount)}\n"
        f"• Прочее: {rub0(others_amount)}\n"
        f"• Компенсации: {rub0(compensation_amount)}"
    )

    return msg
