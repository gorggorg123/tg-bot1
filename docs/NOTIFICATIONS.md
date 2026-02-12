# Система уведомлений

Система push-уведомлений для Ozon Seller Bot, основанная на паттернах из [a-ulianov/OzonAPI](https://github.com/a-ulianov/OzonAPI).

## Возможности

### Типы уведомлений

| Тип | Описание | Проверка |
|-----|----------|----------|
| 📝 Новые отзывы | Уведомление о новых отзывах покупателей | Каждые 5 мин |
| ❓ Новые вопросы | Уведомление о новых вопросах | Каждые 5 мин |
| 💬 Новые сообщения | Уведомление о сообщениях в чатах | Каждые 5 мин |
| 📦 Заказы FBO | Уведомление о новых FBO заказах | Каждые 5 мин |
| 🏭 Заказы FBS | Уведомление о новых FBS заказах | Каждые 5 мин |

### Настройки

- **Главный выключатель** — включить/выключить все уведомления
- **По типам** — включить/выключить отдельные типы
- **Тихие часы** — не отправлять уведомления в заданный период (например, 23:00-08:00)

## Использование

### Команды

```
/notifications — открыть настройки уведомлений
/notify        — альтернативная команда
```

### Из главного меню

Кнопка "🔔 Уведомления" в главном меню бота.

## Архитектура

### Модули

```
botapp/notifications/
├── __init__.py      # Экспорт API
├── config.py        # Настройки и хранение состояния
├── checker.py       # Фоновая проверка новых данных
├── sender.py        # Отправка уведомлений
└── handlers.py      # Telegram обработчики
```

### Принцип работы

1. **Polling** — фоновая задача периодически проверяет Ozon API
2. **Дедупликация** — ID уже виденных элементов сохраняются в памяти
3. **Батчинг** — несколько новых элементов группируются в одно сообщение
4. **Тихие часы** — уведомления не отправляются в заданный период

### Хранение данных

Настройки сохраняются в `data/notifications.json`:

```json
{
  "411767534": {
    "enabled": true,
    "reviews_enabled": true,
    "questions_enabled": true,
    "chats_enabled": true,
    "orders_fbo_enabled": true,
    "orders_fbs_enabled": true,
    "quiet_hours_enabled": false,
    "quiet_hours_start": 23,
    "quiet_hours_end": 8,
    "total_notifications_sent": 42
  }
}
```

## API для разработчиков

### Отправка уведомления

```python
from botapp.notifications import send_notification, NotificationType

await send_notification(
    user_id=411767534,
    notification_type=NotificationType.NEW_REVIEW,
    title="Новый отзыв",
    body="Отличный товар!",
    data={"review_id": "12345"},
    product_name="Обувница для прихожей",
    rating=5,
)
```

### Групповое уведомление

```python
from botapp.notifications import send_batch_notification, NotificationType

await send_batch_notification(
    user_id=411767534,
    notification_type=NotificationType.NEW_ORDER_FBO,
    items=[
        {"product_name": "Товар 1", "amount": 1500},
        {"product_name": "Товар 2", "amount": 2000},
    ],
)
```

### Управление checker'ом

```python
from botapp.notifications import (
    start_notification_checker,
    stop_notification_checker,
    is_checker_running,
)

# Запуск
start_notification_checker()

# Проверка статуса
if is_checker_running():
    print("Checker работает")

# Остановка
stop_notification_checker()
```

### Настройки пользователя

```python
from botapp.notifications import get_user_settings, update_user_settings

# Получить
settings = get_user_settings(user_id)
print(settings.reviews_enabled)

# Обновить
update_user_settings(user_id, reviews_enabled=False)
```

## Конфигурация

### Интервалы

В файле `checker.py`:

```python
DEFAULT_CHECK_INTERVAL = 300  # 5 минут между полными проверками
MIN_CHECK_INTERVAL = 60       # Минимум 1 минута между проверками одного типа
```

### Размеры кеша

```python
# Максимум ID в памяти (для дедупликации)
MAX_REVIEW_IDS = 1000
MAX_QUESTION_IDS = 1000
MAX_CHAT_IDS = 500
MAX_ORDER_NUMBERS = 2000
```

## Интеграция с Ульяновым (OzonAPI)

Система использует паттерны из `a-ulianov/OzonAPI`:

- **Асинхронный дизайн** — все операции асинхронные
- **Умное ограничение запросов** — соблюдение лимитов API
- **Автоповторы** — при сбоях запросы повторяются
- **Детальное логирование** — все операции логируются
- **Гибкая конфигурация** — настройка через dataclass

## Расширение

### Добавление нового типа уведомлений

1. Добавить тип в `NotificationType` (sender.py)
2. Добавить форматтер `_format_*_notification` (sender.py)
3. Добавить checker `_check_new_*` (checker.py)
4. Добавить настройку в `NotificationSettings` (config.py)
5. Обновить UI в handlers.py
