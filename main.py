import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from dotenv import load_dotenv
from fastapi import FastAPI

from botapp.account import get_account_info_text
from botapp.finance import get_finance_today_text
from botapp.keyboards import (
    MenuCallbackData,
    ReviewsCallbackData,
    fbo_menu_keyboard,
    main_menu_keyboard,
    reviews_navigation_keyboard,
)
from botapp.orders import get_orders_today_text
from botapp.ozon_client import get_client
from botapp.reviews import MODE_UNANSWERED, get_reviews_page, trim_for_telegram

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
REVIEWS_STATE_MODE_KEY = "reviews_mode"
REVIEWS_STATE_PAGE_KEY = "reviews_page"
REVIEWS_STATE_MSG_KEY = "reviews_message_id"
REVIEWS_DEFAULT_MODE = MODE_UNANSWERED


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
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(Command("fbo"))
@router.message(F.text == "📦 FBO")
async def cmd_fbo(message: Message) -> None:
    text = await get_orders_today_text()
    await message.answer(text, reply_markup=fbo_menu_keyboard())


@router.message(Command("reviews"))
@router.message(F.text == "⭐ Отзывы")
async def cmd_reviews(message: Message, state: FSMContext) -> None:
    await state.update_data(
        {
            REVIEWS_STATE_MODE_KEY: REVIEWS_DEFAULT_MODE,
            REVIEWS_STATE_PAGE_KEY: 0,
            REVIEWS_STATE_MSG_KEY: None,
        }
    )
    await _render_reviews_page(message=message, state=state, mode=REVIEWS_DEFAULT_MODE, page=0)


async def _render_reviews_page(
    *,
    state: FSMContext,
    mode: str | None = None,
    page: int | None = None,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
) -> None:
    data = await state.get_data()
    current_mode = mode or data.get(REVIEWS_STATE_MODE_KEY, REVIEWS_DEFAULT_MODE)
    current_page = page if page is not None else data.get(REVIEWS_STATE_PAGE_KEY, 0)

    page_view = await get_reviews_page(mode=current_mode, page=current_page)
    text = trim_for_telegram(page_view.text)
    markup = reviews_navigation_keyboard(page_view.mode, page_view.page, page_view.total_pages)

    target = callback.message if callback else message
    if target is None:
        return

    last_message_id = data.get(REVIEWS_STATE_MSG_KEY)

    try:
        if target.text == text:
            if callback:
                await callback.answer("Без изменений")
            return
        await target.edit_text(text, reply_markup=markup)
        new_message_id = target.message_id
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc):
            if callback:
                await callback.answer("Без изменений")
            return
        send_func = callback.message.answer if callback else message.answer  # type: ignore[union-attr]
        sent = await send_func(text, reply_markup=markup)
        new_message_id = sent.message_id
        if last_message_id and callback:
            try:
                await callback.bot.delete_message(chat_id=callback.message.chat.id, message_id=last_message_id)
            except TelegramBadRequest:
                pass

    await state.update_data(
        {
            REVIEWS_STATE_MODE_KEY: page_view.mode,
            REVIEWS_STATE_PAGE_KEY: page_view.page,
            REVIEWS_STATE_MSG_KEY: new_message_id,
        }
    )


@router.callback_query(MenuCallbackData.filter(F.section == "home"))
async def cb_home(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    await callback.message.answer("Главное меню", reply_markup=main_menu_keyboard())

    view = await get_review_view(user_id, period_key, index)

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
    await callback.message.answer(text, reply_markup=main_menu_keyboard())


@router.callback_query(ReviewsCallbackData.filter())
async def cb_reviews(callback: CallbackQuery, callback_data: ReviewsCallbackData, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    current_mode = callback_data.mode or data.get(REVIEWS_STATE_MODE_KEY, REVIEWS_DEFAULT_MODE)

    if callback_data.action == "toggle":
        await state.update_data({REVIEWS_STATE_PAGE_KEY: 0})
        await _render_reviews_page(state=state, mode=current_mode, page=0, callback=callback)
        return

    if callback_data.action == "page":
        page = callback_data.page or 0
        await _render_reviews_page(state=state, mode=current_mode, page=page, callback=callback)
        return

    # fallback: показать актуальный список
    await _render_reviews_page(state=state, mode=current_mode, callback=callback)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
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
