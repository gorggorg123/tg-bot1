import asyncio
import logging
import os
from typing import Awaitable, Callable

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from fastapi import FastAPI
from dotenv import load_dotenv

from botapp.account import get_account_info_text
from botapp.finance import get_finance_today_text
from botapp.orders import get_orders_today_text
from botapp.reviews import (
    get_reviews_menu_text,
    get_reviews_month_text,
    get_latest_review,
    get_reviews_today_text,
    get_reviews_week_text,
)
from botapp.tg import main_menu_kb
from botapp.keyboards import reviews_periods_keyboard, fbo_keyboard
from botapp.ozon_client import get_client
from botapp.reviews_ai import draft_reply

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID", "").strip()
OZON_API_KEY = os.getenv("OZON_API_KEY", "").strip()

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN is not set")
if not OZON_CLIENT_ID or not OZON_API_KEY:
    raise RuntimeError("OZON_CLIENT_ID / OZON_API_KEY are not set")

router = Router()
_last_reviews_period = "today"


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = "Добро пожаловать! Выберите раздел в меню или используйте команды."
    await message.answer(text, reply_markup=main_menu_kb())


@router.message(Command("fin_today"))
async def cmd_fin_today(message: Message) -> None:
    text = await get_finance_today_text()
    await message.answer(text)


@router.message(Command("account"))
async def cmd_account(message: Message) -> None:
    text = await get_account_info_text()
    await message.answer(text)


@router.message(Command("reviews_today"))
async def cmd_reviews_today(message: Message) -> None:
    global _last_reviews_period
    _last_reviews_period = "today"
    text = await get_reviews_today_text()
    await message.answer(text, reply_markup=reviews_periods_keyboard())


@router.message(Command("reviews_week"))
async def cmd_reviews_week(message: Message) -> None:
    global _last_reviews_period
    _last_reviews_period = "week"
    text = await get_reviews_week_text()
    await message.answer(text, reply_markup=reviews_periods_keyboard())


@router.message(Command("reviews_month"))
async def cmd_reviews_month(message: Message) -> None:
    global _last_reviews_period
    _last_reviews_period = "month"
    text = await get_reviews_month_text()
    await message.answer(text, reply_markup=reviews_periods_keyboard())


@router.callback_query(F.data == "fin_today")
async def cb_fin_today(callback: CallbackQuery) -> None:
    await callback.answer()  # закрываем часы
    text = await get_finance_today_text()
    await callback.message.answer(text)


@router.callback_query(F.data == "fbo_menu")
async def cb_fbo_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_orders_today_text()
    await callback.message.answer(text, reply_markup=fbo_keyboard())


@router.callback_query(F.data == "fbo_summary")
async def cb_fbo_summary(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_orders_today_text()
    try:
        await callback.message.edit_text(text, reply_markup=fbo_keyboard())
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=fbo_keyboard())


@router.callback_query(F.data == "fbo_month")
async def cb_fbo_month(callback: CallbackQuery) -> None:
    await callback.answer("Сводка за месяц скоро")
    await callback.message.answer(
        "Месячная сводка пока в разработке, покажем как только будет готово.",
        reply_markup=fbo_keyboard(),
    )


@router.callback_query(F.data == "fbo_filter")
async def cb_fbo_filter(callback: CallbackQuery) -> None:
    await callback.answer("Фильтр скоро")


@router.callback_query(F.data == "to_menu")
async def cb_to_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Главное меню", reply_markup=main_menu_kb())


@router.callback_query(F.data == "fbo_menu")
async def cb_fbo_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_orders_today_text()
    await callback.message.answer(text, reply_markup=fbo_keyboard())


@router.callback_query(F.data == "fbo_summary")
async def cb_fbo_summary(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_orders_today_text()
    try:
        await callback.message.edit_text(text, reply_markup=fbo_keyboard())
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=fbo_keyboard())


@router.callback_query(F.data == "fbo_month")
async def cb_fbo_month(callback: CallbackQuery) -> None:
    await callback.answer("Сводка за месяц скоро")
    await callback.message.answer(
        "Месячная сводка пока в разработке, покажем как только будет готово.",
        reply_markup=fbo_keyboard(),
    )


@router.callback_query(F.data == "fbo_filter")
async def cb_fbo_filter(callback: CallbackQuery) -> None:
    await callback.answer("Фильтр скоро")


@router.callback_query(F.data == "to_menu")
async def cb_to_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Главное меню", reply_markup=main_menu_kb())


@router.callback_query(F.data == "account_info")
async def cb_account_info(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_account_info_text()
    await callback.message.answer(text)


@router.callback_query(F.data == "full_analytics")
async def cb_full_analytics(callback: CallbackQuery) -> None:
    await callback.answer()
    # пока заглушка, позже допилим по Ульянову
    await callback.message.answer("📊 Полная аналитика скоро будет доступна.")


@router.callback_query(F.data == "reviews")
async def cb_reviews(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_reviews_menu_text()
    await callback.message.answer(text, reply_markup=reviews_periods_keyboard())


async def _send_reviews_period(
    callback: CallbackQuery, fetch_text: Callable[[], Awaitable[str]], period_key: str
) -> None:
    global _last_reviews_period
    _last_reviews_period = period_key
    await callback.answer()
    text = await fetch_text()
    markup = reviews_periods_keyboard()
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            await callback.answer("Этот период уже выбран")
        else:
            raise


@router.callback_query(F.data == "reviews_today")
async def cb_reviews_today(callback: CallbackQuery) -> None:
    await _send_reviews_period(callback, get_reviews_today_text, "today")


@router.callback_query(F.data == "reviews_week")
async def cb_reviews_week(callback: CallbackQuery) -> None:
    await _send_reviews_period(callback, get_reviews_week_text, "week")


@router.callback_query(F.data == "reviews_month")
async def cb_reviews_month(callback: CallbackQuery) -> None:
    await _send_reviews_period(callback, get_reviews_month_text, "month")


@router.callback_query(F.data.in_({"reviews_prev", "reviews_next"}))
async def cb_reviews_pagination(callback: CallbackQuery) -> None:
    await callback.answer("Пагинация скоро")


@router.callback_query(F.data == "reviews_ai_draft")
async def cb_reviews_ai_draft(callback: CallbackQuery) -> None:
    await callback.answer()
    review = await get_latest_review(_last_reviews_period)
    if not review:
        await callback.message.answer("Свежих отзывов в выбранном периоде нет.")
        return

    reply = await draft_reply(review)
    await callback.message.answer(f"💡 Черновик ответа:\n{reply}")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


bot = Bot(
    token=TG_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = build_dispatcher()
app = FastAPI()


async def start_bot() -> None:
    logger.info("Запускаю Telegram-бота (long polling)…")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Startup: validating Ozon credentials and creating polling task")
    # убедимся, что креды присутствуют, инициализируя клиент
    get_client()
    asyncio.create_task(start_bot())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Shutdown: closing Ozon client and bot")
    try:
        client = get_client()
    except Exception:
        client = None
    if client:
        await client.aclose()
    await bot.session.close()


@app.get("/")
async def root() -> dict:
    return {"status": "ok", "detail": "Ozon bot is running"}


__all__ = ["app", "bot", "dp", "router"]
