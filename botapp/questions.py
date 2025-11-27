from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)

router = Router(name="questions")

# --- Настройки Ozon ---

OZON_API_URL = "https://api-seller.ozon.ru"
OZON_CLIENT_ID = os.getenv("OZON_CLIENT_ID")
OZON_API_KEY = os.getenv("OZON_API_KEY")

# Сколько вопросов показываем на странице
QUESTIONS_PER_PAGE = 5


# --- FSM для ручного ответа ---

class QuestionAnswerState(StatesGroup):
    waiting_for_text = State()


# --- Callback data ---

class QuestionsCallbackData(CallbackData, prefix="q"):
    action: str
    page: int = 0
    question_id: Optional[str] = None


# --- Модель вопроса (простая dataclass, без Pydantic) ---

@dataclass
class Question:
    question_id: str
    product_name: str
    text: str
    created_at: Optional[datetime] = None
    answers_count: int = 0
    status: Optional[str] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Optional["Question"]:
        """
        Аккуратно достаём поля из "сырых" данных Ozon.
        Максимально терпимо к изменениям формата ответа.
        """
        # ID вопроса
        qid = data.get("question_id") or data.get("id")
        if qid is None:
            logger.warning("Пропускаю вопрос без question_id/id: %r", data)
            return None

        # Название товара
        product_name = (
            data.get("product_name")
            or (data.get("product") or {}).get("name")
            or "Товар без названия"
        )

        # Текст вопроса
        text = (
            data.get("text")
            or data.get("question_text")
            or data.get("message")
            or ""
        ).strip()

        # Дата создания
        created_raw = (
            data.get("created_at")
            or data.get("created_at_time")
            or data.get("created")
            or data.get("date")
        )
        created_at: Optional[datetime] = None
        if isinstance(created_raw, str):
            try:
                created_at = datetime.fromisoformat(
                    created_raw.replace("Z", "+00:00")
                )
            except ValueError:
                created_at = None

        answers_count = int(data.get("answers_count") or 0)
        status = data.get("status")

        return cls(
            question_id=str(qid),
            product_name=product_name,
            text=text,
            created_at=created_at,
            answers_count=answers_count,
            status=status,
        )


# --- Простейший кеш, чтобы не бегать в API лишний раз ---

QUESTIONS_CACHE: Dict[str, Question] = {}


# --- Низкоуровневый клиент Ozon только для раздела вопросов ---

async def _ozon_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Универсальный POST в Ozon Seller API.
    Не зависит от Pydantic, возвращает обычный dict.
    """
    if not OZON_CLIENT_ID or not OZON_API_KEY:
        raise RuntimeError(
            "Не заданы переменные окружения OZON_CLIENT_ID и OZON_API_KEY"
        )

    headers = {
        "Client-Id": OZON_CLIENT_ID,
        "Api-Key": OZON_API_KEY,
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(
        base_url=OZON_API_URL, timeout=30
    ) as client:
        resp = await client.post(path, headers=headers, json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.exception(
                "Ozon %s вернул ошибку %s, тело: %s",
                path,
                e,
                resp.text,
            )
            raise

        try:
            data = resp.json()
        except ValueError:
            logger.error("Не удалось распарсить JSON от Ozon: %s", resp.text)
            raise

    return data


async def fetch_questions_page(page: int) -> List[Question]:
    """
    Получаем список вопросов для конкретной страницы.
    Страница начинается с 0.
    """
    offset = page * QUESTIONS_PER_PAGE
    payload = {
        "filter": {
            # максимально широкий фильтр; при необходимости можно сузить
            "status": "ALL",
            "question_type": "ALL",
        },
        "limit": QUESTIONS_PER_PAGE,
        "offset": offset,
    }

    raw = await _ozon_post("/v1/question/list", payload)

    # Ozon обычно кладёт данные в "result", но на всякий случай делаем fallback
    container = raw.get("result") or raw
    items = container.get("questions") or container.get("items") or []

    questions: List[Question] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = Question.from_api(item)
        if q:
            questions.append(q)
            QUESTIONS_CACHE[q.question_id] = q

    return questions


async def send_answer_to_ozon(question_id: str, answer_text: str) -> None:
    """
    Отправка ответа на вопрос через /v1/question/answer/create.
    """
    payload = {
        "question_id": int(question_id),
        "text": answer_text,
    }

    # В разных версиях API параметр может называться "answer" или "text".
    # Пробуем дублировать, чтобы наверняка.
    payload["answer"] = answer_text

    await _ozon_post("/v1/question/answer/create", payload)


# --- Форматирование текста и клавиатур ---

def _format_question_card(q: Question) -> str:
    lines: List[str] = []
    lines.append(f"❓ <b>{q.product_name}</b>")
    lines.append("")
    if q.text:
        lines.append(q.text)
        lines.append("")

    if q.created_at:
        lines.append(
            f"🕒 {q.created_at.strftime('%d.%m.%Y %H:%M')}"
        )

    meta_parts = []
    meta_parts.append(f"💬 Ответов: {q.answers_count}")
    if q.status:
        meta_parts.append(f"Статус: {q.status}")
    if meta_parts:
        lines.append("")
        lines.append(" · ".join(meta_parts))

    return "\n".join(lines)


def _format_questions_list_title(page: int) -> str:
    return f"📨 Вопросы покупателей (страница {page + 1})"


def build_questions_list_keyboard(
    questions: List[Question],
    page: int,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    # Кнопка на каждый вопрос
    for q in questions:
        title = f"{q.product_name[:25]} • {q.text[:40]}".strip()
        if len(q.text) > 40:
            title += "…"
        kb.button(
            text=title,
            callback_data=QuestionsCallbackData(
                action="open",
                page=page,
                question_id=q.question_id,
            ).pack(),
        )

    kb.adjust(1)

    # Навигация по страницам
    nav = InlineKeyboardBuilder()

    if page > 0:
        nav.button(
            text="⬅️ Назад",
            callback_data=QuestionsCallbackData(
                action="page",
                page=page - 1,
            ).pack(),
        )

    # Кнопка-«заглушка» для текущей страницы
    nav.button(
        text=f"{page + 1}",
        callback_data="questions:noop",
    )

    # Вперёд — всегда показываем, Ozon сам вернёт пустой список на последней
    nav.button(
        text="Вперёд ➡️",
        callback_data=QuestionsCallbackData(
            action="page",
            page=page + 1,
        ).pack(),
    )

    nav.adjust(3)
    kb.attach(nav)

    return kb.as_markup()


def build_question_card_keyboard(
    q: Question,
    page: int,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.button(
        text="✍️ Ответить",
        callback_data=QuestionsCallbackData(
            action="answer",
            page=page,
            question_id=q.question_id,
        ).pack(),
    )
    kb.button(
        text="⬅️ К списку",
        callback_data=QuestionsCallbackData(
            action="page",
            page=page,
        ).pack(),
    )

    kb.adjust(1)
    return kb.as_markup()


# --- Вспомогательная функция для показа списка ---

async def _show_questions_page(
    target: Message | CallbackQuery,
    page: int,
) -> None:
    questions = await fetch_questions_page(page)

    if not questions and page > 0:
        # если страница пустая (например, ушли слишком далеко вперёд) —
        # откатываемся на первую
        page = 0
        questions = await fetch_questions_page(page)

    text_lines: List[str] = [_format_questions_list_title(page)]

    if not questions:
        text_lines.append("")
        text_lines.append("Пока нет вопросов.")
    else:
        text_lines.append("")
        for q in questions:
            preview = q.text[:60].replace("\n", " ")
            if len(q.text) > 60:
                preview += "…"
            text_lines.append(f"• {q.product_name[:25]} — {preview}")

    text = "\n".join(text_lines)
    keyboard = build_questions_list_keyboard(questions, page)

    if isinstance(target, Message):
        await target.answer(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await target.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )


# --- Handlers ---

@router.message(Command("questions"))
async def cmd_questions(message: Message) -> None:
    """
    Команда /questions — открыть список вопросов (первая страница).
    Если у тебя есть главное меню с кнопкой "Вопросы покупателей",
    просто сделай, чтобы эта кнопка вызывала эту же функцию.
    """
    await _show_questions_page(message, page=0)


@router.callback_query(QuestionsCallbackData.filter(F.action == "page"))
async def cb_questions_page(
    callback: CallbackQuery,
    callback_data: QuestionsCallbackData,
) -> None:
    await _show_questions_page(callback, page=callback_data.page)
    await callback.answer()


@router.callback_query(QuestionsCallbackData.filter(F.action == "open"))
async def cb_question_open(
    callback: CallbackQuery,
    callback_data: QuestionsCallbackData,
) -> None:
    qid = callback_data.question_id
    if not qid:
        await callback.answer(
            "Не удалось определить вопрос.",
            show_alert=True,
        )
        return

    q = QUESTIONS_CACHE.get(qid)
    if q is None:
        # На всякий случай подгружаем страницу ещё раз
        questions = await fetch_questions_page(callback_data.page)
        for item in questions:
            if item.question_id == qid:
                q = item
                break

    if q is None:
        await callback.answer(
            "Не удалось найти этот вопрос. Попробуйте обновить список.",
            show_alert=True,
        )
        return

    text = _format_question_card(q)
    keyboard = build_question_card_keyboard(q, callback_data.page)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(QuestionsCallbackData.filter(F.action == "answer"))
async def cb_question_answer(
    callback: CallbackQuery,
    callback_data: QuestionsCallbackData,
    state: FSMContext,
) -> None:
    qid = callback_data.question_id
    if not qid:
        await callback.answer(
            "Не удалось определить вопрос.",
            show_alert=True,
        )
        return

    q = QUESTIONS_CACHE.get(qid)
    if q is None:
        questions = await fetch_questions_page(callback_data.page)
        for item in questions:
            if item.question_id == qid:
                q = item
                break

    if q is None:
        await callback.answer(
            "Не удалось найти этот вопрос. Попробуйте обновить список.",
            show_alert=True,
        )
        return

    await state.update_data(
        question_id=q.question_id,
        page=callback_data.page,
    )
    await state.set_state(QuestionAnswerState.waiting_for_text)

    await callback.message.answer(
        f"Напишите, пожалуйста, ответ для вопроса по товару "
        f"«{q.product_name}»:\n\n{q.text}"
    )
    await callback.answer()


@router.message(QuestionAnswerState.waiting_for_text)
async def process_answer_text(
    message: Message,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    qid = data.get("question_id")
    page = int(data.get("page") or 0)
    answer_text = (message.text or "").strip()

    if not qid:
        await message.answer(
            "Не удалось определить вопрос. Попробуйте ещё раз через меню вопросов."
        )
        await state.clear()
        return

    if not answer_text:
        await message.answer("Ответ не может быть пустым, напишите текст.")
        return

    try:
        await send_answer_to_ozon(qid, answer_text)
    except Exception as e:
        logger.exception("Не удалось отправить ответ на вопрос %s", qid)
        await message.answer(
            f"⚠️ Не удалось отправить ответ в Ozon.\nОшибка: {e}"
        )
        return

    await state.clear()
    await message.answer("✅ Ответ отправлен в Ozon.")

    # Можно сразу вернуть пользователя на ту же страницу списка вопросов
    await _show_questions_page(message, page=page)


@router.callback_query(F.data == "questions:noop")
async def cb_questions_noop(callback: CallbackQuery) -> None:
    """
    Глушилка для центральной кнопки с номером страницы.
    Ничего не делает, просто убирает крутилку у пользователя.
    """
    await callback.answer()

