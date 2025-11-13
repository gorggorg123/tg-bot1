import os
import requests
from fastapi import APIRouter, Request

from .finance import build_fin_today_message

router = APIRouter()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_API_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"


def _check_tg():
    if not TG_BOT_TOKEN:
        raise RuntimeError("Не задан TG_BOT_TOKEN в переменных окружения")


def tg_call(method: str, payload: dict) -> dict:
    """
    Простая синхронная обёртка над Telegram Bot API.
    """
    _check_tg()
    url = f"{TG_API_URL}/{method}"
    resp = requests.post(url, json=payload, timeout=10)
    data = resp.json()
    if not data.get("ok", False):
        raise RuntimeError(f"Telegram {method} -> {data}")
    return data


# ====== Клавиатуры (дизайн как в JS) ======

kb_root = {
    "inline_keyboard": [
        [{"text": "📊 Полная аналитика", "callback_data": "menu_full"}],
        [{"text": "📦 FBO", "callback_data": "menu_fbo"}],
        [{"text": "🏦 Финансы", "callback_data": "menu_fin"}],
        [{"text": "⭐ Отзывы", "callback_data": "menu_rev"}],
        [{"text": "🧠 ИИ", "callback_data": "menu_ai"}],
    ]
}

kb_fin = {
    "inline_keyboard": [
        [{"text": "🏦 Финансы за сегодня", "callback_data": "fin_today"}],
        # дальше будем добавлять "месяц", "период", графики и т.д.
        [{"text": "🏠 В меню", "callback_data": "back_root"}],
    ]
}


# ====== Обработчики ======

def handle_start(chat_id: int):
    tg_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "Выберите раздел 👇",
            "reply_markup": kb_root,
        },
    )


def handle_fin_today(chat_id: int):
    try:
        msg = build_fin_today_message()
        tg_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML",
            },
        )
    except Exception as e:
        tg_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"⚠️ Не удалось получить финансы за сегодня.\n{e}",
            },
        )


def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_call("editMessageText", payload)


@router.post("/telegram")
async def telegram_webhook(request: Request):
    """
    Единая точка входа для Telegram-вебхука.
    Обрабатываем:
      - /start
      - /fin_today
      - нажатия на кнопки меню (пока только финансы)
    """
    update = await request.json()

    message = update.get("message")
    callback = update.get("callback_query")

    # ====== Обычное сообщение ======
    if message:
        chat_id = message["chat"]["id"]
        text = (message.get("text") or "").strip()

        if text == "/start":
            handle_start(chat_id)
            return {"ok": True}

        if text == "/fin_today":
            handle_fin_today(chat_id)
            return {"ok": True}

        # Эхо + подсказка по командам (временная)
        tg_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "Команды:\n/start\n/fin_today",
            },
        )
        return {"ok": True}

    # ====== Callback-кнопки ======
    if callback:
        chat_id = callback["message"]["chat"]["id"]
        message_id = callback["message"]["message_id"]
        data = callback.get("data", "")

        # нужно ответить на callback, чтобы "часики" исчезли
        try:
            tg_call("answerCallbackQuery", {"callback_query_id": callback["id"]})
        except Exception:
            pass

        if data == "back_root":
            edit_message_text(chat_id, message_id, "Выберите раздел 👇", kb_root)
            return {"ok": True}

        # раздел Финансы
        if data == "menu_fin":
            edit_message_text(chat_id, message_id, "Раздел «🏦 Финансы»", kb_fin)
            return {"ok": True}

        # остальные разделы пока-заглушки, но дизайн уже есть
        if data in {"menu_full", "menu_fbo", "menu_rev", "menu_ai"}:
            edit_message_text(
                chat_id,
                message_id,
                "Раздел пока не реализован. Начинаем с «🏦 Финансы за сегодня».",
                kb_root,
            )
            return {"ok": True}

        if data == "fin_today":
            # вместо отправки нового сообщения просто редактируем старое
            try:
                msg = build_fin_today_message()
                edit_message_text(chat_id, message_id, msg, kb_fin)
            except Exception as e:
                edit_message_text(
                    chat_id,
                    message_id,
                    f"⚠️ Не удалось получить финансы за сегодня.\n{e}",
                    kb_fin,
                )
            return {"ok": True}

    # Если Telegram прислал что-то ещё (например, service message)
    return {"ok": True}
