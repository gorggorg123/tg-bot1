from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🏦 Финансы за сегодня")],
        [KeyboardButton(text="📦 Заказы за сегодня")],
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
    "• «📦 Заказы за сегодня»"
)


def reviews_periods_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сегодня", callback_data="reviews_today")],
            [InlineKeyboardButton(text="7 дней", callback_data="reviews_week")],
            [InlineKeyboardButton(text="Месяц", callback_data="reviews_month")],
        ]
    )
