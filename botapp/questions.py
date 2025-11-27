from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

# Router для раздела вопросов (чтобы main.py мог его подключить)
router = Router(name="questions")


@router.message(Command("questions"))
async def questions_unavailable(message: Message) -> None:
    """
    Простейший обработчик команды /questions.

    Сейчас мы не трогаем логику работы с Ozon API по вопросам,
    задача этого файла — чтобы бот успешно запускался.
    Позже сюда можно вернуть полноценную реализацию.
    """
    await message.answer(
        "Раздел вопросов временно недоступен.\n\n"
        "Сейчас я занимаюсь починкой отзывов и основной логики бота. "
        "Как только блок вопросов будет обновлён — он снова заработает."
    )


def register_question_token(question_id: str) -> str:
    """
    Backward-compatibility функция для старого кода клавиатур.

    Раньше, судя по имени, здесь мог регистрироваться токен вопроса,
    который потом использовался в callback_data.

    В новой версии, чтобы не ломать существующие импорты, функция
    просто приводит идентификатор вопроса к строке и возвращает его.
    Этого достаточно, чтобы импорт из botapp.keyboards не падал
    и чтобы, при необходимости, callback_data мог содержать этот ID.
    """
    return str(question_id)

