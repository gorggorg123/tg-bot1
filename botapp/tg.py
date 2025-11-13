import os
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request

from .finance import get_finance_today_text


logger = logging.getLogger("botapp.tg")
logger.setLevel(logging.INFO)

router = APIRouter()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    raise RuntimeError("Не задана переменная окружения TG_BOT_TOKEN")

TG_API_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"


# ====================== Вспомогательные функции Telegram ======================

async def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Универсальный вызов Telegram Bot API.
    Игнорируем специфическую ошибку 'message is not modified'
    для editMessage* методов, чтобы не падать с 500.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{TG_API_URL}/{method}", json=payload)

    data = resp.json()
    if not data.get("ok", False):
        desc = data.get("description", "")
        error_code = data.get("error_code")

        # Игнорируем "message is not modified" для editMessage*
        if (
            method in ("editMessageText", "editMessageCaption", "editMessageReplyMarkup")
            and error_code == 400
            and "message is not modified" in desc
        ):
            logger.info("Telegram: игнорируем 'message is not modified'")
            return data

        logger.error("Telegram %s error: %s", method, data)
        raise RuntimeError(f"Telegram {method} -> {data}")

    return data


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    return await tg_call("sendMessage", payload)


async def edit_message_text(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    return await tg_call("editMessageText", payload)


async def answer_callback_query(callback_query_id: str) -> None:
    await tg_call("answerCallbackQuery", {"callback_query_id": callback_query_id})


# ====================== Клавиатуры ======================

def kb_root() -> Dict[str, Any]:
    """
    Главное меню.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "🏦 Финансы", "callback_data": "sec:fin"},
            ],
            [
                {"text": "ℹ️ Что ты умеешь", "callback_data": "sec:help"},
            ],
        ]
    }


def kb_fin() -> Dict[str, Any]:
    """
    Меню раздела Финансы.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "📅 Финансы за сегодня", "callback_data": "fin:today"},
            ],
            [
                {"text": "⬅️ В главное меню", "callback_data": "sec:root"},
            ],
        ]
    }


# ====================== Тексты ======================

def start_text() -> str:
    return (
        "Привет! 😊 Я бот на FastAPI + Render.\n\n"
        "⚙️ Сейчас умею:\n"
        "• <b>/fin_today</b> — сводка по финансам за сегодня (по API Ozon).\n\n"
        "Нажми «🏦 Финансы» ниже, чтобы получить сводку."
    )


def help_text() -> str:
    return (
        "ℹ️ <b>Что я умею сейчас</b>\n\n"
        "• <b>/fin_today</b> — финансы за сегодня по данным Ozon Seller API.\n"
        "• Кнопка «🏦 Финансы» — то же самое, но через меню.\n\n"
        "Дальше можно расширять: аналитика FBO, реклама, отчёты и т.д. 🚀"
    )


# ====================== Обработчики логики ======================

async def handle_start(chat_id: int) -> None:
    await send_message(chat_id, start_text(), reply_markup=kb_root())


async def handle_fin_today(
    chat_id: int,
    message_id: Optional[int] = None,
    from_callback: bool = False,
) -> None:
    text = await get_finance_today_text()
    full_text = f"📅 <b>Финансы за сегодня</b>\n\n{text}"

    if from_callback and message_id is not None:
        await edit_message_text(chat_id, message_id, full_text, reply_markup=kb_fin())
    else:
        await send_message(chat_id, full_text, reply_markup=kb_fin())


# ====================== Webhook ======================

@router.post("/tg")
async def telegram_webhook(request: Request) -> Dict[str, Any]:
    """
    Основной webhook-обработчик Telegram.
    """
    update = await request.json()
    logger.info("Telegram update: %s", update)

    # Обычные сообщения
    if "message" in update:
        msg = update["message"]
        chat = msg["chat"]
        chat_id = chat["id"]
        text = msg.get("text", "") or ""

        if text.startswith("/start"):
            await handle_start(chat_id)
        elif text.startswith("/fin_today"):
            await handle_fin_today(chat_id)
        else:
            await send_message(
                chat_id,
                "Не знаю такой команды 😅\n"
                "Попробуй /start",
                reply_markup=kb_root(),
            )

        return {"ok": True}

    # Callback-кнопки
    if "callback_query" in update:
        cq = update["callback_query"]
        data = cq.get("data") or ""
        message = cq.get("message") or {}
        chat_id = message["chat"]["id"]
        message_id = message["message_id"]
        callback_id = cq["id"]

        # убираем "часики" у кнопки
        await answer_callback_query(callback_id)

        if data == "sec:root":
            await edit_message_text(
                chat_id,
                message_id,
                "Выбери раздел 👇",
                reply_markup=kb_root(),
            )
        elif data == "sec:fin":
            await edit_message_text(
                chat_id,
                message_id,
                "Раздел «Финансы» 💰",
                reply_markup=kb_fin(),
            )
        elif data == "sec:help":
            await edit_message_text(
                chat_id,
                message_id,
                help_text(),
                reply_markup=kb_root(),
            )
        elif data == "fin:today":
            await handle_fin_today(chat_id, message_id=message_id, from_callback=True)
        else:
            # На всякий случай — неизвестные кнопки
            await send_message(chat_id, "Пока не понимаю эту кнопку 🤔")

        return {"ok": True}

    # На всякий случай — другие типы апдейтов игнорируем
    return {"ok": True}
