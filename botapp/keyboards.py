# botapp/keyboards.py
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class MenuCallbackData(CallbackData, prefix="m"):
    section: str  # reviews | questions | chats | home


class ReviewsCallbackData(CallbackData, prefix="rv"):
    action: str
    category: str
    page: int
    token: str


class QuestionsCallbackData(CallbackData, prefix="q"):
    action: str
    category: str
    page: int
    token: str


class ChatCallbackData(CallbackData, prefix="c"):
    action: str
    page: int
    token: str


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Отзывы", callback_data=MenuCallbackData(section="reviews").pack())],
            [InlineKeyboardButton(text="❓ Вопросы", callback_data=MenuCallbackData(section="questions").pack())],
            [InlineKeyboardButton(text="💬 Чаты", callback_data=MenuCallbackData(section="chats").pack())],
        ]
    )


def reviews_list_keyboard(*, category: str, page: int, total_pages: int, items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    rows.append(
        [
            InlineKeyboardButton(text="Все", callback_data=ReviewsCallbackData(action="list", category="all", page=0, token="").pack()),
            InlineKeyboardButton(text="Без ответа", callback_data=ReviewsCallbackData(action="list", category="unanswered", page=0, token="").pack()),
            InlineKeyboardButton(text="С ответом", callback_data=ReviewsCallbackData(action="list", category="answered", page=0, token="").pack()),
        ]
    )
    for it in items:
        rows.append([InlineKeyboardButton(text=it["label"], callback_data=ReviewsCallbackData(action="open", category=category, page=page, token=it["token"]).pack())])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=ReviewsCallbackData(action="page", category=category, page=page - 1, token="").pack()))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{max(1, total_pages)}", callback_data=ReviewsCallbackData(action="noop", category=category, page=page, token="").pack()))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=ReviewsCallbackData(action="page", category=category, page=page + 1, token="").pack()))
    nav.append(InlineKeyboardButton(text="🔄", callback_data=ReviewsCallbackData(action="refresh", category=category, page=page, token="").pack()))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCallbackData(section="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def review_card_keyboard(*, category: str, page: int, token: str, can_send: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="⬅️ К списку", callback_data=ReviewsCallbackData(action="list", category=category, page=page, token="").pack()),
            InlineKeyboardButton(text="🔄", callback_data=ReviewsCallbackData(action="open", category=category, page=page, token=token).pack()),
        ],
        [
            InlineKeyboardButton(text="🤖 ИИ-ответ", callback_data=ReviewsCallbackData(action="ai", category=category, page=page, token=token).pack()),
            InlineKeyboardButton(text="🛠 Пересобрать", callback_data=ReviewsCallbackData(action="reprompt", category=category, page=page, token=token).pack()),
        ],
        [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data=ReviewsCallbackData(action="manual", category=category, page=page, token=token).pack())],
    ]
    if can_send:
        rows.append([InlineKeyboardButton(text="📤 Отправить в Ozon", callback_data=ReviewsCallbackData(action="send", category=category, page=page, token=token).pack())])

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCallbackData(section="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def questions_list_keyboard(*, category: str, page: int, total_pages: int, items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    rows.append(
        [
            InlineKeyboardButton(text="Все", callback_data=QuestionsCallbackData(action="list", category="all", page=0, token="").pack()),
            InlineKeyboardButton(text="Без ответа", callback_data=QuestionsCallbackData(action="list", category="unanswered", page=0, token="").pack()),
            InlineKeyboardButton(text="С ответом", callback_data=QuestionsCallbackData(action="list", category="answered", page=0, token="").pack()),
        ]
    )

    for it in items:
        rows.append([InlineKeyboardButton(text=it["label"], callback_data=QuestionsCallbackData(action="open", category=category, page=page, token=it["token"]).pack())])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=QuestionsCallbackData(action="page", category=category, page=page - 1, token="").pack()))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{max(1, total_pages)}", callback_data=QuestionsCallbackData(action="noop", category=category, page=page, token="").pack()))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=QuestionsCallbackData(action="page", category=category, page=page + 1, token="").pack()))
    nav.append(InlineKeyboardButton(text="🔄", callback_data=QuestionsCallbackData(action="refresh", category=category, page=page, token="").pack()))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCallbackData(section="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def question_card_keyboard(*, category: str, page: int, token: str, can_send: bool, has_answer: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="⬅️ К списку", callback_data=QuestionsCallbackData(action="list", category=category, page=page, token="").pack()),
            InlineKeyboardButton(text="🔄", callback_data=QuestionsCallbackData(action="open", category=category, page=page, token=token).pack()),
        ],
        [
            InlineKeyboardButton(text="🤖 ИИ-ответ", callback_data=QuestionsCallbackData(action="ai", category=category, page=page, token=token).pack()),
            InlineKeyboardButton(text="🛠 Пересобрать", callback_data=QuestionsCallbackData(action="reprompt", category=category, page=page, token=token).pack()),
        ],
        [InlineKeyboardButton(text="✍️ Ввести вручную", callback_data=QuestionsCallbackData(action="manual", category=category, page=page, token=token).pack())],
        [InlineKeyboardButton(text="🧹 Очистить черновик", callback_data=QuestionsCallbackData(action="clear_draft", category=category, page=page, token=token).pack())],
    ]
    if has_answer:
        rows.append([InlineKeyboardButton(text="⬇️ Подставить ответ из Ozon", callback_data=QuestionsCallbackData(action="prefill", category=category, page=page, token=token).pack())])
    if can_send:
        rows.append([InlineKeyboardButton(text="📤 Отправить в Ozon", callback_data=QuestionsCallbackData(action="send", category=category, page=page, token=token).pack())])

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCallbackData(section="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chats_list_keyboard(*, page: int, total_pages: int, items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for it in items:
        title = it["title"]
        unread = int(it.get("unread_count") or 0)
        label = f"{title} {'• ' + str(unread) if unread > 0 else ''}".strip()
        rows.append([InlineKeyboardButton(text=label, callback_data=ChatCallbackData(action="open", page=page, token=it["token"]).pack())])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=ChatCallbackData(action="page", page=page - 1, token="").pack()))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{max(1, total_pages)}", callback_data=ChatCallbackData(action="noop", page=page, token="").pack()))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=ChatCallbackData(action="page", page=page + 1, token="").pack()))
    nav.append(InlineKeyboardButton(text="🔄", callback_data=ChatCallbackData(action="refresh", page=page, token="").pack()))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data=MenuCallbackData(section="home").pack())])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_header_keyboard(*, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data=ChatCallbackData(action="refresh_thread", page=0, token=token).pack()),
                InlineKeyboardButton(text="⬆️ Старые", callback_data=ChatCallbackData(action="older", page=0, token=token).pack()),
            ],
            [
                InlineKeyboardButton(text="🤖 ИИ-ответ", callback_data=ChatCallbackData(action="ai", page=0, token=token).pack()),
                InlineKeyboardButton(text="🛠 Пересобрать промпт", callback_data=ChatCallbackData(action="reprompt", page=0, token=token).pack()),
            ],
            [InlineKeyboardButton(text="⬅️ К чатам", callback_data=ChatCallbackData(action="exit", page=0, token=token).pack())],
        ]
    )


def chat_ai_draft_keyboard(*, token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Отправить", callback_data=ChatCallbackData(action="send_ai", page=0, token=token).pack()),
                InlineKeyboardButton(text="✍️ Редактировать", callback_data=ChatCallbackData(action="edit_ai", page=0, token=token).pack()),
            ],
            [
                InlineKeyboardButton(text="🤖 Перегенерировать", callback_data=ChatCallbackData(action="ai", page=0, token=token).pack()),
                InlineKeyboardButton(text="🧹 Очистить", callback_data=ChatCallbackData(action="clear", page=0, token=token).pack()),
            ],
        ]
    )
