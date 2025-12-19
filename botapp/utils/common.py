# botapp/utils.py
from __future__ import annotations

import asyncio
import logging
from typing import Union

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


async def safe_delete_message(bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception:
        logger.exception("Unexpected error while deleting message %s/%s", chat_id, message_id)
        return False


async def _delete_after(bot, chat_id: int, message_id: int, ttl: int) -> None:
    try:
        await asyncio.sleep(max(1, int(ttl)))
        await safe_delete_message(bot, chat_id, message_id)
    except Exception:
        return


async def send_ephemeral_message(
    target: Union[CallbackQuery, Message],
    *,
    text: str,
    ttl: int = 4,
    as_alert: bool = False,
) -> None:
    """
    Унифицированные “временные” сообщения:
      - Если target = CallbackQuery: показываем toast/alert через callback.answer()
      - Если target = Message: отправляем сообщение и удаляем через ttl
    """
    text = (text or "").strip()
    if not text:
        return

    if isinstance(target, CallbackQuery):
        try:
            await target.answer(text, show_alert=bool(as_alert))
            return
        except Exception:
            # fallback: если answer не работает — попробуем отправить сообщением
            try:
                msg = await target.message.answer(text)
                asyncio.create_task(_delete_after(target.message.bot, msg.chat.id, msg.message_id, ttl))
            except Exception:
                return
        return

    try:
        msg = await target.answer(text)
        asyncio.create_task(_delete_after(target.bot, msg.chat.id, msg.message_id, ttl))
    except Exception:
        return


async def safe_edit_text(
    message: Message,
    *,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
) -> bool:
    """
    Безопасное редактирование текста сообщения (иногда нужно вне message_gc).
    """
    try:
        await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=True,
        )
        return True
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return True
        return False
    except (TelegramForbiddenError,):
        return False
    except Exception:
        logger.exception("Unexpected error while editing message %s/%s", message.chat.id, message.message_id)
        return False
