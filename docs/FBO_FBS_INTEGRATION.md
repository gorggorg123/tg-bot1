# Интеграция FBO и FBS отправок

## Обзор

Бот теперь поддерживает оба типа отправок Ozon:

- **FBO (Fulfillment by Ozon)** — товары хранятся на складе Ozon
- **FBS (Fulfillment by Seller)** — товары хранятся на вашем складе

## Новые возможности

### Объединённая статистика

По умолчанию показывается объединённая статистика FBO+FBS:

```
📦 Отправки (FBO+FBS) • Сводка
24 января 2026

Сегодня
📊 Заказано: 15 / 45 000 ₽
✅ Без отмен: 14 / 42 000 ₽
❌ Отмен: 1 / 3 000 ₽
🔁 Возвраты: 0 шт
📍 FBO: 10 | FBS: 5 (⏳ 2 ожидают)
🚚 В доставке: 3
🧾 Средний чек (без отмен): 3 000 ₽
```

### Переключение режимов

В интерфейсе доступны кнопки для переключения:

- **📊 Все (FBO+FBS)** — объединённая статистика
- **📦 Только FBO** — только отправки со склада Ozon
- **🏭 Только FBS** — только ваши отправки

### Статусы FBS

Для FBS отслеживаются дополнительные статусы:

| Статус | Описание |
|--------|----------|
| `awaiting_registration` | Ожидает регистрации |
| `acceptance_in_progress` | Приёмка в процессе |
| `awaiting_approve` | Ожидает подтверждения |
| `awaiting_packaging` | Ожидает упаковки |
| `awaiting_deliver` | Ожидает отправки |
| `delivering` | В доставке |
| `driver_pickup` | У курьера |
| `delivered` | Доставлено |
| `cancelled` | Отменено |

## API методы

### OzonClient

```python
# Получить только FBO отправки
fbo_postings = await client.get_fbo_postings(date_from, date_to)

# Получить только FBS отправки
fbs_postings = await client.get_fbs_postings(date_from, date_to)

# Получить все отправки (FBO + FBS)
all_postings = await client.get_all_postings(
    date_from, 
    date_to,
    include_fbo=True,  # по умолчанию True
    include_fbs=True,  # по умолчанию True
)
```

### Logic функции

```python
from botapp.sections.fbo.logic import (
    get_orders_today_text,
    get_orders_month_text,
    get_fbo_today_text,
    get_fbs_today_text,
)

# Объединённая статистика
text = await get_orders_today_text(mode="all")

# Только FBO
text = await get_orders_today_text(mode="fbo")

# Только FBS
text = await get_orders_today_text(mode="fbs")
```

## Структура данных

Каждая отправка помечается полем `_fulfillment_type`:

```python
{
    "posting_number": "12345-67890",
    "status": "delivering",
    "products": [...],
    "_fulfillment_type": "fbs"  # или "fbo"
}
```

## Разбивка в статистике

Функция `_format_fulfillment_breakdown` форматирует разбивку:

```
📍 FBO: 10 | FBS: 5 (⏳ 2 ожидают)
🚚 В доставке: 3
```

## Совместимость

- Существующий код, использующий `get_orders_today_text()` без параметров, продолжит работать
- По умолчанию показывается объединённая статистика FBO+FBS
- Для получения только FBO используйте `mode="fbo"`
