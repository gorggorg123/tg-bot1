# Интеграция идей из a-ulianov/OzonAPI

Документ отслеживает интеграцию подходов из [a-ulianov/OzonAPI](https://github.com/a-ulianov/OzonAPI).

## ✅ Реализовано

### 1. Продвинутая конфигурация (`botapp/ozon_api_config.py`)
- [x] Класс `OzonAPIConfigProfile` с полной настройкой
- [x] Поддержка множественных профилей (default/write)
- [x] Загрузка из `.env` с префиксом `OZON_`
- [x] Совместимость с переменными `OZON_SELLER_*` (как в a-ulianov/OzonAPI)
- [x] Валидация параметров
- [x] OAuth токен (поле есть, интеграция готова)

### 2. Интеграция конфига в OzonClient (`botapp/ozon_client.py`)
- [x] Таймауты из профиля: `total_timeout`, `connect_timeout`
- [x] Параметры ретраев: `max_retries`, `retry_min_wait`, `retry_max_wait`, `retry_multiplier`
- [x] Rate limiting: `max_requests_per_second`
- [x] Логирование: `enable_request_logging`
- [x] Метрики: `enable_metrics`
- [x] OAuth токен и дополнительные заголовки (`Authorization`, `User-Agent`, extra headers)

### 3. Экспоненциальный backoff
- [x] Метод `_calculate_backoff()` с формулой из OzonAPI
- [x] Формула: `min(retry_max_wait, retry_min_wait * (retry_multiplier ^ attempt))`
- [x] Jitter для предотвращения thundering herd

### 4. Контекстный менеджер
- [x] `async with OzonClient() as client:` поддерживается
- [x] Автоматическое закрытие ресурсов
- [x] Флаг `_closed` для предотвращения повторного закрытия

### 5. Метрики производительности
- [x] Класс `OzonClientMetrics` с полями:
  - `total_requests`, `successful_requests`, `failed_requests`
  - `total_response_time`, `average_response_time`
  - `cache_hits`, `cache_misses`, `cache_hit_rate`
  - `rate_limit_hits`, `retries_count`
- [x] Метод `get_metrics_summary()` для форматированного вывода

### 6. Rate Limiting (`botapp/api/rate_limiting.py`)
- [x] `SimpleRateLimiter` с логированием и статистикой
- [x] Глобальный лимитер из конфига
- [x] Per-endpoint лимитеры с документированными значениями
- [x] `ENDPOINT_LIMITS` - справочник лимитов по эндпоинтам
- [x] `get_all_limiter_stats()` - сводка по всем лимитерам

### 6.1 Совместимость версий чатов
- [x] Fallback по версиям API для чатов (v3 -> v1 -> v2) на случай изменений Ozon

### 7. Фабрика клиентов по профилю
- [x] `get_client(profile_name="default")` - основной метод
- [x] `get_write_client()` - для операций записи
- [x] Синглтон для default профиля
- [x] Логирование создания клиента

### 8. Health Check
- [x] `health_check()` - проверка API через /v1/seller/info
- [x] `startup_health_check()` - для вызова при старте бота
- [x] Измерение latency
- [x] Интеграция с метриками

### 9. Логирование запросов
- [x] Опциональное логирование каждого запроса
- [x] Уровни: DEBUG для успешных, WARNING для ретраев, ERROR для ошибок
- [x] Request ID в логах

### 10. Обновлённая документация
- [x] `.env.example` с полным описанием параметров
- [x] Группировка параметров по категориям
- [x] Рекомендуемые значения

## 📋 Можно добавить позже

### Circuit Breaker
- [ ] Временная остановка запросов после серии ошибок
- [ ] Автоматическое восстановление после паузы

### Webhooks Ozon
- [ ] Приём webhook уведомлений от Ozon
- [ ] Обновление данных в реальном времени

### Кеширование на диск
- [ ] Интеграция `enable_cache`, `cache_ttl_seconds`, `cache_dir` из профиля
- [ ] LRU-кеш для статичных данных (категории, атрибуты)

### Асинхронное логирование
- [ ] Неблокирующая запись логов
- [ ] Очередь сообщений для файлового логирования

## Примеры использования

### Базовое использование с конфигом

```python
from botapp.ozon_client import get_client, startup_health_check

async def main():
    # Health check при старте
    if not await startup_health_check():
        print("Ozon API недоступен!")
        return
    
    # Получение клиента (берёт настройки из OzonAPIConfig)
    client = get_client()
    
    # Использование с контекстным менеджером
    async with get_client() as client:
        seller = await client.get_seller_info()
        print(f"Продавец: {seller}")
```

### Работа с метриками

```python
client = get_client()

# После серии запросов
print(client.get_metrics_summary())

# Или детально
metrics = client.metrics
print(f"Успешность: {metrics.success_rate:.1f}%")
print(f"Среднее время: {metrics.average_response_time:.3f}s")
```

### Использование профилей

```python
# Чтение (default профиль)
read_client = get_client()

# Запись (write профиль, если настроен)
write_client = get_client(profile_name="write")

# Или через хелпер
write_client = get_write_client()
```

## Ссылки

- [a-ulianov/OzonAPI на GitHub](https://github.com/a-ulianov/OzonAPI)
- [Документация Ozon Seller API](https://docs.ozon.ru/api/seller/)
- [Лимиты Ozon API](https://docs.ozon.ru/api/seller/throttling/)
