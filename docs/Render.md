# Render deployment notes

## Persistent disk

- Render сохраняет данные только внутри смонтированного диска. Всё, что пишется вне mount path, будет теряться при рестарте/деплое.
- Укажите переменную окружения `STORAGE_DIR` (рекомендовано) или используйте предоставленный Render `RENDER_DISK_PATH`. Пример: `STORAGE_DIR=/var/data`.
- Все файлы бота (очереди outreach, SKU title cache, user storage) кладутся в `ROOT`, который выбирается по порядку: `STORAGE_DIR` → `RENDER_DISK_PATH` → `PERSIST_DIR`/`PERSISTENT_DIR` → локальный `data/`.
- При старте (`main.py`) бот логирует значения `STORAGE_DIR`, `RENDER_DISK_PATH` и выбранный `ROOT`, а также делает probe-файл, чтобы убедиться в доступности диска.

## Что хранится на диске

- `outreach_queue.json`, `outreach_sent.json`, `outreach_dead.json` — очередь и метаданные рассылок.
- `sku_title_cache.json` — кеш названий SKU.
- Остальные файлы в `botapp.utils.storage` (ответы на отзывы/вопросы, состояние чатов, настройки).

## Проверка после деплоя

1. Убедитесь, что в логах есть строка `Storage: STORAGE_DIR=..., RENDER_DISK_PATH=..., resolved ROOT=...` и probe прошёл успешно.
2. Создайте/обновите очередь или кеш, перезапустите сервис — данные должны сохраниться.
