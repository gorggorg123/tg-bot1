import json
import os
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request

from .ozon_client import (
    build_fin_today_message,
    build_orders_today_message,
    build_seller_info_message,
)

router = APIRouter()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")

if not TG_BOT_TOKEN:
    print("⚠️ TG_BOT_TOKEN не задан. Бот не сможет отправлять сообщения в Telegram.")

TG_API_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/" if TG_BOT_TOKEN else None

# Инлайн-клавиатура главного меню
KB_ROOT_INLINE: Dict[str, Any] = {
    "inline_keyboard": [
        [{"text": "📊 Финансы сегодня", "callback_data": "finance_today"}],
        [{"text": "📦 Заказы за сегодня", "callback_data": "orders_today"}],
        [{"text": "🧾 Аккаунт Ozon", "callback_data": "seller_info"}],
        [{"text": "📊 Полная аналитика", "callback_data": "analytics_full"}],
        [{"text": "📦 FBO", "callback_data": "fbo"}],
        [{"text": "⭐ Отзывы", "callback_data": "reviews"}],
        [{"text": "🧠 ИИ", "callback_data": "ai"}],
    ]
}


async def tg_call(method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Вызов Telegram Bot API.
    Ошибки логируем, но не роняем сервер.
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
        print(f"Telegram {method} error: {data}")

    return data


async def send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> None:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        # parse_mode убрали, чтобы не было проблем с Markdown
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    await tg_call("sendMessage", payload)


async def answer_callback_query(callback_query_id: str) -> None:
    """Просто отвечаем callback'у, чтобы не висела 'часовая' иконка."""
    await tg_call("answerCallbackQuery", {"callback_query_id": callback_query_id})


@router.post("/tg")
async def telegram_webhook(request: Request):
    """
    Единственная точка входа для вебхука.

    Обрабатываем:
      - обычные сообщения (message) -> /start, "Меню"
      - callback_query -> кнопки инлайн-клавиатуры
    """
    update = await request.json()
    print("Telegram update:", update)

    # --------- callback_query (инлайн-кнопки) ---------
    if "callback_query" in update:
        cb = update["callback_query"]
        data = cb.get("data") or ""
        from_user = cb.get("from") or {}
        message = cb.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        cb_id = cb.get("id")

        if cb_id:
            await answer_callback_query(cb_id)

        if chat_id is None:
            return {"ok": True}

        # Маршрутизация по data
        if data == "finance_today":
            try:
                msg = await build_fin_today_message()
            except Exception as e:
                msg = (
                    "⚠️ Не удалось получить финансы за сегодня.\n"
                    f"Ошибка: {e!s}"
                )
            await send_message(chat_id, msg, reply_markup=KB_ROOT_INLINE)
            return {"ok": True}

        if data == "orders_today":
            try:
                msg = await build_orders_today_message()
            except Exception as e:
                msg = (
                    "⚠️ Не удалось получить заказы за сегодня.\n"
                    f"Ошибка: {e!s}"
                )
            await send_message(chat_id, msg, reply_markup=KB_ROOT_INLINE)
            return {"ok": True}

        if data == "seller_info":
            try:
                msg = await build_seller_info_message()
            except Exception as e:
                msg = (
                    "⚠️ Не удалось получить данные об аккаунте Ozon.\n"
                    f"Ошибка: {e!s}"
                )
            await send_message(chat_id, msg, reply_markup=KB_ROOT_INLINE)
            return {"ok": True}

        # Заглушки на остальные разделы
        if data == "analytics_full":
            await send_message(
                chat_id,
                "Раздел «📊 Полная аналитика» пока не реализован.\n"
                "Сейчас доступны:\n"
                "• 📊 Финансы сегодня\n"
                "• 📦 Заказы за сегодня\n"
                "• 🧾 Аккаунт Ozon",
                reply_markup=KB_ROOT_INLINE,
            )
            return {"ok": True}

        if data == "fbo":
            await send_message(
                chat_id,
                "Раздел «📦 FBO» пока не реализован.\n"
                "План: детализация остатков и заказов по складам FBO.",
                reply_markup=KB_ROOT_INLINE,
            )
            return {"ok": True}

        if data == "reviews":
            await send_message(
                chat_id,
                "Раздел «⭐ Отзывы» пока не реализован.\n"
                "План: последние отзывы, рейтинг по SKU, быстрые ответы.",
                reply_markup=KB_ROOT_INLINE,
            )
            return {"ok": True}

        if data == "ai":
            await send_message(
                chat_id,
                "Раздел «🧠 ИИ» пока не реализован.\n"
                "План: брифинг по аккаунту, прогноз выручки, Q&A.",
                reply_markup=KB_ROOT_INLINE,
            )
            return {"ok": True}

        # На всякий случай
        await send_message(
            chat_id,
            "Неизвестная команда кнопки.",
            reply_markup=KB_ROOT_INLINE,
        )
        return {"ok": True}

    # --------- обычные сообщения (message) ---------
    message = update.get("message") or update.get("edited_message")
    if not message:
        # Например, service message — просто подтверждаем
        return {"ok": True}

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return {"ok": True}

    text = message.get("text") or ""

    # /start или просто "Меню"
    if text.startswith("/start") or text == "Меню":
        await send_message(chat_id, "Выберите раздел 👇", reply_markup=KB_ROOT_INLINE)
        return {"ok": True}

    # Команды текстом, если вдруг захочешь вызывать без кнопок
    if text == "/finance_today":
        try:
            msg = await build_fin_today_message()
        except Exception as e:
            msg = (
                "⚠️ Не удалось получить финансы за сегодня.\n"
                f"Ошибка: {e!s}"
            )
        await send_message(chat_id, msg, reply_markup=KB_ROOT_INLINE)
        return {"ok": True}

    if text == "/orders_today":
        try:
            msg = await build_orders_today_message()
        except Exception as e:
            msg = (
                "⚠️ Не удалось получить заказы за сегодня.\n"
                f"Ошибка: {e!s}"
            )
        await send_message(chat_id, msg, reply_markup=KB_ROOT_INLINE)
        return {"ok": True}

    if text == "/seller_info":
        try:
            msg = await build_seller_info_message()
        except Exception as e:
            msg = (
                "⚠️ Не удалось получить данные об аккаунте Ozon.\n"
                f"Ошибка: {e!s}"
            )
        await send_message(chat_id, msg, reply_markup=KB_ROOT_INLINE)
        return {"ok": True}

    # Всё остальное — просто говорим про меню
    await send_message(
        chat_id,
        "Не понимаю команду.\nНажмите /start или кнопку «Меню», чтобы увидеть разделы.",
        reply_markup=KB_ROOT_INLINE,
    )
    return {"ok": True}
