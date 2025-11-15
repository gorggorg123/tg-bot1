from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

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
