from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

__all__ = ["send_ephemeral_message"]


async def send_ephemeral_message(
    callback: CallbackQuery,
    text: str,
    show_alert: bool = False,
) -> None:
    """Respond to a callback with a short-lived notification."""

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
