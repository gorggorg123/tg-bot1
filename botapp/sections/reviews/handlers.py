# botapp/reviews_handlers.py
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from botapp.ai_memory import ApprovedAnswer, get_approved_memory_store
from botapp.api.ai_client import generate_review_reply
from botapp.keyboards import MenuCallbackData
from botapp.sections.reviews.keyboards import (
    ReviewsCallbackData,
    review_card_keyboard,
    reviews_list_keyboard,
)
from botapp.utils.message_gc import (
    SECTION_REVIEW_CARD,
    SECTION_REVIEW_PROMPT,
    SECTION_REVIEWS_LIST,
    delete_section_message,
    render_section,
    send_section_message,
)
from botapp.api.ozon_client import OzonAPIError, get_client, get_write_client, has_write_credentials
from botapp.sections.reviews.logic import (
    find_review,
    format_review_card_text,
    get_review_and_card,
    get_reviews_table,
    mark_review_answered,
    encode_review_id,
    refresh_review_from_api,
    refresh_reviews_from_api,
    resolve_review_id,
)
from botapp.utils.storage import get_review_reply, upsert_review_reply
from botapp.utils import send_ephemeral_message

logger = logging.getLogger(__name__)
router = Router()
_send_locks: dict[tuple[int, str], asyncio.Lock] = {}


class ReviewStates(StatesGroup):
    reprompt = State()
    manual = State()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _review_text_for_ai(card: "ReviewCard") -> tuple[str, bool]:
    """Return review text for AI along with a flag indicating if it was empty."""

    raw = (card.text or "").strip()
    if raw:
        return raw, False
    return "(отзыв без текста)", True


async def _safe_callback_answer(cb: CallbackQuery | None, text: str | None = None, *, show_alert: bool = False) -> None:
    if cb is None:
        return
    try:
        await cb.answer(text=text, show_alert=show_alert)
    except Exception:
        logger.debug("callback.answer failed", exc_info=True)


def _get_send_lock(user_id: int, review_id: str | None) -> asyncio.Lock:
    key = (user_id, review_id or "")
    if key not in _send_locks:
        _send_locks[key] = asyncio.Lock()
    return _send_locks[key]


async def _clear_other_review_sections(bot, user_id: int) -> None:
    await delete_section_message(user_id, SECTION_REVIEW_PROMPT, bot, force=True)


async def _show_reviews_list(
    *,
    user_id: int,
    category: str,
    page: int,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
    force_refresh: bool = False,
) -> None:
    if force_refresh:
        await refresh_reviews_from_api(user_id)

    text, items, safe_page, total_pages = await get_reviews_table(
        user_id=user_id,
        category=category,
        page=page,
    )
    markup = reviews_list_keyboard(category=category, page=safe_page, total_pages=total_pages, items=items)
    sent = await send_section_message(
        SECTION_REVIEWS_LIST,
        text=text,
        reply_markup=markup,
        callback=callback,
        message=message,
        user_id=user_id,
    )
    if sent:
        await delete_section_message(
            user_id,
            SECTION_REVIEW_CARD,
            sent.bot,
            force=True,
            preserve_message_id=sent.message_id,
        )
        await delete_section_message(
            user_id,
            SECTION_REVIEW_PROMPT,
            sent.bot,
            force=True,
            preserve_message_id=sent.message_id,
        )


async def _show_review_card(
    *,
    user_id: int,
    category: str,
    page: int,
    token: str,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
) -> None:
    rid = resolve_review_id(user_id, token) or token
    if not rid:
        await send_ephemeral_message(callback or message, text="⚠️ Не удалось открыть отзыв (нет review_id).")
        return

    card = find_review(user_id, rid)
    if card is None:
        await refresh_reviews_from_api(user_id)
        card = find_review(user_id, rid)

    if card is None:
        await send_ephemeral_message(callback or message, text="⚠️ Отзыв не найден. Обновите список.")
        return

    # Lazy-load details/comments when opening a card
    try:
        await refresh_review_from_api(card, get_client())
    except Exception:
        pass

    saved = get_review_reply(card.id) or {}
    draft = (saved.get("draft") or "").strip() or None

    already_answered = False
    try:
        already_answered = bool(card.has_answer)
    except Exception:
        already_answered = bool(card.answered or (card.answer_text or "").strip())

    can_send = bool(has_write_credentials() and draft and not already_answered)
    period_title = "Отзывы"
    view, _ = await get_review_and_card(user_id, category, index=0, review_id=card.id)
    if view and view.period:
        period_title = view.period

    token = token or encode_review_id(user_id, card.id) or card.id

    text = format_review_card_text(
        card=card,
        period_title=period_title,
        current_answer=draft,
    )
    markup = review_card_keyboard(
        category=category,
        page=page,
        index=(view.index if view else 0),
        review_id=card.id,
        token=token,
        can_send=can_send,
    )

    sent = await send_section_message(
        SECTION_REVIEW_CARD,
        text=text,
        reply_markup=markup,
        callback=callback,
        message=message,
        user_id=user_id,
    )
    if sent:
        await delete_section_message(
            user_id,
            SECTION_REVIEW_PROMPT,
            sent.bot,
            force=True,
            preserve_message_id=sent.message_id,
        )


@router.message(F.text == "/reviews")
async def cmd_reviews(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_reviews_list(user_id=message.from_user.id, category="all", page=0, message=message, force_refresh=False)


@router.callback_query(MenuCallbackData.filter(F.section == "reviews"))
async def menu_reviews(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_reviews_list(user_id=callback.from_user.id, category="all", page=0, callback=callback, force_refresh=False)


@router.callback_query(ReviewsCallbackData.filter())
async def reviews_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    data = ReviewsCallbackData.unpack(callback.data)

    action = (data.action or "").strip()
    category = (data.category or "all").strip()
    page = int(data.page or 0)
    token = (data.token or data.review_id or "").strip()

    await callback.answer()

    if action in ("noop", ""):
        return

    if action in ("list", "page", "refresh", "list_page"):
        await _show_reviews_list(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            force_refresh=(action == "refresh"),
        )
        return

    if action in ("open", "open_card"):
        await _show_review_card(
            user_id=user_id,
            category=category,
            page=page,
            token=token,
            callback=callback,
        )
        return

    if action in ("ai", "card_ai"):
        await _safe_callback_answer(callback, "Пересобираю…")

        rid = resolve_review_id(user_id, token) or token
        card = find_review(user_id, rid)
        if not card:
            await send_ephemeral_message(callback, text="⚠️ Отзыв не найден. Обновите список.")
            return

        try:
            await refresh_review_from_api(card, get_client())
        except Exception:
            pass

        saved = get_review_reply(card.id) or {}
        previous = (saved.get("draft") or "").strip() or None

        review_text, _ = _review_text_for_ai(card)

        try:
            draft = await generate_review_reply(
                review_text=review_text,
                product_name=card.product_name,
                sku=str(card.product_id or card.offer_id or "") or None,
                rating=card.rating,
                previous_answer=previous,
                user_prompt=None,
            )
        except Exception as exc:
            await send_ephemeral_message(callback, text=f"⚠️ ИИ-ответ не получился: {exc}")
            return

        draft = (draft or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ ИИ вернул пустой ответ.")
            return

        upsert_review_reply(
            review_id=card.id,
            created_at=_to_iso(card.created_at),
            product_name=card.product_name,
            rating=card.rating,
            review_text=card.text,
            draft=draft,
            draft_source="ai",
            sent_to_ozon=False,
            sent_at=None,
            meta={"generated_at": _now_iso()},
        )

        await _show_review_card(user_id=user_id, category=category, page=page, token=token, callback=callback)
        return

    if action in ("reprompt", "card_reprompt"):
        await state.set_state(ReviewStates.reprompt)
        await state.update_data(category=category, page=page, token=token)
        await render_section(
            SECTION_REVIEW_PROMPT,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            text=(
                "<b>Пересобрать ответ на отзыв</b>\n\n"
                "Напиши пожелания к ответу (тон, стиль, что обязательно учесть).\n"
                "Отмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            mode="section_only",
        )
        await send_ephemeral_message(
            callback,
            text="✍️ Напиши пожелания к ответу одним сообщением. Отмена: /cancel",
            ttl=6,
        )
        return

    if action in ("manual", "card_manual"):
        await state.set_state(ReviewStates.manual)
        await state.update_data(category=category, page=page, token=token)
        await render_section(
            SECTION_REVIEW_PROMPT,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            text=(
                "<b>Ввод ответа вручную</b>\n\n"
                "Отправь текст ответа одним сообщением.\n"
                "Отмена: /cancel"
            ),
            reply_markup=None,
            callback=callback,
            mode="section_only",
        )
        return

    if action == "clear":
        await _safe_callback_answer(callback, "Очищаю черновик…")

        rid = resolve_review_id(user_id, token) or token
        card = find_review(user_id, rid)
        if not card:
            await send_ephemeral_message(callback, text="⚠️ Отзыв не найден.")
            return

        upsert_review_reply(
            review_id=card.id,
            created_at=_to_iso(card.created_at),
            product_name=card.product_name,
            rating=card.rating,
            review_text=card.text,
            draft="",
            draft_source="",
            sent_to_ozon=False,
            sent_at=None,
            meta={"cleared_at": _now_iso()},
        )

        await send_ephemeral_message(callback, text="✅ Черновик удалён.", ttl=3)
        await _show_review_card(user_id=user_id, category=category, page=page, token=encode_review_id(user_id, card.id), callback=callback)
        return

    if action == "send":
        await _safe_callback_answer(callback, "Отправляю…")

        rid = resolve_review_id(user_id, token) or token
        card = find_review(user_id, rid)
        if not card:
            await _safe_callback_answer(callback, "⚠️ Отзыв не найден", show_alert=True)
            return

        lock = _get_send_lock(user_id, card.id)
        async with lock:
            saved = get_review_reply(card.id) or {}
            draft = (saved.get("draft") or "").strip()
            if len(draft) < 2:
                await _safe_callback_answer(
                    callback,
                    "⚠️ Нет черновика. Сначала сгенерируй ИИ-ответ или введи вручную.",
                    show_alert=True,
                )
                return

            if not has_write_credentials():
                await _safe_callback_answer(
                    callback,
                    "⚠️ Нет write-доступа к Ozon (ключи OZON_WRITE_*).",
                    show_alert=True,
                )
                return

            client = get_write_client()
            if client is None:
                await _safe_callback_answer(callback, "⚠️ Write-client не инициализирован.", show_alert=True)
                return

            try:
                await client.review_comment_create(card.id, draft)
            except OzonAPIError as exc:
                await _safe_callback_answer(callback, f"⚠️ Ozon отклонил отправку: {exc}", show_alert=True)
                return
            except Exception:
                logger.exception("review_comment_create failed")
                await _safe_callback_answer(
                    callback, "⚠️ Не удалось отправить ответ. Попробуй позже.", show_alert=True
                )
                return

            draft_source = (saved.get("draft_source") or "manual")
            upsert_review_reply(
                review_id=card.id,
                created_at=_to_iso(card.created_at),
                product_name=card.product_name,
                rating=card.rating,
                review_text=card.text,
                draft="",
                draft_source=draft_source,
                sent_to_ozon=True,
                sent_at=_now_iso(),
                meta={"sent": True},
            )

            try:
                mark_review_answered(card.id, user_id, text=draft)
            except Exception:
                logger.exception(
                    "Failed to mark review answered locally review_id=%s", card.id
                )

            try:
                rec = ApprovedAnswer.now_iso(
                    kind="review",
                    ozon_entity_id=str(card.id),
                    input_text=card.text or "",
                    answer_text=draft,
                    product_id=str(card.product_id or card.offer_id or "") or None,
                    product_name=card.product_name,
                    rating=card.rating,
                    meta={
                        "answered_via": "ai" if draft_source == "ai" else "manual",
                    },
                )
                get_approved_memory_store().add_approved_answer(rec)
            except Exception:
                logger.exception(
                    "Failed to persist review reply to memory review_id=%s", card.id
                )

        await _safe_callback_answer(callback, "✅ Ответ отправлен в Ozon.")
        await _show_review_card(user_id=user_id, category=category, page=page, token=token, callback=callback)
        return

    await send_ephemeral_message(callback, text=f"⚠️ Неизвестное действие: {action}")


@router.message(F.text == "/cancel")
async def cancel_review_fsm(message: Message, state: FSMContext) -> None:
    st = await state.get_state()
    if st not in (ReviewStates.reprompt.state, ReviewStates.manual.state):
        return
    await state.clear()
    await send_ephemeral_message(message, text="Ок, отменил.", ttl=3)


@router.message(ReviewStates.reprompt)
async def review_reprompt_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()
    await state.clear()

    token = (payload.get("token") or "").strip()
    category = (payload.get("category") or "all").strip()
    page = int(payload.get("page") or 0)

    rid = resolve_review_id(user_id, token) or token
    card = find_review(user_id, rid)
    if not card:
        await send_ephemeral_message(message, text="⚠️ Отзыв не найден. Открой карточку заново.")
        return

    wish = (message.text or "").strip()
    if len(wish) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    try:
        await refresh_review_from_api(card, get_client())
    except Exception:
        pass

    saved = get_review_reply(card.id) or {}
    previous = (saved.get("draft") or "").strip() or None
    review_text, _ = _review_text_for_ai(card)

    try:
        draft = await generate_review_reply(
            review_text=review_text,
            product_name=card.product_name,
            sku=str(card.product_id or card.offer_id or "") or None,
            rating=card.rating,
            previous_answer=previous,
            user_prompt=wish,
        )
    except Exception as exc:
        await send_ephemeral_message(message, text=f"⚠️ ИИ-ответ не получился: {exc}")
        return

    draft = (draft or "").strip()
    if len(draft) < 2:
        await send_ephemeral_message(message, text="⚠️ ИИ вернул пустой ответ.")
        return

    upsert_review_reply(
        review_id=card.id,
        created_at=_to_iso(card.created_at),
        product_name=card.product_name,
        rating=card.rating,
        review_text=card.text,
        draft=draft,
        draft_source="reprompt",
        sent_to_ozon=False,
        sent_at=None,
        meta={"wish": wish, "generated_at": _now_iso()},
    )

    await send_ephemeral_message(message, text="✅ Пересобрал. Открой карточку — черновик обновлён.", ttl=4)
    await _show_review_card(user_id=user_id, category=category, page=page, token=token, message=message)


@router.message(ReviewStates.manual)
async def review_manual_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()
    await state.clear()

    token = (payload.get("token") or "").strip()
    category = (payload.get("category") or "all").strip()
    page = int(payload.get("page") or 0)

    rid = resolve_review_id(user_id, token) or token
    card = find_review(user_id, rid)
    if not card:
        await send_ephemeral_message(message, text="⚠️ Отзыв не найден. Открой карточку заново.")
        return

    txt = (message.text or "").strip()
    if len(txt) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    upsert_review_reply(
        review_id=card.id,
        created_at=_to_iso(card.created_at),
        product_name=card.product_name,
        rating=card.rating,
        review_text=card.text,
        draft=txt,
        draft_source="manual",
        sent_to_ozon=False,
        sent_at=None,
        meta={"saved_at": _now_iso()},
    )

    await send_ephemeral_message(message, text="✅ Сохранил черновик.", ttl=3)
    await _show_review_card(user_id=user_id, category=category, page=page, token=token, message=message)
