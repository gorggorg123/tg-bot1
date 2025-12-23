from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from botapp.ozon_client import chat_send_message
from botapp.utils.storage import (
    get_outreach_interval_seconds,
    is_outreach_enabled,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTREACH_TEXT = (
    "Спасибо, что выбрали нас! Если что-то нужно уточнить по товару или эксплуатации — "
    "пишите в этот чат, ответим оперативно. Хорошего дня и будем рады вашему следующему заказу."
)


@dataclass
class OutreachJob:
    user_id: int
    chat_id: str
    text: str
    created_at: datetime


_queue: asyncio.Queue[OutreachJob] = asyncio.Queue()


def enqueue_outreach(job: OutreachJob) -> None:
    try:
        _queue.put_nowait(job)
    except asyncio.QueueFull:
        logger.warning("Outreach queue is full; dropping job chat=%s", job.chat_id)
        return
    logger.info("Enqueued outreach job chat=%s size=%s", job.chat_id, _queue.qsize())


async def outreach_sender_loop(stop_event: asyncio.Event) -> None:
    logger.info("Outreach sender loop started")
    while True:
        if stop_event.is_set() and _queue.empty():
            break
        try:
            job = await asyncio.wait_for(_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            if stop_event.is_set():
                break
            continue

        try:
            if stop_event.is_set():
                logger.info("Stop requested; dropping outreach job chat=%s", job.chat_id)
                continue

            if not is_outreach_enabled(job.user_id):
                logger.info("Outreach disabled; skipping job chat=%s", job.chat_id)
                continue

            await chat_send_message(job.chat_id, job.text)
            logger.info("Outreach sent chat=%s", job.chat_id)
        except Exception as e:  # noqa: BLE001
            logger.exception("Outreach send failed for chat=%s: %s", job.chat_id, e)
        finally:
            _queue.task_done()

        interval = max(1, get_outreach_interval_seconds(job.user_id))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break

    logger.info("Outreach sender loop stopped")


__all__ = [
    "DEFAULT_OUTREACH_TEXT",
    "OutreachJob",
    "enqueue_outreach",
    "outreach_sender_loop",
]
