#!/usr/bin/env python3
"""Тестовый скрипт для создания чата с постингом со статусом 'доставляется'.

Использование:
    python scripts/test_chat_delivering.py [posting_number]

Если posting_number не указан, скрипт найдет первый постинг со статусом 'delivering_to_customer' за сегодня.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

from botapp.config import load_ozon_config
from botapp.ozon_client import (
    chat_start,
    chat_history,
    get_client,
    get_posting_details,
)

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("test_chat_delivering")


async def find_delivering_posting_today() -> str | None:
    """Находит первый постинг со статусом 'delivering_to_customer' за сегодня."""
    client = get_client()
    
    # Получаем постинги за сегодня
    now = datetime.now(timezone.utc)
    date_from = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    date_to = now.isoformat()
    
    logger.info("Поиск постингов за сегодня (с %s по %s)...", date_from, date_to)
    
    fbo_postings = await client.get_fbo_postings(date_from, date_to)
    logger.info("Найдено постингов: %s", len(fbo_postings or []))
    
    # Ищем постинг со статусом "delivering_to_customer"
    for posting in fbo_postings or []:
        if not isinstance(posting, dict):
            continue
        
        posting_number = posting.get("posting_number") or posting.get("order_number")
        status = (posting.get("status") or "").lower().strip()
        
        logger.info("Постинг %s: статус = %s", posting_number, status)
        
        if status == "delivering_to_customer":
            logger.info("✅ Найден постинг со статусом 'delivering_to_customer': %s", posting_number)
            return str(posting_number)
    
    logger.warning("Не найдено постингов со статусом 'delivering_to_customer' за сегодня")
    return None


async def test_create_chat(posting_number: str) -> None:
    """Тестирует создание чата для указанного постинга."""
    logger.info("=" * 60)
    logger.info("Тест создания чата для постинга: %s", posting_number)
    logger.info("=" * 60)
    
    # Получаем детали постинга
    logger.info("1. Получение деталей постинга...")
    try:
        details, schema = await get_posting_details(posting_number)
        if details:
            status = (details.get("status") or "").lower().strip()
            logger.info("   Статус постинга: %s (schema: %s)", status, schema)
            
                # Показываем доступные поля с датами
            date_fields = [k for k in details.keys() if "date" in k.lower() or "at" in k.lower() or "time" in k.lower()]
            if date_fields:
                logger.info("   Поля с датами/временем: %s", ", ".join(date_fields))
                for field in date_fields:  # Показываем все поля
                    value = details.get(field)
                    logger.info("     - %s = %r", field, value)  # Показываем даже None для отладки
        else:
            logger.warning("   Не удалось получить детали постинга")
    except Exception as e:
        logger.warning("   Ошибка при получении деталей: %s", e)
    
    # Пытаемся создать чат
    logger.info("2. Создание чата через chat_start...")
    try:
        chat_result = await chat_start(posting_number)
        if isinstance(chat_result, dict):
            chat_id = chat_result.get("chat_id") or chat_result.get("id")
            if chat_id:
                chat_id_str = str(chat_id).strip()
                logger.info("   ✅ Чат успешно создан!")
                logger.info("   Chat ID: %s", chat_id_str)
                logger.info("   Полный ответ: %s", chat_result)
                
                # Пытаемся получить историю чата
                logger.info("3. Проверка истории чата...")
                try:
                    history = await chat_history(chat_id_str, limit=5)
                    if history:
                        logger.info("   ✅ История чата доступна (сообщений: %s)", len(history) if isinstance(history, list) else "?")
                    else:
                        logger.info("   ℹ️ История чата пуста (это нормально для нового чата)")
                except Exception as hist_exc:
                    error_msg = str(hist_exc).lower()
                    if "expired" in error_msg or "access period" in error_msg:
                        logger.warning("   ⚠️ Чат истек или недоступен: %s", hist_exc)
                    else:
                        logger.warning("   ⚠️ Ошибка при получении истории: %s", hist_exc)
                
                logger.info("=" * 60)
                logger.info("✅ ТЕСТ УСПЕШЕН: Чат создан для постинга %s", posting_number)
                logger.info("   Chat ID: %s", chat_id_str)
                logger.info("=" * 60)
                return
            else:
                logger.error("   ❌ Чат создан, но chat_id не найден в ответе")
                logger.error("   Ответ: %s", chat_result)
        else:
            logger.error("   ❌ Неожиданный формат ответа: %s", type(chat_result))
    except Exception as e:
        error_msg = str(e).lower()
        logger.error("   ❌ Ошибка при создании чата: %s", e)
        
        if "expired" in error_msg or "access period" in error_msg:
            logger.error("   Причина: чат истек или недоступен")
        elif "403" in str(e) or "forbidden" in error_msg:
            logger.error("   Причина: нет прав на создание чата (проверьте OZON_WRITE_* credentials)")
        elif "not available" in error_msg:
            logger.error("   Причина: создание чата недоступно для этого типа доставки")
        
        logger.info("=" * 60)
        logger.error("❌ ТЕСТ НЕУДАЧЕН")
        logger.info("=" * 60)


async def main() -> None:
    """Основная функция."""
    # Проверяем конфигурацию
    try:
        cfg = load_ozon_config()
        if not cfg.client_id or not cfg.api_key:
            logger.error("Не заданы OZON credentials (OZON_CLIENT_ID/OZON_API_KEY).")
            sys.exit(1)
        if not cfg.write_client_id or not cfg.write_api_key:
            logger.warning("Не заданы OZON_WRITE_* credentials. Создание чатов может быть недоступно.")
    except Exception as e:
        logger.error("Ошибка при загрузке конфигурации: %s", e)
        sys.exit(1)
    
    # Получаем posting_number из аргументов или ищем автоматически
    posting_number = None
    if len(sys.argv) > 1:
        posting_number = sys.argv[1].strip()
        logger.info("Используется указанный posting_number: %s", posting_number)
    else:
        logger.info("Posting_number не указан, ищем постинг со статусом 'delivering_to_customer' за сегодня...")
        posting_number = await find_delivering_posting_today()
    
    if not posting_number:
        logger.error("Не удалось найти постинг для теста.")
        logger.info("")
        logger.info("Использование:")
        logger.info("  python scripts/test_chat_delivering.py [posting_number]")
        logger.info("")
        logger.info("Примеры постингов со статусом 'Доставляется' за сегодня:")
        logger.info("  - 0172044278-0006-1")
        logger.info("  - 71874375-0107-1")
        logger.info("  - 31022041-0425-1")
        sys.exit(1)
    
    # Тестируем создание чата
    await test_create_chat(posting_number)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
    except Exception as e:
        logger.exception("Критическая ошибка: %s", e)
        sys.exit(1)
