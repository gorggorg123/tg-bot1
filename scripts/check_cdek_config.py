#!/usr/bin/env python3
"""Проверка конфигурации CDEK API."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    """Проверка конфигурации CDEK."""
    logger.info("=" * 60)
    logger.info("Проверка конфигурации CDEK API")
    logger.info("=" * 60)
    
    try:
        from botapp.config import load_cdek_config
        
        # Загружаем конфигурацию
        logger.info("Загрузка конфигурации CDEK...")
        config = load_cdek_config()
        
        logger.info("[OK] Конфигурация загружена успешно!")
        logger.info("")
        logger.info("Параметры конфигурации:")
        logger.info("  CDEK_CLIENT_ID: %s", config.client_id[:20] + "..." if len(config.client_id) > 20 else config.client_id)
        logger.info("  CDEK_CLIENT_SECRET: %s", "***" if config.client_secret else "(не задан)")
        logger.info("  CDEK_BASE_URL: %s", config.base_url)
        logger.info("  CDEK_SENDER_CITY: %s", config.sender_city)
        logger.info("  CDEK_SENDER_NAME: %s", config.sender_name)
        logger.info("  CDEK_SENDER_PHONE: %s", config.sender_phone)
        logger.info("  CDEK_DEFAULT_TARIFF_NAME: %s", config.default_tariff_name)
        logger.info("  CDEK_SENDER_PVZ: %s", config.sender_pvz or "(не задан)")
        logger.info("  CDEK_TIMEOUT_S: %s", config.timeout_s)
        
        # Проверяем подключение к CDEK API
        logger.info("")
        logger.info("Проверка подключения к CDEK API...")
        from botapp.api.cdek_client import get_cdek_client
        
        client = get_cdek_client(config)
        
        try:
            # Пробуем получить токен
            logger.info("Получение access token...")
            token = await client._get_access_token()
            logger.info("[OK] Access token получен успешно!")
            logger.info("  Token (первые 20 символов): %s...", token[:20])
            
            # Пробуем получить список городов
            logger.info("")
            logger.info("Проверка API: получение списка городов...")
            cities = await client.get_cities(city="Ижевск", use_cache=False)
            if cities:
                logger.info("[OK] API работает! Найдено городов: %d", len(cities))
                if cities:
                    logger.info("  Пример: %s (код: %s)", cities[0].get("city", "N/A"), cities[0].get("code", "N/A"))
            else:
                logger.warning("[WARN] API вернул пустой список городов")
            
            logger.info("")
            logger.info("=" * 60)
            logger.info("[OK] ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ УСПЕШНО!")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.error("")
            logger.error("[ERROR] Ошибка при проверке API:")
            logger.error("  %s", e)
            logger.error("")
            logger.error("Проверьте:")
            logger.error("  1. Правильность CDEK_CLIENT_ID и CDEK_CLIENT_SECRET")
            logger.error("  2. Доступность интернета")
            logger.error("  3. Правильность CDEK_BASE_URL")
            logger.error("")
            sys.exit(1)
        finally:
            await client.aclose()
            
    except ValueError as e:
        logger.error("")
        logger.error("[ERROR] Ошибка конфигурации:")
        logger.error("  %s", e)
        logger.error("")
        logger.error("Убедитесь, что в файле .env указаны все необходимые переменные:")
        logger.error("  CDEK_CLIENT_ID")
        logger.error("  CDEK_CLIENT_SECRET")
        logger.error("  CDEK_SENDER_NAME")
        logger.error("  CDEK_SENDER_PHONE")
        logger.error("")
        sys.exit(1)
    except Exception as e:
        logger.error("")
        logger.error("[ERROR] Неожиданная ошибка:")
        logger.error("  %s", e, exc_info=True)
        logger.error("")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
