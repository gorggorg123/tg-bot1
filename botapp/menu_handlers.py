# botapp/menu_handlers.py
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from botapp.keyboards import (
    MenuCallbackData,
    back_home_keyboard,
    fbo_menu_keyboard,
    finance_menu_keyboard,
    main_menu_keyboard,
)
from botapp.sections.finance import logic as finance
from botapp.sections.fbo import logic as orders
from botapp.utils.message_gc import (
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
    get_section_message_id,
    safe_remove_message,
    send_section_message,
)
from botapp.utils.storage import is_outreach_enabled, set_outreach_enabled

logger = logging.getLogger(__name__)
router = Router()


async def _close_all_sections(
    bot,
    user_id: int,
    *,
    preserve_menu: bool = False,
    preserve_message_id: int | None = None,
    preserve_message_ids: set[int] | None = None,
) -> None:
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

    preserve_set: set[int] | None = None
    if preserve_message_ids:
        preserve_set = {int(mid) for mid in preserve_message_ids}
    if preserve_message_id is not None:
        preserve_set = (preserve_set or set()) | {int(preserve_message_id)}

    if not preserve_menu:
        logger.info("Deleting previous menu for user_id=%s before rendering new one", user_id)
        await delete_section_message(user_id, SECTION_MENU, bot, force=True)

    for sec in sections:
        try:
            await delete_section_message(
                user_id,
                sec,
                bot,
                force=True,
                preserve_message_ids=preserve_set,
            )
        except Exception:
            logger.exception("Failed to delete section=%s for user=%s", sec, user_id)


async def _show_menu(*, user_id: int, callback: CallbackQuery | None = None, message: Message | None = None) -> None:
    text = (
        "<b>Ozon Seller Bot</b>\n\n"
        "Выберите раздел:\n"
        "• <b>Отзывы</b> — список → карточка → ИИ/пересборка → отправка\n"
        "• <b>Вопросы</b> — список → карточка → ИИ/пересборка → отправка\n"
        "• <b>Чаты</b> — переписка «пузырями», ИИ-ответ с учётом истории\n"
    )

    enabled = is_outreach_enabled(user_id)

    await send_section_message(
        SECTION_MENU,
        text=text,
        reply_markup=main_menu_keyboard(outreach_enabled=enabled),
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
    trigger_mid = callback.message.message_id if callback.message else None
    await _show_menu(user_id=user_id, callback=callback)
    menu_mid = get_section_message_id(user_id, SECTION_MENU)
    preserve_set = {int(menu_mid)} if menu_mid is not None else None

    await _close_all_sections(
        callback.message.bot,
        user_id,
        preserve_menu=True,
        preserve_message_ids=preserve_set,
    )

    if trigger_mid is not None and (menu_mid is None or int(menu_mid) != int(trigger_mid)):
        await safe_remove_message(callback.message.bot, callback.message.chat.id, int(trigger_mid))


@router.callback_query(MenuCallbackData.filter(F.section == "menu"))
async def menu_alias(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await state.clear()
    trigger_mid = callback.message.message_id if callback.message else None
    await _show_menu(user_id=user_id, callback=callback)
    menu_mid = get_section_message_id(user_id, SECTION_MENU)
    preserve_set = {int(menu_mid)} if menu_mid is not None else None
    await _close_all_sections(
        callback.message.bot,
        user_id,
        preserve_menu=True,
        preserve_message_ids=preserve_set,
    )

    if trigger_mid is not None and (menu_mid is None or int(menu_mid) != int(trigger_mid)):
        await safe_remove_message(callback.message.bot, callback.message.chat.id, int(trigger_mid))


@router.callback_query(MenuCallbackData.filter(F.section == "outreach"))
async def toggle_outreach(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    curr = is_outreach_enabled(user_id)
    set_outreach_enabled(user_id, not curr)
    await callback.answer("Рассылка включена" if not curr else "Рассылка выключена")
    await _show_menu(user_id=user_id, callback=callback)


@router.callback_query(MenuCallbackData.filter(F.section == "fbo"))
async def menu_fbo(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await state.clear()
    preserve_mid = callback.message.message_id if callback.message else None
    logger.info(
        "Switch section: from=%s to=%s, preserve_menu=%s mid=%s",
        "menu",
        SECTION_FBO,
        True,
        preserve_mid,
    )
    await _close_all_sections(
        callback.message.bot,
        user_id,
        preserve_menu=True,
        preserve_message_ids={preserve_mid} if preserve_mid is not None else None,
    )
    data = MenuCallbackData.unpack(callback.data)
    action = data.action

    if action in {"open", "summary", None}:
        text = await orders.get_orders_today_text()
    elif action == "month":
        text = await orders.get_orders_month_text()
    elif action == "filter":
        text = "🔍 Фильтр скоро будет доступен."
    else:
        text = "📦 FBO: выберите действие."

    await send_section_message(
        SECTION_FBO,
        user_id=user_id,
        text=text,
        reply_markup=fbo_menu_keyboard(),
        callback=callback,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "fin_today"))
async def menu_finance(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await state.clear()
    preserve_mid = callback.message.message_id if callback.message else None
    logger.info(
        "Switch section: from=%s to=%s, preserve_menu=%s mid=%s",
        "menu",
        SECTION_FINANCE_TODAY,
        True,
        preserve_mid,
    )
    await _close_all_sections(
        callback.message.bot,
        user_id,
        preserve_menu=True,
        preserve_message_ids={preserve_mid} if preserve_mid is not None else None,
    )
    data = MenuCallbackData.unpack(callback.data)
    action = data.action

    if action in {"open", "summary", None}:
        text = await finance.get_finance_today_text()
    elif action == "month":
        text = await finance.get_finance_month_summary_text()
    else:
        text = "🏦 Финансы: выберите период."

    await send_section_message(
        SECTION_FINANCE_TODAY,
        user_id=user_id,
        text=text,
        reply_markup=finance_menu_keyboard(),
        callback=callback,
    )
