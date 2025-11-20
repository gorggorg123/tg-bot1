import asyncio
import logging
import os

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
from botapp.keyboards import (
    MenuCallbackData,
    ReviewsCallbackData,
    account_keyboard,
    fbo_menu_keyboard,
    main_menu_keyboard,
    reviews_navigation_keyboard,
    reviews_periods_keyboard,
)
from botapp.orders import get_orders_today_text
from botapp.ozon_client import get_client
from botapp.reviews import (
    get_ai_reply_for_review,
    get_current_review,
    get_review_view,
    get_reviews_menu_text,
)

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
    text = "Добро пожаловать! Выберите раздел в меню."
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(Command("fin_today"))
@router.message(F.text == "📊 Финансы за сегодня")
async def cmd_fin_today(message: Message) -> None:
    text = await get_finance_today_text()
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(Command("account"))
@router.message(F.text == "👤 Аккаунт Ozon")
async def cmd_account(message: Message) -> None:
    text = await get_account_info_text()
    await message.answer(text, reply_markup=account_keyboard())


@router.message(Command("fbo"))
@router.message(F.text == "📦 FBO")
async def cmd_fbo(message: Message) -> None:
    text = await get_orders_today_text()
    await message.answer(text, reply_markup=fbo_menu_keyboard())


@router.message(Command("reviews"))
@router.message(F.text == "⭐ Отзывы")
async def cmd_reviews(message: Message) -> None:
    text = await get_reviews_menu_text()
    await message.answer(text, reply_markup=reviews_periods_keyboard())


async def _send_review_card(
    *,
    user_id: int,
    period_key: str,
    index: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    global _last_reviews_period
    _last_reviews_period = period_key

    view = await get_review_view(user_id, period_key, index)

    if view.total == 0:
        text = view.text
        markup = reviews_periods_keyboard()
    else:
        text = view.text
        markup = reviews_navigation_keyboard(period_key, view.index, view.total)

    target = callback.message if callback else message
    if target is None:
        return

    try:
        if target.text == text:
            if callback:
                await callback.answer("Этот период уже выбран")
            return
        await target.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            if callback:
                await callback.answer("Этот период уже выбран")
        else:
            await target.answer(text, reply_markup=markup)


@router.callback_query(MenuCallbackData.filter(F.section == "home"))
async def cb_home(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    await callback.message.answer("Главное меню", reply_markup=main_menu_keyboard())


@router.callback_query(MenuCallbackData.filter(F.section == "fbo"))
async def cb_fbo(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    action = callback_data.action
    if action == "summary":
        text = await get_orders_today_text()
        try:
            await callback.message.edit_text(text, reply_markup=fbo_menu_keyboard())
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=fbo_menu_keyboard())
    elif action == "month":
        await callback.message.answer(
            "Месячная сводка пока в разработке, покажем как только будет готово.",
            reply_markup=fbo_menu_keyboard(),
        )
    elif action == "filter":
        await callback.message.answer("Фильтр скоро", reply_markup=fbo_menu_keyboard())


@router.callback_query(MenuCallbackData.filter(F.section == "account"))
async def cb_account(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    text = await get_account_info_text()
    await callback.message.answer(text, reply_markup=account_keyboard())


@router.callback_query(ReviewsCallbackData.filter())
async def cb_reviews(callback: CallbackQuery, callback_data: ReviewsCallbackData) -> None:
    action = callback_data.action
    period_key = callback_data.period or _last_reviews_period
    user_id = callback.from_user.id

    if action == "period":
        await callback.answer()
        await _send_review_card(user_id=user_id, period_key=period_key, index=0, callback=callback)
        return

    if action == "open":
        await callback.answer()
        index = callback_data.index or 0
        await _send_review_card(user_id=user_id, period_key=period_key, index=index, callback=callback)
        return

    if action == "ai":
        await callback.answer("Готовим ответ…", show_alert=False)
        review = await get_current_review(user_id, period_key)
        if not review:
            await callback.message.answer("Свежих отзывов в выбранном периоде нет.")
            return
        try:
            draft = await get_ai_reply_for_review(review)
            await callback.message.answer(f"✍️ Черновик ответа ИИ:\n\n{draft}")
        except Exception:
            await callback.message.answer(
                "⚠️ Не удалось получить ответ от ИИ, попробуйте позже."
            )
        return

    if action == "change_period":
        await callback.answer()
        text = await get_reviews_menu_text()
        try:
            await callback.message.edit_text(text, reply_markup=reviews_periods_keyboard())
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=reviews_periods_keyboard())
        return

    if action == "back_menu":
        await callback.answer()
        text = await get_reviews_menu_text()
        try:
            await callback.message.edit_text(text, reply_markup=reviews_periods_keyboard())
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=reviews_periods_keyboard())
        return


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
