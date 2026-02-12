"""
Prefetch module - предзагрузка данных для ускорения работы
Загружает часто используемые данные в кеш при старте бота
"""

import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)


class PrefetchManager:
    """
    Менеджер предзагрузки данных
    
    Загружает в кеш:
    - Список всех товаров
    - Активные чаты
    - Непрочитанные сообщения
    - Новые вопросы/отзывы
    """
    
    def __init__(self):
        self.is_ready = False
        self._prefetch_task: Optional[asyncio.Task] = None
    
    async def start_prefetch(self, user_id: int):
        """
        Запуск предзагрузки для пользователя
        
        Args:
            user_id: ID пользователя Telegram
        """
        if self._prefetch_task and not self._prefetch_task.done():
            logger.warning("Prefetch уже запущен")
            return
        
        logger.info(f"🚀 Запуск prefetch для пользователя {user_id}")
        self._prefetch_task = asyncio.create_task(self._prefetch_all(user_id))
    
    async def _prefetch_all(self, user_id: int):
        """Основная логика предзагрузки"""
        try:
            # 1. Загрузка каталога товаров
            await self._prefetch_products(user_id)
            
            # 2. Загрузка списка чатов
            await self._prefetch_chats(user_id)
            
            # 3. Загрузка вопросов
            await self._prefetch_questions(user_id)
            
            # 4. Загрузка отзывов
            await self._prefetch_reviews(user_id)
            
            self.is_ready = True
            logger.info("✅ Prefetch завершён успешно")
            
        except Exception as e:
            logger.error(f"❌ Ошибка prefetch: {e}", exc_info=True)
            self.is_ready = False
    
    async def _prefetch_products(self, user_id: int):
        """Предзагрузка каталога товаров"""
        logger.info("📦 Prefetch: загрузка каталога товаров...")
        
        try:
            from botapp.products_service import ProductsCacheService
            
            service = ProductsCacheService()
            products = await service.get_cached_catalog(user_id)
            
            logger.info(f"✅ Загружено {len(products)} товаров")
        except Exception as e:
            logger.error(f"Ошибка загрузки товаров: {e}")
    
    async def _prefetch_chats(self, user_id: int):
        """Предзагрузка списка чатов"""
        logger.info("💬 Prefetch: загрузка чатов...")
        
        try:
            from botapp.sections.chats.logic import fetch_chat_list_view
            
            view = await fetch_chat_list_view(user_id, page=0)
            
            logger.info(f"✅ Загружено {view.total_count} чатов")
        except Exception as e:
            logger.error(f"Ошибка загрузки чатов: {e}")
    
    async def _prefetch_questions(self, user_id: int):
        """Предзагрузка списка вопросов"""
        logger.info("❓ Prefetch: загрузка вопросов...")
        
        try:
            from botapp.sections.questions.logic import fetch_questions_list
            
            questions = await fetch_questions_list(user_id)
            
            logger.info(f"✅ Загружено {len(questions)} вопросов")
        except Exception as e:
            logger.error(f"Ошибка загрузки вопросов: {e}")
    
    async def _prefetch_reviews(self, user_id: int):
        """Предзагрузка списка отзывов"""
        logger.info("⭐ Prefetch: загрузка отзывов...")
        
        try:
            from botapp.sections.reviews.logic import fetch_recent_reviews
            
            reviews = await fetch_recent_reviews(user_id)
            
            logger.info(f"✅ Загружено {len(reviews)} отзывов")
        except Exception as e:
            logger.error(f"Ошибка загрузки отзывов: {e}")
    
    async def wait_ready(self, timeout: float = 30.0):
        """
        Ожидание завершения prefetch
        
        Args:
            timeout: Максимальное время ожидания (сек)
        
        Returns:
            True если prefetch завершён, False если timeout
        """
        if self.is_ready:
            return True
        
        if not self._prefetch_task:
            return False
        
        try:
            await asyncio.wait_for(self._prefetch_task, timeout=timeout)
            return self.is_ready
        except asyncio.TimeoutError:
            logger.warning(f"Prefetch не завершился за {timeout}с")
            return False


# Глобальный экземпляр
_prefetch_manager: Optional[PrefetchManager] = None


def get_prefetch_manager() -> PrefetchManager:
    """Получить глобальный PrefetchManager"""
    global _prefetch_manager
    
    if _prefetch_manager is None:
        _prefetch_manager = PrefetchManager()
    
    return _prefetch_manager


async def start_prefetch_for_user(user_id: int):
    """
    Запустить prefetch для пользователя
    
    Usage:
        # При старте бота или /start команды
        await start_prefetch_for_user(message.from_user.id)
    """
    manager = get_prefetch_manager()
    await manager.start_prefetch(user_id)
