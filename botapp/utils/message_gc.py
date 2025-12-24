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

# --- СЕКЦИИ (Константы) ---
SECTION_MENU = "menu"
SECTION_REVIEWS_LIST = "reviews_list"
SECTION_REVIEW_CARD = "review_card"
SECTION_REVIEW_PROMPT = "review_prompt"
SECTION_QUESTIONS_LIST = "questions_list"
SECTION_QUESTION_CARD = "question_card"
SECTION_QUESTION_PROMPT = "question_prompt"
SECTION_CHATS_LIST = "chats_list"
SECTION_CHAT_HISTORY = "chat_history"
SECTION_CHAT_PROMPT = "chat_prompt"
SECTION_FBO = "fbo"
SECTION_FINANCE_TODAY = "finance_today"
SECTION_ACCOUNT = "account"

# --- ВОТ ЭТИХ КОНСТАНТ НЕ ХВАТАЛО ---
SECTION_WAREHOUSE_MENU = "warehouse_menu"
SECTION_WAREHOUSE_PLAN = "warehouse_plan"
SECTION_WAREHOUSE_PROMPT = "warehouse_prompt"
# ------------------------------------

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

# --- БЕЗОПАСНЫЕ МЕТОДЫ TG ---

async def _safe_delete(bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception:
        return False

async def _safe_edit(bot, chat_id: int, message_id: int, text: str, reply_markup, parse_mode="HTML"):
    try:
        res = await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text,
            reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=True
        )
        if isinstance(res, Message): return res
        return None
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower(): return NOT_MODIFIED
        return None
    except Exception:
        return None

async def safe_remove_message(bot, chat_id: int, message_id: int) -> bool:
    return await _safe_delete(bot, chat_id, message_id)

# --- ГЛАВНАЯ ЛОГИКА ---

async def render_section(
    section: str, *, bot, chat_id: int, user_id: int, text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
    callback: CallbackQuery | None = None,
    mode: str = "edit_trigger",
) -> Message | None:
    
    parse_mode = parse_mode or "HTML"
    trigger_mid = int(callback.message.message_id) if (callback and callback.message) else None
    
    prev = _get_ref(user_id, section)
    target_mid: int | None = None

    if prev and prev.chat_id == int(chat_id):
        target_mid = int(prev.message_id)
    elif trigger_mid is not None:
        target_mid = int(trigger_mid)

    if target_mid:
        edited = await _safe_edit(bot, chat_id, target_mid, text, reply_markup, parse_mode)
        if edited is NOT_MODIFIED:
            _set_ref(user_id, section, int(chat_id), target_mid)
            return None
        if edited:
            _set_ref(user_id, section, int(chat_id), target_mid)
            return edited
        
        await safe_remove_message(bot, chat_id, target_mid)

    try:
        sent = await bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup,
            parse_mode=parse_mode, disable_web_page_preview=True
        )
    except Exception:
        logger.exception(f"Send failed {section}")
        return None

    _set_ref(user_id, section, chat_id, sent.message_id)

    if trigger_mid and trigger_mid != sent.message_id:
         if target_mid != trigger_mid: 
            await safe_remove_message(bot, chat_id, trigger_mid)

    return sent

async def delete_section_message(user_id: int, section: str, bot, force=False, preserve_message_id=None) -> bool:
    ref = _get_ref(user_id, section)
    if not ref: return False

    if preserve_message_id and int(preserve_message_id) == int(ref.message_id):
        _pop_ref(user_id, section)
        return True

    popped = _pop_ref(user_id, section)
    if not popped: return False

    return await safe_remove_message(bot, int(popped.chat_id), int(popped.message_id))