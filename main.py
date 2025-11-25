import asyncio
import logging
import os
from contextlib import suppress
from typing import Dict, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
    review_card_keyboard,
    reviews_list_keyboard,
)
from botapp.orders import get_orders_today_text
from botapp.ozon_client import get_client, get_write_client, has_write_credentials
from botapp.ai_client import generate_review_reply
from botapp.reviews import (
    ReviewCard,
    format_review_card_text,
    get_ai_reply_for_review,
    get_review_and_card,
    get_review_by_id,
    get_review_by_index,
    get_review_view,
    get_reviews_table,
    mark_review_answered,
    encode_review_id,
    resolve_review_id,
    refresh_reviews,
    trim_for_telegram,
    build_reviews_preview,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
ENABLE_TG_POLLING = os.getenv("ENABLE_TG_POLLING", "1") == "1"

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN is not set")
router = Router()
_polling_task: asyncio.Task | None = None
_polling_lock = asyncio.Lock()
_last_service_messages: Dict[int, int] = {}
_reviews_list_messages: Dict[int, Tuple[int, int]] = {}
_review_card_messages: Dict[int, Tuple[int, int]] = {}
_local_answers: Dict[Tuple[int, str], str] = {}
_local_answer_status: Dict[Tuple[int, str], str] = {}


def get_last_answer(user_id: int, review_id: str | None) -> str | None:
    """Вернуть последний сохранённый ответ (final или draft)."""

    if not review_id:
        return None
    try:
        return _local_answers.get((user_id, review_id))
    except Exception as exc:  # pragma: no cover - аварийная защита от порчи стейта
        logger.warning("Failed to read local answer for %s: %s", review_id, exc)
        return None


class ReviewAnswerStates(StatesGroup):
    reprompt = State()
    manual = State()


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


def _remember_list_message(user_id: int, chat_id: int, message_id: int) -> None:
    _reviews_list_messages[user_id] = (chat_id, message_id)
    remember_service_message(user_id, message_id)


def _remember_card_message(user_id: int, chat_id: int, message_id: int) -> None:
    _review_card_messages[user_id] = (chat_id, message_id)


async def _send_reviews_list(
    *,
    user_id: int,
    category: str,
    page: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
) -> None:
    text, items, safe_page, total_pages = await get_reviews_table(
        user_id=user_id, category=category, page=page
    )
    markup = reviews_list_keyboard(
        category=category, page=safe_page, total_pages=total_pages, items=items
    )

    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    stored = _reviews_list_messages.get(user_id)
    preferred_id = stored[1] if stored else None
    target_msg_id = None
    if target and target.message_id == preferred_id:
        target_msg_id = target.message_id
    elif preferred_id:
        target_msg_id = preferred_id

    # Стараемся переиспользовать одно сообщение списка
    if target_msg_id:
        try:
            edited = await active_bot.edit_message_text(
                text=text,
                chat_id=active_chat,
                message_id=target_msg_id,
                reply_markup=markup,
            )
            _remember_list_message(user_id, active_chat, edited.message_id)
            return
        except TelegramBadRequest:
            with suppress(Exception):
                await delete_message_safe(active_bot, active_chat, target_msg_id)

    sent = await send_service_message(active_bot, active_chat, user_id, text, reply_markup=markup)
    _remember_list_message(user_id, active_chat, sent.message_id)


def remember_service_message(user_id: int, message_id: int) -> None:
    _last_service_messages[user_id] = message_id


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "Привет! Я помогу быстро смотреть финансы, заказы и отзывы Ozon.\n"
        "Выберите раздел через кнопки ниже."
    )
    await send_service_message(
        message.bot,
        message.chat.id,
        message.from_user.id,
        text,
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("fin_today"))
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
async def cmd_reviews(message: Message) -> None:
    user_id = message.from_user.id
    await refresh_reviews(user_id)
    await _send_reviews_list(
        user_id=user_id,
        category="all",
        page=0,
        message=message,
        bot=message.bot,
        chat_id=message.chat.id,
    )


async def _send_review_card(
    *,
    user_id: int,
    category: str,
    index: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    review_id: str | None = None,
    page: int = 0,
    answer_override: str | None = None,
) -> None:
    view, card = await get_review_and_card(user_id, category, index, review_id=review_id)
    if view.total == 0 or not card:
        text = trim_for_telegram(view.text)
        markup = main_menu_keyboard()
    else:
        current_answer = answer_override or await _get_local_answer(user_id, card.id)
        text = format_review_card_text(
            card=card,
            index=view.index,
            total=view.total,
            period_title=view.period,
            user_id=user_id,
            current_answer=current_answer,
        )
        markup = review_card_keyboard(
            category=category,
            page=page,
            review_id=encode_review_id(user_id, card.id),
            can_send=has_write_credentials(),
        )

    target = callback.message if callback else message
    list_msg = _reviews_list_messages.get(user_id)
    if list_msg and target and target.message_id == list_msg[1]:
        target = None  # Не трогаем сообщение-таблицу

    active_bot = None
    active_chat = None
    if target:
        active_bot = target.bot
        active_chat = target.chat.id
    elif callback and callback.message:
        active_bot = callback.message.bot
        active_chat = callback.message.chat.id

    if not active_bot or active_chat is None:
        return

    stored = _review_card_messages.get(user_id)
    preferred_chat_id, preferred_msg_id = stored if stored else (None, None)

    if preferred_msg_id and preferred_chat_id == active_chat:
        try:
            edited = await active_bot.edit_message_text(
                text=text,
                chat_id=preferred_chat_id,
                message_id=preferred_msg_id,
                reply_markup=markup,
            )
            _remember_card_message(user_id, preferred_chat_id, edited.message_id)
            return
        except TelegramBadRequest:
            with suppress(Exception):
                await delete_message_safe(active_bot, preferred_chat_id, preferred_msg_id)

    if target:
        try:
            edited = await target.edit_text(text, reply_markup=markup)
            _remember_card_message(user_id, active_chat, edited.message_id)
            if preferred_msg_id and preferred_msg_id != edited.message_id:
                with suppress(Exception):
                    await delete_message_safe(active_bot, active_chat, preferred_msg_id)
            return
        except TelegramBadRequest:
            with suppress(Exception):
                await delete_message_safe(active_bot, active_chat, target.message_id)

    sent = await active_bot.send_message(active_chat, text, reply_markup=markup)
    _remember_card_message(user_id, active_chat, sent.message_id)
    if preferred_msg_id and preferred_msg_id != sent.message_id:
        with suppress(Exception):
            await delete_message_safe(active_bot, active_chat, preferred_msg_id)


async def _get_local_answer(user_id: int, review_id: str | None) -> str | None:
    if not review_id:
        return None
    return get_last_answer(user_id, review_id)


async def _remember_local_answer(user_id: int, review_id: str | None, text: str) -> None:
    if not review_id:
        return
    _local_answers[(user_id, review_id)] = text
    _local_answer_status[(user_id, review_id)] = "draft"


async def _delete_card_message(user_id: int, bot: Bot) -> None:
    card = _review_card_messages.pop(user_id, None)
    if card:
        chat_id, msg_id = card
        await delete_message_safe(bot, chat_id, msg_id)


async def _handle_ai_reply(
    *,
    callback: CallbackQuery | Message,
    category: str,
    page: int,
    review: ReviewCard | None,
    user_prompt: str | None = None,
) -> None:
    if not review:
        target = callback.message if isinstance(callback, CallbackQuery) else callback
        await target.answer("Свежих отзывов нет.")
        return

    user_id = callback.from_user.id if isinstance(callback, CallbackQuery) else callback.from_user.id
    target = callback.message if isinstance(callback, CallbackQuery) else callback

    current_answer = await _get_local_answer(user_id, review.id)
    draft = await generate_review_reply(
        review_text=review.text,
        product_name=review.product_name,
        rating=review.rating,
        previous_answer=current_answer,
        user_prompt=user_prompt,
    )

    if not draft:
        await target.answer("⚠️ Не удалось получить ответ от ИИ")
        return

    final_answer = draft
    await _remember_local_answer(user_id, review.id, final_answer)
    mark_review_answered(review.id, user_id, final_answer)
    await _send_review_card(
        user_id=user_id,
        category=category,
        index=0,
        callback=callback if isinstance(callback, CallbackQuery) else None,
        message=target if isinstance(target, Message) else None,
        review_id=review.id,
        page=page,
        answer_override=final_answer,
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
    elif action == "open":
        text = await get_orders_today_text()
        await callback.message.answer(text, reply_markup=fbo_menu_keyboard())
    elif action == "home":
        await callback.message.answer("Главное меню", reply_markup=main_menu_keyboard())


@router.callback_query(MenuCallbackData.filter(F.section == "account"))
async def cb_account(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    text = await get_account_info_text()
    await callback.message.answer(text, reply_markup=account_keyboard())


@router.callback_query(MenuCallbackData.filter(F.section == "fin_today"))
async def cb_fin_today(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    text = await get_finance_today_text()
    await callback.message.answer(text, reply_markup=main_menu_keyboard())


@router.callback_query(ReviewsCallbackData.filter())
async def cb_reviews(callback: CallbackQuery, callback_data: ReviewsCallbackData, state: FSMContext) -> None:
    action = callback_data.action
    category = callback_data.category or "unanswered"
    index = callback_data.index or 0
    user_id = callback.from_user.id
    review_token = callback_data.review_id
    review_id = resolve_review_id(user_id, review_token)
    page = callback_data.page or 0

    if action in {"list", "list_page"}:
        await callback.answer()
        if action == "list":
            await refresh_reviews(user_id)
        await _send_reviews_list(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
        )
        await _delete_card_message(user_id, callback.message.bot)
        return

    if action == "open_card":
        await callback.answer()
        await _send_review_card(
            user_id=user_id,
            category=category,
            index=index,
            callback=callback,
            review_id=review_id,
            page=page,
        )
        return

    if action == "list_page":
        await callback.answer()
        await _send_reviews_list(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
        )
        return

    if action == "card_ai":
        await callback.answer("Готовим ответ…", show_alert=False)
        review, new_index = await get_review_by_id(user_id, category, review_id)
        await _handle_ai_reply(
            callback=callback,
            category=category,
            page=page,
            review=review,
        )
        return

    if action == "card_reprompt":
        await callback.answer()
        await state.set_state(ReviewAnswerStates.reprompt)
        await state.update_data(review_id=review_id, category=category, page=page)
        await callback.message.answer("Напишите свои пожелания к ответу, я пересоберу текст.")
        return

    if action == "card_manual":
        await callback.answer()
        await state.set_state(ReviewAnswerStates.manual)
        await state.update_data(review_id=review_id, category=category, page=page)
        await callback.message.answer("Пришлите текст ответа, я сохраню его как текущий.")
        return

    if action == "send":
        await callback.answer()
        if not review_id:
            await callback.message.answer(
                "Не удалось определить ID отзыва, попробуйте обновить список."
            )
            return

        review, _ = await get_review_by_id(user_id, category, review_id)
        if not review:
            await callback.message.answer("Отзыв не найден, обновите список.")
            return

        final_answer = await _get_local_answer(user_id, review.id)
        if not final_answer:
            final_answer = review.answer_text
        if final_answer:
            final_answer = final_answer.strip()

        if not final_answer:
            await callback.message.answer(
                "Нет сохранённого текста ответа. Сначала сгенерируйте или введите ответ."
            )
            return

        client = get_write_client()
        if not client:
            await callback.message.answer(
                "Отправка на Ozon недоступна: не задан OZON_API_KEY_WRITE."
            )
            return

        try:
            await client.create_review_comment(review.id, final_answer)
        except Exception as exc:
            logger.warning("Failed to send review %s to Ozon: %s", review.id, exc)
            _local_answers[(user_id, review.id)] = final_answer
            _local_answer_status[(user_id, review.id)] = "error"
            await callback.message.answer(
                "Не удалось отправить ответ в Ozon. Проверьте права write-ключа или попробуйте позже."
            )
            return

        _local_answers[(user_id, review.id)] = final_answer
        _local_answer_status[(user_id, review.id)] = "sent"
        mark_review_answered(review.id, user_id, final_answer)
        await callback.message.answer("Ответ отправлен в Ozon ✅")
        await _send_review_card(
            user_id=user_id,
            category=category,
            index=index,
            callback=callback,
            review_id=review_id,
            page=page,
            answer_override=final_answer,
        )
        return

    # fallback для неизвестных сообщений
    await callback.message.answer(
        "Выберите действие в меню ниже", reply_markup=main_menu_keyboard()
    )


@router.message(ReviewAnswerStates.reprompt)
async def handle_reprompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    review_id = data.get("review_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    review, _ = await get_review_by_id(user_id, category, review_id)
    if not review:
        await message.answer("Не удалось найти отзыв для пересборки.")
        return

    await _handle_ai_reply(
        callback=message,  # type: ignore[arg-type]
        category=category,
        page=page,
        review=review,
        user_prompt=(message.text or message.caption or ""),
    )


@router.message(ReviewAnswerStates.manual)
async def handle_manual_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    review_id = data.get("review_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text = (message.text or message.caption or "").strip()
    if not text:
        await message.answer("Ответ пустой, пришлите текст.")
        return

    await _remember_local_answer(user_id, review_id, text)
    mark_review_answered(review_id, user_id, text)
    await _send_review_card(
        user_id=user_id,
        category=category,
        index=0,
        message=message,
        review_id=review_id,
        page=page,
        answer_override=text,
    )


@router.message()
async def handle_any(message: Message) -> None:
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
    """Стартуем polling один раз за процесс."""

    global _polling_task
    async with _polling_lock:
        if _polling_task and not _polling_task.done():
            logger.info("Polling уже запущен, повторно не стартуем")
            return
        if _polling_task and _polling_task.done():
            _polling_task = None

        # На Render запускается только один web-инстанс с ENABLE_TG_POLLING=1.
        # Второй инстанс/воркер должен выставлять ENABLE_TG_POLLING=0, иначе Telegram
        # вернёт TelegramConflictError из-за параллельного polling.
        logger.info("Telegram bot polling started (single instance)")
        _polling_task = asyncio.create_task(
            dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
            )
        )

    try:
        await _polling_task
    except asyncio.CancelledError:
        logger.info("Polling task cancelled, shutting down")
        raise


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Startup: validating Ozon credentials")
    get_client()

    if not ENABLE_TG_POLLING:
        # Локально ставим ENABLE_TG_POLLING=0, чтобы не лезть в Telegram,
        # пока прод на Render работает с ENABLE_TG_POLLING=1.
        logger.info("Telegram polling is disabled by ENABLE_TG_POLLING=0")
        return

    logger.info("Startup: creating polling task")
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
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await _polling_task
    await bot.session.close()


@app.get("/")
async def root() -> dict:
    return {"status": "ok", "detail": "Ozon bot is running"}


@app.get("/reviews")
async def reviews(days: int = 30) -> dict:
    """HTTP-эндпоинт для выборки отзывов и названий товаров по SKU."""

    return await build_reviews_preview(days=days)


__all__ = ["app", "bot", "dp", "router"]
