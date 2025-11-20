import asyncio
import logging
import os
from typing import Dict, Tuple

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
    review_draft_keyboard,
    reviews_navigation_keyboard,
    reviews_root_keyboard,
)
from botapp.orders import get_orders_today_text
from botapp.ozon_client import get_client
from botapp.ai_client import AIClientError
from botapp.reviews import (
    ReviewCard,
    get_ai_reply_for_review,
    get_review_and_card,
    get_review_by_id,
    get_review_by_index,
    get_review_view,
    mark_review_answered,
    refresh_reviews,
    trim_for_telegram,
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
_draft_cache: Dict[Tuple[int, str], str] = {}
_pending_edit: Dict[int, Tuple[str | None, str, int]] = {}
_polling_task: asyncio.Task | None = None
_last_service_messages: Dict[int, int] = {}


def _get_draft_key(user_id: int, review_id: str | None) -> Tuple[int, str]:
    return (user_id, review_id or "unknown")


async def delete_message_safe(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest as exc:
        if "message to delete not found" in str(exc):
            return
        if "message can't be deleted" in str(exc):
            return
        logger.debug("Skip delete message: %s", exc)


async def send_service_message(
    bot: Bot, chat_id: int, user_id: int, text: str, reply_markup=None
) -> Message:
    """Отправить служебное сообщение, удалив предыдущее для пользователя."""

    prev = _last_service_messages.get(user_id)
    if prev:
        await delete_message_safe(bot, chat_id, prev)

    sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    _last_service_messages[user_id] = sent.message_id
    return sent


def remember_service_message(user_id: int, message_id: int) -> None:
    _last_service_messages[user_id] = message_id


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "Привет! Я помогу быстро смотреть финансы, заказы и отзывы Ozon.\n"
        "Выберите раздел на клавиатуре ниже."
    )
    await send_service_message(
        message.bot,
        message.chat.id,
        message.from_user.id,
        text,
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("fin_today"))
@router.message(F.text == "📊 Финансы сегодня")
async def cmd_fin_today(message: Message) -> None:
    text = await get_finance_today_text()
    await send_service_message(
        message.bot,
        message.chat.id,
        message.from_user.id,
        text,
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("account"))
@router.message(F.text == "⚙️ Аккаунт Ozon")
async def cmd_account(message: Message) -> None:
    text = await get_account_info_text()
    await send_service_message(
        message.bot,
        message.chat.id,
        message.from_user.id,
        text,
        reply_markup=account_keyboard(),
    )


@router.message(Command("fbo"))
@router.message(F.text == "📦 FBO за сегодня")
async def cmd_fbo(message: Message) -> None:
    text = await get_orders_today_text()
    await send_service_message(
        message.bot,
        message.chat.id,
        message.from_user.id,
        text,
        reply_markup=fbo_menu_keyboard(),
    )


@router.message(Command("reviews"))
@router.message(F.text == "⭐ Отзывы")
async def cmd_reviews(message: Message) -> None:
    user_id = message.from_user.id
    session = await refresh_reviews(user_id)
    if not session.unanswered_reviews:
        await send_service_message(
            message.bot,
            message.chat.id,
            user_id,
            "Неотвеченных отзывов нет. Можно посмотреть отвеченные или обновить список.",
            reply_markup=reviews_root_keyboard(),
        )
        return

    await _send_review_card(user_id=user_id, category="unanswered", index=0, message=message)


async def _send_review_card(
    *,
    user_id: int,
    category: str,
    index: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    review_id: str | None = None,
) -> None:
    view, card = await get_review_and_card(user_id, category, index, review_id=review_id)
    if view.total == 0:
        text = trim_for_telegram(view.text)
        markup = reviews_root_keyboard()
    else:
        text = trim_for_telegram(view.text)
        markup = reviews_navigation_keyboard(category, view.index, view.total, card.id if card else None)

    target = callback.message if callback else message
    if target is None:
        return

    try:
        await target.edit_text(text, reply_markup=markup)
        remember_service_message(user_id, target.message_id)
    except TelegramBadRequest:
        sent = await send_service_message(target.bot, target.chat.id, user_id, text, reply_markup=markup)
        remember_service_message(user_id, sent.message_id)


def _store_draft(user_id: int, review_id: str | None, text: str) -> None:
    _draft_cache[_get_draft_key(user_id, review_id)] = text


def _get_draft(user_id: int, review_id: str | None) -> str | None:
    return _draft_cache.get(_get_draft_key(user_id, review_id))


async def _handle_ai_reply(callback: CallbackQuery, category: str, index: int, review: ReviewCard | None) -> None:
    if not review:
        await callback.message.answer("Свежих отзывов нет.")
        return

    try:
        draft = await get_ai_reply_for_review(review)
    except AIClientError as exc:
        await callback.message.answer(exc.user_message)
        return
    except Exception:
        logger.exception("AI generation failed")
        await callback.message.answer("⚠️ Не удалось получить ответ от ИИ, попробуйте позже.")
        return

    _store_draft(callback.from_user.id, review.id, draft)
    await callback.message.answer(
        f"Черновик ответа от ИИ:\n\n{draft}",
        reply_markup=review_draft_keyboard(category, index, review.id),
    )


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
    category = callback_data.category or "unanswered"
    index = callback_data.index or 0
    user_id = callback.from_user.id
    review_id = callback_data.review_id

    if action == "open_list":
        await callback.answer()
        await refresh_reviews(user_id)
        await _send_review_card(user_id=user_id, category=category, index=0, callback=callback)
        return

    if action == "nav":
        await callback.answer()
        await _send_review_card(user_id=user_id, category=category, index=index, callback=callback, review_id=review_id)
        return

    if action == "switch":
        await callback.answer()
        await _send_review_card(user_id=user_id, category=category, index=0, callback=callback)
        return

    if action == "ai":
        await callback.answer("Готовим ответ…", show_alert=False)
        review, new_index = await get_review_by_id(user_id, category, review_id)
        await _handle_ai_reply(callback, category, new_index, review)
        return

    if action == "regen":
        await callback.answer("Готовим новый ответ…", show_alert=False)
        review, new_index = await get_review_by_id(user_id, category, review_id)
        await _handle_ai_reply(callback, category, new_index, review)
        return

    if action == "edit":
        await callback.answer()
        _, current_index = await get_review_by_id(user_id, category, review_id)
        _pending_edit[user_id] = (callback_data.review_id, category, current_index)
        await callback.message.answer(
            "Пришлите отредактированный текст ответа одним сообщением."
        )
        return

    if action == "mark":
        await callback.answer()
        mark_review_answered(review_id, user_id)
        await _send_review_card(user_id=user_id, category=category, index=index, callback=callback, review_id=review_id)
        return

    if action == "send":
        await callback.answer()
        review_id = callback_data.review_id
        mark_review_answered(review_id, user_id)
        await callback.message.answer(
            "Ответ отмечен как отправленный. (Отправка в Ozon пока не реализована)"
        )
        await _send_review_card(user_id=user_id, category=category, index=index, callback=callback, review_id=review_id)
        return


@router.message()
async def handle_any(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if user_id in _pending_edit:
        review_id, category, index = _pending_edit.pop(user_id)
        text = (message.text or message.caption or "").strip()
        if not text:
            await message.answer("Не удалось сохранить пустой ответ, попробуйте ещё раз.")
            return
        _store_draft(user_id, review_id, text)
        mark_review_answered(review_id, user_id)
        await message.answer(
            f"Черновик обновлён:\n\n{text}",
            reply_markup=review_draft_keyboard(category, index, review_id),
        )
        await _send_review_card(user_id=user_id, category=category, index=index, message=message, review_id=review_id)
        return

    # fallback для неизвестных сообщений
    await message.answer("Выберите действие в меню ниже", reply_markup=main_menu_keyboard())


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
    global _polling_task
    if _polling_task and not _polling_task.done():
        return

    logger.info("Запускаю Telegram-бота (long polling)…")
    _polling_task = asyncio.create_task(
        dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    )
    await _polling_task


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Startup: validating Ozon credentials and creating polling task")
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
