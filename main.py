# main.py
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from dotenv import load_dotenv
from fastapi import FastAPI

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

# --- НОВЫЕ ИМПОРТЫ ---
from botapp.config import load_ozon_config, OzonConfig
from botapp.db import init_db
from botapp.api.client import BotOzonClient
from botapp.products_service import ProductsService
# ---------------------

from botapp.jobs.outreach_sender import outreach_sender_loop
from botapp.router import router as root_router
from botapp.storage import flush_storage

logger = logging.getLogger("main")

load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return (default or "").strip()


def setup_logging() -> None:
    level = _env("LOG_LEVEL", default="INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def validate_env() -> OzonConfig:
    """
    Проверяем переменные и возвращаем конфиг Ozon.
    """
    token = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан TG_BOT_TOKEN (или TELEGRAM_BOT_TOKEN).")

    cfg = load_ozon_config()
    if not cfg.client_id or not cfg.api_key:
        raise RuntimeError(
            "Не заданы OZON credentials (OZON_CLIENT_ID/OZON_API_KEY)."
        )

    if not _env("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY не задан — ИИ-генерация будет недоступна.")

    return cfg


def build_fsm_storage():
    redis_url = _env("REDIS_URL")
    if not redis_url:
        return MemoryStorage()

    try:
        from aiogram.fsm.storage.redis import RedisStorage
        logger.info("Using Redis FSM storage")
        return RedisStorage.from_url(redis_url)
    except Exception as exc:
        logger.warning("Failed to init RedisStorage (%s). Falling back to MemoryStorage.", exc)
        return MemoryStorage()


# -----------------------------------------------------------------------------
# Global setup
# -----------------------------------------------------------------------------

setup_logging()
OZON_CFG = validate_env()  # Загружаем конфиг сразу

TG_BOT_TOKEN = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
ENABLE_TG_POLLING = _env("ENABLE_TG_POLLING", default="1") == "1"
DROP_PENDING_UPDATES = _env("DROP_PENDING_UPDATES", default="1") in ("1", "true", "True", "yes", "YES")

bot = Bot(token=TG_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=build_fsm_storage())
dp.include_router(root_router)

app = FastAPI()

_polling_task: asyncio.Task | None = None
_polling_lock = asyncio.Lock()
_outreach_task: asyncio.Task | None = None
_outreach_stop: asyncio.Event | None = None

# Глобальная ссылка на клиента для корректного закрытия
_ozon_client: BotOzonClient | None = None


async def start_polling_once() -> None:
    """
    Стартуем polling ровно один раз на процесс.
    """
    global _polling_task
    async with _polling_lock:
        if _polling_task and not _polling_task.done():
            logger.info("Polling already running")
            return
        
        # Зависимости уже внедрены в dp.workflow_data в on_startup
        _polling_task = asyncio.create_task(
            dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                drop_pending_updates=DROP_PENDING_UPDATES,
            )
        )

    try:
        await _polling_task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Polling crashed: %s", exc)
        raise


@app.on_event("startup")
async def on_startup() -> None:
    global _ozon_client, _outreach_task, _outreach_stop

    # 1. Инициализация Базы Данных (создание таблиц)
    logger.info("Startup: initializing Database")
    await init_db()

    # 2. Инициализация Нового Клиента Ozon (v2/v3)
    logger.info("Startup: initializing Ozon Client (BotOzonClient)")
    _ozon_client = BotOzonClient(
        client_id=OZON_CFG.client_id,
        api_key=OZON_CFG.api_key
    )

    # 3. Инициализация Сервиса Товаров (с БД)
    logger.info("Startup: initializing Products Service")
    products_service = ProductsService(_ozon_client)

    # 4. Внедрение зависимостей в Dispatcher
    # Теперь в любом хендлере можно добавить аргументы:
    # async def handler(msg: Message, ozon_client: BotOzonClient, products_service: ProductsService):
    dp.workflow_data.update({
        "ozon_client": _ozon_client,
        "products_service": products_service,
        "ozon_config": OZON_CFG
    })

    # 5. Запуск фоновых задач
    _outreach_stop = asyncio.Event()
    logger.info("Startup: starting outreach sender loop")
    # ВАЖНО: Убедитесь, что outreach_sender_loop обновлен для использования нового клиента, 
    # либо передавайте его туда, если потребуется рефакторинг sender'а.
    _outreach_task = asyncio.create_task(outreach_sender_loop(_outreach_stop))

    if not ENABLE_TG_POLLING:
        logger.info("ENABLE_TG_POLLING=0 — Telegram polling disabled")
        return

    logger.info("Startup: starting Telegram polling task")
    asyncio.create_task(start_polling_once())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Shutdown: flushing storage, stopping tasks, closing clients")

    with suppress(Exception):
        flush_storage()

    global _outreach_task, _outreach_stop
    if _outreach_stop is not None:
        _outreach_stop.set()
    if _outreach_task and not _outreach_task.done():
        _outreach_task.cancel()
        with suppress(asyncio.CancelledError):
            await _outreach_task

    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await _polling_task

    # Закрытие сессии Ozon клиента (если библиотека это поддерживает/требует)
    # Обычно aiohttp сессия внутри библиотеки закрывается сама или через GC, 
    # но если есть явный метод close(), его стоит вызвать.
    # В текущей реализации библиотеки ozonapi явного close для внешнего вызова может не быть,
    # но aiohttp ClientSession управляется внутри.

    with suppress(Exception):
        await bot.session.close()


@app.api_route("/", methods=["GET", "HEAD"])
async def root() -> dict:
    return {"status": "ok", "detail": "Ozon bot is running (New Architecture)"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


__all__ = ["app", "bot", "dp"]