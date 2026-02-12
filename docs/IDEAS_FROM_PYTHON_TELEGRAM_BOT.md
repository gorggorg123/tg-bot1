# Идеи из python-telegram-bot для адаптации

## 📋 Обзор

[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — популярная библиотека с множеством полезных паттернов, которые можно адаптировать для нашего бота на aiogram.

## 🎯 Приоритетные улучшения

### 1. ✅ JobQueue (APScheduler) — ВЫСОКИЙ ПРИОРИТЕТ

**Проблема:** Сейчас периодические задачи (chat_autoreply, notifications) запускаются через `asyncio.create_task` с ручным управлением.

**Решение:** Использовать APScheduler для планирования задач.

**Преимущества:**
- ✅ Автоматическое управление расписанием
- ✅ Поддержка cron-выражений
- ✅ Перезапуск задач при сбоях
- ✅ Отложенные задачи (one-time jobs)
- ✅ Группировка задач

**Пример адаптации:**

```python
# botapp/jobs/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

# Периодические задачи
scheduler.add_job(
    chat_autoreply_job,
    trigger=IntervalTrigger(seconds=60),
    id="chat_autoreply",
    replace_existing=True,
)

scheduler.add_job(
    notification_checker_job,
    trigger=IntervalTrigger(seconds=300),
    id="notifications",
    replace_existing=True,
)

# Задачи по расписанию (например, ежедневные отчёты)
scheduler.add_job(
    daily_report_job,
    trigger=CronTrigger(hour=9, minute=0),  # Каждый день в 9:00
    id="daily_report",
)

# Отложенные задачи
scheduler.add_job(
    send_reminder,
    trigger="date",
    run_date=datetime.now() + timedelta(hours=1),
    args=[user_id, "Напоминание"],
)
```

**Интеграция в run_local.py:**

```python
from botapp.jobs.scheduler import scheduler

# В main():
scheduler.start()
logger.info("[+] JobQueue запущен")

# В finally:
scheduler.shutdown()
```

---

### 2. ✅ CallbackDataCache — СРЕДНИЙ ПРИОРИТЕТ

**Проблема:** Сейчас используется `TokenStore` с TTL, но для больших payload (например, списки чатов/вопросов) можно оптимизировать.

**Решение:** Добавить кеширование callback_data с автоматической очисткой.

**Преимущества:**
- ✅ Экономия места в callback_data (короткие ID вместо полных данных)
- ✅ Автоматическая очистка старых записей
- ✅ Поддержка произвольных типов данных

**Пример адаптации:**

```python
# botapp/utils/callback_cache.py
from cachetools import TTLCache
from typing import Any, Optional
import hashlib
import json

class CallbackDataCache:
    """Кеш для callback_data с TTL."""
    
    def __init__(self, maxsize: int = 10000, ttl: int = 3600):
        self._cache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._counter = 0
    
    def put(self, data: dict) -> str:
        """Сохранить данные и вернуть короткий ID."""
        key = f"cb_{self._counter}"
        self._counter = (self._counter + 1) % 1000000
        self._cache[key] = data
        return key
    
    def get(self, key: str) -> Optional[dict]:
        """Получить данные по ключу."""
        return self._cache.get(key)
    
    def clear_user(self, user_id: int):
        """Очистить кеш пользователя."""
        # Можно добавить фильтрацию по user_id в данных
        pass

callback_cache = CallbackDataCache()
```

**Использование:**

```python
# Вместо передачи полных данных в callback_data
callback_data = MenuCallbackData(
    section="chats",
    action="open",
    extra=callback_cache.put({"chat_id": chat_id, "page": 0})
).pack()
```

---

### 3. ✅ BasePersistence — СРЕДНИЙ ПРИОРИТЕТ

**Проблема:** `MemoryStorage` теряет все FSM состояния при перезапуске бота.

**Решение:** Реализовать персистентное хранилище на основе файлов/БД.

**Преимущества:**
- ✅ Сохранение состояний между перезапусками
- ✅ Восстановление диалогов после сбоев
- ✅ История состояний для отладки

**Пример адаптации:**

```python
# botapp/storage/fsm_persistence.py
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from pathlib import Path
import json
from typing import Optional, Any

class FilePersistence(BaseStorage):
    """Файловое хранилище для FSM состояний."""
    
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
    
    def _get_file_path(self, key: StorageKey) -> Path:
        return self.path / f"{key.bot_id}_{key.user_id}_{key.chat_id}.json"
    
    async def set_state(self, key: StorageKey, state: Optional[str] = None) -> None:
        file_path = self._get_file_path(key)
        data = {}
        if file_path.exists():
            data = json.loads(file_path.read_text())
        data["state"] = state
        file_path.write_text(json.dumps(data))
    
    async def get_state(self, key: StorageKey) -> Optional[str]:
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return None
        data = json.loads(file_path.read_text())
        return data.get("state")
    
    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        file_path = self._get_file_path(key)
        current = {}
        if file_path.exists():
            current = json.loads(file_path.read_text())
        current["data"] = data
        file_path.write_text(json.dumps(current))
    
    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return {}
        data = json.loads(file_path.read_text())
        return data.get("data", {})
```

**Использование:**

```python
# В run_local.py
from botapp.storage.fsm_persistence import FilePersistence

storage = FilePersistence(STORAGE_ROOT / "fsm")
dp = Dispatcher(storage=storage)
```

---

### 4. ✅ Shortcut методы — НИЗКИЙ ПРИОРИТЕТ

**Проблема:** Много повторяющегося кода для reply/edit сообщений.

**Решение:** Добавить удобные методы-обёртки.

**Пример адаптации:**

```python
# botapp/utils/shortcuts.py
from aiogram.types import Message, CallbackQuery
from aiogram import Bot

async def reply_text(
    message: Message,
    text: str,
    **kwargs
) -> Message:
    """Удобный reply_text с автоматическим форматированием."""
    return await message.answer(text, **kwargs)

async def edit_text(
    callback: CallbackQuery,
    text: str,
    **kwargs
) -> bool:
    """Удобный edit_text с обработкой ошибок."""
    try:
        await callback.message.edit_text(text, **kwargs)
        return True
    except Exception:
        return False

async def delete_message(
    bot: Bot,
    chat_id: int,
    message_id: int
) -> bool:
    """Удалить сообщение с обработкой ошибок."""
    try:
        await bot.delete_message(chat_id, message_id)
        return True
    except Exception:
        return False
```

---

### 5. ✅ ConversationHandler паттерны — НИЗКИЙ ПРИОРИТЕТ

**Проблема:** Многошаговые диалоги (настройки, импорт) реализованы через FSM вручную.

**Решение:** Использовать паттерны ConversationHandler для упрощения.

**Пример адаптации:**

```python
# botapp/handlers/conversations.py
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

class SettingsStates(StatesGroup):
    waiting_for_interval = State()
    waiting_for_quiet_hours = State()

router = Router()

@router.callback_query(F.data == "settings:interval")
async def start_interval_setting(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите интервал проверки (в минутах):")
    await state.set_state(SettingsStates.waiting_for_interval)

@router.message(SettingsStates.waiting_for_interval)
async def process_interval(message: Message, state: FSMContext):
    try:
        interval = int(message.text)
        # Сохранить настройку
        await message.answer(f"Интервал установлен: {interval} минут")
        await state.clear()
    except ValueError:
        await message.answer("Введите число!")
```

---

## 📊 Сравнение подходов

| Функция | Текущий подход | python-telegram-bot подход | Преимущества |
|---------|---------------|---------------------------|--------------|
| Периодические задачи | `asyncio.create_task` | `JobQueue` (APScheduler) | Автоматическое управление, cron |
| Callback data | `TokenStore` | `CallbackDataCache` | Оптимизация, автоматическая очистка |
| FSM Storage | `MemoryStorage` | `BasePersistence` | Сохранение между перезапусками |
| Reply/Edit | Прямые вызовы | Shortcut методы | Меньше кода, единообразие |
| Многошаговые диалоги | Ручной FSM | `ConversationHandler` | Упрощение логики |

---

## 🚀 План внедрения

### Фаза 1: JobQueue (APScheduler)
1. Установить `apscheduler`
2. Создать `botapp/jobs/scheduler.py`
3. Мигрировать `chat_autoreply` и `notifications` на scheduler
4. Добавить поддержку cron-задач

### Фаза 2: BasePersistence
1. Реализовать `FilePersistence`
2. Заменить `MemoryStorage` на `FilePersistence`
3. Протестировать сохранение/восстановление состояний

### Фаза 3: CallbackDataCache
1. Создать `CallbackDataCache` на основе `cachetools`
2. Интегрировать в существующие keyboards
3. Оптимизировать большие payload

### Фаза 4: Shortcut методы
1. Создать `botapp/utils/shortcuts.py`
2. Постепенно мигрировать handlers
3. Обновить документацию

---

## 📚 Полезные ссылки

- [python-telegram-bot JobQueue](https://docs.python-telegram-bot.org/en/stable/telegram.ext.jobqueue.html)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
- [python-telegram-bot Persistence](https://docs.python-telegram-bot.org/en/stable/telegram.ext.basepersistence.html)
- [cachetools Documentation](https://cachetools.readthedocs.io/)

---

## ✅ Рекомендации

**Начать с:** JobQueue (APScheduler) — даст наибольший эффект при минимальных изменениях.

**Затем:** BasePersistence — улучшит надёжность бота.

**Опционально:** CallbackDataCache и Shortcut методы — для оптимизации и удобства.
