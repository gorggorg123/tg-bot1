import json
import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Request

from .finance import build_fin_today_message
from .ozon_client import build_seller_info_message

router = APIRouter()

# --- Telegram токен и URL API ---
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    print("⚠️ TG_BOT_TOKEN не задан. Бот не сможет отправлять сообщения в Telegram.")

TG_API_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/" if TG_BOT_TOKEN else None

# --- Доступ к Ozon API (для orders_today) ---
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")
OZON_API_URL = "https://api-seller.ozon.ru"


# --- Инлайн-клавиатура главного меню ---
KB_ROOT = {
    "inline_keyboard": [
        [
            {"text": "📊 Финансы сегодня", "callback_data": "finance_today"},
        ],
        [
            {"text": "📦 Заказы за сегодня", "callback_data": "orders_today"},
        ],
        [
            {"text": "🧾 Аккаунт Ozon", "callback_data": "seller_info"},
        ],
        [
            {"text": "📊 Полная аналитика", "callback_data": "analytics_full"},
        ],
        [
            {"text": "📦 FBO", "callback_data": "fbo"},
        ],
        [
            {"text": "⭐ Отзывы", "callback_data": "reviews"},
        ],
        [
            {"text": "🧠 ИИ", "callback_data": "ai"},
        ],
    ]
}


# ====================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======================

async def tg_call(method: str, payload: dict) -> dict:
    """
    Вызов метода Telegram Bot API.
    Ошибки логируем, но НЕ роняем сервер (чтобы не было 500 из-за editMessageText и т.п.).
    """
    if not TG_API_URL:
        raise RuntimeError("TG_BOT_TOKEN не задан.")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TG_API_URL + method, json=payload)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        print(f"Telegram {method} -> не JSON, статус {resp.status_code}")
        return {"ok": False, "status_code": resp.status_code}

    if not data.get("ok"):
        # Просто логируем, без raise
        print(f"Telegram {method} error: {data}")

    return data


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    await tg_call("sendMessage", payload)


async def answer_callback_query(
    callback_query_id: str,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert

    await tg_call("answerCallbackQuery", payload)


# ====================== ЛОГИКА "ЗАКАЗЫ ЗА СЕГОДНЯ" ======================

def _msk_today_range_utc() -> tuple[str, str]:
    """
    Возвращает (from, to) для "сегодня по МСК", но в UTC (ISO с Z),
    чтобы подставить в фильтр Ozon.
    """
    MSK_SHIFT_HOURS = 3

    now_utc = datetime.now(timezone.utc)
    now_msk = now_utc + timedelta(hours=MSK_SHIFT_HOURS)

    start_msk = datetime(now_msk.year, now_msk.month, now_msk.day)
    start_utc = start_msk - timedelta(hours=MSK_SHIFT_HOURS)
    end_utc = start_utc + timedelta(days=1)

    def to_iso_z(dt: datetime) -> str:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    return to_iso_z(start_utc), to_iso_z(end_utc)


async def _ozon_post(endpoint: str, payload: dict) -> dict:
    """
    Простой клиент к Ozon Seller API, только то, что нужно для orders_today.
    """
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        raise RuntimeError("OZON_CLIENT_ID или OZON_API_KEY не заданы.")

    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
    }

    url = OZON_API_URL + endpoint

    async with httpx.AsyncClient(timeout=25) as client:
        resp = await client.post(url, json=payload, headers=headers)

    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise RuntimeError(f"Ozon {endpoint}: не JSON, статус {resp.status_code}")

    if "result" not in data:
        raise RuntimeError(f"Ozon {endpoint}: нет поля 'result': {data}")

    return data["result"]


async def _fetch_orders_today() -> list[dict]:
    """
    Получаем FBO-заказы за сегодня.
    Для простоты ограничиваемся FBO и limit=100.
    """
    date_from, date_to = _msk_today_range_utc()

    payload = {
        "dir": "ASC",
        "filter": {
            "since": date_from,
            "to": date_to,
            # статус можно уточнить при желании
            "status": "all",
        },
        "limit": 100,
        "offset": 0,
        "with": {
            "analytics_data": True,
            "financial_data": True,
        },
    }

    result = await _ozon_post("/v3/posting/fbo/list", payload)
    postings = result.get("postings", [])
    return postings


async def build_orders_today_message() -> str:
    """
    Собираем текст для блока «📦 Заказы за сегодня».
    """
    try:
        postings = await _fetch_orders_today()
    except Exception as e:
        return (
            "⚠️ Не удалось получить заказы за сегодня.\n"
            f"Ошибка: {e!s}"
        )

    if not postings:
        return "📦 Заказы за сегодня: *заказов по FBO нет*."

    total = len(postings)

    # Примеры нескольких заказов
    examples = []
    for p in postings[:5]:
        pn = p.get("posting_number", "—")
        cut_off = p.get("cutoff_at") or p.get("in_process_at") or ""
        if cut_off:
            examples.append(f"`{pn}` – {cut_off}")
        else:
            examples.append(f"`{pn}`")

    examples_text = "\n".join(examples)

    msg = (
        "*📦 Заказы за сегодня (FBO)*\n\n"
        f"Всего заказов: *{total}*.\n\n"
        f"Примеры:\n{examples_text}"
    )

    return msg


# ====================== ВЕБХУК ======================

@router.post("/tg")
async def telegram_webhook(request: Request):
    """
    Единственная точка входа для вебхука.
    Обрабатываем и обычные сообщения, и callback_query от инлайн-клавиатуры.
    """
    update = await request.json()
    print("Telegram update:", update)

    # ---------- СНАЧАЛА callback_query (кнопки) ----------
    callback = update.get("callback_query")
    if callback:
        cb_id = callback.get("id")
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        data = callback.get("data") or ""

        if cb_id:
            # Просто отвечаем без текста, чтобы "часики" исчезли
            await answer_callback_query(cb_id)

        if chat_id is None:
            return {"ok": True}

        # Обработка конкретных кнопок
        if data == "finance_today":
            try:
                msg = await build_fin_today_message()
            except Exception as e:
                msg = (
                    "⚠️ Не удалось получить финансы за сегодня.\n"
                    f"Ошибка: {e!s}"
                )
            await send_message(chat_id, msg, reply_markup=KB_ROOT)
            return {"ok": True}

        if data == "seller_info":
            msg = await build_seller_info_message()
            await send_message(chat_id, msg, reply_markup=KB_ROOT)
            return {"ok": True}

        if data == "orders_today":
            msg = await build_orders_today_message()
            await send_message(chat_id, msg, reply_markup=KB_ROOT)
            return {"ok": True}

        # Заглушки для остальных пунктов меню
        if data == "analytics_full":
            await send_message(
                chat_id,
                "Раздел *«📊 Полная аналитика»* пока не реализован.\n"
                "Сейчас доступны:\n"
                "• *📊 Финансы сегодня*\n"
                "• *📦 Заказы за сегодня*\n"
                "• *🧾 Аккаунт Ozon*",
                reply_markup=KB_ROOT,
            )
            return {"ok": True}

        if data == "fbo":
            await send_message(
                chat_id,
                "Раздел *«📦 FBO»* пока не реализован.",
                reply_markup=KB_ROOT,
            )
            return {"ok": True}

        if data == "reviews":
            await send_message(
                chat_id,
                "Раздел *«⭐ Отзывы»* пока не реализован.",
                reply_markup=KB_ROOT,
            )
            return {"ok": True}

        if data == "ai":
            await send_message(
                chat_id,
                "Раздел *«🧠 ИИ»* пока не реализован.",
                reply_markup=KB_ROOT,
            )
            return {"ok": True}

        # Неизвестный callback — просто ок
        return {"ok": True}

    # ---------- Обычные сообщения ----------
    message = update.get("message") or update.get("edited_message")
    if not message:
        # Например, service message — просто подтверждаем
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = message.get("text") or ""

    # --- /start + возврат в меню ---
    if text.startswith("/start") or text == "Меню":
        await send_message(
            chat_id,
            "Выберите раздел 👇",
            reply_markup=KB_ROOT,
        )
        return {"ok": True}

    # --- Финансы за сегодня (через текстовую команду) ---
    if text in ("/fin_today", "📊 Финансы", "📊 Финансы сегодня"):
        try:
            msg = await build_fin_today_message()
        except Exception as e:
            msg = (
                "⚠️ Не удалось получить финансы за сегодня.\n"
                f"Ошибка: {e!s}"
            )

        await send_message(chat_id, msg, reply_markup=KB_ROOT)
        return {"ok": True}

    # --- Информация о продавце (через текстовую команду) ---
    if text in ("/seller_info", "🧾 Аккаунт Ozon"):
        msg = await build_seller_info_message()
        await send_message(chat_id, msg, reply_markup=KB_ROOT)
        return {"ok": True}

    # --- Заказы за сегодня (если захочешь команду) ---
    if text in ("/orders_today", "📦 Заказы за сегодня"):
        msg = await build_orders_today_message()
        await send_message(chat_id, msg, reply_markup=KB_ROOT)
        return {"ok": True}

    # --- Всё остальное ---
    await send_message(
        chat_id,
        "Не понял команду 🤔\n"
        "Нажмите кнопку ниже, чтобы открыть меню:",
        reply_markup=KB_ROOT,
    )
    return {"ok": True}
