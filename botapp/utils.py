from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Dict, Tuple

from aiogram import Bot
from aiogram.types import Message

from botapp.message_gc import delete_message_safe

__all__ = ["send_ephemeral_message"]

# Keep per-user tracking of ephemeral notifications (chat_id, message_id, task)
_ephemeral_messages: Dict[int, Tuple[int, int, asyncio.Task]] = {}


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
    """Send a short-lived message and delete it after ``delay`` seconds."""

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
