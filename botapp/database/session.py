"""
Database session management
Async PostgreSQL connection через SQLAlchemy
"""

import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from botapp.database.models import Base

logger = logging.getLogger(__name__)

# Global engine и session maker
_engine = None
_async_session = None


async def init_db(database_url: str, echo: bool = False) -> None:
    """
    Инициализация базы данных
    
    Args:
        database_url: PostgreSQL connection string (e.g. postgresql+asyncpg://user:pass@host/db)
        echo: Логировать все SQL запросы
    """
    global _engine, _async_session
    
    logger.info(f"Инициализация PostgreSQL: {database_url.split('@')[1] if '@' in database_url else database_url}")
    
    _engine = create_async_engine(
        database_url,
        echo=echo,
        poolclass=NullPool,  # Для простоты, можно использовать QueuePool
        pool_pre_ping=True,  # Проверка подключения перед использованием
    )
    
    _async_session = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    # Создание таблиц (если не существуют)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("✅ PostgreSQL инициализирован успешно")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Получить async session для работы с БД
    
    Usage:
        async with get_session() as session:
            result = await session.execute(select(User))
    """
    if _async_session is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    async with _async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db() -> None:
    """Закрытие подключения к БД"""
    global _engine
    
    if _engine:
        await _engine.dispose()
        logger.info("PostgreSQL connection closed")
