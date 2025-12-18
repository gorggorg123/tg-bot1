# botapp/menu_handlers.py
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botapp.keyboards import MenuCallbackData, main_menu_keyboard
from botapp.message_gc import (
    SECTION_ACCOUNT,
    SECTION_CHAT_HISTORY,
    SECTION_CHAT_PROMPT,
    SECTION_CHATS_LIST,
    SECTION_FBO,
    SECTION_FINANCE_TODAY,
    SECTION_MENU,
    SECTION_QUESTIONS_LIST,
    SECTION_QUESTION_CARD,
    SECTION_QUESTION_PROMPT,
    SECTION_REVIEWS_LIST,
    SECTION_REVIEW_CARD,
    SECTION_REVIEW_PROMPT,
    SECTION_WAREHOUSE_MENU,
    SECTION_WAREHOUSE_PLAN,
    SECTION_WAREHOUSE_PROMPT,
    delete_section_message,
    send_section_message,
)

logger = logging.getLogger(__name__)
router = Router()


async def _close_all_sections(bot, user_id: int, *, preserve_menu: bool = False) -> None:
    sections = [
        SECTION_REVIEWS_LIST,
        SECTION_REVIEW_CARD,
        SECTION_REVIEW_PROMPT,
        SECTION_QUESTIONS_LIST,
        SECTION_QUESTION_CARD,
        SECTION_QUESTION_PROMPT,
        SECTION_CHATS_LIST,
        SECTION_CHAT_HISTORY,
        SECTION_CHAT_PROMPT,
        SECTION_FBO,
        SECTION_FINANCE_TODAY,
        SECTION_ACCOUNT,
        SECTION_WAREHOUSE_MENU,
        SECTION_WAREHOUSE_PLAN,
        SECTION_WAREHOUSE_PROMPT,
    ]
    if not preserve_menu:
        sections.append(SECTION_MENU)

    for sec in sections:
        try:
            await delete_section_message(user_id, sec, bot, force=True)
        except Exception:
            continue


async def _show_menu(*, user_id: int, callback: CallbackQuery | None = None, message: Message | None = None) -> None:
    text = (
        "<b>Ozon Seller Bot</b>\n\n"
        "Выберите раздел:\n"
        "• <b>Отзывы</b> — список → карточка → ИИ/пересборка → отправка\n"
        "• <b>Вопросы</b> — список → карточка → ИИ/пересборка → отправка\n"
        "• <b>Чаты</b> — переписка «пузырями», ИИ-ответ с учётом истории\n"
    )

    await send_section_message(
        SECTION_MENU,
        text=text,
        reply_markup=main_menu_keyboard(),
        callback=callback,
        message=message,
        user_id=user_id,
    )


@router.message(F.text.in_({"/start", "/menu"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    await state.clear()
    await _close_all_sections(message.bot, user_id)
    await _show_menu(user_id=user_id, message=message)


@router.callback_query(MenuCallbackData.filter(F.section == "home"))
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await state.clear()
    await _close_all_sections(callback.message.bot, user_id, preserve_menu=False)
    await _show_menu(user_id=user_id, callback=callback)


@router.callback_query(MenuCallbackData.filter(F.section == "menu"))
async def menu_alias(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await state.clear()
    await _close_all_sections(callback.message.bot, user_id, preserve_menu=False)
    await _show_menu(user_id=user_id, callback=callback)
