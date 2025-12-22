# botapp/chats_handlers.py
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Tuple

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from botapp.ai_memory import ApprovedAnswer, get_approved_memory_store
from botapp.api.ai_client import generate_chat_reply
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
    refresh_chat_thread,
    resolve_chat_id,
    _bubble_text,
    _fmt_time,
)
from botapp.keyboards import MenuCallbackData, back_home_keyboard
from botapp.sections.chats.keyboards import (
    ChatCallbackData,
    chat_header_keyboard,
    chat_ai_draft_keyboard,
    chats_list_keyboard,
)
from botapp.utils.message_gc import (
    SECTION_ACCOUNT,
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
    SECTION_WAREHOUSE_MENU,
    SECTION_WAREHOUSE_PLAN,
    SECTION_WAREHOUSE_PROMPT,
    delete_section_message,
    send_section_message,
)
from botapp.api.ozon_client import OzonAPIError, chat_send_message, download_chat_file
from botapp.utils.storage import get_chat_ai_state, save_chat_ai_state
from botapp.utils import safe_delete_message, send_ephemeral_message

logger = logging.getLogger(__name__)
router = Router()

# (user_id, ozon_chat_id) -> [tg_message_id...]
_CHAT_BUBBLES: Dict[Tuple[int, str], List[int]] = {}


async def _send_with_retry(chat_id: str, text: str, attempts: int = 3) -> None:
    delay = 1.2
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            await chat_send_message(chat_id, text)
            return
        except OzonAPIError as exc:
            last_exc = exc
            if attempt >= attempts - 1:
                break
            await asyncio.sleep(delay)
            delay *= 1.6
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts - 1:
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

    for msg in messages:
        bubble_text = _bubble_text(msg)
        if bubble_text.strip():
            sent = await bot.send_message(chat_id, bubble_text, parse_mode="HTML")
            _CHAT_BUBBLES[key].append(sent.message_id)

        if not msg.media_urls:
            continue

        role_label = "👤 Покупатель" if msg.role == "buyer" else ("🏪 Мы" if msg.role == "seller" else "Сообщение")
        tm = _fmt_time(msg.created_at)
        caption = f"{role_label}: фото"
        if tm:
            caption += f"\n<i>{tm}</i>"

        for url in msg.media_urls:
            try:
                content, filename, _content_type = await download_chat_file(url)
                fname = filename or url.rsplit("/", 1)[-1] or "photo.jpg"
                photo = BufferedInputFile(content, filename=fname)
                sent_photo = await bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML")
                _CHAT_BUBBLES[key].append(sent_photo.message_id)
            except Exception:
                logger.exception("Failed to send chat photo from %s", url)
                fallback = f"{role_label}: {url}"
                sent = await bot.send_message(chat_id, fallback)
                _CHAT_BUBBLES[key].append(sent.message_id)


class ChatStates(StatesGroup):
    reprompt = State()
    edit_ai = State()


def _build_ai_context_text(norm_msgs) -> str:
    lines: list[str] = []
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


async def _clear_other_sections(bot, user_id: int, preserve_message_id: int | None = None) -> None:
    for section in (
        SECTION_FBO,
        SECTION_FINANCE_TODAY,
        SECTION_ACCOUNT,
        SECTION_REVIEWS_LIST,
        SECTION_REVIEW_CARD,
        SECTION_REVIEW_PROMPT,
        SECTION_QUESTIONS_LIST,
        SECTION_QUESTION_CARD,
        SECTION_QUESTION_PROMPT,
        SECTION_WAREHOUSE_MENU,
        SECTION_WAREHOUSE_PLAN,
        SECTION_WAREHOUSE_PROMPT,
    ):
        await delete_section_message(
            user_id,
            section,
            bot,
            force=True,
            preserve_message_id=preserve_message_id,
        )


async def _show_chats_list(user_id: int, page: int, callback: CallbackQuery | None = None, message: Message | None = None, force_refresh: bool = False) -> None:
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
    )
    if sent:
        await delete_section_message(user_id, SECTION_CHAT_HISTORY, sent.bot, preserve_message_id=sent.message_id)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, sent.bot, force=True)


async def _show_chat_thread(user_id: int, token: str, callback: CallbackQuery | None = None, message: Message | None = None, force_refresh: bool = False, show_only_buyer: bool = True) -> None:
    if not (callback or message):
        return

    bot = callback.message.bot if callback and callback.message else message.bot
    tg_chat_id = callback.message.chat.id if callback and callback.message else message.chat.id

    ozon_chat_id = resolve_chat_id(user_id, token) or token
    if not ozon_chat_id:
        await send_ephemeral_message(callback or message, text="⚠️ Не удалось открыть чат (нет chat_id).")
        return

    await _delete_bubbles_in_chat(bot, tg_chat_id, user_id, ozon_chat_id)

    await send_section_message(
        SECTION_CHAT_HISTORY,
        text=f"<b>Чат</b>\nID: <code>{ozon_chat_id}</code>\n\nНиже — последние сообщения.",
        reply_markup=chat_header_keyboard(token=token, page=last_seen_page(user_id)),
        callback=callback,
        message=message,
        user_id=user_id,
    )

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


@router.message(F.text == "/chats")
async def cmd_chats(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    await state.clear()
    await _clear_other_sections(message.bot, user_id, preserve_message_id=message.message_id)
    await _show_chats_list(user_id=user_id, page=0, message=message, force_refresh=True)


@router.callback_query(MenuCallbackData.filter(F.section == "chats"))
async def open_chats_from_menu(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    await state.clear()
    preserve_mid = callback.message.message_id if callback.message else None
    await _clear_other_sections(
        callback.message.bot, user_id, preserve_message_id=preserve_mid
    )
    await _show_chats_list(user_id=user_id, page=0, callback=callback, force_refresh=True)


@router.callback_query(ChatCallbackData.filter())
async def chats_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    data = ChatCallbackData.unpack(callback.data)

    action = (data.action or "").strip()
    page = int(data.page or 0)
    token = (data.token or "").strip()

    try:
        await callback.answer()
    except Exception:
        pass

    if action in ("noop", ""):
        return

    if action in ("page", "refresh"):
        await _show_chats_list(user_id=user_id, page=page, callback=callback, force_refresh=(action == "refresh"))
        return

    if action == "list":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        if ozon_chat_id:
            await _delete_bubbles_in_chat(callback.message.bot, callback.message.chat.id, user_id, ozon_chat_id)
        await delete_section_message(user_id, SECTION_CHAT_HISTORY, callback.message.bot, force=True)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.message.bot, force=True)
        target_page = page if page is not None else last_seen_page(user_id)
        await _show_chats_list(user_id=user_id, page=target_page, callback=callback, force_refresh=False)
        return

    if action == "open":
        await _show_chat_thread(user_id=user_id, token=token, callback=callback, force_refresh=True, show_only_buyer=True)
        return

    if action == "refresh_thread":
        await _show_chat_thread(user_id=user_id, token=token, callback=callback, force_refresh=True, show_only_buyer=True)
        return

    if action == "older":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        try:
            await load_older_messages(user_id=user_id, chat_id=ozon_chat_id, pages=1, limit=30)
        except Exception:
            pass
        await _show_chat_thread(user_id=user_id, token=token, callback=callback, force_refresh=True, show_only_buyer=True)
        return

    if action == "exit":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        await _delete_bubbles_in_chat(callback.message.bot, callback.message.chat.id, user_id, ozon_chat_id)
        await delete_section_message(user_id, SECTION_CHAT_HISTORY, callback.message.bot, force=True)
        await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.message.bot, force=True)
        target_page = page if page is not None else last_seen_page(user_id)
        await _show_chats_list(user_id=user_id, page=target_page, callback=callback, force_refresh=False)
        return

    if action == "clear":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        await _delete_bubbles_in_chat(callback.message.bot, callback.message.chat.id, user_id, ozon_chat_id)
        save_chat_ai_state(chat_id=ozon_chat_id, ai_draft="", user_prompt="", meta={"cleared": True})
        await send_ephemeral_message(callback, text="🧹 Очищено.")
        await _show_chat_thread(user_id=user_id, token=token, callback=callback, force_refresh=False, show_only_buyer=True)
        return

    if action == "ai":
        ozon_chat_id = resolve_chat_id(user_id, token) or token

        th = await refresh_chat_thread(user_id=user_id, chat_id=ozon_chat_id, force=True, limit=30)
        norm = normalize_thread_messages(th.raw_messages, customer_only=True, include_seller=True)
        context_text = _build_ai_context_text(norm) or "История пуста. Ответь вежливо и уточни детали заказа при необходимости."

        st = get_chat_ai_state(ozon_chat_id) or {}
        user_prompt = (st.get("user_prompt") or "").strip() or None

        try:
            draft = await generate_chat_reply(messages_text=context_text, user_prompt=user_prompt)
        except Exception as exc:
            await send_ephemeral_message(callback, text=f"⚠️ ИИ-ответ не получился: {exc}")
            return

        draft = (draft or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ ИИ вернул пустой ответ.")
            return

        save_chat_ai_state(chat_id=ozon_chat_id, ai_draft=draft, user_prompt=user_prompt or "", meta={"len": len(draft)})

        await send_section_message(
            SECTION_CHAT_PROMPT,
            text="<b>ИИ-черновик:</b>\n\n" + draft,
            reply_markup=chat_ai_draft_keyboard(token=token),
            callback=callback,
            user_id=user_id,
        )
        return

    if action == "set_my_prompt":
        await state.set_state(ChatStates.reprompt)
        await state.update_data(token=token)

        await send_section_message(
            SECTION_CHAT_PROMPT,
            text=(
                "<b>Свой промт для ИИ</b>\n\n"
                "Отправь текст с пожеланиями к стилю/ответу.\n"
                "Отмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            user_id=user_id,
        )
        return

    if action == "ai_my_prompt":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        st = get_chat_ai_state(ozon_chat_id) or {}
        user_prompt = (st.get("user_prompt") or "").strip()
        if not user_prompt:
            # попросим ввести промт и вернемся
            await state.set_state(ChatStates.reprompt)
            await state.update_data(token=token)
            await send_section_message(
                SECTION_CHAT_PROMPT,
                text=(
                    "<b>Свой промт для ИИ</b>\n\n"
                    "Отправь текст с пожеланиями к стилю/ответу.\n"
                    "Отмена: /cancel"
                ),
                reply_markup=None,
                callback=callback,
                user_id=user_id,
            )
            return

        th = await refresh_chat_thread(user_id=user_id, chat_id=ozon_chat_id, force=True, limit=30)
        norm = normalize_thread_messages(th.raw_messages, customer_only=True, include_seller=True)
        context_text = _build_ai_context_text(norm) or "История пуста. Ответь вежливо и уточни детали заказа при необходимости."

        try:
            draft = await generate_chat_reply(messages_text=context_text, user_prompt=user_prompt)
        except Exception as exc:
            await send_ephemeral_message(callback, text=f"⚠️ ИИ-ответ не получился: {exc}")
            return

        draft = (draft or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ ИИ вернул пустой ответ.")
            return

        save_chat_ai_state(chat_id=ozon_chat_id, ai_draft=draft, user_prompt=user_prompt, meta={"len": len(draft), "custom": True})

        await send_section_message(
            SECTION_CHAT_PROMPT,
            text="<b>ИИ-черновик:</b>\n\n" + draft,
            reply_markup=chat_ai_draft_keyboard(token=token),
            callback=callback,
            user_id=user_id,
        )
        return

    if action == "reprompt":
        await state.set_state(ChatStates.reprompt)
        await state.update_data(token=token)

        await send_section_message(
            SECTION_CHAT_PROMPT,
            text=(
                "<b>Пересобрать промпт для ИИ</b>\n\n"
                "Напиши правила/пожелания (тон, стиль, что обязательно учесть).\n"
                "Отмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            user_id=user_id,
        )
        return

    if action == "edit_ai":
        await state.set_state(ChatStates.edit_ai)
        await state.update_data(token=token)

        await send_section_message(
            SECTION_CHAT_PROMPT,
            text="<b>Редактирование ИИ-черновика</b>\n\nОтправь новый текст одним сообщением.\nОтмена: /cancel",
            reply_markup=None,
            callback=callback,
            user_id=user_id,
        )
        return

    if action == "send_ai":
        ozon_chat_id = resolve_chat_id(user_id, token) or token
        st = get_chat_ai_state(ozon_chat_id) or {}
        draft = (st.get("ai_draft") or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ Нет ИИ-черновика. Сначала нажми «ИИ-ответ».")
            return

        try:
            await _send_with_retry(ozon_chat_id, draft)
        except OzonAPIError as exc:
            hint = (
                " Обновите чат или дождитесь сообщения от покупателя, если нельзя писать первым."
            )
            await send_ephemeral_message(
                callback,
                text=f"⚠️ Ozon отклонил отправку: {exc}.{hint}",
            )
            return
        except Exception:
            logger.exception("chat_send_message failed")
            await send_ephemeral_message(callback, text="⚠️ Не удалось отправить сообщение. Попробуйте позже.")
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

        await send_ephemeral_message(callback, text="✅ Сообщение отправлено в чат Ozon.")
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
    await state.clear()

    token = (payload.get("token") or "").strip()
    ozon_chat_id = resolve_chat_id(user_id, token) or token

    user_prompt = (message.text or "").strip()
    if len(user_prompt) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    st = get_chat_ai_state(ozon_chat_id) or {}
    save_chat_ai_state(chat_id=ozon_chat_id, ai_draft=st.get("ai_draft") or "", user_prompt=user_prompt, meta={"set": True})
    await send_ephemeral_message(message, text="✅ Промпт сохранён. Нажми «ИИ-ответ» в шапке чата.", ttl=4)


@router.message(ChatStates.edit_ai)
async def chat_edit_ai_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()
    await state.clear()

    token = (payload.get("token") or "").strip()
    ozon_chat_id = resolve_chat_id(user_id, token) or token

    txt = (message.text or "").strip()
    if len(txt) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    st = get_chat_ai_state(ozon_chat_id) or {}
    save_chat_ai_state(chat_id=ozon_chat_id, ai_draft=txt, user_prompt=st.get("user_prompt") or "", meta={"edited": True})

    await send_section_message(
        SECTION_CHAT_PROMPT,
        text="<b>ИИ-черновик обновлён:</b>\n\n" + txt,
        reply_markup=chat_ai_draft_keyboard(token=token),
        message=message,
        user_id=user_id,
    )
