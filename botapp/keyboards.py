"""Набор клавиатур и фабрик callback_data для навигации бота."""

from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.ozon_client import has_write_credentials
from botapp.questions import register_question_token


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
    token: Optional[str] = None
    page: Optional[int] = None


class ChatsCallbackData(CallbackData, prefix="chats"):
    action: str
    chat_id: Optional[str] = None
    page: Optional[int] = None
    token: Optional[str] = None
    

class WarehouseCallbackData(CallbackData, prefix="warehouse"):
    action: str
    product_id: Optional[int] = None
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
                        category="all",
                        page=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💬 Чаты с покупателями",
                    callback_data=MenuCallbackData(
                        section="chats",
                        action="open",
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏬 Склад",
                    callback_data=MenuCallbackData(
                        section="warehouse",
                        action="open",
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


def warehouse_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Приёмка",
                    callback_data=WarehouseCallbackData(action="receive").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Отбор под заказ",
                    callback_data=WarehouseCallbackData(action="pick").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Инвентаризация",
                    callback_data=WarehouseCallbackData(action="inventory").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Риск остатков",
                    callback_data=WarehouseCallbackData(action="risk").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💡 Спросить ИИ",
                    callback_data=WarehouseCallbackData(action="ask_ai").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ В меню",
                    callback_data=MenuCallbackData(
                        section="home", action="open"
                    ).pack(),
                )
            ],
        ]
    )


def warehouse_receive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📂 Выбрать из списка",
                    callback_data=WarehouseCallbackData(action="receive_list", page=0).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 Найти по названию",
                    callback_data=WarehouseCallbackData(action="receive_search_name").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Найти по артикулу (SKU)",
                    callback_data=WarehouseCallbackData(action="receive_search_sku").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧠 Продиктовать текстом (ИИ)",
                    callback_data=WarehouseCallbackData(action="receive_ai").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ Назад",
                    callback_data=WarehouseCallbackData(action="receive_back").pack(),
                )
            ],
        ]
    )


def warehouse_catalog_keyboard(
    options: list[tuple[str, str]], page: int, total_pages: int
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for text, data in options:
        rows.append([InlineKeyboardButton(text=text, callback_data=data)])

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅",
                callback_data=WarehouseCallbackData(action="receive_list", page=page - 1).pack(),
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{max(total_pages, 1)}",
            callback_data=WarehouseCallbackData(action="noop").pack(),
        )
    )
    if page + 1 < total_pages:
        nav_row.append(
            InlineKeyboardButton(
                text="➡",
                callback_data=WarehouseCallbackData(action="receive_list", page=page + 1).pack(),
            )
        )
    rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить каталог",
                callback_data=WarehouseCallbackData(action="receive_list_refresh", page=page).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅ Назад",
                callback_data=WarehouseCallbackData(action="receive").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def warehouse_results_keyboard(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for text, data in options:
        rows.append([InlineKeyboardButton(text=text, callback_data=data)])
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅ Назад",
                callback_data=WarehouseCallbackData(action="receive").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def warehouse_labels_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, сделать файл",
                    callback_data=WarehouseCallbackData(action="labels_yes").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, только записать количество",
                    callback_data=WarehouseCallbackData(action="labels_no").pack(),
                )
            ],
        ]
    )


def warehouse_ai_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, всё верно",
                    callback_data=WarehouseCallbackData(action="receive_ai_confirm_yes").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Нет, изменить",
                    callback_data=WarehouseCallbackData(action="receive_ai_confirm_no").pack(),
                )
            ],
        ]
    )


def pick_plan_keyboard(posting_number: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отбор завершён",
                    callback_data=WarehouseCallbackData(action="pick_confirm").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=WarehouseCallbackData(action="pick_cancel").pack(),
                )
            ],
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


# ---------------------------------------------------------------------------
# Отзывы: навигация
# ---------------------------------------------------------------------------


def reviews_navigation_keyboard(
    category: str, index: int, total: int, review_id: str | None
) -> InlineKeyboardMarkup:
    """Старая сигнатура клавиатуры карточки (для обратной совместимости).

    Сейчас карточку строит :func:`review_card_keyboard`, но эту фабрику
    оставляем для совместимости с внешними вызовами.
    """

    page = index
    total_pages = total
    safe_total_pages = max(total_pages, 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏮️" if page > 0 else "◀️ Назад",
                    callback_data=ReviewsCallbackData(
                        action="list_page",
                        category=category,
                        page=max(page - 1, 0),
                        review_id=review_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"Стр. {page + 1}/{safe_total_pages}",
                    callback_data=ReviewsCallbackData(
                        action="noop", category=category, page=page
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
                    callback_data=ReviewsCallbackData(
                        action="list_page",
                        category=category,
                        page=min(page + 1, max(total_pages - 1, 0)),
                        review_id=review_id,
                    ).pack(),
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Отзывы: карточка
# ---------------------------------------------------------------------------


def review_card_keyboard(
    *,
    category: str,
    index: int,
    review_id: str | None,
    page: int = 0,
    can_send: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✉️ Ответ через ИИ",
                callback_data=ReviewsCallbackData(
                    action="card_ai",
                    category=category,
                    index=index,
                    review_id=review_id,
                    page=page,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Ввести ответ вручную",
                callback_data=ReviewsCallbackData(
                    action="card_manual",
                    category=category,
                    index=index,
                    review_id=review_id,
                    page=page,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="🔁 Пересобрать по моему промту",
                callback_data=ReviewsCallbackData(
                    action="card_reprompt",
                    category=category,
                    index=index,
                    review_id=review_id,
                    page=page,
                ).pack(),
            )
        ],
    ]

    if can_send and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Отправить на Ozon",
                    callback_data=ReviewsCallbackData(
                        action="send",
                        category=category,
                        index=index,
                        review_id=review_id,
                        page=page,
                    ).pack(),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку",
                    callback_data=ReviewsCallbackData(
                        action="list_page",
                        category=category,
                        index=index,
                        review_id=review_id,
                        page=page,
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
# Вопросы: карточка
# ---------------------------------------------------------------------------


def question_card_keyboard(
    *,
    category: str,
    page: int,
    token: str | None = None,
    can_send: bool = True,
    has_answer: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✉️ Ответ через ИИ",
                callback_data=QuestionsCallbackData(
                    action="card_ai",
                    category=category,
                    page=page,
                    token=token,
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
                    token=token,
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
                    token=token,
                ).pack(),
            )
        ],
    ]

    if has_answer:
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text="✏️ Обновить ответ",
                    callback_data=QuestionsCallbackData(
                        action="prefill",
                        category=category,
                        page=page,
                        token=token,
                    ).pack(),
                )
            ],
        )

    if can_send and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Отправить на Ozon",
                    callback_data=QuestionsCallbackData(
                        action="send",
                        category=category,
                        page=page,
                        token=token,
                    ).pack(),
                )
            ]
        )

    if has_answer and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить ответ",
                    callback_data=QuestionsCallbackData(
                        action="delete",
                        category=category,
                        page=page,
                        token=token,
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
                        action="page",
                        category=category,
                        page=page,
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
    items: list[tuple[str, str, int]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

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

    filter_row = [
        InlineKeyboardButton(
            text="Все",
            callback_data=ReviewsCallbackData(action="list", category="all", page=0).pack(),
        ),
        InlineKeyboardButton(
            text="Без ответа",
            callback_data=ReviewsCallbackData(action="list", category="unanswered", page=0).pack(),
        ),
        InlineKeyboardButton(
            text="С ответом",
            callback_data=ReviewsCallbackData(action="list", category="answered", page=0).pack(),
        ),
    ]

    safe_total_pages = max(total_pages, 1)
    nav_row = [
        InlineKeyboardButton(
            text="⏮️" if page > 0 else "◀️ Назад",
            callback_data=ReviewsCallbackData(
                action="list_page", category=category, page=max(page - 1, 0)
            ).pack(),
        ),
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{safe_total_pages}",
            callback_data=ReviewsCallbackData(action="noop", category=category, page=page).pack(),
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
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Вопросы: список
# ---------------------------------------------------------------------------


def questions_list_keyboard(
    *,
    user_id: int,
    category: str,
    page: int,
    total_pages: int,
    items: list[tuple[str, str, int]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for label, _unused_question_id, idx in items:
        token = register_question_token(user_id=user_id, category=category, index=idx)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=QuestionsCallbackData(
                        action="open",
                        category=category,
                        token=token,
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
                action="page",
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
                action="page",
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
                    text="✏️ Подредактировать", callback_data="edit_review"
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Чаты с покупателями
# ---------------------------------------------------------------------------


def chats_list_keyboard(
    *,
    items: list[tuple[str, str]],
    page: int,
    total_pages: int,
    unread_only: bool = False,
    show_service: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, caption in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=caption,
                    callback_data=ChatsCallbackData(action="open", chat_id=chat_id).pack(),
                )
            ]
        )

    safe_total = max(total_pages, 1)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔎 Только непрочитанные" if not unread_only else "📄 Все чаты",
                callback_data=ChatsCallbackData(action="filter", page=page).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="🛡️ Показывать служебные" if not show_service else "🙈 Скрыть служебные",
                callback_data=ChatsCallbackData(action="service", page=page).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️" if page > 0 else "⏮️",
                callback_data=ChatsCallbackData(action="list", page=max(page - 1, 0)).pack(),
            ),
            InlineKeyboardButton(
                text=f"Стр. {page + 1}/{safe_total}",
                callback_data=ChatsCallbackData(action="noop", page=page).pack(),
            ),
            InlineKeyboardButton(
                text="➡️" if page + 1 < total_pages else "⏭️",
                callback_data=ChatsCallbackData(
                    action="list", page=min(page + 1, max(total_pages - 1, 0))
                ).pack(),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_actions_keyboard(
    chat_id: str,
    *,
    attachments_total: int = 0,
    photo_count: int = 0,
    file_count: int = 0,
    oversized: bool = False,
    attachment_tokens: list[tuple[str, str, str | None]] | None = None,
    has_draft: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if attachments_total:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"📷 Фото ({photo_count})"
                        if oversized and photo_count
                        else f"📎 Вложения ({attachments_total})"
                    ),
                    callback_data=ChatsCallbackData(
                        action="media_photos" if oversized and photo_count else "media_all",
                        chat_id=chat_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"📄 Файлы ({file_count})",
                    callback_data=ChatsCallbackData(action="media_files", chat_id=chat_id).pack(),
                ),
            ]
        )
        if oversized:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⬇️ Скачать всё",
                        callback_data=ChatsCallbackData(action="media_all", chat_id=chat_id).pack(),
                    )
                ]
            )

    if attachment_tokens:
        for token, label, _kind in attachment_tokens:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=ChatsCallbackData(
                            action="file",
                            chat_id=chat_id,
                            token=token,
                        ).pack(),
                    )
                ]
            )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✏️ Ввести вручную",
                    callback_data=ChatsCallbackData(action="manual", chat_id=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✉️ Ответ через ИИ",
                    callback_data=ChatsCallbackData(action="ai", chat_id=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔁 Пересобрать по моему промту",
                    callback_data=ChatsCallbackData(action="reprompt", chat_id=chat_id).pack(),
                )
            ],
        ]
    )

    if has_draft:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Отправить",
                    callback_data=ChatsCallbackData(action="draft_send", chat_id=chat_id).pack(),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🔄 Обновить чат",
                    callback_data=ChatsCallbackData(action="refresh", chat_id=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку чатов",
                    callback_data=ChatsCallbackData(action="list", page=0).pack(),
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


def chat_ai_confirm_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return chat_draft_keyboard(chat_id)


def chat_draft_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить",
                    callback_data=ChatsCallbackData(action="draft_send", chat_id=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔁 Пересобрать по моему промту",
                    callback_data=ChatsCallbackData(action="reprompt", chat_id=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить вручную",
                    callback_data=ChatsCallbackData(action="draft_edit", chat_id=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к чату",
                    callback_data=ChatsCallbackData(action="open", chat_id=chat_id).pack(),
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
    "ChatsCallbackData",
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
    "chats_list_keyboard",
    "chat_actions_keyboard",
    "chat_ai_confirm_keyboard",
    "chat_draft_keyboard",
    "account_keyboard",
]
