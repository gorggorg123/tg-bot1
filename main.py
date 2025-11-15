import asyncio
import logging
import os

from fastapi import FastAPI
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from botapp.finance import get_finance_today_text
from botapp.orders import get_orders_today_text
from botapp.keyboards import main_menu_keyboard, NOT_IMPLEMENTED_TEXT

# ---------- Логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------- ENV ----------
load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
if not TG_BOT_TOKEN:
    raise RuntimeError("Не задан TG_BOT_TOKEN в переменных окружения")

# ---------- Aiogram ----------
bot = Bot(
    token=TG_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
)
dp = Dispatcher()

# ---------- FastAPI ----------
app = FastAPI(title="Ozon Seller Telegram Bot")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Ozon Seller bot is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- Хендлеры бота ----------

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "Привет! 😊 Я бот для аналитики Ozon Seller (Python + aiogram + FastAPI).\n\n"
        "Сейчас умею:\n"
        "• /fin_today — финансы за сегодня\n"
        "• /orders_today — FBO-заказы за сегодня\n\n"
        "Можно пользоваться кнопками в меню."
    )
    await message.answer(text, reply_markup=main_menu_keyboard)


@dp.message(Command("fin_today"))
@dp.message(F.text == "🏦 Финансы за сегодня")
async def cmd_fin_today(message: Message) -> None:
    try:
        text = await get_finance_today_text()
        await message.answer(text)
    except Exception as e:
        logger.exception("Ошибка при получении финансов: %s", e)
        await message.answer(
            "⚠️ Не удалось получить финансы за сегодня.\n"
            f"Ошибка: {e}"
        )


@dp.message(Command("orders_today"))
@dp.message(F.text == "📦 Заказы за сегодня")
async def cmd_orders_today(message: Message) -> None:
    try:
        text = await get_orders_today_text()
        await message.answer(text)
    except Exception as e:
        logger.exception("Ошибка при получении заказов: %s", e)
        await message.answer(
            "⚠️ Не удалось получить заказы за сегодня.\n"
            f"Ошибка: {e}"
        )


@dp.message(F.text.in_(
    ["📂 Аккаунт Ozon", "📊 Полная аналитика", "📦 FBO", "⭐ Отзывы", "🧠 ИИ"]
))
async def cmd_not_implemented(message: Message) -> None:
    await message.answer(NOT_IMPLEMENTED_TEXT)


# ---------- Запуск бота при старте FastAPI ----------

async def _run_bot() -> None:
    """
    Запускает long polling. Вызовется из FastAPI startup.
    """
    logger.info("Запускаю Telegram-бота (long polling)…")
    await dp.start_polling(bot)


@app.on_event("startup")
async def on_startup() -> None:
    """
    Render запускает uvicorn main:app → FastAPI вызывает этот хук.
    Внутри поднимаем бота в отдельной задаче.
    """
    asyncio.create_task(_run_bot())
    logger.info("Startup completed: bot task created.")


# Локальный запуск (для отладки на компе)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=10000, reload=True)
