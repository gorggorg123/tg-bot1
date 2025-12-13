from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

__all__ = ["send_ephemeral_message", "send_ephemeral_from_callback", "safe_edit_text"]


async def send_ephemeral_message(
    target: Bot | CallbackQuery | Message,
    *args,
    text: str | None = None,
    chat_id: int | None = None,
    user_id: int | None = None,
    show_alert: bool = False,
) -> None:
    """Send a short-lived notification without breaking legacy call sites.

    Accepted call styles (for backwards compatibility):
    - send_ephemeral_message(callback, "text")
    - send_ephemeral_message(callback, None, "text")
    - send_ephemeral_message(bot, chat_id, "text")
    - send_ephemeral_message(bot, chat_id, text="text")
    """

    # Backwards compatible positional parsing
    if text is None:
        if len(args) == 1:
            text = args[0]
        elif len(args) >= 2:
            chat_id = chat_id if chat_id is not None else args[0]
            text = args[1]
    if chat_id is None and len(args) >= 1 and text is None:
        chat_id = args[0]

    if text is None:
        raise TypeError("send_ephemeral_message() missing required 'text' argument")

    # Compatibility: allow passing CallbackQuery directly
    if isinstance(target, CallbackQuery):
        await send_ephemeral_from_callback(target, text, show_alert=show_alert)
        return

    # Compatibility: allow passing Message directly
    if isinstance(target, Message):
        await send_ephemeral_from_callback(target, text, show_alert=show_alert)
        return

    if chat_id is None:
        return

    target_chat = user_id or chat_id
    try:
        await target.send_message(target_chat, text)
    except TelegramBadRequest:
        pass


async def safe_edit_text(message: Message, text: str, *, reply_markup=None):
    """Edit a message without crashing on common race conditions.

    * Silently ignores "message is not modified" to avoid noisy warnings.
    * If the message is gone ("message to edit not found"), returns None so caller
      can decide to send a fresh message instead.
    """

    try:
        return await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        lower_exc = str(exc).lower()
        if "message is not modified" in lower_exc:
            return message
        if "message to edit not found" in lower_exc:
            return None
        raise


async def send_ephemeral_from_callback(
    callback: CallbackQuery | Message,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    """Convenience wrapper for callbacks/messages."""

    if isinstance(callback, CallbackQuery):
        try:
            await callback.answer(text, show_alert=show_alert)
        except TelegramBadRequest:
            pass
        return

    if isinstance(callback, Message):
        try:
            await callback.answer(text)
        except TelegramBadRequest:
            pass
