# botapp/utils/message_gc.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from botapp.utils.section_refs_store import (
    SectionRef,
    get_ref as _store_get_ref,
    mark_stale as _store_mark_stale,
    pop_ref as _store_pop_ref,
    set_ref as _store_set_ref,
)

logger = logging.getLogger(__name__)

NOT_MODIFIED = object()

# -----------------------------------------------------------------------------
# Section IDs (единый реестр “экранов”)
# -----------------------------------------------------------------------------

SECTION_MENU = "menu"

SECTION_REVIEWS_LIST = "reviews_list"
SECTION_REVIEW_CARD = "review_card"
SECTION_REVIEW_PROMPT = "review_prompt"

SECTION_QUESTIONS_LIST = "questions_list"
SECTION_QUESTION_CARD = "question_card"
SECTION_QUESTION_PROMPT = "question_prompt"

SECTION_CHATS_LIST = "chats_list"
SECTION_CHAT_HISTORY = "chat_history"  # “шапка” чата с кнопками
SECTION_CHAT_PROMPT = "chat_prompt"  # ИИ-черновик / репромпт / редактирование

SECTION_FBO = "fbo"
SECTION_FINANCE_TODAY = "finance_today"

SECTION_CDEK = "cdek"

# -----------------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _key(user_id: int, section: str) -> tuple[int, str]:
    return (int(user_id), str(section))


def _get_ref(user_id: int, section: str) -> SectionRef | None:
    return _store_get_ref(user_id, section)


def _set_ref(user_id: int, section: str, chat_id: int, message_id: int) -> None:
    _store_set_ref(user_id, section, chat_id, message_id)


def _pop_ref(user_id: int, section: str) -> SectionRef | None:
    return _store_pop_ref(user_id, section)


# -----------------------------------------------------------------------------
# Public getters (нужны, чтобы безопасно убирать trigger-сообщение)
# -----------------------------------------------------------------------------


def get_section_ref(user_id: int, section: str) -> SectionRef | None:
    """Публичный read-only доступ к реестру секций."""
    return _get_ref(user_id, section)


def get_section_message_id(user_id: int, section: str) -> int | None:
    ref = _get_ref(user_id, section)
    return ref.message_id if ref else None


# -----------------------------------------------------------------------------
# Safe Telegram ops
# -----------------------------------------------------------------------------


async def _safe_delete(bot, chat_id: int, message_id: int) -> bool:
    """deleteMessage. Возвращает True только при реальном успехе."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        msg = str(exc).lower()
        if "message to delete not found" in msg:
            logger.debug("Message already deleted %s/%s", chat_id, message_id)
            return True
        if "message can't be deleted" in msg:
            logger.debug("Can't delete %s/%s (too old / not allowed). Treating as removed.", chat_id, message_id)
            return True
        if "message identifier is not specified" in msg:
            logger.debug("Message identifier missing for %s/%s, treating as removed", chat_id, message_id)
            return True

        logger.debug("Failed to delete message %s/%s: %s", chat_id, message_id, exc)
        return False
    except Exception:
        logger.exception("Unexpected error while deleting message %s/%s", chat_id, message_id)
        return False


async def _safe_clear(bot, chat_id: int, message_id: int) -> bool:
    """Fallback: делаем сообщение пустым (\u200b) и убираем inline-клавиатуру."""
    try:
        res = await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="\u200b",
            reply_markup=None,
            parse_mode=None,
            disable_web_page_preview=True,
        )
        return isinstance(res, Message) or res is None
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        msg = str(exc).lower()
        if "message to edit not found" in msg:
            logger.debug("Message already absent while clearing %s/%s", chat_id, message_id)
            return True
        if "message is not modified" in msg:
            return True
        logger.debug("Failed to clear message %s/%s via edit: %s", chat_id, message_id, exc)
        return False
    except Exception:
        logger.exception("Unexpected error while clearing message %s/%s", chat_id, message_id)
        return False


async def _safe_edit(
    bot,
    *,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    parse_mode: str = "HTML",
) -> Optional[Message]:
    """editMessageText. Возвращает Message / NOT_MODIFIED / None."""
    try:
        res = await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        if isinstance(res, Message):
            return res
        return None
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return NOT_MODIFIED
        return None
    except TelegramForbiddenError:
        return None
    except Exception:
        logger.exception("Unexpected error while editing message %s/%s", chat_id, message_id)
        return None


# -----------------------------------------------------------------------------
# One-call “remove”: delete -> clear fallback
# -----------------------------------------------------------------------------


async def safe_remove_message(bot, chat_id: int, message_id: int) -> bool:
    """Удалить сообщение, а если не получилось — зачистить через edit."""
    ok_del = await _safe_delete(bot, chat_id, message_id)
    if ok_del:
        return True
    logger.info("Clear fallback used for %s/%s", chat_id, message_id)
    ok_clear = await _safe_clear(bot, chat_id, message_id)
    return ok_clear


# -----------------------------------------------------------------------------
# Rendering (1 сообщение на секцию)
# -----------------------------------------------------------------------------


async def render_section(
    section: str,
    *,
    bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    callback: CallbackQuery | None = None,
    mode: str = "edit_trigger",
    edit_current_message: bool = False,
) -> Message | None:
    """Рендер секции.

    Дизайн:
      - Для каждой секции хранится ref на единственное сообщение.
      - Если есть ref в том же чате — редактируем его.
      - Если редактирование не удалось — отправляем новое и удаляем/зачищаем старое.
      - Для menu стараемся всегда редактировать menu anchor (ref), а не trigger.
      - Если edit_current_message=True, редактируем trigger вместо поиска старого ref.
    """
    parse_mode = parse_mode or "HTML"

    # Сейчас проект использует только "edit_trigger"; оставляем mode для совместимости.
    if mode not in {"edit_trigger", "section_only"}:
        mode = "edit_trigger"

    trigger_mid: int | None = None
    if callback and callback.message:
        trigger_mid = int(callback.message.message_id)

    prev = _get_ref(user_id, section)
    prev_same_chat = bool(prev and prev.chat_id == int(chat_id))

    # Используем флажки, чтобы понимать, пробовали ли мы превратить trigger в меню
    # и нужно ли его чистить при падении edit.
    menu_trigger_attempted = False
    menu_trigger_succeeded = False
    new_message_sent = False  # Флаг, чтобы избежать двойной отправки
    sent: Message | None = None  # Инициализируем sent для безопасности

    # 0) Если edit_current_message=True, сначала пробуем редактировать trigger message
    if edit_current_message and trigger_mid is not None:
        logger.debug("render_section: edit_current_message=True, trying to edit trigger mid=%s for section=%s", trigger_mid, section)
        edited = await _safe_edit(
            bot,
            chat_id=int(chat_id),
            message_id=int(trigger_mid),
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if edited is NOT_MODIFIED:
            _set_ref(user_id, section, int(chat_id), int(trigger_mid))
            # Удаляем старый ref если он отличается от trigger
            if prev_same_chat and prev is not None and int(prev.message_id) != int(trigger_mid):
                logger.debug("render_section: deleting old ref mid=%s after editing trigger", prev.message_id)
                await safe_remove_message(bot, int(chat_id), int(prev.message_id))
            logger.info("Section %s ref set to trigger mid=%s for user_id=%s (edit_current_message)", section, trigger_mid, user_id)
            return None
        if edited:
            _set_ref(user_id, section, int(chat_id), int(trigger_mid))
            # Удаляем старый ref если он отличается от trigger
            if prev_same_chat and prev is not None and int(prev.message_id) != int(trigger_mid):
                logger.debug("render_section: deleting old ref mid=%s after editing trigger", prev.message_id)
                await safe_remove_message(bot, int(chat_id), int(prev.message_id))
            logger.info("Section %s ref set to trigger mid=%s for user_id=%s (edit_current_message)", section, trigger_mid, user_id)
            return edited
        # Редактирование не удалось - удаляем trigger перед отправкой нового сообщения
        logger.debug("render_section: failed to edit trigger mid=%s for section=%s, will delete trigger and send new", trigger_mid, section)
        await safe_remove_message(bot, int(chat_id), int(trigger_mid))
        # Очищаем ref, чтобы не пытаться редактировать его снова в блоке #1
        if prev_same_chat and prev is not None and int(prev.message_id) == int(trigger_mid):
            prev_same_chat = False
        # Также очищаем prev, чтобы блок #1 не пытался редактировать уже удаленное сообщение
        prev = None
        prev_same_chat = False  # ВАЖНО: также сбрасываем флаг после очистки prev

    # 1) Если у секции уже есть сообщение в этом чате — редактируем его.
    if prev_same_chat and prev is not None:
        target_mid = int(prev.message_id)
        edited = await _safe_edit(
            bot,
            chat_id=int(chat_id),
            message_id=target_mid,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if edited is NOT_MODIFIED:
            _set_ref(user_id, section, int(chat_id), target_mid)
            return None
        if edited:
            _set_ref(user_id, section, int(chat_id), target_mid)
            return edited

        # edit failed -> удаляем старое сообщение и отправляем новое
        logger.info(
            "Edit failed for section=%s user_id=%s target_mid=%s, deleting old and sending new message",
            section,
            user_id,
            target_mid,
        )
        # Удаляем старое сообщение перед отправкой нового, чтобы избежать дублирования
        await safe_remove_message(bot, int(chat_id), target_mid)
        
        try:
            sent = await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            new_message_sent = True  # ВАЖНО: устанавливаем флаг, чтобы не отправить еще раз в блоке #3
        except Exception:
            logger.exception("Failed to send fallback section message section=%s user_id=%s", section, user_id)
            return None

        # Продолжаем выполнение, чтобы удалить другие конфликтующие секции (блок #4)
        # Не возвращаемся здесь, а продолжаем до блока #4 и #5

    # 2) Нет prev в этом чате.
    #    Для menu: если ref отсутствует, можно попробовать edit trigger, но это рискованно,
    #    поэтому сначала пытаемся edit trigger, а если не вышло — отправляем новое и (опц.) чистим trigger.
    if section == SECTION_MENU and trigger_mid is not None:
        menu_trigger_attempted = True
        edited = await _safe_edit(
            bot,
            chat_id=int(chat_id),
            message_id=int(trigger_mid),
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if edited is NOT_MODIFIED:
            menu_trigger_succeeded = True
            _set_ref(user_id, section, int(chat_id), int(trigger_mid))
            logger.info("Menu ref set to mid=%s for user_id=%s", trigger_mid, user_id)
            return None
        if edited:
            menu_trigger_succeeded = True
            _set_ref(user_id, section, int(chat_id), int(trigger_mid))
            logger.info("Menu ref set to mid=%s for user_id=%s", trigger_mid, user_id)
            return edited

        logger.info("Edit failed for menu trigger mid=%s user_id=%s, sending new menu", trigger_mid, user_id)

    # 3) Отправляем новое сообщение секции (только если еще не отправлено)
    if not new_message_sent:
        try:
            sent = await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            new_message_sent = True
        except Exception:
            logger.exception("Failed to send section message section=%s user_id=%s", section, user_id)
            return None
    else:
        # Сообщение уже отправлено в блоке #1, используем его
        # sent уже установлен в блоке #1
        logger.debug("render_section: message already sent in block #1, skipping block #3")
    
    # ВАЖНО: Проверяем, что sent определен перед использованием
    if sent is None:
        logger.error("render_section: sent is None after all attempts for section=%s user_id=%s", section, user_id)
        return None

    # 4) Автоматически удаляем другие секции при открытии новой (кроме меню и связанных секций)
    # Это предотвращает накопление сообщений в чате
    if section != SECTION_MENU:
        # Определяем конфликтующие секции (которые должны быть удалены при открытии новой)
        all_sections = [
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
        ]
        
        # Удаляем конфликтующие секции, кроме текущей и связанных
        # Сохраняем только фактически отображаемое сообщение секции.
        # Если render_section отправил НОВОЕ сообщение, trigger нельзя
        # автоматически сохранять, иначе старые экраны остаются "висячими".
        preserve_mids = {int(sent.message_id)}
        
        # Определяем связанные секции, которые не нужно удалять
        related_sections = set()
        if section == SECTION_REVIEWS_LIST:
            related_sections = {SECTION_REVIEW_CARD, SECTION_REVIEW_PROMPT}
        elif section == SECTION_REVIEW_CARD:
            related_sections = {SECTION_REVIEWS_LIST, SECTION_REVIEW_PROMPT}
        elif section == SECTION_REVIEW_PROMPT:
            related_sections = {SECTION_REVIEWS_LIST, SECTION_REVIEW_CARD}
        elif section == SECTION_QUESTIONS_LIST:
            related_sections = {SECTION_QUESTION_CARD, SECTION_QUESTION_PROMPT}
        elif section == SECTION_QUESTION_CARD:
            related_sections = {SECTION_QUESTIONS_LIST, SECTION_QUESTION_PROMPT}
        elif section == SECTION_QUESTION_PROMPT:
            related_sections = {SECTION_QUESTIONS_LIST, SECTION_QUESTION_CARD}
        elif section == SECTION_CHATS_LIST:
            # Режим "один экран": список чатов не должен жить параллельно с карточкой/промптом чата.
            related_sections = set()
        elif section == SECTION_CHAT_HISTORY:
            # Режим "один экран": шапка чата заменяет список и prompt.
            related_sections = set()
        elif section == SECTION_CHAT_PROMPT:
            # Режим "один экран": prompt (ИИ/СДЭК) заменяет остальные chat-секции.
            related_sections = set()
        
        # Удаляем конфликтующие секции (все, кроме текущей, связанных и меню)
        # ВАЖНО: если секция та же самая (например, открываем новый chats_list),
        # то удаляем старую версию этой секции, но не новое сообщение
        deleted_count = 0
        current_message_id = int(sent.message_id)
        for sec in all_sections:
            # Пропускаем связанные секции (они не конфликтуют)
            if sec in related_sections:
                continue
            
            ref = _get_ref(user_id, sec)
            if ref and ref.chat_id == int(chat_id):
                # Если это та же секция, проверяем, что это не только что отправленное сообщение
                if sec == section:
                    if ref.message_id == current_message_id:
                        continue  # Это новое сообщение, не удаляем
                    # Это старая версия той же секции - удаляем
                    logger.debug("Auto-deleting old version of section=%s (mid=%s) for user=%s when opening new %s (mid=%s)", 
                                sec, ref.message_id, user_id, section, current_message_id)
                else:
                    # Это другая секция - удаляем
                    logger.debug("Auto-deleting conflicting section=%s for user=%s when opening %s", sec, user_id, section)
                
                try:
                    deleted = await delete_section_message(
                        user_id,
                        sec,
                        bot,
                        force=True,
                        preserve_message_ids=preserve_mids,
                    )
                    if deleted:
                        deleted_count += 1
                except Exception:
                    logger.debug("Failed to auto-delete section=%s for user=%s", sec, user_id, exc_info=True)
        
        if deleted_count > 0:
            logger.info("Auto-deleted %s conflicting sections for user_id=%s when opening %s", deleted_count, user_id, section)
        
        # Также удаляем меню, если оно не является trigger-сообщением
        menu_ref = _get_ref(user_id, SECTION_MENU)
        if menu_ref and menu_ref.chat_id == int(chat_id):
            # Не удаляем меню, если оно является trigger-сообщением
            if trigger_mid is None or int(menu_ref.message_id) != int(trigger_mid):
                try:
                    deleted = await delete_section_message(
                        user_id,
                        SECTION_MENU,
                        bot,
                        force=True,
                        preserve_message_ids=preserve_mids,
                    )
                    if deleted:
                        logger.debug("Auto-deleted menu for user=%s when opening %s", user_id, section)
                except Exception:
                    logger.debug("Failed to auto-delete menu for user=%s", user_id, exc_info=True)

    # 5) Удаляем/зачищаем предыдущее сообщение секции (если было), кроме trigger (его обычно хочет убрать GC секций).
    # ВАЖНО: удаляем только если prev в том же чате и это не новое сообщение, которое мы только что отправили
    if prev:
        prev_chat_id = int(prev.chat_id)
        prev_message_id = int(prev.message_id)
        current_chat_id = int(chat_id)
        current_message_id = int(sent.message_id)
        
        # Удаляем только если:
        # 1. prev в том же чате
        # 2. prev не является trigger-сообщением
        # 3. prev не является новым сообщением, которое мы только что отправили
        if (prev_chat_id == current_chat_id and 
            not (trigger_mid is not None and prev_message_id == int(trigger_mid)) and
            prev_message_id != current_message_id):
            logger.debug("Deleting previous section message section=%s user_id=%s chat_id=%s prev_mid=%s new_mid=%s", 
                        section, user_id, prev_chat_id, prev_message_id, current_message_id)
            await safe_remove_message(bot, prev_chat_id, prev_message_id)

    _set_ref(user_id, section, int(chat_id), int(sent.message_id))
    if section == SECTION_MENU:
        logger.info("Menu ref set to mid=%s for user_id=%s", sent.message_id, user_id)

    # Если мы вынужденно отправили новое меню при callback — можно попытаться убрать trigger, чтобы не оставалось дублей.
    # Но только если trigger не равен новому menu mid.
    if (
        section == SECTION_MENU
        and menu_trigger_attempted
        and not menu_trigger_succeeded
        and trigger_mid is not None
        and int(trigger_mid) != int(sent.message_id)
    ):
        # Никаких попов/реестров тут: просто best-effort очистка.
        await safe_remove_message(bot, int(chat_id), int(trigger_mid))

    return sent


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


async def send_section_message(
    section: str,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
    user_id: int | None = None,
    edit_current_message: bool = False,
) -> Message | None:
    """Единая точка для "экранов" (sections).
    
    Args:
        edit_current_message: Если True, редактирует текущее сообщение (callback trigger)
                              вместо поиска старого ref секции. Полезно для "назад к списку".
    """
    if callback is None and message is None:
        return None

    bot = callback.message.bot if callback and callback.message else message.bot if message else None
    if bot is None:
        return None

    if user_id is None:
        if callback and callback.from_user:
            user_id = callback.from_user.id
        elif message and message.from_user:
            user_id = message.from_user.id
    if user_id is None:
        return None

    if callback and callback.message and callback.message.chat:
        chat_id = callback.message.chat.id
    elif message and message.chat:
        chat_id = message.chat.id
    else:
        return None

    return await render_section(
        section,
        bot=bot,
        chat_id=int(chat_id),
        user_id=int(user_id),
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML",
        callback=callback,
        mode="edit_trigger",
        edit_current_message=edit_current_message,
    )


async def delete_section_message(
    user_id: int,
    section: str,
    bot,
    *,
    force: bool = False,
    preserve_message_id: int | None = None,
    preserve_message_ids: set[int] | None = None,
) -> bool:
    """Удаляет сообщение секции, если оно зарегистрировано."""
    ref = _get_ref(user_id, section)
    if not ref:
        return False
    preserve_set: set[int] | None = None
    if preserve_message_ids:
        preserve_set = {int(mid) for mid in preserve_message_ids}
    if preserve_message_id is not None:
        preserve_set = (preserve_set or set()) | {int(preserve_message_id)}

    if preserve_set and int(ref.message_id) in preserve_set:
        logger.info("Preserve mid=%s section=%s user_id=%s -> dropping ref", ref.message_id, section, user_id)
        popped = _pop_ref(user_id, section)
        if popped:
            logger.info("Popped ref for section=%s user_id=%s", section, user_id)
        return True

    logger.info(
        "Deleting section message section=%s user_id=%s chat_id=%s mid=%s force=%s preserve_mid=%s",
        section,
        user_id,
        ref.chat_id,
        ref.message_id,
        force,
        preserve_set,
    )

    ok = await safe_remove_message(bot, int(ref.chat_id), int(ref.message_id))
    logger.info(
        "Delete attempt result section=%s user_id=%s chat_id=%s mid=%s ok=%s",
        section,
        user_id,
        ref.chat_id,
        ref.message_id,
        ok,
    )

    if ok:
        popped = _pop_ref(user_id, section)
        if popped:
            logger.info("Popped ref for section=%s user_id=%s", section, user_id)
        return True

    if force:
        popped = _pop_ref(user_id, section)
        if popped:
            logger.info("Force pop ref for section=%s user_id=%s", section, user_id)
        return True

    if not ok:
        _store_mark_stale(user_id, section)

    return False


__all__ = [
    "SECTION_MENU",
    "SECTION_REVIEWS_LIST",
    "SECTION_REVIEW_CARD",
    "SECTION_REVIEW_PROMPT",
    "SECTION_QUESTIONS_LIST",
    "SECTION_QUESTION_CARD",
    "SECTION_QUESTION_PROMPT",
    "SECTION_CHATS_LIST",
    "SECTION_CHAT_HISTORY",
    "SECTION_CHAT_PROMPT",
    "SECTION_FBO",
    "SECTION_FINANCE_TODAY",
    "SectionRef",
    "get_section_ref",
    "get_section_message_id",
    "safe_remove_message",
    "render_section",
    "send_section_message",
    "delete_section_message",
]
