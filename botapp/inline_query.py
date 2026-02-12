"""
Inline Query handler - поиск через @bot_name query
Позволяет искать товары, чаты, вопросы прямо в строке ввода
"""

import logging
from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from aiogram.utils.markdown import hbold

logger = logging.getLogger(__name__)

router = Router()


@router.inline_query()
async def handle_inline_query(inline_query: InlineQuery):
    """
    Обработка inline запросов
    
    Примеры:
        @bot_name iPhone      - поиск товаров
        @bot_name chat 123    - поиск чата
        @bot_name question    - поиск вопросов
    """
    query = inline_query.query.strip().lower()
    user_id = inline_query.from_user.id
    
    logger.info(f"Inline query от {user_id}: '{query}'")
    
    results = []
    
    # 1. Поиск по товарам
    if len(query) >= 3:
        product_results = await search_products(user_id, query)
        results.extend(product_results)
    
    # 2. Поиск по чатам
    if query.startswith("chat"):
        chat_results = await search_chats(user_id, query)
        results.extend(chat_results)
    
    # 3. Поиск по вопросам
    if query.startswith("question") or query.startswith("вопрос"):
        question_results = await search_questions(user_id, query)
        results.extend(question_results)
    
    # 4. Помощь если пусто
    if not results:
        results = get_help_results()
    
    # Отправка результатов
    await inline_query.answer(
        results[:50],  # Максимум 50 результатов
        cache_time=60,  # Кеш на 1 минуту
        is_personal=True,  # Результаты персональные
    )


async def search_products(user_id: int, query: str) -> list:
    """Поиск товаров по названию"""
    try:
        from botapp.products_service import ProductsCacheService
        
        service = ProductsCacheService()
        products = await service.get_cached_catalog(user_id)
        
        # Фильтрация по названию
        filtered = [
            p for p in products
            if query in p.get('offer_id', '').lower() or query in p.get('name', '').lower()
        ]
        
        results = []
        for idx, product in enumerate(filtered[:20]):  # Максимум 20 товаров
            results.append(
                InlineQueryResultArticle(
                    id=f"product_{idx}_{product.get('product_id')}",
                    title=product.get('name', 'Без названия')[:64],
                    description=f"Артикул: {product.get('offer_id', 'N/A')}",
                    input_message_content=InputTextMessageContent(
                        message_text=f"{hbold('Товар:')} {product.get('name', 'N/A')}\n"
                                     f"{hbold('Артикул:')} {product.get('offer_id', 'N/A')}\n"
                                     f"{hbold('ID:')} {product.get('product_id', 'N/A')}",
                        parse_mode="HTML",
                    ),
                )
            )
        
        logger.info(f"Найдено {len(results)} товаров по запросу '{query}'")
        return results
        
    except Exception as e:
        logger.error(f"Ошибка поиска товаров: {e}")
        return []


async def search_chats(user_id: int, query: str) -> list:
    """Поиск чатов"""
    try:
        from botapp.sections.chats.logic import fetch_chat_list_view
        
        view = await fetch_chat_list_view(user_id, page=0)
        
        results = []
        for idx, chat in enumerate(view.chats[:10]):
            results.append(
                InlineQueryResultArticle(
                    id=f"chat_{idx}_{chat.chat_id}",
                    title=f"Чат {chat.chat_id}",
                    description=f"Непрочитанных: {chat.unread_count}",
                    input_message_content=InputTextMessageContent(
                        message_text=f"{hbold('Чат ID:')} {chat.chat_id}\n"
                                     f"{hbold('Непрочитанных:')} {chat.unread_count}",
                        parse_mode="HTML",
                    ),
                )
            )
        
        return results
        
    except Exception as e:
        logger.error(f"Ошибка поиска чатов: {e}")
        return []


async def search_questions(user_id: int, query: str) -> list:
    """Поиск вопросов"""
    try:
        from botapp.sections.questions.logic import fetch_questions_list
        
        questions = await fetch_questions_list(user_id)
        
        results = []
        for idx, q in enumerate(questions[:10]):
            results.append(
                InlineQueryResultArticle(
                    id=f"question_{idx}_{q.get('id')}",
                    title=q.get('text', 'Вопрос')[:64],
                    description=f"Товар: {q.get('product_name', 'N/A')}",
                    input_message_content=InputTextMessageContent(
                        message_text=f"{hbold('Вопрос:')} {q.get('text', 'N/A')}\n"
                                     f"{hbold('Товар:')} {q.get('product_name', 'N/A')}",
                        parse_mode="HTML",
                    ),
                )
            )
        
        return results
        
    except Exception as e:
        logger.error(f"Ошибка поиска вопросов: {e}")
        return []


def get_help_results() -> list:
    """Результаты помощи"""
    return [
        InlineQueryResultArticle(
            id="help_1",
            title="📱 Как искать товары",
            description="Введите название или артикул",
            input_message_content=InputTextMessageContent(
                message_text=f"{hbold('Поиск товаров:')}\n"
                             "Просто введите название или артикул товара\n\n"
                             f"{hbold('Пример:')} @bot_name iPhone",
                parse_mode="HTML",
            ),
        ),
        InlineQueryResultArticle(
            id="help_2",
            title="💬 Как искать чаты",
            description="Введите 'chat'",
            input_message_content=InputTextMessageContent(
                message_text=f"{hbold('Поиск чатов:')}\n"
                             "Введите 'chat' для поиска всех чатов\n\n"
                             f"{hbold('Пример:')} @bot_name chat",
                parse_mode="HTML",
            ),
        ),
        InlineQueryResultArticle(
            id="help_3",
            title="❓ Как искать вопросы",
            description="Введите 'question'",
            input_message_content=InputTextMessageContent(
                message_text=f"{hbold('Поиск вопросов:')}\n"
                             "Введите 'question' или 'вопрос'\n\n"
                             f"{hbold('Пример:')} @bot_name question",
                parse_mode="HTML",
            ),
        ),
    ]
