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
    SECTION_CDEK,
    delete_section_message,
    send_section_message,
)

logger = logging.getLogger(__name__)
router = Router()


async def _close_all_sections(
    bot,
    user_id: int,
    *,
    preserve_menu: bool = False,
    preserve_message_id: int | None = None,
    preserve_message_ids: set[int] | None = None,
    exclude_sections: list[str] | None = None,
) -> None:
    """Закрывает все секции, кроме указанных исключений.
    
    Args:
        bot: Telegram bot instance
        user_id: User ID
        preserve_menu: Если True, меню не удаляется
        preserve_message_id: ID сообщения, которое нужно сохранить
        preserve_message_ids: Множество ID сообщений, которые нужно сохранить
        exclude_sections: Список секций, которые не нужно удалять
    """
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
        SECTION_CDEK,
    ]

    exclude_set = set(exclude_sections or [])

    preserve_set: set[int] | None = None
    if preserve_message_ids:
        preserve_set = {int(mid) for mid in preserve_message_ids}
    if preserve_message_id is not None:
        preserve_set = (preserve_set or set()) | {int(preserve_message_id)}

    if not preserve_menu and SECTION_MENU not in exclude_set:
        logger.debug("Deleting previous menu for user_id=%s before rendering new one", user_id)
        try:
            await delete_section_message(user_id, SECTION_MENU, bot, force=True)
        except Exception:
            logger.debug("Failed to delete menu for user=%s", user_id, exc_info=True)

    deleted_count = 0
    for sec in sections:
        if sec in exclude_set:
            continue
        try:
            deleted = await delete_section_message(
                user_id,
                sec,
                bot,
                force=True,
                preserve_message_ids=preserve_set,
            )
            if deleted:
                deleted_count += 1
        except Exception:
            logger.debug("Failed to delete section=%s for user=%s", sec, user_id, exc_info=True)
    
    if deleted_count > 0:
        logger.debug("Closed %s sections for user_id=%s", deleted_count, user_id)


async def _show_menu(
    *, 
    user_id: int, 
    callback: CallbackQuery | None = None, 
    message: Message | None = None,
    edit_current_message: bool = False,
) -> None:
    """Показать главное меню.
    
    Args:
        user_id: ID пользователя
        callback: Callback query (для редактирования существующего сообщения)
        message: Message (для отправки нового сообщения)
        edit_current_message: Если True, редактирует текущее сообщение вместо создания нового
    """
    text = (
        "🛒 <b>Ozon Seller Bot</b>\n\n"
        "Выберите раздел:\n\n"
        "🗂 <b>Коммуникации</b>\n"
        "• 📝 Отзывы\n"
        "• ❓ Вопросы\n"
        "• 💬 Чаты\n\n"
        "⚙️ <b>Операции</b>\n"
        "• 📦 Отправки (FBO/FBS)\n"
        "• 🚚 СДЭК\n"
        "• 💵 Финансы\n\n"
        "🔔 <b>Сервис</b>\n"
        "• Уведомления\n\n"
        "Подсказка: для отправки СДЭК из диалога откройте нужный чат и нажмите «🚚 СДЭК из чата»."
    )

    await send_section_message(
        SECTION_MENU,
        text=text,
        reply_markup=main_menu_keyboard(),
        callback=callback,
        message=message,
        user_id=user_id,
        edit_current_message=edit_current_message,
    )


@router.message(F.text.in_({"/start", "/menu"}))
async def cmd_start(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    logger.info("Starting bot for user_id=%s", user_id)
    await state.clear()
    await _close_all_sections(message.bot, user_id)
    await _show_menu(user_id=user_id, message=message)


@router.callback_query(MenuCallbackData.filter(F.section == "home"))
async def menu_home(callback: CallbackQuery, state: FSMContext) -> None:
    """Возврат в главное меню (кнопка Домой)."""
    user_id = callback.from_user.id
    logger.info("Returning to home menu for user_id=%s", user_id)
    await state.clear()
    # Редактируем текущее сообщение в меню, чтобы избежать дублирования
    await _show_menu(user_id=user_id, callback=callback, edit_current_message=True)


@router.callback_query(MenuCallbackData.filter(F.section == "menu"))
async def menu_alias(callback: CallbackQuery, state: FSMContext) -> None:
    """Альтернативный возврат в меню."""
    user_id = callback.from_user.id
    logger.info("Returning to menu (alias) for user_id=%s", user_id)
    await state.clear()
    # Редактируем текущее сообщение в меню, чтобы избежать дублирования
    await _show_menu(user_id=user_id, callback=callback, edit_current_message=True)


# Хранение текущего режима отображения FBO/FBS для каждого пользователя
_user_fbo_mode: dict[int, str] = {}


def _get_fbo_mode(user_id: int) -> str:
    """Получить текущий режим отображения FBO/FBS для пользователя."""
    return _user_fbo_mode.get(user_id, "all")


def _set_fbo_mode(user_id: int, mode: str) -> None:
    """Установить режим отображения FBO/FBS для пользователя."""
    if mode in ("all", "fbo", "fbs"):
        _user_fbo_mode[user_id] = mode


@router.callback_query(MenuCallbackData.filter(F.section == "fbo"))
async def menu_fbo(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    logger.info("Opening FBO/FBS section for user_id=%s", user_id)
    await state.clear()
    
    data = MenuCallbackData.unpack(callback.data)
    action = data.action
    
    # Определяем режим отображения
    mode = _get_fbo_mode(user_id)
    
    # Обработка переключения режима
    if action == "mode_all":
        mode = "all"
        _set_fbo_mode(user_id, mode)
        action = "summary"  # Показываем сводку за сегодня
    elif action == "mode_fbo":
        mode = "fbo"
        _set_fbo_mode(user_id, mode)
        action = "summary"
    elif action == "mode_fbs":
        mode = "fbs"
        _set_fbo_mode(user_id, mode)
        action = "summary"

    # Получаем текст в зависимости от действия
    if action in {"open", "summary", "refresh", None}:
        text = await orders.get_orders_today_text(mode=mode)
    elif action == "month":
        text = await orders.get_orders_month_text(mode=mode)
    else:
        text = "📦 Отправки: выберите действие."

    # Редактируем текущее сообщение
    await send_section_message(
        SECTION_FBO,
        user_id=user_id,
        text=text,
        reply_markup=fbo_menu_keyboard(mode=mode),
        callback=callback,
        edit_current_message=True,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "fin_today"))
async def menu_finance(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    logger.info("Opening Finance from menu for user_id=%s", user_id)
    await state.clear()
    
    data = MenuCallbackData.unpack(callback.data)
    action = data.action

    if action in {"open", "summary", None}:
        text = await finance.get_finance_today_text()
    elif action == "month":
        text = await finance.get_finance_month_summary_text()
    else:
        text = "🏦 Финансы: выберите период."

    # Редактируем текущее сообщение (меню) в раздел Финансов, чтобы избежать дублирования
    await send_section_message(
        SECTION_FINANCE_TODAY,
        user_id=user_id,
        text=text,
        reply_markup=finance_menu_keyboard(),
        callback=callback,
        edit_current_message=True,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "notifications"))
async def menu_notifications(callback: CallbackQuery, state: FSMContext) -> None:
    """Открыть настройки уведомлений из меню."""
    from botapp.notifications.handlers import _format_settings_text, _build_settings_keyboard
    
    user_id = callback.from_user.id
    logger.info("Opening Notifications from menu for user_id=%s", user_id)
    await state.clear()
    
    text = _format_settings_text(user_id)
    keyboard = _build_settings_keyboard(user_id)
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    
    await callback.answer()


@router.callback_query(MenuCallbackData.filter(F.section == "cdek"))
async def menu_cdek(callback: CallbackQuery, state: FSMContext) -> None:
    """Открыть раздел CDEK из меню."""
    from botapp.sections.cdek.keyboards import cdek_main_keyboard
    from botapp.utils.message_gc import SECTION_CDEK, send_section_message
    
    user_id = callback.from_user.id
    logger.info("Opening CDEK section for user_id=%s", user_id)
    await state.clear()
    
    text = (
        "🚚 <b>СДЭК</b>\n\n"
        "Выберите действие:"
    )
    
    await send_section_message(
        SECTION_CDEK,
        user_id=user_id,
        text=text,
        reply_markup=cdek_main_keyboard(),
        callback=callback,
        edit_current_message=True,
    )
    
    await callback.answer()
