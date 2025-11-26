"""Набор клавиатур и фабрик callback_data для навигации бота."""

from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.ozon_client import has_write_credentials


class MenuCallbackData(CallbackData, prefix="menu"):
    """Универсальный callback для внутренних меню.

    section: название раздела (reviews, fbo, account, home, fin_today)
    action: действие внутри раздела (open/summary/month/filter/etc)
    extra: дополнительный параметр (период, индекс и т.д.)
    """

    section: str
    action: str
    extra: Optional[str] = None


class ReviewsCallbackData(CallbackData, prefix="reviews"):
    """Callback для раздела отзывов.

    action:
      - list / list_page      — показать список
      - open_card             — открыть карточку
      - card_ai               — сгенерировать ответ ИИ
      - card_reprompt         — запросить промпт от пользователя и пересобрать
      - card_manual           — ручной ввод ответа
      - send                  — отправка ответа на Ozon
      - regen                 — пересоздать черновик ответа
      - edit                  — отредактировать черновик
      - nav                   — вернуться к карточке
      - noop                  — кнопка без действия (страница N/M)
    """

    action: str
    category: Optional[str] = None
    index: Optional[int] = None
    review_id: Optional[str] = None
    page: Optional[int] = None


class QuestionsCallbackData(CallbackData, prefix="questions"):
    action: str
    category: Optional[str] = None
    index: Optional[int] = None
    question_id: Optional[str] = None
    page: Optional[int] = None


# ---------------------------------------------------------------------------
# Главное меню
# ---------------------------------------------------------------------------


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню главных разделов."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Финансы сегодня",
                    callback_data=MenuCallbackData(
                        section="fin_today",
                        action="open",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 FBO за сегодня",
                    callback_data=MenuCallbackData(
                        section="fbo",
                        action="summary",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Отзывы",
                    callback_data=ReviewsCallbackData(
                        action="list",
                        category="all",
                        page=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❓ Вопросы",
                    callback_data=QuestionsCallbackData(
                        action="list",
                        category="unanswered",
                        page=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Аккаунт Ozon",
                    callback_data=MenuCallbackData(
                        section="account",
                        action="open",
                    ).pack(),
                )
            ],
        ]
    )


def back_home_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой возврата в главное меню."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(
                        section="home",
                        action="open",
                    ).pack(),
                )
            ]
        ]
    )


# ---------------------------------------------------------------------------
# FBO
# ---------------------------------------------------------------------------


def fbo_menu_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню раздела FBO."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Сводка",
                    callback_data=MenuCallbackData(
                        section="fbo",
                        action="summary",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Месяц",
                    callback_data=MenuCallbackData(
                        section="fbo",
                        action="month",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 Фильтр",
                    callback_data=MenuCallbackData(
                        section="fbo",
                        action="filter",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(
                        section="home",
                        action="open",
                    ).pack(),
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Отзывы: корень и карточка
# ---------------------------------------------------------------------------


def reviews_root_keyboard() -> InlineKeyboardMarkup:
    """Простое меню раздела отзывов (используется редко)."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    # сразу берём актуальный список без ответа
                    text="🔄 Обновить отзывы",
                    callback_data=ReviewsCallbackData(
                        action="list",
                        category="unanswered",
                        page=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(
                        section="home",
                        action="open",
                    ).pack(),
                )
            ],
        ]
    )


def reviews_navigation_keyboard(
    category: str,
    index: int,
    total: int,
    review_id: str | None,
) -> InlineKeyboardMarkup:
    """Старая сигнатура клавиатуры карточки (для обратной совместимости).

    Сейчас просто прокидываем в новую фабрику, считаем, что page = 0.
    """

    return review_card_keyboard(
        category=category,
        page=0,
        review_id=review_id,
    )


def review_card_keyboard(
    *,
    category: str,
    page: int,
    review_id: str | None,
    can_send: bool = True,
) -> InlineKeyboardMarkup:
    """Кнопки под карточкой отзыва."""

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✉️ Ответ через ИИ",
                callback_data=ReviewsCallbackData(
                    action="card_ai",
                    category=category,
                    page=page,
                    review_id=review_id,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="🔁 Пересобрать по моему промту",
                callback_data=ReviewsCallbackData(
                    action="card_reprompt",
                    category=category,
                    page=page,
                    review_id=review_id,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Ввести ответ вручную",
                callback_data=ReviewsCallbackData(
                    action="card_manual",
                    category=category,
                    page=page,
                    review_id=review_id,
                ).pack(),
            )
        ],
    ]

    # Кнопка отправки только если есть права и это актуальный магазин
    if can_send and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Отправить на Ozon",
                    callback_data=ReviewsCallbackData(
                        action="send",
                        category=category,
                        page=page,
                        review_id=review_id,
                    ).pack(),
                )
            ]
        )

    # Навигация
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку",
                    callback_data=ReviewsCallbackData(
                        action="list_page",
                        category=category,
                        page=page,
                        review_id=review_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(
                        section="home",
                        action="open",
                    ).pack(),
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def question_card_keyboard(
    *,
    category: str,
    page: int,
    question_id: str | None,
    can_send: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✉️ Ответ через ИИ",
                callback_data=QuestionsCallbackData(
                    action="card_ai",
                    category=category,
                    page=page,
                    question_id=question_id,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Ввести ответ вручную",
                callback_data=QuestionsCallbackData(
                    action="card_manual",
                    category=category,
                    page=page,
                    question_id=question_id,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="🔁 Пересобрать по моему промту",
                callback_data=QuestionsCallbackData(
                    action="card_reprompt",
                    category=category,
                    page=page,
                    question_id=question_id,
                ).pack(),
            )
        ],
    ]

    if can_send and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Отправить на Ozon",
                    callback_data=QuestionsCallbackData(
                        action="send",
                        category=category,
                        page=page,
                        question_id=question_id,
                    ).pack(),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку",
                    callback_data=QuestionsCallbackData(
                        action="list_page",
                        category=category,
                        page=page,
                        question_id=question_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Отзывы: список
# ---------------------------------------------------------------------------


def reviews_list_keyboard(
    *,
    category: str,
    page: int,
    total_pages: int,
    items: list[tuple[str, str | None, int]],
) -> InlineKeyboardMarkup:
    """Клавиатура списка отзывов.

    items: список кортежей вида (label, review_id, index)
    """

    rows: list[list[InlineKeyboardButton]] = []

    # Кнопки самих отзывов
    for label, review_id, idx in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=ReviewsCallbackData(
                        action="open_card",
                        category=category,
                        index=idx,
                        review_id=review_id,
                        page=page,
                    ).pack(),
                )
            ]
        )

    # Фильтры по статусу
    filter_row = [
        InlineKeyboardButton(
            text="Все",
            callback_data=ReviewsCallbackData(
                action="list",
                category="all",
                page=0,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="Без ответа",
            callback_data=ReviewsCallbackData(
                action="list",
                category="unanswered",
                page=0,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="С ответом",
            callback_data=ReviewsCallbackData(
                action="list",
                category="answered",
                page=0,
            ).pack(),
        ),
    ]

    # Постраничная навигация
    safe_total_pages = max(total_pages, 1)

    nav_row = [
        InlineKeyboardButton(
            text="◀️ Назад" if page > 0 else "⏮️",
            callback_data=ReviewsCallbackData(
                action="list_page",
                category=category,
                page=max(page - 1, 0),
            ).pack(),
        ),
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{safe_total_pages}",
            callback_data=ReviewsCallbackData(
                action="noop",
                category=category,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
            callback_data=ReviewsCallbackData(
                action="list_page",
                category=category,
                page=min(page + 1, max(total_pages - 1, 0)),
            ).pack(),
        ),
    ]

    rows.append(filter_row)
    rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В главное меню",
                callback_data=MenuCallbackData(
                    section="home",
                    action="open",
                ).pack(),
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def questions_list_keyboard(
    *,
    category: str,
    page: int,
    total_pages: int,
    items: list[tuple[str, str, int]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for label, question_id, idx in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=QuestionsCallbackData(
                        action="open_card",
                        category=category,
                        index=idx,
                        question_id=question_id,
                        page=page,
                    ).pack(),
                )
            ]
        )

    filter_row = [
        InlineKeyboardButton(
            text="Все",
            callback_data=QuestionsCallbackData(action="list", category="all", page=0).pack(),
        ),
        InlineKeyboardButton(
            text="Без ответа",
            callback_data=QuestionsCallbackData(action="list", category="unanswered", page=0).pack(),
        ),
        InlineKeyboardButton(
            text="С ответом",
            callback_data=QuestionsCallbackData(action="list", category="answered", page=0).pack(),
        ),
    ]

    safe_total_pages = max(total_pages, 1)
    nav_row = [
        InlineKeyboardButton(
            text="⏮️" if page > 0 else "◀️ Назад",
            callback_data=QuestionsCallbackData(
                action="list_page",
                category=category,
                page=max(page - 1, 0),
            ).pack(),
        ),
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{safe_total_pages}",
            callback_data=QuestionsCallbackData(action="noop", category=category, page=page).pack(),
        ),
        InlineKeyboardButton(
            text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
            callback_data=QuestionsCallbackData(
                action="list_page",
                category=category,
                page=min(page + 1, max(total_pages - 1, 0)),
            ).pack(),
        ),
    ]

    rows.append(filter_row)
    rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В главное меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Отзывы: черновик ответа
# ---------------------------------------------------------------------------


def review_draft_keyboard(
    category: str,
    index: int,
    review_id: str | None,
) -> InlineKeyboardMarkup:
    """Кнопки под черновиком ответа (после генерации ИИ)."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 Отправить как есть",
                    callback_data=ReviewsCallbackData(
                        action="send",
                        category=category,
                        index=index,
                        review_id=review_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="♻️ Сгенерировать ещё",
                    callback_data=ReviewsCallbackData(
                        action="regen",
                        category=category,
                        index=index,
                        review_id=review_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Отредактировать",
                    callback_data=ReviewsCallbackData(
                        action="edit",
                        category=category,
                        index=index,
                        review_id=review_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к отзыву",
                    callback_data=ReviewsCallbackData(
                        action="nav",
                        category=category,
                        index=index,
                        review_id=review_id,
                    ).pack(),
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Аккаунт
# ---------------------------------------------------------------------------


def account_keyboard() -> InlineKeyboardMarkup:
    """Пока просто кнопка возврата домой — можно расширить позже."""

    return back_home_keyboard()


__all__ = [
    "MenuCallbackData",
    "ReviewsCallbackData",
    "QuestionsCallbackData",
    "main_menu_keyboard",
    "back_home_keyboard",
    "fbo_menu_keyboard",
    "reviews_root_keyboard",
    "reviews_navigation_keyboard",
    "review_card_keyboard",
    "reviews_list_keyboard",
    "questions_list_keyboard",
    "question_card_keyboard",
    "review_draft_keyboard",
    "account_keyboard",
]
