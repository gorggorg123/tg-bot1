"""Helpers to keep only one active screen message per section for each user."""
from __future__ import annotations

import logging
from contextlib import suppress
from typing import Dict, Tuple

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

SECTION_MENU = "menu"
SECTION_REVIEWS_LIST = "reviews_list"
SECTION_REVIEW_CARD = "review_card"
SECTION_QUESTIONS_LIST = "questions_list"
SECTION_QUESTION_CARD = "question_card"
SECTION_FBO = "fbo"
SECTION_FINANCE_TODAY = "finance_today"
SECTION_ACCOUNT = "account"
SECTION_QUESTIONS_LIST = "questions_list"
SECTION_QUESTION_CARD = "question_card"

_section_messages: Dict[int, Dict[str, Tuple[int, int]]] = {}


async def delete_message_safe(bot: Bot, chat_id: int, message_id: int) -> None:
    """Delete a Telegram message, swallowing benign errors."""

    try:
        await bot.delete_message(chat_id, message_id)
        logger.info("Deleted message %s for chat %s", message_id, chat_id)
    except TelegramBadRequest as exc:
        # Message already gone or too old – this is fine for GC purposes.
        logger.debug("Skip delete message %s in chat %s: %s", message_id, chat_id, exc)
    except Exception:
        logger.exception("Failed to delete message %s in chat %s", message_id, chat_id)


def _remember_message(user_id: int, section: str, chat_id: int, message_id: int) -> None:
    section_state = _section_messages.setdefault(user_id, {})
    section_state[section] = (chat_id, message_id)


def _pop_section(user_id: int, section: str) -> Tuple[int, int] | None:
    return _section_messages.get(user_id, {}).pop(section, None)


async def delete_section_message(
    user_id: int, section: str, bot: Bot, preserve_message_id: int | None = None
) -> None:
    """Remove stored message for section if it exists.

    If ``preserve_message_id`` equals the stored message id, we only forget the
    section without deleting the Telegram message (useful when the message was
    reused via ``edit_text`` for another section).
    """

    stored = _pop_section(user_id, section)
    if stored:
        chat_id, message_id = stored
        if preserve_message_id is not None and preserve_message_id == message_id:
            logger.debug(
                "Skip deleting section '%s' message %s for user %s (preserved)",
                section,
                message_id,
                user_id,
            )
            return

        logger.info(
            "Deleting previous section '%s' message %s for user %s", section, message_id, user_id
        )
        await delete_message_safe(bot, chat_id, message_id)


def _resolve_context(
    message: Message | None,
    callback: CallbackQuery | None,
    bot: Bot | None,
    chat_id: int | None,
    user_id: int | None,
) -> tuple[Bot, int, int]:
    active_bot = bot
    active_chat = chat_id
    active_user = user_id

    if callback:
        active_bot = active_bot or (callback.message.bot if callback.message else callback.bot)
        active_chat = active_chat or (callback.message.chat.id if callback.message else None)
        active_user = active_user or callback.from_user.id

    if message:
        active_bot = active_bot or message.bot
        active_chat = active_chat or message.chat.id
        active_user = active_user or message.from_user.id

    if not active_bot or active_chat is None or active_user is None:
        raise ValueError("Cannot resolve bot/chat/user context for section message")

    return active_bot, active_chat, active_user


async def send_section_message(
    section: str,
    *,
    text: str,
    reply_markup=None,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
    user_id: int | None = None,
    **kwargs,
) -> Message:
    """Send or edit a section screen, cleaning previous one for the user."""

    active_bot, active_chat, active_user = _resolve_context(message, callback, bot, chat_id, user_id)
    stored = _section_messages.get(active_user, {}).get(section)

    if stored and stored[0] == active_chat:
        try:
            edited = await active_bot.edit_message_text(
                text=text,
                chat_id=stored[0],
                message_id=stored[1],
                reply_markup=reply_markup,
                **kwargs,
            )
            _remember_message(active_user, section, stored[0], edited.message_id)
            return edited
        except TelegramBadRequest as exc:
            logger.info(
                "Cannot edit previous section '%s' message %s for user %s: %s",
                section,
                stored[1],
                active_user,
                exc,
            )
            await delete_message_safe(active_bot, stored[0], stored[1])
        except Exception:
            logger.exception(
                "Failed to edit previous section '%s' message %s for user %s",
                section,
                stored[1],
                active_user,
            )
            await delete_message_safe(active_bot, stored[0], stored[1])

    # If callback message exists, try to reuse it before sending a brand new one
    if callback and callback.message and callback.message.chat.id == active_chat:
        try:
            edited = await callback.message.edit_text(text, reply_markup=reply_markup, **kwargs)
            _remember_message(active_user, section, active_chat, edited.message_id)
            # Remove stale stored message if it differs
            if stored and stored[1] != edited.message_id:
                with suppress(Exception):
                    await delete_message_safe(active_bot, stored[0], stored[1])
            return edited
        except TelegramBadRequest as exc:
            logger.info(
                "Callback message edit failed for section '%s' in chat %s: %s",
                section,
                active_chat,
                exc,
            )
        except Exception:
            logger.exception("Failed to edit callback message for section '%s'", section)

    if stored:
        await delete_message_safe(active_bot, stored[0], stored[1])

    sent = await active_bot.send_message(active_chat, text, reply_markup=reply_markup, **kwargs)
    _remember_message(active_user, section, active_chat, sent.message_id)
    return sent


__all__ = [
    "SECTION_MENU",
    "SECTION_REVIEWS_LIST",
    "SECTION_REVIEW_CARD",
    "SECTION_QUESTIONS_LIST",
    "SECTION_QUESTION_CARD",
    "SECTION_FBO",
    "SECTION_FINANCE_TODAY",
    "SECTION_ACCOUNT",
    "SECTION_QUESTIONS_LIST",
    "SECTION_QUESTION_CARD",
    "delete_message_safe",
    "delete_section_message",
    "send_section_message",
]
