# Ozon Bot Patch v1 (generated)

Этот архив содержит файлы, которые мы переписывали в чате.
Скопируй их в свой репозиторий, заменив существующие.

Важно:
- В архиве **нет** файлов `botapp/reviews_handlers.py`, `botapp/questions_handlers.py` и `botapp/chats.py`.
  Они нужны, чтобы новый роутер (`botapp/router.py`) заработал.
  Если их нет в проекте — бот не запустится (ImportError). Мы добавим их следующим шагом.

Порядок применения:
1) Замени/добавь файлы из архива в свой проект (с сохранением структуры папок).
2) Убедись, что `.env` НЕ коммитится (добавь в `.gitignore`: `.env` и `*.env`).
3) В Render задай env:
   - TG_BOT_TOKEN
   - OZON_SELLER_CLIENT_ID / OZON_SELLER_API_KEY (или OZON_CLIENT_ID / OZON_API_KEY)
   - OPENAI_API_KEY (если нужны ИИ-кнопки)
   - ENABLE_TG_POLLING=1
4) Если Render Web Service: старт-команда:
   uvicorn main:app --host 0.0.0.0 --port $PORT

Если у тебя сейчас запуск через другой файл (например `tg.py`), то:
- Либо переключись на `main.py` как entrypoint,
- Либо перенеси код main.py в твой текущий entrypoint.

Далее:
- Следующим сообщением мы добавим handlers для Reviews/Questions и `botapp/chats.py` под “пузыри”.
