#!/usr/bin/env python3
"""
Примеры использования модернизированного Ozon API клиента
На основе a-ulianov/OzonAPI
"""
import asyncio
import logging
from datetime import datetime, timedelta

from botapp.ozon_api_client import get_ozon_client, get_write_client
from botapp.logging_config import setup_logging, log_performance

# Настройка логирования
setup_logging(log_level="INFO")
logger = logging.getLogger(__name__)


@log_performance("пример_получения_каталога")
async def example_get_catalog():
    """Пример получения каталога товаров с кешированием"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 1: Получение каталога товаров")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    # Получение всех видимых товаров
    products = await client.get_product_list_all(visibility='VISIBLE')
    logger.info("Получено товаров: %s", len(products))
    
    # Вывод первых 3 товаров
    for i, product in enumerate(products[:3], 1):
        logger.info(
            "%s. %s (ID: %s, offer_id: %s, SKU: %s)",
            i,
            product.get("name", "N/A"),
            product.get("product_id", "N/A"),
            product.get("offer_id", "N/A"),
            product.get("sku", "N/A"),
        )
    
    return products


@log_performance("пример_получения_инфо_о_товарах")
async def example_get_product_info():
    """Пример получения информации о товарах батчем с кешированием"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 2: Получение информации о товарах (batch + cache)")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    # Получение списка товаров
    products = await client.get_product_list_all(visibility='VISIBLE', limit_per_page=10)
    
    if not products:
        logger.warning("Нет товаров для примера")
        return
    
    # Берем первые 5 product_id
    product_ids = [p.get("product_id") for p in products[:5] if p.get("product_id")]
    
    if not product_ids:
        logger.warning("Нет product_id для примера")
        return
    
    logger.info("Получение информации о %s товарах...", len(product_ids))
    
    # Первый запрос - пойдет в API
    info_items = await client.get_product_info_batch(product_ids=product_ids, use_cache=True)
    logger.info("Первый запрос: получено %s товаров", len(info_items))
    
    # Второй запрос - возьмется из кеша
    info_items_cached = await client.get_product_info_batch(product_ids=product_ids, use_cache=True)
    logger.info("Второй запрос (из кеша): получено %s товаров", len(info_items_cached))
    
    # Вывод информации
    for item in info_items[:3]:
        logger.info(
            "Товар: %s | SKU: %s | Баркоды: %s",
            item.get("name", "N/A"),
            item.get("sku", "N/A"),
            len(item.get("barcodes", [])),
        )


@log_performance("пример_получения_заказов_fbo")
async def example_get_fbo_orders():
    """Пример получения FBO заказов за период"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 3: Получение FBO заказов")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    # Получение заказов за последние 7 дней
    date_to = datetime.utcnow()
    date_from = date_to - timedelta(days=7)
    
    date_from_iso = date_from.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_to_iso = date_to.strftime("%Y-%m-%dT%H:%M:%S.999Z")
    
    logger.info("Получение FBO заказов с %s по %s", date_from_iso, date_to_iso)
    
    postings = await client.get_fbo_postings_range(
        date_from=date_from_iso,
        date_to=date_to_iso,
        include_analytics=True,
        include_financial=True,
    )
    
    logger.info("Получено FBO заказов: %s", len(postings))
    
    # Статистика по статусам
    status_counts = {}
    for posting in postings:
        status = posting.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    logger.info("Статистика по статусам:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        logger.info("  - %s: %s", status, count)


@log_performance("пример_получения_складов")
async def example_get_warehouses():
    """Пример получения списка складов"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 4: Получение списка складов")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    warehouses = await client.get_warehouses(use_cache=True)
    logger.info("Получено складов: %s", len(warehouses))
    
    for wh in warehouses:
        logger.info(
            "Склад: %s (ID: %s, тип: %s)",
            wh.get("name", "N/A"),
            wh.get("warehouse_id", "N/A"),
            wh.get("type", "N/A"),
        )


@log_performance("пример_работы_с_чатами")
async def example_work_with_chats():
    """Пример работы с чатами (V3 API)"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 5: Работа с чатами")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    try:
        # Получение списка чатов
        response = await client.chat_list_v3(
            filter_data={"chat_type": ["buyer_seller"]},
            limit=10,
        )
        
        result = response.get("result", {})
        chats = result.get("chats", [])
        
        logger.info("Получено чатов: %s", len(chats))
        
        # Вывод информации о первых 3 чатах
        for i, chat in enumerate(chats[:3], 1):
            logger.info(
                "%s. Chat ID: %s | Unread: %s | Created: %s",
                i,
                chat.get("chat_id", "N/A"),
                chat.get("unread_count", 0),
                chat.get("created_at", "N/A"),
            )
        
        # Получение истории первого чата (если есть)
        if chats:
            first_chat_id = chats[0].get("chat_id")
            if first_chat_id:
                logger.info("Получение истории чата %s...", first_chat_id)
                
                history_response = await client.chat_history_v3(
                    chat_id=first_chat_id,
                    limit=5,
                    direction="backward",
                )
                
                hist_result = history_response.get("result", {})
                messages = hist_result.get("messages", [])
                
                logger.info("Получено сообщений: %s", len(messages))
                
                for msg in messages[:3]:
                    logger.info(
                        "  - От: %s | Текст: %s",
                        msg.get("user", {}).get("type", "N/A"),
                        msg.get("text", "")[:50],
                    )
    
    except Exception as e:
        logger.error("Ошибка при работе с чатами: %s", e)
        logger.info("(Возможно, у вас нет Premium+ подписки или недостаточно прав)")


@log_performance("пример_получения_финансов")
async def example_get_finance():
    """Пример получения финансовых данных"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 6: Получение финансовых данных")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    # Получение данных за последние 7 дней
    date_to = datetime.utcnow()
    date_from = date_to - timedelta(days=7)
    
    date_from_iso = date_from.strftime("%Y-%m-%dT00:00:00Z")
    date_to_iso = date_to.strftime("%Y-%m-%dT23:59:59Z")
    
    logger.info("Получение финансов с %s по %s", date_from_iso, date_to_iso)
    
    totals = await client.get_finance_totals(
        date_from=date_from_iso,
        date_to=date_to_iso,
        transaction_type="all",
    )
    
    # Вывод основных показателей
    accruals = totals.get("accruals_for_sale", 0)
    refunds = totals.get("refunds_and_cancellations", 0)
    commission = totals.get("sale_commission", 0)
    
    logger.info("Финансовые показатели за период:")
    logger.info("  - Начисления за продажи: %.2f руб", accruals)
    logger.info("  - Возвраты и отмены: %.2f руб", abs(refunds))
    logger.info("  - Комиссия: %.2f руб", abs(commission))
    logger.info("  - Итого продаж: %.2f руб", accruals - abs(refunds))


@log_performance("пример_аналитики")
async def example_get_analytics():
    """Пример получения аналитических данных с кешированием"""
    logger.info("=" * 60)
    logger.info("ПРИМЕР 7: Получение аналитики")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    # Получение данных за последний месяц
    date_to = datetime.utcnow()
    date_from = date_to - timedelta(days=30)
    
    date_from_str = date_from.strftime("%Y-%m-%d")
    date_to_str = date_to.strftime("%Y-%m-%d")
    
    try:
        logger.info("Получение аналитики с %s по %s", date_from_str, date_to_str)
        
        # Первый запрос - пойдет в API
        analytics = await client.get_analytics_data(
            date_from=date_from_str,
            date_to=date_to_str,
            metrics=["revenue", "ordered_units"],
            dimensions=["sku"],
            use_cache=True,
            limit=10,
        )
        
        result = analytics.get("result", {})
        data_rows = result.get("data", [])
        
        logger.info("Получено строк данных: %s", len(data_rows))
        
        # Вывод топ-3 по выручке
        logger.info("Топ-3 товара по выручке:")
        for i, row in enumerate(data_rows[:3], 1):
            dimensions = row.get("dimensions", [{}])
            metrics = row.get("metrics", [0, 0])
            
            sku = dimensions[0].get("name", "N/A") if dimensions else "N/A"
            revenue = metrics[0] if len(metrics) > 0 else 0
            units = metrics[1] if len(metrics) > 1 else 0
            
            logger.info("  %s. SKU: %s | Выручка: %.2f | Продано: %s", i, sku, revenue, units)
        
        # Второй запрос - из кеша
        analytics_cached = await client.get_analytics_data(
            date_from=date_from_str,
            date_to=date_to_str,
            metrics=["revenue", "ordered_units"],
            dimensions=["sku"],
            use_cache=True,
            limit=10,
        )
        logger.info("Повторный запрос выполнен из кеша")
    
    except Exception as e:
        logger.error("Ошибка при получении аналитики: %s", e)
        logger.info("(Возможно, у вас нет необходимых прав для аналитики)")


async def example_show_metrics():
    """Пример просмотра метрик производительности"""
    logger.info("=" * 60)
    logger.info("МЕТРИКИ ПРОИЗВОДИТЕЛЬНОСТИ")
    logger.info("=" * 60)
    
    client = get_ozon_client()
    
    # Вывод метрик
    metrics_summary = client.get_metrics_summary()
    logger.info("Метрики клиента:")
    logger.info(metrics_summary)
    
    # Детальная информация
    metrics = client.metrics
    logger.info("")
    logger.info("Детальная информация:")
    logger.info("  - Всего запросов: %s", metrics.total_requests)
    logger.info("  - Успешных: %s", metrics.successful_requests)
    logger.info("  - Ошибок: %s", metrics.failed_requests)
    logger.info("  - Среднее время ответа: %.3f сек", metrics.average_response_time)
    logger.info("  - Процент успешности: %.1f%%", metrics.success_rate)
    logger.info("  - Попаданий в кеш: %s", metrics.cache_hits)
    logger.info("  - Промахов кеша: %s", metrics.cache_misses)
    logger.info("  - Процент попаданий в кеш: %.1f%%", metrics.cache_hit_rate)
    logger.info("  - Rate limit hits: %s", metrics.rate_limit_hits)
    logger.info("  - Retries count: %s", metrics.retries_count)


async def main():
    """Главная функция - запуск всех примеров"""
    logger.info("╔" + "=" * 58 + "╗")
    logger.info("║" + " " * 12 + "ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ OZON API" + " " * 15 + "║")
    logger.info("║" + " " * 15 + "На основе a-ulianov/OzonAPI" + " " * 15 + "║")
    logger.info("╚" + "=" * 58 + "╝")
    logger.info("")
    
    try:
        # Запуск примеров
        await example_get_catalog()
        logger.info("")
        
        await example_get_product_info()
        logger.info("")
        
        await example_get_fbo_orders()
        logger.info("")
        
        await example_get_warehouses()
        logger.info("")
        
        await example_work_with_chats()
        logger.info("")
        
        await example_get_finance()
        logger.info("")
        
        await example_get_analytics()
        logger.info("")
        
        # Вывод метрик
        await example_show_metrics()
        
    except KeyboardInterrupt:
        logger.info("Прервано пользователем")
    except Exception as e:
        logger.error("Ошибка при выполнении примеров: %s", e, exc_info=True)
    finally:
        logger.info("")
        logger.info("=" * 60)
        logger.info("ПРИМЕРЫ ЗАВЕРШЕНЫ")
        logger.info("=" * 60)


if __name__ == "__main__":
    # Для запуска примеров необходимо:
    # 1. Установить переменные окружения в .env:
    #    OZON_CLIENT_ID=your_client_id
    #    OZON_API_KEY=your_api_key
    # 2. Запустить скрипт:
    #    python examples/ozonapi_usage.py
    
    asyncio.run(main())
