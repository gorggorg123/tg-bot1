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

from botapp.config import load_ozon_config
from botapp.ozon_client import close_clients, get_client, has_write_credentials
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


def validate_env() -> None:
    """
    Фейлимся заранее, чтобы не ловить “странные” ошибки по месту.
    """
    token = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задан TG_BOT_TOKEN (или TELEGRAM_BOT_TOKEN).")

    cfg = load_ozon_config()
    if not cfg.client_id or not cfg.api_key:
        raise RuntimeError(
            "Не заданы OZON credentials (OZON_CLIENT_ID/OZON_API_KEY или OZON_SELLER_CLIENT_ID/OZON_SELLER_API_KEY)."
        )

    # OpenAI — не делаем fatal, но предупреждаем (ИИ-кнопки будут падать при вызове)
    if not _env("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY не задан — ИИ-генерация будет недоступна.")

    if not has_write_credentials():
        logger.warning(
            "Write-доступ к Ozon не задан (OZON_WRITE_* / OZON_SELLER_WRITE_*). "
            "Отправка ответов/сообщений будет недоступна."
        )


def build_fsm_storage():
    """
    Опционально Redis FSM:
      REDIS_URL=redis://:pass@host:6379/0
    """
    redis_url = _env("REDIS_URL")
    if not redis_url:
        return MemoryStorage()

    try:
        from aiogram.fsm.storage.redis import RedisStorage  # type: ignore
        logger.info("Using Redis FSM storage")
        return RedisStorage.from_url(redis_url)
    except Exception as exc:
        logger.warning("Failed to init RedisStorage (%s). Falling back to MemoryStorage.", exc)
        return MemoryStorage()


# -----------------------------------------------------------------------------
# Global app/bot/dp (для uvicorn main:app)
# -----------------------------------------------------------------------------

setup_logging()
validate_env()

TG_BOT_TOKEN = _env("TG_BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
ENABLE_TG_POLLING = _env("ENABLE_TG_POLLING", default="1") == "1"
DROP_PENDING_UPDATES = _env("DROP_PENDING_UPDATES", default="1") in ("1", "true", "True", "yes", "YES")

bot = Bot(token=TG_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=build_fsm_storage())
dp.include_router(root_router)

app = FastAPI()

_polling_task: asyncio.Task | None = None
_polling_lock = asyncio.Lock()


async def start_polling_once() -> None:
    """
    Стартуем polling ровно один раз на процесс.
    """
    global _polling_task
    async with _polling_lock:
        if _polling_task and not _polling_task.done():
            logger.info("Polling already running")
            return
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
    logger.info("Startup: init Ozon client")
    get_client()

    if not ENABLE_TG_POLLING:
        logger.info("ENABLE_TG_POLLING=0 — Telegram polling disabled")
        return

    logger.info("Startup: starting Telegram polling task")
    asyncio.create_task(start_polling_once())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Shutdown: flushing storage, stopping polling, closing clients")

    with suppress(Exception):
        flush_storage()

    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await _polling_task

    with suppress(Exception):
        await close_clients()

    with suppress(Exception):
        await bot.session.close()


@app.api_route("/", methods=["GET", "HEAD"])
async def root() -> dict:
    return {"status": "ok", "detail": "Ozon bot is running"}


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


__all__ = ["app", "bot", "dp"]
