# main.py (фрагменты)

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from botapp.tg import main_menu_kb
from botapp.finance import get_finance_today_text
from botapp.orders import get_orders_today_text
from botapp.account import get_account_info_text
from botapp.reviews import (
    get_reviews_menu_text,
    get_reviews_today_text,
    get_reviews_week_text,
    get_reviews_month_text,
)

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = "Добро пожаловать! Выберите раздел в меню или используйте команды."
    await message.answer(text, reply_markup=main_menu_kb())


@router.message(Command("reviews_today"))
async def cmd_reviews_today(message: Message) -> None:
    text = await get_reviews_today_text()
    await message.answer(text)


@router.message(Command("reviews_week"))
async def cmd_reviews_week(message: Message) -> None:
    text = await get_reviews_week_text()
    await message.answer(text)


@router.message(Command("reviews_month"))
async def cmd_reviews_month(message: Message) -> None:
    text = await get_reviews_month_text()
    await message.answer(text)


# --- callbacks ---

@router.callback_query(F.data == "fin_today")
async def cb_fin_today(callback: CallbackQuery) -> None:
    await callback.answer()  # закрываем часы
    text = await get_finance_today_text()
    await callback.message.answer(text)


@router.callback_query(F.data == "orders_today")
async def cb_orders_today(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_orders_today_text()
    await callback.message.answer(text)


@router.callback_query(F.data == "account_info")
async def cb_account_info(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_account_info_text()
    await callback.message.answer(text)


@router.callback_query(F.data == "full_analytics")
async def cb_full_analytics(callback: CallbackQuery) -> None:
    await callback.answer()
    # пока заглушка, позже допилим по Ульянову
    await callback.message.answer("📊 Полная аналитика скоро будет доступна.")


@router.callback_query(F.data == "reviews")
async def cb_reviews(callback: CallbackQuery) -> None:
    await callback.answer()
    text = await get_reviews_menu_text()
    await callback.message.answer(text)
