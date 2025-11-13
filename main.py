import os
import requests
from fastapi import FastAPI, Request

app = FastAPI()

# Берём токен бота из переменной окружения
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")


def tg_call(method: str, payload: dict):
    """
    Вспомогательная функция: отправка запросов к Telegram Bot API.
    """
    if not TG_BOT_TOKEN:
        print("⚠️ TG_BOT_TOKEN не задан в переменных окружения!")
        return None

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print("Ошибка при запросе к Telegram:", e)
        return None


@app.get("/")
async def root():
    return {"status": "ok", "message": "Ozon bot is alive"}


@app.post("/tg")
async def telegram_webhook(request: Request):
    """
    Вебхук от Telegram. Сюда будут приходить все апдейты.
    """
    update = await request.json()
    print("Telegram update:", update)

    # Пытаемся достать обычное сообщение
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text") or ""

    chat_id = chat.get("id")
    if chat_id is None:
        # Ничего не можем ответить
        return {"ok": True}

    # Простейшая логика
    if text == "/start":
        tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": "Привет! 😊 Я бот на FastAPI + Render.\nПока что я только проверяю, что связка работает."
        })
    else:
        tg_call("sendMessage", {
            "chat_id": chat_id,
            "text": f"Ты написал: {text}"
        })

    return {"ok": True}
