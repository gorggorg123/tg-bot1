import os
from datetime import datetime, timedelta, timezone

import requests
from fastapi import FastAPI, Request

app = FastAPI()

# Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")

# Ozon
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")

MSK_SHIFT_HOURS = 3


def tg_call(method: str, payload: dict):
    """
    Запрос к Telegram Bot API.
    """
    if not TG_BOT_TOKEN:
        print("⚠️ TG_BOT_TOKEN не задан в переменных окружения!")
        return None

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("Ошибка при запросе к Telegram:", e)
        return None


def msk_today_range_iso():
    """
    Возвращает (from_iso, to_iso) для СЕГОДНЯ по МСК,
    но в формате UTC ISO8601, как любит Ozon (…T00:00:00Z).
    """
    now_utc = datetime.now(timezone.utc)
    # Переводим в МСК
    now_msk = now_utc + timedelta(hours=MSK_SHIFT_HOURS)
    start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
    end_msk = start_msk + timedelta(days=1) - timedelta(seconds=1)
    # Обратно в UTC
    start_utc = start_msk - timedelta(hours=MSK_SHIFT_HOURS)
    end_utc = end_msk - timedelta(hours=MSK_SHIFT_HOURS)
    # Формат без микросекунд, с Z на конце
    f = start_utc.isoformat().replace("+00:00", "Z")
    t = end_utc.isoformat().replace("+00:00", "Z")
    return f, t


def ozon_post(path: str, body: dict):
    """
    Базовый POST к Ozon Seller API.
    path: например, '/v3/finance/transaction/totals'
    """
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        raise RuntimeError("OZON_CLIENT_ID / OZON_API_KEY не заданы в переменных окружения")

    url = "https://api-seller.ozon.ru" + path
    headers = {
        "Client-Id": OZON_CLIENT_ID.strip(),
        "Api-Key": OZON_API_KEY.strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    resp = requests.post(url, json=body, headers=headers, timeout=25)
    if not resp.ok:
        # кидаем исключение с текстом ответа (там полезная ошибка)
        raise RuntimeError(f"Ozon {path} -> HTTP {resp.status_code}: {resp.text}")

    return resp.json()


@app.get("/")
async def root():
    return {"status": "ok", "message": "Ozon bot is alive"}


@app.post("/tg")
async def telegram_webhook(request: Request):
    """
    Вебхук от Telegram. Сюда прилетают все апдейты.
    """
    update = await request.json()
    print("Telegram update:", update)

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text") or ""

    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = (text or "").strip()

    # ----- Команда /start -----
    if text == "/start":
        tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": (
                "Привет! 😊 Я бот на FastAPI + Render.\n"
                "⚙️ Сейчас умею:\n"
                "/fin_today — сводка по финансам за сегодня (по API Ozon)."
            )
        })
        return {"ok": True}

    # ----- Команда /fin_today -----
    if text == "/fin_today":
        try:
            date_from, date_to = msk_today_range_iso()
            body = {
                "date": {
                    "from": date_from,
                    "to": date_to
                },
                "transaction_type": "all"
            }

            data = ozon_post("/v3/finance/transaction/totals", body)
            result = data.get("result") or {}

            # безопасный парсер чисел
            def n(x):
                try:
                    return float(str(x).replace(" ", "").replace(",", ".")) if x is not None else 0.0
                except Exception:
                    return 0.0

            accruals_for_sale = n(result.get("accruals_for_sale"))
            sale_commission = n(result.get("sale_commission"))
            processing_and_delivery = n(result.get("processing_and_delivery"))
            refunds_and_cancellations = n(result.get("refunds_and_cancellations"))
            services_amount = n(result.get("services_amount"))
            others_amount = n(result.get("others_amount"))
            compensation_amount = n(result.get("compensation_amount"))

            # как в твоём JS:
            sales = accruals_for_sale - refunds_and_cancellations
            expenses = (
                abs(sale_commission)
                + abs(processing_and_delivery)
                + max(0.0, -refunds_and_cancellations)
                + abs(services_amount) + abs(others_amount)
            )
            total_accrued = (
                accruals_for_sale
                + sale_commission
                + processing_and_delivery
                + refunds_and_cancellations
                + services_amount
                + others_amount
                + compensation_amount
            )

            def rub0(x: float) -> str:
                try:
                    return f"{int(round(x)):,} ₽".replace(",", " ")
                except Exception:
                    return f"{x:.0f} ₽"

            msg = (
                "<b>🏦 Финансы за сегодня (МСК)</b>\n\n"
                f"Начислено всего: <b>{rub0(total_accrued)}</b>\n"
                f"Продажи:         <b>{rub0(sales)}</b>\n"
                f"Расходы:         <b>{rub0(expenses)}</b>\n\n"
                f"Вознаграждение Ozon: {rub0(sale_commission)}\n"
                f"Доставка:             {rub0(processing_and_delivery)}\n"
                f"Возвраты/отмены:      {rub0(refunds_and_cancellations)}\n"
                f"Прочие услуги:        {rub0(services_amount + others_amount)}\n"
                f"Компенсации:          {rub0(compensation_amount)}"
            )

            tg_call("sendMessage", {
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML"
            })

        except Exception as e:
            print("Ошибка при запросе к Ozon:", e)
            tg_call("sendMessage", {
                "chat_id": chat_id,
                "text": f"⚠️ Не удалось получить финансы за сегодня.\n{e}"
            })

        return {"ok": True}

    # ----- Все остальные сообщения — эхо -----
    tg_call("sendMessage", {
        "chat_id": chat_id,
        "text": f"Ты написал: {text}\n\nДоступные команды:\n/start\n/fin_today"
    })
    return {"ok": True}
