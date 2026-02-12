# botapp/questions_handlers.py
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from botapp.ai_memory import ApprovedAnswer, get_approved_memory_store
from botapp.api.ai_client import generate_answer_for_question
from botapp.keyboards import MenuCallbackData
from botapp.sections.questions.keyboards import (
    QuestionsCallbackData,
    question_card_keyboard,
    questions_list_keyboard,
)
from botapp.utils.message_gc import (
    SECTION_QUESTION_CARD,
    SECTION_QUESTION_PROMPT,
    SECTION_QUESTIONS_LIST,
    delete_section_message,
    render_section,
    send_section_message,
)
from botapp.api.ozon_client import OzonAPIError, has_write_credentials, send_question_answer
from botapp.sections.questions.logic import (
    ensure_question_answer_text,
    find_question,
    format_question_card_text,
    get_question_by_index,
    invalidate_answer_cache,
    get_questions_pretty_period,
    get_questions_table,
    refresh_questions,
    resolve_question_id,
    resolve_question_token,
)
from botapp.utils.storage import get_question_answer, upsert_question_answer
from botapp.utils import send_ephemeral_message

logger = logging.getLogger(__name__)
router = Router()


class QuestionStates(StatesGroup):
    reprompt = State()
    manual = State()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_positive_sku(*candidates) -> int | None:
    for cand in candidates:
        if cand is None:
            continue
        try:
            s = str(cand).strip()
        except Exception:
            continue
        if not s.isdigit():
            continue
        try:
            v = int(s)
        except Exception:
            continue
        if v > 0:
            return v
    return None


async def _show_questions_list(
    *,
    user_id: int,
    category: str,
    page: int,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
    force_refresh: bool = False,
    edit_current_message: bool = False,
) -> None:
    if force_refresh:
        await refresh_questions(user_id, category=category, force=True)

    text, items, safe_page, total_pages = await get_questions_table(user_id=user_id, category=category, page=page)
    markup = questions_list_keyboard(
        user_id=user_id,
        category=category,
        page=safe_page,
        total_pages=total_pages,
        items=items,
    )

    sent = await send_section_message(
        SECTION_QUESTIONS_LIST,
        text=text,
        reply_markup=markup,
        callback=callback,
        message=message,
        user_id=user_id,
        edit_current_message=edit_current_message,
    )
    if sent:
        await delete_section_message(
            user_id,
            SECTION_QUESTION_CARD,
            sent.bot,
            force=True,
            preserve_message_id=sent.message_id,
        )
        await delete_section_message(
            user_id,
            SECTION_QUESTION_PROMPT,
            sent.bot,
            force=True,
            preserve_message_id=sent.message_id,
        )


async def _show_question_card(
    *,
    user_id: int,
    category: str,
    page: int,
    token: str,
    callback: CallbackQuery | None = None,
    message: Message | None = None,
    force_refresh: bool = False,
) -> None:
    q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
    qid = getattr(q, "id", None) if q else None

    if force_refresh:
        await refresh_questions(user_id, category=category, force=True)
        if qid:
            q = find_question(user_id, qid)

    if not q:
        await send_ephemeral_message(callback or message, text="⚠️ Вопрос не найден. Обновите список.")
        return

    await ensure_question_answer_text(q, user_id=user_id)

    saved = get_question_answer(q.id) or {}
    draft = (saved.get("answer") or "").strip() or None

    period_title = get_questions_pretty_period(user_id)
    text = format_question_card_text(q, answer_override=draft, period_title=period_title)

    can_send = bool(has_write_credentials() and draft and not (q.has_answer or (q.answer_text or "").strip()))
    markup = question_card_keyboard(
        category=category,
        page=page,
        token=token,
        can_send=can_send,
        has_answer=bool(q.has_answer or (q.answer_text or "").strip()),
    )

    sent = await send_section_message(
        SECTION_QUESTION_CARD,
        text=text,
        reply_markup=markup,
        callback=callback,
        message=message,
        user_id=user_id,
    )
    if sent:
        await delete_section_message(user_id, SECTION_QUESTION_PROMPT, sent.bot, force=True)


@router.message(F.text == "/questions")
async def cmd_questions(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_questions_list(user_id=message.from_user.id, category="all", page=0, message=message, force_refresh=False)


@router.callback_query(MenuCallbackData.filter(F.section == "questions"))
async def menu_questions(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    logger.info("Opening questions from menu for user_id=%s", user_id)
    await state.clear()
    # Редактируем текущее сообщение (меню) в список вопросов, чтобы избежать дублирования
    await _show_questions_list(
        user_id=user_id, 
        category="all", 
        page=0, 
        callback=callback, 
        force_refresh=False,
        edit_current_message=True,
    )


@router.callback_query(QuestionsCallbackData.filter())
async def questions_callbacks(callback: CallbackQuery, state: FSMContext) -> None:
    user_id = callback.from_user.id
    data = QuestionsCallbackData.unpack(callback.data)

    action = (data.action or "").strip()
    category = (data.category or "all").strip()
    page = int(data.page or 0)
    token = (data.token or "").strip()

    try:
        await callback.answer()
    except Exception:
        pass

    if action in ("noop", ""):
        return

    if action in ("list", "page", "refresh"):
        logger.info("Questions: action=%s, category=%s, page=%s, user_id=%s", action, category, page, user_id)
        
        # При возврате к списку с карточки (action="list") редактируем текущее сообщение
        edit_current = (action == "list")
        
        logger.info("Questions: calling _show_questions_list, edit_current_message=%s", edit_current)
        await _show_questions_list(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            force_refresh=(action == "refresh"),
            edit_current_message=edit_current,
        )
        logger.info("Questions: _show_questions_list completed")
        return

    if action == "open":
        await _show_question_card(user_id=user_id, category=category, page=page, token=token, callback=callback, force_refresh=False)
        return

    if action == "prefill":
        q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
        if not q:
            await send_ephemeral_message(callback, text="⚠️ Вопрос не найден.")
            return
        await ensure_question_answer_text(q, user_id=user_id)
        if not (q.answer_text or "").strip():
            await send_ephemeral_message(callback, text="⚠️ Не удалось загрузить ответ из Ozon.")
            return

        upsert_question_answer(
            question_id=q.id,
            created_at=q.created_at,
            sku=q.sku,
            product_name=q.product_name,
            question=q.question_text,
            answer=q.answer_text,
            answer_source="ozon_prefill",
            answer_sent_to_ozon=False,
            answer_sent_at=None,
            meta={"prefill_at": _now_iso()},
        )
        await send_ephemeral_message(callback, text="✅ Подставил ответ из Ozon как черновик.", ttl=3)
        await _show_question_card(user_id=user_id, category=category, page=page, token=token, callback=callback, force_refresh=False)
        return

    if action in ("clear", "clear_draft"):
        q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
        if not q:
            await send_ephemeral_message(callback, text="⚠️ Вопрос не найден.")
            return

        upsert_question_answer(
            question_id=q.id,
            created_at=q.created_at,
            sku=q.sku,
            product_name=q.product_name,
            question=q.question_text,
            answer="",
            answer_source="",
            answer_sent_to_ozon=False,
            answer_sent_at=None,
            meta={"cleared_at": _now_iso()},
        )
        await send_ephemeral_message(callback, text="🧹 Черновик очищен.", ttl=3)
        await _show_question_card(user_id=user_id, category=category, page=page, token=token, callback=callback, force_refresh=False)
        return

    if action in ("ai", "card_ai"):
        q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
        if not q:
            await refresh_questions(user_id, category="all", force=True)
            q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
            if not q:
                try:
                    await callback.answer("⚠️ Вопрос не найден. Нажми «Обновить» в списке.", show_alert=True)
                except Exception:
                    await send_ephemeral_message(callback, text="⚠️ Вопрос не найден. Нажми «Обновить» в списке.")
                logger.warning("Question not found for AI action", extra={"token": token, "action": action, "user_id": user_id})
                return

        if not (q.question_text or "").strip():
            await send_ephemeral_message(callback, text="⚠️ Нет текста вопроса.")
            return

        saved = get_question_answer(q.id) or {}
        previous = (saved.get("answer") or "").strip() or None

        logger.info("AI start: user_id=%s qid=%s sku=%s", user_id, q.id, getattr(q, "sku", None))
        try:
            draft = await generate_answer_for_question(
                q.question_text or "",
                product_name=q.product_name,
                sku=q.sku,
                existing_answer=previous,
                user_prompt=None,
            )
        except Exception as exc:
            logger.exception("AI failed for question qid=%s token=%s", q.id if q else None, token)
            await send_ephemeral_message(callback, text=f"⚠️ ИИ-ответ не получился: {exc}", as_alert=True)
            return

        draft = (draft or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ ИИ вернул пустой ответ.")
            return

        upsert_question_answer(
            question_id=q.id,
            created_at=q.created_at,
            sku=q.sku,
            product_name=q.product_name,
            question=q.question_text,
            answer=draft,
            answer_source="ai",
            answer_sent_to_ozon=False,
            answer_sent_at=None,
            meta={"generated_at": _now_iso()},
        )

        await send_ephemeral_message(callback, text="✅ Черновик создан.", ttl=3)
        await _show_question_card(user_id=user_id, category=category, page=page, token=token, callback=callback, force_refresh=False)
        return

    if action in ("reprompt", "card_reprompt"):
        await state.set_state(QuestionStates.reprompt)
        await state.update_data(category=category, page=page, token=token)
        await render_section(
            SECTION_QUESTION_PROMPT,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            text=(
                "<b>Пересобрать ответ на вопрос</b>\n\n"
                "Напиши пожелания (тон, что учесть).\n"
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
        await state.set_state(QuestionStates.manual)
        await state.update_data(category=category, page=page, token=token)
        await render_section(
            SECTION_QUESTION_PROMPT,
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

    if action == "send":
        q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
        if not q:
            await send_ephemeral_message(callback, text="⚠️ Вопрос не найден.")
            return

        saved = get_question_answer(q.id) or {}
        draft = (saved.get("answer") or "").strip()
        if len(draft) < 2:
            await send_ephemeral_message(callback, text="⚠️ Нет черновика. Сначала сгенерируй ИИ-ответ или введи вручную.")
            return

        if not has_write_credentials():
            await send_ephemeral_message(callback, text="⚠️ Нет write-доступа к Ozon (ключи OZON_WRITE_*).")
            return

        sku = _coerce_positive_sku(q.sku, getattr(q, "product_id", None))
        if sku is None:
            logger.warning("Cannot determine SKU for question send: qid=%s raw_sku=%r raw_pid=%r", q.id, getattr(q, "sku", None), getattr(q, "product_id", None))
            await send_ephemeral_message(callback, text="⚠️ Не удалось определить SKU для отправки ответа. Нажмите «Обновить» и попробуйте снова.", as_alert=True)
            return
        try:
            ok = await send_question_answer(q.id, draft, sku=sku)
            if ok is False:
                await send_ephemeral_message(callback, text="⚠️ Не удалось отправить ответ: отсутствует SKU. Обновите список и попробуйте снова.", as_alert=True)
                return
        except OzonAPIError as exc:
            await send_ephemeral_message(callback, text=f"⚠️ Ozon отклонил отправку: {exc}")
            return
        except Exception:
            logger.exception("send_question_answer failed")
            await send_ephemeral_message(callback, text="⚠️ Не удалось отправить ответ. Попробуй позже.")
            return

        answer_source = (saved.get("answer_source") or "manual")
        upsert_question_answer(
            question_id=q.id,
            created_at=q.created_at,
            sku=q.sku,
            product_name=q.product_name,
            question=q.question_text,
            answer=draft,
            answer_source=answer_source,
            answer_sent_to_ozon=True,
            answer_sent_at=_now_iso(),
            meta={"sent": True},
        )

        # update local flag to show as answered
        q.has_answer = True
        q.answer_text = draft

        invalidate_answer_cache(q.id, user_id=user_id)

        try:
            rec = ApprovedAnswer.now_iso(
                kind="question",
                ozon_entity_id=str(q.id),
                input_text=q.question_text or "",
                answer_text=draft,
                product_id=q.sku or q.product_id,
                product_name=q.product_name,
                rating=None,
                meta={"answered_via": "ai" if answer_source == "ai" else "manual"},
            )
            get_approved_memory_store().add_approved_answer(rec)
        except Exception:
            logger.exception("Failed to persist question answer to memory")

        await send_ephemeral_message(callback, text="✅ Ответ отправлен в Ozon.", ttl=4)
        await _show_question_card(user_id=user_id, category=category, page=page, token=token, callback=callback, force_refresh=True)
        return

    await send_ephemeral_message(callback, text=f"⚠️ Неизвестное действие: {action}")


@router.message(F.text == "/cancel")
async def cancel_question_fsm(message: Message, state: FSMContext) -> None:
    st = await state.get_state()
    if st not in (QuestionStates.reprompt.state, QuestionStates.manual.state):
        return
    await state.clear()
    await send_ephemeral_message(message, text="Ок, отменил.", ttl=3)


@router.message(QuestionStates.reprompt)
async def question_reprompt_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()
    await state.clear()

    token = (payload.get("token") or "").strip()
    category = (payload.get("category") or "all").strip()
    page = int(payload.get("page") or 0)

    q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
    if not q:
        await send_ephemeral_message(message, text="⚠️ Вопрос не найден. Открой карточку заново.")
        return

    wish = (message.text or "").strip()
    if len(wish) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    saved = get_question_answer(q.id) or {}
    previous = (saved.get("answer") or "").strip() or None

    try:
        draft = await generate_answer_for_question(
            q.question_text or "",
            product_name=q.product_name,
            sku=q.sku,
            existing_answer=previous,
            user_prompt=wish,
        )
    except Exception as exc:
        await send_ephemeral_message(message, text=f"⚠️ ИИ-ответ не получился: {exc}")
        return

    draft = (draft or "").strip()
    if len(draft) < 2:
        await send_ephemeral_message(message, text="⚠️ ИИ вернул пустой ответ.")
        return

    upsert_question_answer(
        question_id=q.id,
        created_at=q.created_at,
        sku=q.sku,
        product_name=q.product_name,
        question=q.question_text,
        answer=draft,
        answer_source="reprompt",
        answer_sent_to_ozon=False,
        answer_sent_at=None,
        meta={"wish": wish, "generated_at": _now_iso()},
    )

    await send_ephemeral_message(message, text="✅ Пересобрал. Открой карточку — черновик обновлён.", ttl=4)
    await _show_question_card(user_id=user_id, category=category, page=page, token=token, message=message, force_refresh=False)


@router.message(QuestionStates.manual)
async def question_manual_text(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    payload = await state.get_data()
    await state.clear()

    token = (payload.get("token") or "").strip()
    category = (payload.get("category") or "all").strip()
    page = int(payload.get("page") or 0)

    q = await resolve_question_token(user_id, token) or resolve_question_id(user_id, token)
    if not q:
        await send_ephemeral_message(message, text="⚠️ Вопрос не найден. Открой карточку заново.")
        return

    txt = (message.text or "").strip()
    if len(txt) < 2:
        await send_ephemeral_message(message, text="⚠️ Слишком коротко.", ttl=3)
        return

    upsert_question_answer(
        question_id=q.id,
        created_at=q.created_at,
        sku=q.sku,
        product_name=q.product_name,
        question=q.question_text,
        answer=txt,
        answer_source="manual",
        answer_sent_to_ozon=False,
        answer_sent_at=None,
        meta={"saved_at": _now_iso()},
    )

    await send_ephemeral_message(message, text="✅ Сохранил черновик.", ttl=3)
    await _show_question_card(user_id=user_id, category=category, page=page, token=token, message=message, force_refresh=False)
