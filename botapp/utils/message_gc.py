# botapp/utils/message_gc.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

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
SECTION_ACCOUNT = "account"

SECTION_WAREHOUSE_MENU = "warehouse_menu"
SECTION_WAREHOUSE_PLAN = "warehouse_plan"
SECTION_WAREHOUSE_PROMPT = "warehouse_prompt"

# -----------------------------------------------------------------------------
# In-memory registry: user_id + section -> (chat_id, message_id)
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class SectionRef:
    chat_id: int
    message_id: int
    updated_at: datetime


_REGISTRY: dict[tuple[int, str], SectionRef] = {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _key(user_id: int, section: str) -> tuple[int, str]:
    return (int(user_id), str(section))


def _get_ref(user_id: int, section: str) -> SectionRef | None:
    return _REGISTRY.get(_key(user_id, section))


def _set_ref(user_id: int, section: str, chat_id: int, message_id: int) -> None:
    _REGISTRY[_key(user_id, section)] = SectionRef(
        chat_id=int(chat_id),
        message_id=int(message_id),
        updated_at=_now_utc(),
    )


def _pop_ref(user_id: int, section: str) -> SectionRef | None:
    return _REGISTRY.pop(_key(user_id, section), None)


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
        # Важно: "message to delete not found" НЕ считаем успехом.
        logger.warning("Failed to delete message %s/%s: %s", chat_id, message_id, exc)
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
        logger.warning("Failed to clear message %s/%s via edit: %s", chat_id, message_id, exc)
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
) -> Message | None:
    """Рендер секции.

    Дизайн:
      - Для каждой секции хранится ref на единственное сообщение.
      - Если есть ref в том же чате — редактируем его.
      - Если редактирование не удалось — отправляем новое и удаляем/зачищаем старое.
      - Для menu стараемся всегда редактировать menu anchor (ref), а не trigger.
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

    # 1) Если у секции уже есть сообщение в этом чате — редактируем его.
    if prev_same_chat:
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

        # edit failed -> send new, remove old
        logger.info(
            "Edit failed for section=%s user_id=%s target_mid=%s, sending new message",
            section,
            user_id,
            target_mid,
        )
        try:
            sent = await bot.send_message(
                chat_id=int(chat_id),
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Failed to send fallback section message section=%s user_id=%s", section, user_id)
            return None

        if sent.message_id != target_mid:
            await safe_remove_message(bot, int(chat_id), target_mid)

        _set_ref(user_id, section, int(chat_id), int(sent.message_id))
        if section == SECTION_MENU:
            logger.info("Menu ref set to mid=%s for user_id=%s", sent.message_id, user_id)
        return sent

    # 2) Нет prev в этом чате.
    #    Для menu: если ref отсутствует, можно попробовать edit trigger, но это рискованно,
    #    поэтому сначала пытаемся edit trigger, а если не вышло — отправляем новое и (опц.) чистим trigger.
    if section == SECTION_MENU and trigger_mid is not None:
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
            logger.info("Menu ref set to mid=%s for user_id=%s", trigger_mid, user_id)
            return None
        if edited:
            _set_ref(user_id, section, int(chat_id), int(trigger_mid))
            logger.info("Menu ref set to mid=%s for user_id=%s", trigger_mid, user_id)
            return edited

        logger.info("Edit failed for menu trigger mid=%s user_id=%s, sending new menu", trigger_mid, user_id)

    # 3) Отправляем новое сообщение секции.
    try:
        sent = await bot.send_message(
            chat_id=int(chat_id),
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Failed to send section message section=%s user_id=%s", section, user_id)
        return None

    # 4) Удаляем/зачищаем предыдущее сообщение секции (если было), кроме trigger (его обычно хочет убрать GC секций).
    if prev:
        if not (trigger_mid is not None and int(prev.message_id) == int(trigger_mid)):
            await safe_remove_message(bot, int(prev.chat_id), int(prev.message_id))

    _set_ref(user_id, section, int(chat_id), int(sent.message_id))
    if section == SECTION_MENU:
        logger.info("Menu ref set to mid=%s for user_id=%s", sent.message_id, user_id)

    # Если мы вынужденно отправили новое меню при callback — можно попытаться убрать trigger, чтобы не оставалось дублей.
    # Но только если trigger не равен новому menu mid.
    if section == SECTION_MENU and trigger_mid is not None and int(trigger_mid) != int(sent.message_id):
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
) -> Message | None:
    """Единая точка для “экранов” (sections)."""
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
    )


async def delete_section_message(
    user_id: int,
    section: str,
    bot,
    *,
    force: bool = False,
    preserve_message_id: int | None = None,
) -> bool:
    """Удаляет сообщение секции, если оно зарегистрировано."""
    ref = _get_ref(user_id, section)
    if not ref:
        return False

    if preserve_message_id is not None and int(preserve_message_id) == int(ref.message_id):
        logger.info("Preserve mid=%s section=%s user_id=%s -> dropping ref", preserve_message_id, section, user_id)
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
        preserve_message_id,
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
    "SECTION_ACCOUNT",
    "SECTION_WAREHOUSE_MENU",
    "SECTION_WAREHOUSE_PLAN",
    "SECTION_WAREHOUSE_PROMPT",
    "SectionRef",
    "get_section_ref",
    "get_section_message_id",
    "safe_remove_message",
    "render_section",
    "send_section_message",
    "delete_section_message",
]
