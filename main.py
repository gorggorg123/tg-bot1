from __future__ import annotations

import asyncio
import logging
import os

from fastapi import FastAPI

from run_local import main as bot_main

logger = logging.getLogger("main")
app = FastAPI(title="ozon-tg-bot", version="1.0.0")
_bot_task: asyncio.Task | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _bot_task
    if _bot_task and not _bot_task.done():
        return
    # run_local.py should not start its own HTTP listener when uvicorn is serving PORT.
    os.environ.setdefault("RUN_UNDER_UVICORN", "1")
    # Run Telegram polling loop in background while uvicorn serves health endpoints.
    _bot_task = asyncio.create_task(bot_main(), name="telegram-bot-main")
    logger.info("Bot task started")


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _bot_task
    if not _bot_task:
        return
    _bot_task.cancel()
    try:
        await _bot_task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Bot task crashed during shutdown")
    finally:
        _bot_task = None


@app.get("/")
async def root() -> dict[str, object]:
    alive = _bot_task is not None and not _bot_task.done()
    return {"ok": True, "bot_task_alive": alive}


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    alive = _bot_task is not None and not _bot_task.done()
    return {"status": "ok", "bot_task_alive": alive}
