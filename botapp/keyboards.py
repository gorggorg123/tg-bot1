from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🏦 Финансы за сегодня")],
        [KeyboardButton(text="📂 Аккаунт Ozon")],
        [KeyboardButton(text="📊 Полная аналитика")],
        [KeyboardButton(text="📦 FBO")],
        [KeyboardButton(text="⭐ Отзывы")],
        [KeyboardButton(text="🧠 ИИ")],
    ],
    resize_keyboard=True,
)

NOT_IMPLEMENTED_TEXT = (
    "Этот раздел ещё в разработке.\n\n"
    "Сейчас доступны:\n"
    "• «🏦 Финансы за сегодня»\n"
    "• «📦 FBO»"
)


def reviews_periods_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="reviews_today"),
                InlineKeyboardButton(text="7 дней", callback_data="reviews_week"),
                InlineKeyboardButton(text="Месяц", callback_data="reviews_month"),
            ],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="to_menu")],
        ]
    )


def reviews_navigation_keyboard(has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="◀ Назад", callback_data="reviews_prev" if has_prev else "reviews_prev"),
                InlineKeyboardButton(text="Далее ▶", callback_data="reviews_next" if has_next else "reviews_next"),
            ],
            [InlineKeyboardButton(text="✍ Черновик ответа", callback_data="reviews_ai_draft")],
            [InlineKeyboardButton(text="⬅ К периодам", callback_data="reviews_back")],
        ]
    )


def fbo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Сводка", callback_data="fbo_summary")],
            [InlineKeyboardButton(text="📅 Месяц", callback_data="fbo_month")],
            [InlineKeyboardButton(text="🔍 Фильтр", callback_data="fbo_filter")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="to_menu")],
        ]
    )
