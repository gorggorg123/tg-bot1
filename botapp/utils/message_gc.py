# botapp/message_gc.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Section IDs (единый реестр "экранов")
# -----------------------------------------------------------------------------

SECTION_MENU = "menu"

SECTION_REVIEWS_LIST = "reviews_list"
SECTION_REVIEW_CARD = "review_card"
SECTION_REVIEW_PROMPT = "review_prompt"

SECTION_QUESTIONS_LIST = "questions_list"
SECTION_QUESTION_CARD = "question_card"
SECTION_QUESTION_PROMPT = "question_prompt"

SECTION_CHATS_LIST = "chats_list"
SECTION_CHAT_HISTORY = "chat_history"   # “шапка” чата с кнопками
SECTION_CHAT_PROMPT = "chat_prompt"     # ИИ-черновик / репромпт / редактирование

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
    _REGISTRY[_key(user_id, section)] = SectionRef(chat_id=int(chat_id), message_id=int(message_id), updated_at=_now_utc())


def _pop_ref(user_id: int, section: str) -> SectionRef | None:
    return _REGISTRY.pop(_key(user_id, section), None)


# -----------------------------------------------------------------------------
# Safe Telegram ops
# -----------------------------------------------------------------------------

async def _safe_delete(bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception:
        logger.exception("Unexpected error while deleting message %s/%s", chat_id, message_id)
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
            return None
        return None
    except (TelegramForbiddenError,):
        return None
    except Exception:
        logger.exception("Unexpected error while editing message %s/%s", chat_id, message_id)
        return None


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
    parse_mode = parse_mode or "HTML"

    if mode == "section_only":
        trigger_mid = callback.message.message_id if callback and callback.message else None
        prev = _get_ref(user_id, section)

        if prev and prev.chat_id == chat_id and prev.message_id != trigger_mid:
            edited = await _safe_edit(
                bot,
                chat_id=prev.chat_id,
                message_id=prev.message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            if edited:
                _set_ref(user_id, section, prev.chat_id, prev.message_id)
                return edited

        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("Failed to send section_only message section=%s user_id=%s", section, user_id)
            return None

        if prev and prev.chat_id == chat_id and prev.message_id != trigger_mid:
            await _safe_delete(bot, prev.chat_id, prev.message_id)

        _set_ref(user_id, section, chat_id, sent.message_id)
        return sent

    if callback and callback.message:
        current_mid = callback.message.message_id
        prev = _get_ref(user_id, section)

        if prev and (prev.chat_id != chat_id or prev.message_id != current_mid):
            await _safe_delete(bot, prev.chat_id, prev.message_id)

        edited = await _safe_edit(
            bot,
            chat_id=chat_id,
            message_id=current_mid,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if edited is None:
            try:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.exception(
                    "Failed to send fallback section message section=%s user_id=%s",
                    section,
                    user_id,
                )
                return None

            await _safe_delete(bot, chat_id, current_mid)
            _set_ref(user_id, section, chat_id, sent.message_id)
            return sent

        _set_ref(user_id, section, chat_id, current_mid)
        return edited

    prev = _get_ref(user_id, section)
    if prev and prev.chat_id == chat_id:
        edited = await _safe_edit(
            bot,
            chat_id=prev.chat_id,
            message_id=prev.message_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if edited is None:
            try:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
            except Exception:
                logger.exception("Failed to send section message section=%s user_id=%s", section, user_id)
                return None

            if prev:
                await _safe_delete(bot, prev.chat_id, prev.message_id)

            _set_ref(user_id, section, chat_id, sent.message_id)
            return sent

        _set_ref(user_id, section, prev.chat_id, prev.message_id)
        return edited

    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
    except Exception:
        logger.exception("Failed to send section message section=%s user_id=%s", section, user_id)
        return None

    if prev:
        await _safe_delete(bot, prev.chat_id, prev.message_id)

    _set_ref(user_id, section, chat_id, sent.message_id)
    return sent


# -----------------------------------------------------------------------------
# Public API: send/update one “section message”
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
    """
    Единая точка для “экранов”:
      - если секция уже есть: пытаемся редактировать существующее сообщение
      - если не получается: отправляем новое, а старое (если было) — удаляем
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

    chat_id = None
    if callback and callback.message and callback.message.chat:
        chat_id = callback.message.chat.id
    elif message and message.chat:
        chat_id = message.chat.id
    if chat_id is None:
        return None

    return await render_section(
        section,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
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
    """
    Удаляет сообщение секции, если оно зарегистрировано.
    """
    ref = _get_ref(user_id, section)
    if not ref:
        return False

    if preserve_message_id is not None and int(preserve_message_id) == int(ref.message_id):
        return False

    ok = await _safe_delete(bot, ref.chat_id, ref.message_id)
    _pop_ref(user_id, section)

    if force:
        return True
    return ok


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
    "render_section",
    "send_section_message",
    "delete_section_message",
]
