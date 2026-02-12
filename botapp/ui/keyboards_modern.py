"""
Modern Keyboards with Beautiful Design
======================================
Современные клавиатуры с красивым дизайном, emoji и группировкой

Версия: 2.0
Дата: 2026-01-23
"""
from __future__ import annotations

from typing import Optional, List, Dict, Any
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters.callback_data import CallbackData


# ===== Callback Data =====

class ModernMenuCallback(CallbackData, prefix="modern"):
    """Современный callback с расширенной информацией"""
    section: str
    action: str
    id: Optional[str] = None
    page: Optional[int] = None
    extra: Optional[str] = None


class NavigationCallback(CallbackData, prefix="nav"):
    """Callback для навигации"""
    action: str  # back, home, next, prev
    target: Optional[str] = None  # куда вернуться


# ===== Главное меню =====

def main_menu_modern(
    *,
    reviews_count: int = 0,
    questions_count: int = 0,
    chats_count: int = 0,
    orders_count: int = 0,
) -> InlineKeyboardMarkup:
    """
    Современное главное меню с счетчиками и красивой группировкой
    
    Args:
        reviews_count: Количество новых отзывов
        questions_count: Количество новых вопросов
        chats_count: Количество непрочитанных чатов
        orders_count: Количество новых заказов
    
    Returns:
        Клавиатура главного меню
    """
    # Форматирование счетчиков
    reviews_badge = f" ({reviews_count})" if reviews_count > 0 else ""
    questions_badge = f" ({questions_count})" if questions_count > 0 else ""
    chats_badge = f" ({chats_count})" if chats_count > 0 else ""
    orders_badge = f" ({orders_count})" if orders_count > 0 else ""
    
    keyboard = [
        # 📊 Дашборд (во всю ширину)
        [
            InlineKeyboardButton(
                text="📊 Дашборд и Аналитика",
                callback_data=ModernMenuCallback(
                    section="dashboard",
                    action="open"
                ).pack()
            )
        ],
        # 💬 Коммуникация с покупателями
        [
            InlineKeyboardButton(
                text=f"⭐ Отзывы{reviews_badge}",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="list"
                ).pack()
            ),
            InlineKeyboardButton(
                text=f"❓ Вопросы{questions_badge}",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="list"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"💬 Чаты{chats_badge}",
                callback_data=ModernMenuCallback(
                    section="chats",
                    action="list"
                ).pack()
            ),
        ],
        # 📦 Склад и заказы
        [
            InlineKeyboardButton(
                text=f"📦 ФБО{orders_badge}",
                callback_data=ModernMenuCallback(
                    section="fbo",
                    action="list"
                ).pack()
            ),
            InlineKeyboardButton(
                text="🚚 ФБС",
                callback_data=ModernMenuCallback(
                    section="fbs",
                    action="list"
                ).pack()
            ),
        ],
        # 💰 Финансы и аналитика
        [
            InlineKeyboardButton(
                text="💰 Финансы",
                callback_data=ModernMenuCallback(
                    section="finance",
                    action="summary"
                ).pack()
            ),
            InlineKeyboardButton(
                text="📈 Статистика",
                callback_data=ModernMenuCallback(
                    section="stats",
                    action="summary"
                ).pack()
            ),
        ],
        # 🛍️ Каталог
        [
            InlineKeyboardButton(
                text="🛍️ Товары и Остатки",
                callback_data=ModernMenuCallback(
                    section="products",
                    action="list"
                ).pack()
            ),
        ],
        # ⚙️ Настройки и помощь
        [
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data=ModernMenuCallback(
                    section="settings",
                    action="open"
                ).pack()
            ),
            InlineKeyboardButton(
                text="❔ Помощь",
                callback_data=ModernMenuCallback(
                    section="help",
                    action="open"
                ).pack()
            ),
        ],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Отзывы =====

def reviews_menu_modern(
    *,
    unanswered_count: int = 0,
    answered_count: int = 0,
) -> InlineKeyboardMarkup:
    """
    Меню отзывов с фильтрами
    
    Args:
        unanswered_count: Количество без ответа
        answered_count: Количество с ответом
    """
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"⏳ Без ответа ({unanswered_count})",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="list",
                    extra="unanswered"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"✅ С ответом ({answered_count})",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="list",
                    extra="answered"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="📋 Все отзывы",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="list",
                    extra="all"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data=NavigationCallback(
                    action="home"
                ).pack()
            ),
        ],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def review_actions_keyboard(
    review_id: str,
    *,
    has_comment: bool = False,
    back_target: str = "reviews"
) -> InlineKeyboardMarkup:
    """
    Действия с конкретным отзывом
    
    Args:
        review_id: ID отзыва
        has_comment: Есть ли уже комментарий
        back_target: Куда вернуться назад
    """
    keyboard = []
    
    if not has_comment:
        keyboard.append([
            InlineKeyboardButton(
                text="✍️ Написать ответ",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="reply",
                    id=review_id
                ).pack()
            ),
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="🤖 Сгенерировать с AI",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="ai_generate",
                    id=review_id
                ).pack()
            ),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="👁️ Посмотреть ответ",
                callback_data=ModernMenuCallback(
                    section="reviews",
                    action="view_comment",
                    id=review_id
                ).pack()
            ),
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ К списку отзывов",
            callback_data=NavigationCallback(
                action="back",
                target=back_target
            ).pack()
        ),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Вопросы =====

def questions_menu_modern(
    *,
    unanswered_count: int = 0,
    answered_count: int = 0,
) -> InlineKeyboardMarkup:
    """Меню вопросов с фильтрами"""
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"⏳ Без ответа ({unanswered_count})",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="list",
                    extra="unanswered"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"✅ С ответом ({answered_count})",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="list",
                    extra="answered"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="📋 Все вопросы",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="list",
                    extra="all"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data=NavigationCallback(action="home").pack()
            ),
        ],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def question_actions_keyboard(
    question_id: str,
    *,
    has_answer: bool = False,
    back_target: str = "questions"
) -> InlineKeyboardMarkup:
    """Действия с конкретным вопросом"""
    keyboard = []
    
    if not has_answer:
        keyboard.append([
            InlineKeyboardButton(
                text="✍️ Написать ответ",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="reply",
                    id=question_id
                ).pack()
            ),
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="🤖 Сгенерировать с AI",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="ai_generate",
                    id=question_id
                ).pack()
            ),
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                text="👁️ Посмотреть ответ",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="view_answer",
                    id=question_id
                ).pack()
            ),
        ])
        keyboard.append([
            InlineKeyboardButton(
                text="✏️ Редактировать ответ",
                callback_data=ModernMenuCallback(
                    section="questions",
                    action="edit_answer",
                    id=question_id
                ).pack()
            ),
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ К списку вопросов",
            callback_data=NavigationCallback(
                action="back",
                target=back_target
            ).pack()
        ),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Чаты =====

def chats_menu_modern(
    *,
    unread_count: int = 0,
    all_count: int = 0,
) -> InlineKeyboardMarkup:
    """Меню чатов"""
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"💬 Непрочитанные ({unread_count})",
                callback_data=ModernMenuCallback(
                    section="chats",
                    action="list",
                    extra="unread"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"📋 Все чаты ({all_count})",
                callback_data=ModernMenuCallback(
                    section="chats",
                    action="list",
                    extra="all"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=ModernMenuCallback(
                    section="chats",
                    action="refresh"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data=NavigationCallback(action="home").pack()
            ),
        ],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def chat_actions_keyboard(
    chat_id: str,
    *,
    is_read: bool = False,
    back_target: str = "chats"
) -> InlineKeyboardMarkup:
    """Действия с конкретным чатом"""
    keyboard = []
    
    keyboard.append([
        InlineKeyboardButton(
            text="💬 Написать сообщение",
            callback_data=ModernMenuCallback(
                section="chats",
                action="reply",
                id=chat_id
            ).pack()
        ),
    ])
    
    if not is_read:
        keyboard.append([
            InlineKeyboardButton(
                text="✅ Отметить прочитанным",
                callback_data=ModernMenuCallback(
                    section="chats",
                    action="mark_read",
                    id=chat_id
                ).pack()
            ),
        ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="🔄 Обновить историю",
            callback_data=ModernMenuCallback(
                section="chats",
                action="refresh",
                id=chat_id
            ).pack()
        ),
    ])
    
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ К списку чатов",
            callback_data=NavigationCallback(
                action="back",
                target=back_target
            ).pack()
        ),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Пагинация =====

def pagination_keyboard(
    section: str,
    action: str,
    *,
    current_page: int,
    total_pages: int,
    show_numbers: bool = True,
) -> InlineKeyboardMarkup:
    """
    Клавиатура пагинации
    
    Args:
        section: Секция (reviews, questions, chats, etc.)
        action: Действие (list, etc.)
        current_page: Текущая страница (0-indexed)
        total_pages: Всего страниц
        show_numbers: Показывать номера страниц
    """
    keyboard = []
    
    # Кнопки навигации
    nav_row = []
    
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=ModernMenuCallback(
                    section=section,
                    action=action,
                    page=current_page - 1
                ).pack()
            )
        )
    
    if show_numbers:
        nav_row.append(
            InlineKeyboardButton(
                text=f"📄 {current_page + 1}/{total_pages}",
                callback_data="noop"
            )
        )
    
    if current_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=ModernMenuCallback(
                    section=section,
                    action=action,
                    page=current_page + 1
                ).pack()
            )
        )
    
    if nav_row:
        keyboard.append(nav_row)
    
    # Кнопка "Назад к списку" или "В главное меню"
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ Главное меню",
            callback_data=NavigationCallback(action="home").pack()
        ),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Подтверждение действий =====

def confirmation_keyboard(
    section: str,
    action: str,
    item_id: str,
    *,
    confirm_text: str = "✅ Подтвердить",
    cancel_text: str = "❌ Отменить",
    back_target: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения действия"""
    keyboard = [
        [
            InlineKeyboardButton(
                text=confirm_text,
                callback_data=ModernMenuCallback(
                    section=section,
                    action=f"{action}_confirm",
                    id=item_id
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text=cancel_text,
                callback_data=NavigationCallback(
                    action="back",
                    target=back_target or section
                ).pack()
            ),
        ],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Настройки =====

def settings_menu_modern() -> InlineKeyboardMarkup:
    """Меню настроек"""
    keyboard = [
        [
            InlineKeyboardButton(
                text="🔔 Уведомления",
                callback_data=ModernMenuCallback(
                    section="settings",
                    action="notifications"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="🤖 AI Настройки",
                callback_data=ModernMenuCallback(
                    section="settings",
                    action="ai"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="🔑 API Ключи",
                callback_data=ModernMenuCallback(
                    section="settings",
                    action="api_keys"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="📊 Метрики",
                callback_data=ModernMenuCallback(
                    section="settings",
                    action="metrics"
                ).pack()
            ),
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data=NavigationCallback(action="home").pack()
            ),
        ],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# ===== Утилиты =====

def back_button(target: str = "home", text: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    """Простая кнопка "Назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=text,
                callback_data=NavigationCallback(
                    action="back",
                    target=target
                ).pack()
            )
        ]
    ])


def home_button(text: str = "🏠 Главное меню") -> InlineKeyboardMarkup:
    """Простая кнопка "Домой"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=text,
                callback_data=NavigationCallback(action="home").pack()
            )
        ]
    ])


__all__ = [
    "ModernMenuCallback",
    "NavigationCallback",
    "main_menu_modern",
    "reviews_menu_modern",
    "review_actions_keyboard",
    "questions_menu_modern",
    "question_actions_keyboard",
    "chats_menu_modern",
    "chat_actions_keyboard",
    "pagination_keyboard",
    "confirmation_keyboard",
    "settings_menu_modern",
    "back_button",
    "home_button",
]
