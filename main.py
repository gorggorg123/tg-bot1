import asyncio
import logging
import os
from contextlib import suppress
from datetime import datetime, timezone
from typing import Dict, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from fastapi import FastAPI
from dotenv import load_dotenv

from botapp.account import get_account_info_text
from botapp.finance import get_finance_today_text
from botapp.keyboards import (
    MenuCallbackData,
    QuestionsCallbackData,
    ReviewsCallbackData,
    QuestionsCallbackData,
    account_keyboard,
    fbo_menu_keyboard,
    main_menu_keyboard,
    question_card_keyboard,
    questions_list_keyboard,
    review_card_keyboard,
    reviews_list_keyboard,
)
from botapp.orders import get_orders_today_text
from botapp.ozon_client import (
    OzonAPIError,
    get_client,
    get_write_client,
    has_write_credentials,
    get_question_by_id as api_get_question_by_id,
    get_questions_list,
    list_question_answers,
    delete_question_answer,
    send_question_answer,
)
from botapp.ai_client import generate_review_reply, generate_answer_for_question
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
    refresh_review_from_api,
    encode_review_id,
    resolve_review_id,
    refresh_reviews,
    trim_for_telegram,
    build_reviews_preview,
)
from botapp.questions import (
    find_question,
    format_question_card_text,
    get_question_by_index,
    get_question_index,
    get_questions_table,
    ensure_question_answer_text,
    refresh_questions,
    register_question_token,
    resolve_question_id,
    resolve_question_token,
)
from botapp.storage import append_question_record, upsert_question_answer
from botapp.message_gc import (
    SECTION_ACCOUNT,
    SECTION_FBO,
    SECTION_FINANCE_TODAY,
    SECTION_MENU,
    SECTION_QUESTION_CARD,
    SECTION_QUESTION_PROMPT,
    SECTION_QUESTIONS_LIST,
    SECTION_REVIEW_CARD,
    SECTION_REVIEW_PROMPT,
    SECTION_REVIEWS_LIST,
    delete_message_safe,
    delete_section_message,
    send_section_message,
)
from botapp.questions import (
    format_question_card_text,
    get_question_by_index,
    get_questions_table,
    refresh_questions,
    resolve_question_id,
)

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

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
_ephemeral_messages: Dict[int, Tuple[int, int, asyncio.Task]] = {}
_local_answers: Dict[Tuple[int, str], str] = {}
_local_answer_status: Dict[Tuple[int, str], str] = {}
_question_answers: Dict[Tuple[int, str], str] = {}
_question_answer_status: Dict[Tuple[int, str], str] = {}


def get_last_answer(user_id: int, review_id: str | None) -> str | None:
    """Вернуть последний сохранённый ответ (final или draft)."""

    if not review_id:
        return None
    try:
        return _local_answers.get((user_id, review_id))
    except Exception as exc:  # pragma: no cover - аварийная защита от порчи стейта
        logger.warning("Failed to read local answer for %s: %s", review_id, exc)
        return None


def get_last_question_answer(user_id: int, question_id: str | None) -> str | None:
    if not question_id:
        return None
    try:
        return _question_answers.get((user_id, question_id))
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to read local question answer for %s: %s", question_id, exc)
        return None


class ReviewAnswerStates(StatesGroup):
    reprompt = State()
    manual = State()


async def _delete_later(
    bot: Bot,
    chat_id: int,
    message_id: int,
    delay: int,
    user_id: int | None = None,
) -> None:
    try:
        await asyncio.sleep(delay)
        await delete_message_safe(bot, chat_id, message_id)
    finally:
        if user_id is not None:
            tracked = _ephemeral_messages.get(user_id)
            if tracked and tracked[1] == message_id:
                _ephemeral_messages.pop(user_id, None)


async def send_ephemeral_message(
    bot: Bot,
    chat_id: int,
    text: str,
    delay: int = 15,
    user_id: int | None = None,
    **kwargs,
    ) -> Message:
    """Отправляет служебное сообщение и удаляет его через ``delay`` секунд."""

    if user_id is not None:
        prev = _ephemeral_messages.pop(user_id, None)
        if prev:
            prev_chat_id, prev_msg_id, prev_task = prev
            if prev_task and not prev_task.done():
                prev_task.cancel()
            with suppress(Exception):
                await delete_message_safe(bot, prev_chat_id, prev_msg_id)

    msg = await bot.send_message(chat_id, text, **kwargs)
    delete_task = asyncio.create_task(
        _delete_later(bot, chat_id, msg.message_id, delay, user_id)
    )
    if user_id is not None:
        _ephemeral_messages[user_id] = (chat_id, msg.message_id, delete_task)
    return msg

    draft = answer_override or get_last_question_answer(user_id, question.id)
    text = format_question_card_text(question, answer_override=draft)
    markup = question_card_keyboard(
        category=category, page=page, question_id=question.id, can_send=True
    )

async def _clear_sections(bot: Bot, user_id: int, sections: list[str]) -> None:
    for section in sections:
        await delete_section_message(user_id, section, bot)


def _remember_question_answer(user_id: int, question_id: str, text: str, status: str = "draft") -> None:
    _question_answers[(user_id, question_id)] = text
    _question_answer_status[(user_id, question_id)] = status


def _forget_question_answer(user_id: int, question_id: str) -> None:
    _question_answers.pop((user_id, question_id), None)
    _question_answer_status.pop((user_id, question_id), None)


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

    sent = await send_section_message(
        SECTION_REVIEWS_LIST,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
    )
    await delete_section_message(
        user_id,
        SECTION_REVIEW_CARD,
        active_bot,
        preserve_message_id=sent.message_id if sent else None,
    )


async def _send_questions_list(
    *,
    user_id: int,
    category: str,
    page: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
) -> None:
    try:
        text, items, safe_page, total_pages = await get_questions_table(
            user_id=user_id, category=category, page=page
        )
    except OzonAPIError as exc:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                f"⚠️ Не удалось получить список вопросов. Ошибка: {exc}",
                user_id=user_id,
            )
        logger.warning("Unable to load questions list: %s", exc)
        return
    except Exception:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                "⚠️ Не удалось получить список вопросов. Попробуйте позже.",
                user_id=user_id,
            )
        logger.exception("Unexpected error while loading questions list")
        return
    markup = questions_list_keyboard(
        user_id=user_id,
        category=category,
        page=safe_page,
        total_pages=total_pages,
        items=items,
    )
    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    sent = await send_section_message(
        SECTION_QUESTIONS_LIST,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
    )
    await delete_section_message(
        user_id,
        SECTION_QUESTION_CARD,
        active_bot,
        preserve_message_id=sent.message_id if sent else None,
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "Привет! Я помогу быстро смотреть финансы, заказы и отзывы Ozon.\n"
        "Выберите раздел через кнопки ниже."
    )
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_MENU,
        text=text,
        reply_markup=main_menu_keyboard(),
        message=message,
    )


@router.message(Command("fin_today"))
async def cmd_fin_today(message: Message) -> None:
    text = await get_finance_today_text()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_FINANCE_TODAY,
        text=text,
        reply_markup=main_menu_keyboard(),
        message=message,
    )


@router.message(Command("account"))
async def cmd_account(message: Message) -> None:
    text = await get_account_info_text()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_ACCOUNT,
        text=text,
        reply_markup=account_keyboard(),
        message=message,
    )


@router.message(Command("fbo"))
async def cmd_fbo(message: Message) -> None:
    text = await get_orders_today_text()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_FBO,
        text=text,
        reply_markup=fbo_menu_keyboard(),
        message=message,
    )


@router.message(Command("reviews"))
async def cmd_reviews(message: Message) -> None:
    user_id = message.from_user.id
    await _clear_sections(
        message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await refresh_reviews(user_id)
    await _send_reviews_list(
        user_id=user_id,
        category="all",
        page=0,
        message=message,
        bot=message.bot,
        chat_id=message.chat.id,
    )


@router.message(Command("questions"))
async def cmd_questions(message: Message) -> None:
    user_id = message.from_user.id
    await _clear_sections(
        message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTION_CARD,
        ],
    )
    await refresh_questions(user_id)
    await _send_questions_list(
        user_id=user_id,
        category="unanswered",
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
        client = get_client()
        if client:
            await refresh_review_from_api(card, client)
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
            index=view.index,
            review_id=encode_review_id(user_id, card.id),
            can_send=has_write_credentials(),
            page=page,
        )

    target = callback.message if callback else message

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

    await send_section_message(
        SECTION_REVIEW_CARD,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=active_bot,
        chat_id=active_chat,
        user_id=user_id,
    )


async def _get_local_answer(user_id: int, review_id: str | None) -> str | None:
    if not review_id:
        return None
    return get_last_answer(user_id, review_id)


async def _remember_local_answer(user_id: int, review_id: str | None, text: str) -> None:
    if not review_id:
        return
    _local_answers[(user_id, review_id)] = text
    _local_answer_status[(user_id, review_id)] = "draft"


async def _handle_ai_reply(
    *,
    callback: CallbackQuery | Message,
    category: str,
    page: int,
    review: ReviewCard | None,
    index: int = 0,
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
    await _send_review_card(
        user_id=user_id,
        category=category,
        index=index,
        callback=callback if isinstance(callback, CallbackQuery) else None,
        message=target if isinstance(target, Message) else None,
        review_id=review.id,
        page=page,
        answer_override=final_answer,
    )


async def _send_question_card(
    *,
    user_id: int,
    category: str,
    index: int | None = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    page: int = 0,
    token: str | None = None,
    question=None,
    answer_override: str | None = None,
) -> None:
    resolved_question = question
    if resolved_question is None:
        if token:
            resolved_question = resolve_question_token(user_id, token)
        if resolved_question is None and index is not None:
            resolved_question = get_question_by_index(user_id, category, index)

    if resolved_question is None:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                "Не удалось найти этот вопрос. Обновите список и попробуйте ещё раз.",
                user_id=user_id,
            )
        return

    effective_token = token
    if not effective_token:
        idx = get_question_index(user_id, category, resolved_question.id)
        if idx is None:
            idx = get_question_index(user_id, "all", resolved_question.id)
            if idx is not None:
                category = "all"
        if idx is not None:
            effective_token = register_question_token(
                user_id=user_id, category=category, index=idx
            )

    await ensure_question_answer_text(resolved_question)

    text = format_question_card_text(resolved_question, answer_override=answer_override)
    markup = question_card_keyboard(
        category=category,
        page=page,
        token=effective_token,
        can_send=True,
        has_answer=getattr(resolved_question, "has_answer", False),
    )
    await send_section_message(
        SECTION_QUESTION_CARD,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        user_id=user_id,
    )
    # Сохраняем карточку вопроса без удаления исходного списка, чтобы экран не исчезал.


@router.callback_query(MenuCallbackData.filter(F.section == "home"))
async def cb_home(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_MENU,
        text="Главное меню",
        reply_markup=main_menu_keyboard(),
        callback=callback,
        user_id=user_id,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "fbo"))
async def cb_fbo(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    action = callback_data.action
    user_id = callback.from_user.id
    if action == "summary":
        text = await get_orders_today_text()
        await send_section_message(
            SECTION_FBO,
            text=text,
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "month":
        await send_section_message(
            SECTION_FBO,
            text="Месячная сводка пока в разработке, покажем как только будет готово.",
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "filter":
        await send_section_message(
            SECTION_FBO,
            text="Фильтр скоро",
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "open":
        text = await get_orders_today_text()
        await send_section_message(
            SECTION_FBO,
            text=text,
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "home":
        await _clear_sections(
            callback.message.bot,
            user_id,
            [
                SECTION_FBO,
                SECTION_FINANCE_TODAY,
                SECTION_ACCOUNT,
                SECTION_REVIEWS_LIST,
                SECTION_REVIEW_CARD,
                SECTION_QUESTIONS_LIST,
                SECTION_QUESTION_CARD,
            ],
        )
        await send_section_message(
            SECTION_MENU,
            text="Главное меню",
            reply_markup=main_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )


@router.callback_query(MenuCallbackData.filter(F.section == "account"))
async def cb_account(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    text = await get_account_info_text()
    user_id = callback.from_user.id
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_ACCOUNT,
        text=text,
        reply_markup=account_keyboard(),
        callback=callback,
        user_id=user_id,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "fin_today"))
async def cb_fin_today(callback: CallbackQuery, callback_data: MenuCallbackData) -> None:
    await callback.answer()
    text = await get_finance_today_text()
    user_id = callback.from_user.id
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_FINANCE_TODAY,
        text=text,
        reply_markup=main_menu_keyboard(),
        callback=callback,
        user_id=user_id,
    )


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
        await delete_section_message(user_id, SECTION_REVIEW_CARD, callback.message.bot)
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
            index=new_index or 0,
        )
        return

    if action == "card_reprompt":
        await callback.answer()
        prompt = await send_section_message(
            SECTION_REVIEW_PROMPT,
            text="Напишите свои пожелания к ответу, я пересоберу текст.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(ReviewAnswerStates.reprompt)
        await state.update_data(
            review_id=review_id, category=category, page=page, prompt_message_id=prompt.message_id
        )
        return

    if action == "card_manual":
        await callback.answer()
        prompt = await send_section_message(
            SECTION_REVIEW_PROMPT,
            text="Пришлите текст ответа, я сохраню его как текущий.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(ReviewAnswerStates.manual)
        await state.update_data(
            review_id=review_id, category=category, page=page, prompt_message_id=prompt.message_id
        )
        return

    if action == "send":
        await callback.answer()
        if not review_id:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось определить ID отзыва, попробуйте обновить список.",
                user_id=user_id,
            )
            return

        review, _ = await get_review_by_id(user_id, category, review_id)
        if not review:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Отзыв не найден, обновите список.",
                user_id=user_id,
            )
            return

        final_answer = await _get_local_answer(user_id, review.id)
        if not final_answer:
            final_answer = review.answer_text
        if final_answer:
            final_answer = final_answer.strip()

        if not final_answer:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Нет сохранённого текста ответа. Сначала сгенерируйте или введите ответ.",
                user_id=user_id,
            )
            return

        client = get_write_client()
        if not client:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Отправка на Ozon недоступна: не задан OZON_API_KEY.",
                user_id=user_id,
            )
            return

        try:
            await client.create_review_comment(review.id, final_answer)
        except Exception as exc:
            logger.warning("Failed to send review %s to Ozon: %s", review.id, exc)
            _local_answers[(user_id, review.id)] = final_answer
            _local_answer_status[(user_id, review.id)] = "error"
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось отправить ответ в Ozon. Проверьте права API‑ключа OZON_API_KEY в личном кабинете Ozon или попробуйте позже.",
                user_id=user_id,
            )
            return

        _local_answers[(user_id, review.id)] = final_answer
        _local_answer_status[(user_id, review.id)] = "sent"
        mark_review_answered(review.id, user_id, final_answer)
        await refresh_reviews(user_id)
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Ответ отправлен в Ozon ✅",
            user_id=user_id,
        )
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


@router.callback_query(QuestionsCallbackData.filter())
async def cb_questions(callback: CallbackQuery, callback_data: QuestionsCallbackData, state: FSMContext) -> None:
    user_id = callback.from_user.id
    action = callback_data.action
    category = callback_data.category or "all"
    page = int(callback_data.page or 0)
    token = callback_data.token

    def _resolve_question(
        *, token_value: str | None = None, legacy_data: dict | None = None
    ):
        question = resolve_question_token(user_id, token_value) if token_value else None
        if question:
            return question

        legacy = legacy_data or {}
        q_id = legacy.get("question_id") or legacy.get("id")
        if q_id:
            return resolve_question_id(user_id, q_id)

        idx = legacy.get("index") or legacy.get("question_index")
        if idx is not None:
            try:
                idx_int = int(idx)
            except Exception:
                idx_int = None
            if idx_int is not None:
                cat = legacy.get("category") or category
                return get_question_by_index(user_id, cat, idx_int)
        return None

    if action == "list":
        await callback.answer()
        try:
            await refresh_questions(user_id, category)
        except OzonAPIError as exc:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                f"⚠️ Не удалось получить список вопросов. Ошибка: {exc}",
                user_id=user_id,
            )
            return
        except Exception:
            logger.exception("Unexpected error while refreshing questions")
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "⚠️ Не удалось получить список вопросов. Попробуйте позже.",
                user_id=user_id,
            )
            return

        await _send_questions_list(
            user_id=user_id, category=category, page=page, callback=callback
        )
        return

    if action in {"list_page", "page"}:
        await callback.answer()
        await _send_questions_list(
            user_id=user_id, category=category, page=page, callback=callback
        )
        return

    if action in {"open", "open_card"}:
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await callback.answer(
                "Не удалось найти этот вопрос. Обновите список и попробуйте ещё раз.",
                show_alert=True,
            )
            return

        idx = get_question_index(user_id, category, question.id)
        effective_token = token
        effective_category = category
        if idx is None:
            idx = get_question_index(user_id, "all", question.id)
            if idx is not None:
                effective_category = "all"
        if idx is not None and not effective_token:
            effective_token = register_question_token(
                user_id=user_id, category=effective_category, index=idx
            )

        await callback.answer()
        await _send_question_card(
            user_id=user_id,
            category=effective_category,
            index=idx,
            callback=callback,
            page=page,
            token=effective_token,
            question=question,
        )
        return

    if action == "prefill":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для обновления ответа.",
                user_id=user_id,
            )
            return

        answer_text = question.answer_text
        if not (answer_text or "").strip():
            try:
                answers = await list_question_answers(question.id, limit=1)
                if answers:
                    question.answer_text = answers[0].text or question.answer_text
                    question.answer_id = answers[0].id or question.answer_id
                    question.has_answer = bool(question.answer_text)
                    answer_text = question.answer_text
            except Exception as exc:
                logger.warning("Failed to load current answer for %s: %s", question.id, exc)

        if not (answer_text or "").strip():
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Ответ для этого вопроса в Ozon не найден.",
                user_id=user_id,
            )
            return

        _remember_question_answer(user_id, question.id, answer_text, status="existing")
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=answer_text,
            answer_source="existing",
            answer_sent_to_ozon=True,
        )
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Текущий ответ подставлен в черновик, можно отредактировать и отправить.",
            user_id=user_id,
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            answer_override=answer_text,
            token=token,
            question=question,
        )
        return

    if action == "delete":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Вопрос не найден. Обновите список и попробуйте снова.",
                user_id=user_id,
            )
            return

        answer_id = getattr(question, "answer_id", None)
        if not answer_id:
            try:
                answers = await list_question_answers(question.id, limit=1)
                if answers:
                    answer_id = answers[0].id
                    question.answer_id = answer_id
                    question.answer_text = answers[0].text or question.answer_text
                    question.has_answer = bool(question.answer_text)
            except Exception as exc:
                logger.warning("Failed to fetch answers before delete %s: %s", question.id, exc)

        if not answer_id:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не нашли ответ, который можно удалить.",
                user_id=user_id,
            )
            return

        try:
            await delete_question_answer(question.id, answer_id=answer_id)
        except OzonAPIError as exc:
            logger.warning("Failed to delete question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                str(exc),
                user_id=user_id,
            )
            return
        except Exception as exc:
            logger.warning("Failed to delete question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось удалить ответ, попробуйте позже.",
                user_id=user_id,
            )
            return

        _forget_question_answer(user_id, question.id)
        question.answer_text = None
        question.answer_id = None
        question.has_answer = False
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=None,
            answer_source="deleted",
            answer_sent_to_ozon=False,
        )
        await refresh_questions(user_id, category)
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Ответ удалён в Ozon.",
            user_id=user_id,
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            token=token,
            question=question,
            answer_override=None,
        )
        return

    if action == "card_ai":
        question = _resolve_question(
            token_value=token, legacy_data=callback_data.model_dump()
        )
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для генерации ответа.",
                user_id=user_id,
            )
            return
        ai_answer = await generate_answer_for_question(
            question_text=question.question_text,
            product_name=question.product_name,
            existing_answer=question.answer_text
            or get_last_question_answer(user_id, question.id),
        )
        if not ai_answer:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось сгенерировать ответ, попробуйте ещё раз позже.",
                user_id=user_id,
            )
            return

        _remember_question_answer(user_id, question.id, ai_answer, status="ai")
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=ai_answer,
            answer_source="ai",
            answer_sent_to_ozon=False,
            meta={"chat_id": callback.message.chat.id, "message_id": callback.message.message_id},
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            answer_override=ai_answer,
            token=token,
            question=question,
        )
        return

    if action == "card_manual":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для подготовки ответа.",
                user_id=user_id,
            )
            return
        prompt = await send_section_message(
            SECTION_QUESTION_PROMPT,
            text="Пришлите текст ответа для покупателя.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(QuestionAnswerStates.manual)
        await state.update_data(
            question_token=token,
            question_id=question.id,
            category=category,
            page=page,
            prompt_message_id=prompt.message_id,
        )
        return

    if action == "card_reprompt":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для пересборки ответа.",
                user_id=user_id,
            )
            return
        prompt = await send_section_message(
            SECTION_QUESTION_PROMPT,
            text="Опишите, что изменить или добавить к ответу.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(QuestionAnswerStates.reprompt)
        await state.update_data(
            question_token=token,
            question_id=question.id,
            category=category,
            page=page,
            prompt_message_id=prompt.message_id,
        )
        return

    if action == "send":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Вопрос не найден. Обновите список и попробуйте снова.",
                user_id=user_id,
            )
            return

        answer = get_last_question_answer(user_id, question.id) or question.answer_text
        answer_clean = (answer or "").strip()
        if len(answer_clean) < 2:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Ответ пустой или слишком короткий, сначала отредактируйте текст.",
                user_id=user_id,
            )
            return
        try:
            await send_question_answer(question.id, answer_clean, sku=question.sku)
        except OzonAPIError as exc:
            logger.warning("Failed to send question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                str(exc),
                user_id=user_id,
            )
            return
        except Exception as exc:
            logger.warning("Failed to send question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось отправить ответ в Ozon. Проверьте права API‑ключа OZON_API_KEY.",
                user_id=user_id,
            )
            return

        _remember_question_answer(user_id, question.id, answer_clean, status="sent")
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=answer_clean,
            answer_source=_question_answer_status.get((user_id, question.id), "manual"),
            answer_sent_to_ozon=True,
            answer_sent_at=datetime.now(timezone.utc).isoformat(),
            meta={"chat_id": callback.message.chat.id, "message_id": callback.message.message_id},
        )
        question.has_answer = True
        question.answer_text = answer_clean
        await refresh_questions(user_id, category)
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Ответ отправлен в Ozon ✅",
            user_id=user_id,
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            answer_override=answer,
            token=token,
            question=question,
        )
        return

    await callback.message.answer(
        "Выберите действие в меню ниже", reply_markup=main_menu_keyboard()
    )


@router.message(ReviewAnswerStates.reprompt)
async def handle_reprompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    review_id = data.get("review_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text_payload = (message.text or message.caption or "").strip()
    if not text_payload:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    review, resolved_index = await get_review_by_id(user_id, category, review_id)
    if not review:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Не удалось найти отзыв для пересборки.",
            user_id=user_id,
        )
        await delete_section_message(user_id, SECTION_REVIEW_PROMPT, message.bot, force=True)
        await state.clear()
        return

    await _handle_ai_reply(
        callback=message,  # type: ignore[arg-type]
        category=category,
        page=page,
        review=review,
        index=resolved_index or 0,
        user_prompt=text_payload,
    )
    await delete_section_message(user_id, SECTION_REVIEW_PROMPT, message.bot, force=True)
    await state.clear()


@router.message(ReviewAnswerStates.manual)
async def handle_manual_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    review_id = data.get("review_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text = (message.text or message.caption or "").strip()
    if not text:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    await delete_section_message(user_id, SECTION_REVIEW_PROMPT, message.bot, force=True)
    await state.clear()
    await _remember_local_answer(user_id, review_id, text)
    await _send_review_card(
        user_id=user_id,
        category=category,
        index=0,
        message=message,
        review_id=review_id,
        page=page,
        answer_override=text,
    )


@router.message(QuestionAnswerStates.reprompt)
async def handle_question_reprompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question_token = data.get("question_token")
    question_id = data.get("question_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text_payload = (message.text or message.caption or "").strip()
    if not text_payload:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    question = resolve_question_token(user_id, question_token) if question_token else None
    if question is None and question_id:
        question = find_question(user_id, question_id) or await api_get_question_by_id(
            question_id
        )
    if not question:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Не удалось найти вопрос для пересборки.",
            user_id=user_id,
        )
        await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
        await state.clear()
        return

    previous = get_last_question_answer(user_id, question.id) or question.answer_text
    prompt = text_payload
    ai_answer = await generate_answer_for_question(
        question_text=question.question_text,
        product_name=question.product_name,
        existing_answer=previous,
        user_prompt=prompt,
    )
    _remember_question_answer(user_id, question.id, ai_answer, status="ai_edited")
    upsert_question_answer(
        question_id=question.id,
        created_at=question.created_at,
        sku=question.sku,
        product_name=question.product_name,
        question=question.question_text,
        answer=ai_answer,
        answer_source="ai_edited",
        answer_sent_to_ozon=False,
        meta={"chat_id": message.chat.id, "message_id": message.message_id},
    )
    await _send_question_card(
        user_id=user_id,
        category=category,
        page=page,
        message=message,
        answer_override=ai_answer,
        token=question_token,
        question=question,
    )
    await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
    await state.clear()


@router.message(QuestionAnswerStates.manual)
async def handle_question_manual(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question_token = data.get("question_token")
    question_id = data.get("question_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text = (message.text or message.caption or "").strip()
    if not text:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    question = resolve_question_token(user_id, question_token) if question_token else None
    if question is None and question_id:
        question = find_question(user_id, question_id) or await api_get_question_by_id(
            question_id
        )
    if not question:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Не удалось найти вопрос.",
            user_id=user_id,
        )
        await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
        await state.clear()
        return

    _remember_question_answer(user_id, question.id, text, status="manual")
    upsert_question_answer(
        question_id=question.id,
        created_at=question.created_at,
        sku=question.sku,
        product_name=question.product_name,
        question=question.question_text,
        answer=text,
        answer_source="manual",
        answer_sent_to_ozon=False,
        meta={"chat_id": message.chat.id, "message_id": message.message_id},
    )
    await _send_question_card(
        user_id=user_id,
        category=category,
        page=page,
        message=message,
        answer_override=text,
        token=question_token,
        question=question,
    )
    await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
    await state.clear()


@router.message()
async def handle_any(message: Message) -> None:
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
        ],
    )
    await send_section_message(
        SECTION_MENU,
        text="Выберите действие в меню ниже",
        reply_markup=main_menu_keyboard(),
        message=message,
    )


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


# Summary of latest changes:
# - Added a safe fallback for QuestionAnswerStates import to prevent FSM NameErrors on deploy.
# - Kept question list handling on Ozon-approved statuses with Pydantic parsing and user-facing warnings.

__all__ = ["app", "bot", "dp", "router"]
