# botapp/chats_handlers.py
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from botapp.ai_client import extract_cdek_shipment_data, generate_chat_reply
from botapp.ai_memory import ApprovedAnswer, get_approved_memory_store
from botapp.api.cdek_client import CdekAPIError, CdekAuthError
from botapp.sections.cdek.logic import (
    find_pvz_by_address_hint,
    format_confirmation_card,
    get_city_code,
    get_tariff_by_name,
)
from botapp.config import load_cdek_config
from botapp.states import CdekStates
from botapp.sections.chats.logic import (
    PremiumPlusRequired,
    friendly_chat_error,
    get_chat_bubbles_for_ui,
    get_chats_table,
    load_older_messages,
    last_buyer_message,
    last_buyer_message_text,
    last_seen_page,
    NormalizedMessage,
    normalize_thread_messages,
    refresh_chats_list,
    refresh_chat_thread,
    resolve_chat_id,
    _chat_title_from_cache,
    _bubble_text,
    _fmt_time,
    clear_chat_history_cache,
)
from botapp.keyboards import MenuCallbackData, back_home_keyboard
from botapp.menu_handlers import _close_all_sections
from botapp.sections.chats.keyboards import (
    ChatCallbackData,
    chat_header_keyboard,
    chat_ai_draft_keyboard,
    chats_list_keyboard,
)
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
    delete_section_message,
    render_section,
    send_section_message,
)
from botapp.api.ozon_client import OzonAPIError, chat_send_message, download_chat_file
from botapp.utils.storage import (
    ChatAIState,
    clear_chat_ai_state,
    load_chat_ai_state,
    save_chat_ai_state,
)
from botapp.utils import safe_delete_message, send_ephemeral_message

logger = logging.getLogger(__name__)
router = Router()

# (user_id, ozon_chat_id) -> [tg_message_id...]
_CHAT_BUBBLES: Dict[Tuple[int, str], List[int]] = {}
_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv"}


def _is_guid(value: str | None) -> bool:
    return bool(value and _GUID_RE.fullmatch(str(value).strip()))


def _media_ext(*values: str | None) -> str:
    for value in values:
        if not value:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        if "://" in raw:
            raw = urlparse(raw).path or ""
        tail = raw.rsplit("/", 1)[-1]
        tail = tail.split("?", 1)[0].split("#", 1)[0]
        if "." in tail:
            return "." + tail.rsplit(".", 1)[-1].lower()
    return ""


def _detect_media_kind(
    *, url: str | None = None, filename: str | None = None, content_type: str | None = None
) -> str:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct.startswith("image/"):
        return "photo"
    if ct.startswith("video/"):
        return "video"

    ext = _media_ext(filename, url)
    if ext in _IMAGE_EXTS:
        return "photo"
    if ext in _VIDEO_EXTS:
        return "video"
    return "document"


def _media_caption(role_label: str, tm: str | None, kind: str, *, include_time: bool) -> str:
    label = "📷 фото" if kind == "photo" else ("🎬 видео" if kind == "video" else "📎 файл")
    caption = f"{role_label}: {label}"
    if include_time and tm:
        caption += f"\n<i>{tm}</i>"
    return caption


def _extract_guid_from_message(message: Message | None) -> str | None:
    if not message:
        return None
    for candidate in (
        getattr(message, "text", None),
        getattr(message, "html_text", None),
        getattr(message, "caption", None),
        getattr(message, "html_caption", None),
    ):
        if not candidate:
            continue
        found = _GUID_RE.search(str(candidate))
        if found:
            return found.group(0)
    return None


async def _resolve_ozon_chat_id_robust(
    *,
    user_id: int,
    token: str | None = None,
    chat_id: str | None = None,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
) -> str | None:
    """
    Надежно резолвит chat_id:
    1) прямой GUID из callback/chat_id/token
    2) token->chat_id из текущего кэша
    3) принудительное обновление списка чатов и повторный резолв.
    """
    token_value = (token or "").strip()
    chat_id_value = (chat_id or "").strip()

    for candidate in (chat_id_value, token_value):
        if _is_guid(candidate):
            return candidate

    message_guid = _extract_guid_from_message(callback.message if callback else message)
    if _is_guid(message_guid):
        return message_guid

    for candidate in (chat_id_value, token_value):
        resolved = resolve_chat_id(user_id, candidate)
        if _is_guid(resolved):
            return resolved

    # Важный fallback после перезапуска бота:
    # token map может быть пустым, поэтому обновляем список и пробуем снова.
    try:
        await refresh_chats_list(user_id, force=True)
    except Exception:
        logger.debug("Failed to refresh chats for robust chat_id resolve", exc_info=True)

    for candidate in (chat_id_value, token_value):
        resolved = resolve_chat_id(user_id, candidate)
        if _is_guid(resolved):
            return resolved

    return None


async def _send_with_retry(chat_id: str, text: str, attempts: int = 3) -> None:
    """Отправить сообщение в чат с повторными попытками при ошибках.
    
    Args:
        chat_id: ID чата Ozon
        text: Текст сообщения
        attempts: Количество попыток
        
    Raises:
        OzonAPIError: При ошибке отправки после всех попыток
        Exception: При других ошибках
    """
    delay = 1.2
    last_exc: Exception | None = None
    
    logger.debug("_send_with_retry: chat_id=%s attempts=%d text_length=%d", chat_id, attempts, len(text))
    
    for attempt in range(max(1, attempts)):
        try:
            logger.debug("_send_with_retry attempt %d/%d for chat %s", attempt + 1, attempts, chat_id)
            await chat_send_message(chat_id, text)
            logger.info("_send_with_retry: successfully sent message to chat %s on attempt %d", chat_id, attempt + 1)
            return
        except OzonAPIError as exc:
            last_exc = exc
            error_msg = str(exc).lower()
            
            # Некоторые ошибки не требуют повторных попыток
            if any(keyword in error_msg for keyword in [
                "нет прав", "permission denied", "access denied",
                "отклонил", "rejected", "chat not started",
                "expired", "истек", "недоступен"
            ]):
                logger.warning("_send_with_retry: non-retryable error for chat %s: %s", chat_id, exc)
                raise
            
            if attempt >= attempts - 1:
                logger.error("_send_with_retry: all attempts failed for chat %s: %s", chat_id, exc)
                break
            
            logger.warning(
                "_send_with_retry: attempt %d/%d failed for chat %s: %s, retrying in %.2fs",
                attempt + 1,
                attempts,
                chat_id,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay *= 1.6
        except Exception as exc:
            last_exc = exc
            logger.warning("_send_with_retry: unexpected error on attempt %d/%d for chat %s: %s", attempt + 1, attempts, chat_id, exc)
            
            if attempt >= attempts - 1:
                logger.error("_send_with_retry: all attempts failed for chat %s: %s", chat_id, exc)
                break
            
            await asyncio.sleep(delay)
            delay *= 1.6
    
    if last_exc:
        raise last_exc


def _bkey(user_id: int, ozon_chat_id: str) -> Tuple[int, str]:
    return (int(user_id), str(ozon_chat_id).strip())


async def _delete_bubbles_in_chat(bot, chat_id: int, user_id: int, ozon_chat_id: str) -> None:
    key = _bkey(user_id, ozon_chat_id)
    ids = _CHAT_BUBBLES.pop(key, [])
    for mid in ids:
        await safe_delete_message(bot, chat_id, mid)


async def _send_bubbles(
    bot,
    chat_id: int,
    user_id: int,
    ozon_chat_id: str,
    messages: List[NormalizedMessage],
) -> None:
    key = _bkey(user_id, ozon_chat_id)
    _CHAT_BUBBLES[key] = []
    sent_ids: list[int] = []

    async def _track_or_cleanup(mid: int | None) -> bool:
        bucket = _CHAT_BUBBLES.get(key)
        if bucket is None:
            targets = list(sent_ids)
            if mid is not None:
                targets.append(mid)
            if targets:
                logger.info(
                    "Chat bubbles closed early, cleaning up %d orphan messages for key=%s",
                    len(targets),
                    key,
                )
            for tid in targets:
                await safe_delete_message(bot, chat_id, tid)
            return False

        if mid is not None:
            bucket.append(mid)
            sent_ids.append(mid)
        return True

    active = True
    for msg in messages:
        if _CHAT_BUBBLES.get(key) is None:
            await _track_or_cleanup(None)
            break

        bubble_text = _bubble_text(msg)
        if bubble_text.strip():
            sent = await bot.send_message(chat_id, bubble_text, parse_mode="HTML")
            active = await _track_or_cleanup(sent.message_id)
            if not active:
                break

        if msg.media_urls:
            role_label = "👤 Покупатель" if msg.role == "buyer" else ("🏪 Мы" if msg.role == "seller" else "Сообщение")
            tm = _fmt_time(msg.created_at)

            media_preview_cap = 2
            media_preview_sent = 0
            preview_candidates = msg.media_urls[:media_preview_cap]
            deferred_count = max(0, len(msg.media_urls) - media_preview_cap)
            failed_links: list[str] = []

            for media_url in preview_candidates:
                url_kind = _detect_media_kind(url=media_url)

                # Быстрый путь: Telegram сам подтянет файл по URL без локального скачивания.
                # Имеет смысл только для фото/видео; для документов сразу идём в download fallback.
                if url_kind in ("photo", "video"):
                    caption = _media_caption(
                        role_label, tm, url_kind, include_time=(media_preview_sent == 0)
                    )
                    try:
                        if url_kind == "photo":
                            sent = await bot.send_photo(chat_id, photo=media_url, caption=caption, parse_mode="HTML")
                        else:
                            sent = await bot.send_video(chat_id, video=media_url, caption=caption, parse_mode="HTML")
                        active = await _track_or_cleanup(sent.message_id)
                        if not active:
                            break
                        media_preview_sent += 1
                        continue
                    except Exception:
                        logger.debug(
                            "Direct media preview failed for %s, falling back to download",
                            media_url,
                            exc_info=True,
                        )

                # Fallback: локально скачиваем и отправляем как файл.
                try:
                    content, filename, content_type = await asyncio.wait_for(
                        download_chat_file(media_url), timeout=45
                    )
                    media_kind = _detect_media_kind(
                        url=media_url,
                        filename=filename,
                        content_type=content_type,
                    )
                    caption = _media_caption(
                        role_label, tm, media_kind, include_time=(media_preview_sent == 0)
                    )

                    try:
                        if media_kind == "photo":
                            sent = await bot.send_photo(
                                chat_id,
                                photo=BufferedInputFile(content, filename=filename or "photo.jpg"),
                                caption=caption,
                                parse_mode="HTML",
                            )
                        elif media_kind == "video":
                            sent = await bot.send_video(
                                chat_id,
                                video=BufferedInputFile(content, filename=filename or "video.mp4"),
                                caption=caption,
                                parse_mode="HTML",
                            )
                        else:
                            sent = await bot.send_document(
                                chat_id,
                                document=BufferedInputFile(content, filename=filename or "attachment.bin"),
                                caption=caption,
                                parse_mode="HTML",
                            )
                    except Exception:
                        if media_kind == "document":
                            raise
                        # Последний шанс: если фото/видео не принимается Telegram, отправим как файл.
                        sent = await bot.send_document(
                            chat_id,
                            document=BufferedInputFile(content, filename=filename or "attachment.bin"),
                            caption=caption,
                            parse_mode="HTML",
                        )

                    active = await _track_or_cleanup(sent.message_id)
                    if not active:
                        break
                    media_preview_sent += 1
                except Exception:
                    failed_links.append(media_url)
                    logger.warning("Failed to send media preview for %s", media_url, exc_info=True)

            if failed_links and active:
                shown_links = failed_links[:2]
                links_html = "\n".join(
                    f'<a href="{html.escape(url)}">Открыть медиа {idx + 1}</a>'
                    for idx, url in enumerate(shown_links)
                )
                more_failed = max(0, len(failed_links) - len(shown_links))
                media_note = f"{role_label}: 📎 не удалось встроить медиа."
                if tm and media_preview_sent == 0:
                    media_note += f"\n<i>{tm}</i>"
                media_note += f"\n{links_html}"
                if more_failed:
                    media_note += f"\n…и ещё {more_failed} файл(а)."
                sent = await bot.send_message(chat_id, media_note, parse_mode="HTML", disable_web_page_preview=True)
                active = await _track_or_cleanup(sent.message_id)
                if not active:
                    break

            if deferred_count and active:
                media_note = f"{role_label}: 📎 ещё медиа: {deferred_count} шт."
                if tm and media_preview_sent == 0:
                    media_note += f"\n<i>{tm}</i>"
                sent = await bot.send_message(chat_id, media_note, parse_mode="HTML")
                active = await _track_or_cleanup(sent.message_id)
                if not active:
                    break


def _load_ai_state(user_id: int, chat_id: str) -> ChatAIState:
    state = load_chat_ai_state(user_id, chat_id)
    if state:
        return state
    return ChatAIState(chat_id=str(chat_id).strip(), user_id=int(user_id))


def _save_ai_state(state: ChatAIState) -> None:
    save_chat_ai_state(user_id=state.user_id, chat_id=state.chat_id, state=state)


class ChatStates(StatesGroup):
    reprompt = State()
    edit_ai = State()


def _build_ai_context_text(norm_msgs) -> str:
    """Формирует контекст для ИИ из нормализованных сообщений чата.
    
    Явно указывает, что это чат с покупателем на Ozon, а не отзыв или вопрос.
    """
    lines: list[str] = []
    
    # Добавляем явное указание контекста в начало
    lines.append("=== ЧАТ С ПОКУПАТЕЛЕМ НА OZON ===")
    lines.append("BUYER — сообщения покупателя, SELLER — ответы продавца.")
    lines.append("")
    
    for m in norm_msgs:
        who = "BUYER" if m.role == "buyer" else "SELLER"
        txt = (m.text or "").strip().replace("\n", " ")
        suffix = []
        if m.context:
            if m.context.get("posting_number"):
                suffix.append(f"order {m.context['posting_number']}")
            if m.context.get("sku"):
                suffix.append(f"sku {m.context['sku']}")
        meta = f" ({', '.join(suffix)})" if suffix else ""
        if txt:
            lines.append(f"{who}{meta}: {txt}")
    return "\n".join(lines[-80:])


async def _render_ai_draft_message(
    *,
    token: str,
    draft: str,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
    user_id: int,
    mode: str = "edit_trigger",
) -> None:
    bot = callback.message.bot if callback and callback.message else message.bot if message else None
    chat_id = callback.message.chat.id if callback and callback.message else message.chat.id if message else None
    if bot is None or chat_id is None:
        return
    safe_draft = html.escape(draft)
    await render_section(
        SECTION_CHAT_PROMPT,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
        text="<b>ИИ-черновик:</b>\n\n" + safe_draft,
        reply_markup=chat_ai_draft_keyboard(token=token),
        callback=callback,
        mode=mode,
    )


async def _notify_ai_error(
    callback: CallbackQuery | None,
    message: Message | None,
    text: str,
    *,
    callback_answered: bool = False,
) -> None:
    """Показать ошибку: если callback уже отвечен — в новом сообщении, иначе ephemeral."""
    if callback_answered and callback and getattr(callback, "message", None):
        await callback.message.answer(text)
    else:
        await send_ephemeral_message(callback or message, text=text)


async def _generate_and_render_draft(
    *,
    user_id: int,
    token: str,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
    user_prompt: str | None = None,
    render_mode: str = "edit_trigger",
    callback_answered: bool = False,
) -> None:
    ozon_chat_id = await _resolve_ozon_chat_id_robust(
        user_id=user_id,
        token=token,
        callback=callback,
        message=message,
    ) or token
    if not ozon_chat_id:
        await _notify_ai_error(callback, message, "⚠️ Не удалось определить чат.", callback_answered=callback_answered)
        return
    ai_state = _load_ai_state(user_id, ozon_chat_id)

    th = await refresh_chat_thread(user_id=user_id, chat_id=ozon_chat_id, force=True, limit=30)
    norm = normalize_thread_messages(th.raw_messages, customer_only=True, include_seller=True)
    context_text = _build_ai_context_text(norm) or "История пуста. Ответь вежливо и уточни детали заказа при необходимости."

    try:
        logger.info("Generating chat reply for chat %s, context_text length=%s", ozon_chat_id, len(context_text))
        draft = await generate_chat_reply(messages_text=context_text, user_prompt=user_prompt or ai_state.last_user_prompt)
        logger.info("Chat reply generated successfully for chat %s, draft length=%s", ozon_chat_id, len(draft) if draft else 0)
    except Exception as exc:
        logger.exception("Failed to generate chat reply for chat %s: %s", ozon_chat_id, exc)
        await _notify_ai_error(callback, message, f"⚠️ ИИ-ответ не получился: {exc}", callback_answered=callback_answered)
        return

    draft = (draft or "").strip()
    if len(draft) < 2:
        await _notify_ai_error(callback, message, "⚠️ ИИ вернул пустой ответ.", callback_answered=callback_answered)
        return

    ai_state.draft_text = draft
    ai_state.draft_created_at = datetime.now(timezone.utc)
    ai_state.last_user_prompt = user_prompt or ai_state.last_user_prompt
    _save_ai_state(ai_state)

    try:
        await _render_ai_draft_message(
            token=token,
            draft=draft,
            callback=callback,
            message=message,
            user_id=user_id,
            mode=render_mode,
        )
    except Exception as exc:
        logger.exception("Failed to render AI draft for chat %s: %s", ozon_chat_id, exc)
        await _notify_ai_error(callback, message, f"⚠️ Не удалось показать черновик: {exc}", callback_answered=callback_answered)


async def _show_chats_list(user_id: int, page: int, callback: CallbackQuery | None = None, message: Message | None = None, force_refresh: bool = False, edit_current_message: bool = False) -> None:
    items: list[dict] = []
    try:
        text, items, safe_page, total_pages = await get_chats_table(user_id=user_id, page=page, force_refresh=force_refresh)
        markup = chats_list_keyboard(page=safe_page, total_pages=total_pages, items=items)
    except PremiumPlusRequired:
        text = (
            "Чаты через Seller API доступны только при подписке Premium Plus (Ozon).\n"
            "seller-edu.ozon.ru"
        )
        markup = back_home_keyboard()
    except OzonAPIError as exc:
        logger.warning("Chat list unavailable for user %s: %s", user_id, exc)
        text = friendly_chat_error(exc)
        markup = back_home_keyboard()

    if not items:
        text += "\n\nЧаты пустые или нет доступа к методам чатов этим ключом/тарифом. Проверьте права/подписку."

    sent = await send_section_message(
        SECTION_CHATS_LIST,
        text=text,
        reply_markup=markup,
        callback=callback,
        message=message,
        user_id=user_id,
        edit_current_message=edit_current_message,
    )
    if sent:
        await delete_section_message(user_id, SECTION_CHAT_HISTORY, sent.bot, preserve_message_id=sent.message_id)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, sent.bot, force=True)


async def _show_chat_thread(user_id: int, token: str, callback: CallbackQuery | None = None, message: Message | None = None, force_refresh: bool = False, show_only_buyer: bool = True) -> None:
    if not (callback or message):
        return

    bot = callback.message.bot if callback and callback.message else message.bot
    tg_chat_id = callback.message.chat.id if callback and callback.message else message.chat.id

    ozon_chat_id = await _resolve_ozon_chat_id_robust(
        user_id=user_id,
        token=token,
        callback=callback,
        message=message,
    ) or token
    if not ozon_chat_id:
        await send_ephemeral_message(callback or message, text="⚠️ Не удалось открыть чат (нет chat_id).")
        return

    ai_state = _load_ai_state(user_id, ozon_chat_id)
    ai_state.last_opened_at = datetime.now(timezone.utc)

    await _delete_bubbles_in_chat(bot, tg_chat_id, user_id, ozon_chat_id)

    cached_title = _chat_title_from_cache(user_id, ozon_chat_id)
    safe_title = html.escape(cached_title) if cached_title else None
    title_line = f"<b>{safe_title}</b>" if safe_title else "<b>Чат</b>"
    header_text = (
        f"{title_line}\n"
        f"ID: <code>{ozon_chat_id}</code>\n\n"
        "Ниже последние сообщения.\n"
        "Действия: 🤖 ИИ черновик, 🚚 СДЭК из чата, 🔄 обновить."
    )

    header = await send_section_message(
        SECTION_CHAT_HISTORY,
        text=header_text,
        reply_markup=chat_header_keyboard(
            token=ozon_chat_id,
            chat_id=ozon_chat_id,
            page=last_seen_page(user_id),
        ),
        callback=callback,
        message=message,
        user_id=user_id,
        # Режим "один экран": при callback переиспользуем текущий экран.
        edit_current_message=callback is not None,
    )
    if header and header.message_id:
        ai_state.ui_message_id = header.message_id
    _save_ai_state(ai_state)

    try:
        messages = await get_chat_bubbles_for_ui(
            user_id=user_id,
            chat_id=ozon_chat_id,
            force_refresh=force_refresh,
            customer_only=True,
            include_seller=True,
            max_messages=30,
        )
    except OzonAPIError as exc:
        await send_ephemeral_message(callback or message, text=friendly_chat_error(exc))
        return
    except Exception:
        logger.exception("Failed to load chat thread")
        await send_ephemeral_message(
            callback or message, text="⚠️ Чат временно недоступен. Попробуйте обновить позже."
        )
        return

    if not messages:
        messages = [
            NormalizedMessage(role="seller", text="Пока нет текстовых сообщений.", created_at=None)
        ]

    await _send_bubbles(bot, tg_chat_id, user_id, ozon_chat_id, messages)

    if ai_state.draft_text:
        await _render_ai_draft_message(
            token=token,
            draft=ai_state.draft_text,
            callback=callback,
            message=message,
            user_id=user_id,
        )


@router.message(F.text == "/chats")
async def cmd_chats(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    await state.clear()
    await _close_all_sections(
        message.bot,
        user_id,
        preserve_menu=True,
        preserve_message_id=message.message_id,
    )
    await _show_chats_list(user_id=user_id, page=0, message=message, force_refresh=True)


@router.callback_query(MenuCallbackData.filter(F.section == "chats"))
async def open_chats_from_menu(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    logger.info("Opening chats from menu for user_id=%s", user_id)
    await state.clear()
    # Редактируем текущее сообщение (меню) в список чатов, чтобы избежать дублирования
    await _show_chats_list(
        user_id=user_id, 
        page=0, 
        callback=callback, 
        force_refresh=True,
        edit_current_message=True,
    )


@router.callback_query(ChatCallbackData.filter())
async def chats_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    data = ChatCallbackData.unpack(callback.data)

    action = (data.action or "").strip()
    page = int(data.page or 0)
    token = (data.token or "").strip()
    chat_id = (data.chat_id or "").strip()
    
    # Используем chat_id если token пустой (для обратной совместимости)
    if not token and chat_id:
        token = chat_id

    try:
        await callback.answer()
    except Exception:
        pass

    if action in ("noop", ""):
        return

    if action == "refresh":
        await refresh_chats_list(user_id, force=True)
        await _show_chats_list(user_id=user_id, page=page, callback=callback, force_refresh=False)
        return

    if action == "page":
        await _show_chats_list(user_id=user_id, page=page, callback=callback, force_refresh=False)
        return

    if action == "list":
        # token может быть или коротким токеном или полным chat_id
        ozon_chat_id = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            chat_id=chat_id,
            callback=callback,
        ) or token or chat_id
        logger.info("Chats action='list': token=%r, chat_id=%r, resolved=%r, user_id=%s", token, chat_id, ozon_chat_id, user_id)
        if ozon_chat_id:
            await _delete_bubbles_in_chat(callback.message.bot, callback.message.chat.id, user_id, ozon_chat_id)
        await delete_section_message(user_id, SECTION_CHAT_HISTORY, callback.message.bot, force=True)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.message.bot, force=True)
        
        target_page = page if page is not None else last_seen_page(user_id)
        logger.info("Chats: calling _show_chats_list, target_page=%s, edit_current_message=True", target_page)
        await _show_chats_list(user_id=user_id, page=target_page, callback=callback, force_refresh=False, edit_current_message=True)
        logger.info("Chats: _show_chats_list completed")
        return

    if action == "open":
        chat_ref = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            chat_id=chat_id,
            callback=callback,
        ) or chat_id or token
        await _show_chat_thread(
            user_id=user_id,
            token=chat_ref,
            callback=callback,
            force_refresh=False,
            show_only_buyer=True,
        )
        return

    if action == "refresh_thread":
        chat_ref = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            chat_id=chat_id,
            callback=callback,
        ) or chat_id or token
        await _show_chat_thread(
            user_id=user_id,
            token=chat_ref,
            callback=callback,
            force_refresh=True,
            show_only_buyer=True,
        )
        return

    if action == "older":
        ozon_chat_id = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            chat_id=chat_id,
            callback=callback,
        ) or token or chat_id
        try:
            await load_older_messages(user_id=user_id, chat_id=ozon_chat_id, pages=1, limit=30)
        except Exception:
            pass
        await _show_chat_thread(user_id=user_id, token=ozon_chat_id, callback=callback, force_refresh=True, show_only_buyer=True)
        return

    if action == "exit":
        # Выход из чата к списку (устаревший action, но оставляем для совместимости)
        ozon_chat_id = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            chat_id=chat_id,
            callback=callback,
        ) or token or chat_id
        logger.info("Chats action='exit': token=%r, resolved=%r, user_id=%s", token, ozon_chat_id, user_id)
        if ozon_chat_id:
            await _delete_bubbles_in_chat(callback.message.bot, callback.message.chat.id, user_id, ozon_chat_id)
        await delete_section_message(user_id, SECTION_CHAT_HISTORY, callback.message.bot, force=True)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.message.bot, force=True)
        
        target_page = page if page is not None else last_seen_page(user_id)
        await _show_chats_list(user_id=user_id, page=target_page, callback=callback, force_refresh=False, edit_current_message=True)
        return

    if action == "clear":
        ozon_chat_id = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            chat_id=chat_id,
            callback=callback,
        ) or token or chat_id
        await _delete_bubbles_in_chat(callback.message.bot, callback.message.chat.id, user_id, ozon_chat_id)
        clear_chat_ai_state(user_id, ozon_chat_id)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.message.bot, force=True)
        await send_ephemeral_message(callback, text="🧹 Очищено.")
        await _show_chat_thread(user_id=user_id, token=ozon_chat_id, callback=callback, force_refresh=False, show_only_buyer=True)
        return

    if action == "ai":
        try:
            await callback.answer("⏳ Генерирую ответ...", show_alert=False)
        except Exception:
            pass
        await _generate_and_render_draft(
            user_id=user_id, token=token, callback=callback, render_mode="section_only", callback_answered=True
        )
        return

    if action == "set_my_prompt":
        await state.set_state(ChatStates.reprompt)
        await state.update_data(token=token)

        await render_section(
            SECTION_CHAT_PROMPT,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            text=(
                "<b>Свой промт для ИИ</b>\n\n"
                "Отправь текст с пожеланиями к стилю/ответу.\n"
                "Отмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            mode="section_only",
        )
        return

    if action == "ai_my_prompt":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        ai_state = _load_ai_state(user_id, ozon_chat_id)
        user_prompt = (ai_state.last_user_prompt or "").strip()
        if not user_prompt:
            # попросим ввести промт и вернемся
            await state.set_state(ChatStates.reprompt)
            await state.update_data(token=token)
            await render_section(
                SECTION_CHAT_PROMPT,
                bot=callback.message.bot,
                chat_id=callback.message.chat.id,
                user_id=user_id,
                text=(
                    "<b>Свой промт для ИИ</b>\n\n"
                    "Отправь текст с пожеланиями к стилю/ответу.\n"
                    "Отмена: /cancel"
                ),
                reply_markup=None,
                callback=callback,
                mode="section_only",
            )
            return

        await _generate_and_render_draft(
            user_id=user_id,
            token=token,
            callback=callback,
            user_prompt=user_prompt,
            render_mode="section_only",
        )
        return

    if action == "reprompt":
        await state.set_state(ChatStates.reprompt)
        await state.update_data(token=token)

        await render_section(
            SECTION_CHAT_PROMPT,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            text=(
                "<b>Пересобрать промпт для ИИ</b>\n\n"
                "Напиши правила/пожелания (тон, стиль, что обязательно учесть).\n"
                "Отмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            mode="section_only",
        )
        await send_ephemeral_message(
            callback,
            text="✍️ Напиши промт одним сообщением. Отмена: /cancel",
            ttl=6,
        )
        return

    if action == "edit_ai":
        await state.set_state(ChatStates.edit_ai)
        await state.update_data(token=token)

        await render_section(
            SECTION_CHAT_PROMPT,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            text=(
                "<b>Редактирование ИИ-черновика</b>\n\nОтправь новый текст одним сообщением.\nОтмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            mode="section_only",
        )
        return

    if action == "create_cdek":
        # Обертка всего обработчика в try-except для перехвата любых ошибок
        try:
            logger.info("CDEK from chat: Starting handler, user_id=%s, token=%s", user_id, token)
            
            # Проверяем наличие callback.message
            if not callback or not callback.message:
                logger.error("CDEK from chat: callback or callback.message is None")
                try:
                    await send_ephemeral_message(callback, text="❌ Ошибка: callback недоступен")
                except Exception:
                    pass
                return
            
            ozon_chat_id = await _resolve_ozon_chat_id_robust(
                user_id=user_id,
                token=token,
                chat_id=chat_id,
                callback=callback,
            ) or token or chat_id
            logger.info("CDEK from chat: User %s creating shipment from chat %s", user_id, ozon_chat_id)
            logger.info("CDEK from chat: token=%s, resolved chat_id=%s", token, ozon_chat_id)

            if not _is_guid(ozon_chat_id):
                logger.warning("CDEK from chat: token resolved to non-guid chat_id=%s", ozon_chat_id)
                await send_ephemeral_message(
                    callback,
                    text=(
                        "⚠️ Не удалось определить ID чата.\n"
                        "Обновите список чатов и откройте диалог заново."
                    ),
                )
                return

            # CDEK-сценарий использует ту же секцию prompt, что и ИИ-черновик:
            # очищаем AI draft state, чтобы не получать одновременный UI.
            clear_chat_ai_state(user_id, ozon_chat_id)
            
            # Показываем сообщение об обработке
            logger.info("CDEK from chat: Sending processing message...")
            try:
                processing_msg = await callback.message.answer(
                    "🤖 Анализирую переписку и собираю данные для СДЭК...\n"
                    "Это может занять до 10-20 секунд."
                )
                logger.info("CDEK from chat: Processing message sent, mid=%s", processing_msg.message_id if processing_msg else "None")
            except Exception as proc_msg_error:
                logger.error("CDEK from chat: Failed to send processing message: %s", proc_msg_error, exc_info=True)
                try:
                    await send_ephemeral_message(callback, text="❌ Ошибка при отправке сообщения об обработке")
                except Exception:
                    pass
                return
            
            try:
                # Получаем историю чата
                logger.info("CDEK from chat: Fetching chat history for chat_id=%s...", ozon_chat_id)
                messages = await get_chat_bubbles_for_ui(
                    user_id=user_id,
                    chat_id=ozon_chat_id,
                    force_refresh=False,
                    customer_only=True,
                    include_seller=True,
                    max_messages=50,
                )
                logger.info("CDEK from chat: Chat history fetched, messages count=%d", len(messages) if messages else 0)
                
                if not messages:
                    await processing_msg.delete()
                    await send_ephemeral_message(
                        callback,
                        text="⚠️ В чате нет сообщений для анализа. Убедитесь, что есть переписка с клиентом."
                    )
                    return
                
                # Преобразуем сообщения в текст для AI
                conversation_text = _build_ai_context_text(messages)
                logger.info("CDEK from chat: Built conversation text, length=%d", len(conversation_text))
                
                if not conversation_text.strip():
                    logger.warning("CDEK from chat: Empty conversation text")
                    await processing_msg.delete()
                    await send_ephemeral_message(
                        callback,
                        text="⚠️ Не удалось извлечь текст из переписки."
                    )
                    return
                
                # Извлечение данных через AI
                logger.info("CDEK from chat: Starting AI extraction...")
                try:
                    extracted_data = await extract_cdek_shipment_data(conversation_text)
                    logger.info("CDEK from chat: AI extraction completed, data keys: %s", list(extracted_data.keys()) if isinstance(extracted_data, dict) else "not a dict")
                    
                    # Проверяем, что получили словарь
                    if not isinstance(extracted_data, dict):
                        raise ValueError(f"AI вернул не словарь, а {type(extracted_data)}")
                        
                except Exception as ai_error:
                    logger.error("CDEK from chat: AI extraction failed: %s", ai_error, exc_info=True)
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    await send_ephemeral_message(
                        callback,
                        text=f"❌ Ошибка при извлечении данных через AI: {ai_error}\n\nПопробуйте еще раз."
                    )
                    return
                
                # Проверяем наличие ошибки
                if extracted_data.get("error"):
                    logger.warning("CDEK from chat: AI returned error: %s", extracted_data.get("error"))
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    
                    error_text = (
                        f"❌ <b>Ошибка при извлечении данных:</b>\n"
                        f"{extracted_data['error']}\n\n"
                        "Попробуйте еще раз или введите данные вручную."
                    )
                    
                    # Добавляем кнопку "Ввести данные вручную"
                    from botapp.sections.cdek.keyboards import CdekCallbackData
                    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                    
                    error_keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✏️ Ввести данные вручную",
                                    callback_data=CdekCallbackData(action="edit", extra=f"chat:{ozon_chat_id}").pack(),
                                ),
                            ],
                            [
                                InlineKeyboardButton(
                                    text="⬅️ Назад",
                                    callback_data=ChatCallbackData(action="open", chat_id=ozon_chat_id).pack(),
                                )
                            ],
                        ]
                    )
                    
                    try:
                        await send_section_message(
                            SECTION_CHAT_PROMPT,
                            user_id=user_id,
                            text=error_text,
                            reply_markup=error_keyboard,
                            callback=callback,
                            edit_current_message=True,
                        )
                        await callback.answer("❌ Ошибка извлечения", show_alert=False)
                    except Exception as send_err:
                        logger.error("CDEK from chat: Failed to send error message: %s", send_err)
                        await send_ephemeral_message(callback, text="❌ Ошибка при извлечении данных")
                    return

                # Если ИИ нашел адрес/ориентир ПВЗ, но не код - пробуем подобрать код через CDEK deliverypoints.
                if not extracted_data.get("delivery_pvz_code"):
                    pvz_hint = (
                        str(extracted_data.get("delivery_pvz_address") or "").strip()
                        or str(extracted_data.get("recipient_address") or "").strip()
                    )
                    recipient_city = str(extracted_data.get("recipient_city") or "").strip()
                    if pvz_hint and recipient_city:
                        try:
                            city_code = await get_city_code(recipient_city)
                            if city_code:
                                best_pvz = await find_pvz_by_address_hint(
                                    pvz_hint,
                                    city_code=city_code,
                                    city_name=recipient_city,
                                )
                                if best_pvz and best_pvz.get("pvz"):
                                    matched_code = str(best_pvz["pvz"].get("code") or "").strip().upper()
                                    if matched_code:
                                        extracted_data["delivery_pvz_code"] = matched_code
                                        extracted_data["pvz_match_score"] = round(float(best_pvz.get("score") or 0.0), 3)
                                        logger.info(
                                            "CDEK from chat: auto-matched PVZ by address: code=%s city=%s score=%.2f",
                                            matched_code,
                                            recipient_city,
                                            float(best_pvz.get("score") or 0.0),
                                        )
                        except Exception as pvz_match_error:
                            logger.warning("CDEK from chat: failed to auto-match PVZ by address: %s", pvz_match_error)
                
                # Проверяем наличие обязательных полей
                # ВАЖНО: Проверяем реальное наличие данных, а не только missing_fields
                # Вес посылки НЕ является обязательным полем
                critical_missing = []
                if not extracted_data.get("recipient_fio"):
                    critical_missing.append("recipient_fio")
                if not extracted_data.get("recipient_phone"):
                    critical_missing.append("recipient_phone")
                if not extracted_data.get("recipient_city"):
                    critical_missing.append("recipient_city")
                # package.weight_kg - НЕ обязательное поле, убрано из проверки
                
                if critical_missing:
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    
                    # Используем обычное сообщение вместо ephemeral для длинного текста
                    missing_labels = {
                        "recipient_fio": "ФИО получателя",
                        "recipient_phone": "Телефон получателя",
                        "recipient_city": "Город получателя",
                    }
                    missing_text = ", ".join([missing_labels.get(f, f) for f in critical_missing])
                    
                    error_text = (
                        f"⚠️ <b>Не удалось извлечь обязательные данные:</b>\n"
                        f"{missing_text}\n\n"
                        "Убедитесь, что в переписке указаны:\n"
                        "• ФИО получателя\n"
                        "• Телефон получателя\n"
                        "• Город получателя"
                    )
                    
                    # Добавляем кнопку "Ввести данные вручную"
                    from botapp.sections.cdek.keyboards import CdekCallbackData
                    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                    
                    manual_input_keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="✏️ Ввести данные вручную",
                                    callback_data=CdekCallbackData(action="edit", extra=f"chat:{ozon_chat_id}").pack(),
                                ),
                            ],
                            [
                                InlineKeyboardButton(
                                    text="⬅️ Назад",
                                    callback_data=ChatCallbackData(action="open", chat_id=ozon_chat_id).pack(),
                                )
                            ],
                        ]
                    )
                    
                    try:
                        await send_section_message(
                            SECTION_CHAT_PROMPT,
                            user_id=user_id,
                            text=error_text,
                            reply_markup=manual_input_keyboard,
                            callback=callback,
                            edit_current_message=True,
                        )
                        await callback.answer("⚠️ Данные неполные", show_alert=False)
                    except Exception as send_err:
                        logger.error("CDEK from chat: Failed to send error message: %s", send_err)
                        # Fallback на короткое ephemeral сообщение
                        await send_ephemeral_message(
                            callback,
                            text="⚠️ Не удалось извлечь обязательные данные. Проверьте переписку."
                        )
                    return
                
                # Сохраняем данные в состояние FSM
                await state.set_state(CdekStates.confirming_data)
                await state.update_data(
                    extracted_data=extracted_data,
                    chat_token=ozon_chat_id,
                    ozon_chat_id=ozon_chat_id,
                )
                
                # Получаем информацию о тарифе для отображения
                tariff_info = None
                try:
                    config = load_cdek_config()
                    sender_city_code = await get_city_code(config.sender_city)
                    recipient_city_code = await get_city_code(extracted_data.get("recipient_city", ""))
                    
                    if sender_city_code and recipient_city_code and extracted_data.get("package", {}).get("weight_kg"):
                        package = extracted_data["package"]
                        package_data = {
                            "weight": int(float(package["weight_kg"]) * 1000),
                        }
                        if package.get("length_cm"):
                            package_data["length"] = int(package["length_cm"])
                        if package.get("width_cm"):
                            package_data["width"] = int(package["width_cm"])
                        if package.get("height_cm"):
                            package_data["height"] = int(package["height_cm"])
                        
                        tariff_info = await get_tariff_by_name(
                            config.default_tariff_name,
                            sender_city_code,
                            recipient_city_code,
                            [package_data],
                            order_type=config.order_type,
                        )
                except Exception as e:
                    logger.debug("CDEK: Could not get tariff info: %s", e)
                
                # Форматируем карточку подтверждения
                logger.info("CDEK from chat: Formatting confirmation card...")
                logger.debug("CDEK from chat: extracted_data keys: %s", list(extracted_data.keys()))
                logger.debug("CDEK from chat: tariff_info: %s", tariff_info)
                try:
                    confirmation_text = format_confirmation_card(extracted_data, tariff_info)
                    logger.info("CDEK from chat: Confirmation card formatted, length=%d", len(confirmation_text))
                except Exception as format_error:
                    logger.error("CDEK from chat: Failed to format confirmation card: %s", format_error, exc_info=True)
                    try:
                        await processing_msg.delete()
                    except Exception:
                        pass
                    await send_ephemeral_message(
                        callback,
                        text=f"❌ Ошибка при форматировании карточки: {format_error}\n\nПопробуйте еще раз."
                    )
                    return
                
                try:
                    await processing_msg.delete()
                except Exception as del_error:
                    logger.warning("CDEK from chat: Failed to delete processing message: %s", del_error)
                
                # Используем клавиатуру CDEK с дополнительным параметром для идентификации чата
                from botapp.sections.cdek.keyboards import CdekCallbackData
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
                
                confirmation_keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Создать",
                                callback_data=CdekCallbackData(action="confirm", extra=f"chat:{ozon_chat_id}").pack(),
                            ),
                            InlineKeyboardButton(
                                text="✏️ Исправить",
                                callback_data=CdekCallbackData(action="edit", extra=f"chat:{ozon_chat_id}").pack(),
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                text="⬅️ Назад",
                                callback_data=ChatCallbackData(action="open", chat_id=ozon_chat_id).pack(),
                            )
                        ],
                    ]
                )
                
                logger.info("CDEK from chat: Sending confirmation message (text length=%d)...", len(confirmation_text))
                logger.debug("CDEK from chat: confirmation_text preview: %s", confirmation_text[:200])
                logger.debug("CDEK from chat: callback.message.chat.id=%s, user_id=%s", callback.message.chat.id if callback.message else None, user_id)
                
                try:
                    result_msg = await send_section_message(
                        SECTION_CHAT_PROMPT,
                        user_id=user_id,
                        text=confirmation_text,
                        reply_markup=confirmation_keyboard,
                        callback=callback,
                        edit_current_message=True,
                    )
                    logger.info("CDEK from chat: send_section_message returned: %s", result_msg.message_id if result_msg else "None")
                    
                    if result_msg is None:
                        logger.warning("CDEK from chat: send_section_message returned None, trying alternative method...")
                        # Альтернативный способ: отправляем новое сообщение
                        if callback and callback.message:
                            try:
                                alt_msg = await callback.message.answer(
                                    text=confirmation_text,
                                    reply_markup=confirmation_keyboard,
                                    parse_mode="HTML",
                                )
                                logger.info("CDEK from chat: Confirmation message sent via alternative method, mid=%s", alt_msg.message_id if alt_msg else "None")
                                result_msg = alt_msg
                            except Exception as alt_error:
                                logger.error("CDEK from chat: Alternative send method also failed: %s", alt_error, exc_info=True)
                                raise
                        else:
                            logger.error("CDEK from chat: Cannot use alternative method - callback.message is None")
                            raise ValueError("send_section_message returned None and callback.message is unavailable")
                    else:
                        logger.info("CDEK from chat: Confirmation message sent successfully, mid=%s", result_msg.message_id if result_msg else "None")
                        
                except Exception as send_error:
                    logger.error("CDEK from chat: Failed to send confirmation message: %s", send_error, exc_info=True)
                    try:
                        await send_ephemeral_message(
                            callback,
                            text=f"❌ Ошибка при отправке карточки подтверждения: {send_error}"
                        )
                    except Exception as e2:
                        logger.error("CDEK from chat: Also failed to send error message: %s", e2)
                    return
                
                # Отвечаем на callback только после успешной отправки
                try:
                    await callback.answer("Данные извлечены из переписки")
                    logger.info("CDEK from chat: Callback answered successfully")
                except Exception as answer_error:
                    logger.warning("CDEK from chat: Failed to answer callback: %s", answer_error)
                
            except OzonAPIError as exc:
                logger.error("CDEK from chat: Failed to get chat history: %s", exc, exc_info=True)
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                await send_ephemeral_message(callback, text=f"⚠️ Ошибка получения истории чата: {exc}")
            except Exception as e:
                logger.error("CDEK from chat: Unexpected error in create_cdek handler: %s", e, exc_info=True)
                try:
                    await processing_msg.delete()
                except Exception:
                    pass
                try:
                    await send_ephemeral_message(callback, text=f"❌ Ошибка при обработке: {e}")
                except Exception as e2:
                    logger.error("CDEK from chat: Failed to send error message: %s", e2)
        except Exception as outer_error:
            # Перехватываем любые ошибки, которые могли произойти до внутреннего try-except
            logger.error("CDEK from chat: CRITICAL error in create_cdek handler (outer catch): %s", outer_error, exc_info=True)
            try:
                await send_ephemeral_message(callback, text=f"❌ Критическая ошибка: {outer_error}")
            except Exception:
                pass
        return

    if action == "send_ai":
        ozon_chat_id = await _resolve_ozon_chat_id_robust(
            user_id=user_id,
            token=token,
            callback=callback,
        ) or token
        ai_state = _load_ai_state(user_id, ozon_chat_id)
        draft = (ai_state.draft_text or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ Нет ИИ-черновика. Сначала нажми «ИИ-ответ».")
            return

        logger.info("Attempting to send AI draft to chat %s (user_id=%d, draft_length=%d)", ozon_chat_id, user_id, len(draft))
        
        try:
            await _send_with_retry(ozon_chat_id, draft)
            logger.info("Successfully sent message to chat %s", ozon_chat_id)
        except OzonAPIError as exc:
            error_msg = str(exc).lower()
            logger.error("Failed to send message to chat %s: %s", ozon_chat_id, exc, exc_info=True)
            
            hint = ""
            if "нет прав" in error_msg or "permission" in error_msg:
                hint = " Проверьте настройки OZON_WRITE_* в .env"
            elif "отклонил" in error_msg or "rejected" in error_msg:
                hint = " Обновите чат или дождитесь сообщения от покупателя, если нельзя писать первым."
            elif "expired" in error_msg or "истек" in error_msg:
                hint = " Чат истёк или недоступен. Обновите список чатов."
            elif "not started" in error_msg or "не начат" in error_msg:
                hint = " Чат не активирован. Дождитесь первого сообщения от покупателя."
            else:
                hint = " Проверьте логи для деталей."
            
            await send_ephemeral_message(
                callback,
                text=f"⚠️ Ozon отклонил отправку: {exc}.{hint}",
            )
            return
        except Exception as e:
            logger.exception("Unexpected error sending chat message to %s: %s", ozon_chat_id, e)
            await send_ephemeral_message(
                callback, 
                text=f"⚠️ Не удалось отправить сообщение: {e}. Попробуйте позже."
            )
            return

        try:
            buyer_msg = last_buyer_message(user_id, ozon_chat_id)
            buyer_text = buyer_msg.text if buyer_msg else ""
            rec = ApprovedAnswer.now_iso(
                kind="chat",
                ozon_entity_id=str(ozon_chat_id),
                input_text=buyer_text,
                answer_text=draft,
                product_id=(buyer_msg.context.get("sku") if buyer_msg and buyer_msg.context else None),
                product_name=None,
                rating=None,
                meta={
                    "answered_via": "ai",
                    "posting_number": buyer_msg.context.get("posting_number") if buyer_msg and buyer_msg.context else None,
                },
            )
            get_approved_memory_store().add_approved_answer(rec)
        except Exception:
            logger.exception("Failed to persist chat reply to memory")

        clear_chat_ai_state(user_id, ozon_chat_id)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.message.bot, force=True)
        await send_ephemeral_message(callback, text="✅ Сообщение отправлено в чат Ozon.")
        
        # Очищаем кеш истории чата, чтобы получить свежие данные с новым сообщением
        logger.info("Clearing chat history cache for chat %s after message send", ozon_chat_id)
        await clear_chat_history_cache(ozon_chat_id)
        
        # Небольшая задержка, чтобы Ozon API успел обработать сообщение и вернуть его в истории
        logger.info("Waiting 1.5s for Ozon API to process sent message before refreshing chat thread")
        await asyncio.sleep(1.5)
        
        # Обновляем диалог с force_refresh=True, чтобы получить новое сообщение
        logger.info("Refreshing chat thread after message send: chat_id=%s", ozon_chat_id)
        await _show_chat_thread(user_id=user_id, token=token, callback=callback, force_refresh=True, show_only_buyer=True)
        return

    await send_ephemeral_message(callback, text=f"⚠️ Неизвестное действие: {action}")


@router.message(F.text == "/cancel")
async def cancel_chat_fsm(message: Message, state: FSMContext) -> None:
    st = await state.get_state()
    if st not in (ChatStates.reprompt.state, ChatStates.edit_ai.state):
        return
    await state.clear()
    await send_ephemeral_message(message, text="Ок, отменил.", ttl=3)


@router.message(ChatStates.reprompt)
async def chat_reprompt_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()

    token = (payload.get("token") or "").strip()
    ozon_chat_id = await _resolve_ozon_chat_id_robust(
        user_id=user_id,
        token=token,
        message=message,
    ) or token

    user_prompt = (message.text or "").strip()
    if len(user_prompt) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    await _generate_and_render_draft(
        user_id=user_id,
        token=token,
        message=message,
        user_prompt=user_prompt,
    )

    await state.clear()


@router.message(ChatStates.edit_ai)
async def chat_edit_ai_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()

    token = (payload.get("token") or "").strip()
    ozon_chat_id = await _resolve_ozon_chat_id_robust(
        user_id=user_id,
        token=token,
        message=message,
    ) or token

    txt = (message.text or "").strip()
    if len(txt) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    ai_state = _load_ai_state(user_id, ozon_chat_id)
    ai_state.draft_text = txt
    ai_state.draft_created_at = datetime.now(timezone.utc)
    ai_state.last_user_prompt = ai_state.last_user_prompt or ""
    _save_ai_state(ai_state)

    await _render_ai_draft_message(
        token=token,
        draft=txt,
        message=message,
        user_id=user_id,
    )
    await safe_delete_message(message.bot, message.chat.id, message.message_id)
    await state.clear()
