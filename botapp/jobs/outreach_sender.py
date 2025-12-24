from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from time import monotonic
from uuid import uuid4

from botapp.ozon_client import send_outreach_message
from botapp.utils import outreach_queue_store as oqs
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
    template_id: str | None = None
    template_version: str | None = None
    idempotency_key: str = field(default_factory=lambda: uuid4().hex)
    attempts: int = 0


_queue: asyncio.Queue[OutreachJob] = asyncio.Queue()
_last_send_at_monotonic: float | None = None

GLOBAL_OUTREACH_INTERVAL_SECONDS = 2
RETRY_BACKOFF_SECONDS = [10, 30, 120]


def _job_to_dict(job: OutreachJob) -> dict:
    return {
        "user_id": job.user_id,
        "chat_id": job.chat_id,
        "text": job.text,
        "created_at": job.created_at.isoformat(),
        "template_id": job.template_id,
        "template_version": job.template_version,
        "idempotency_key": job.idempotency_key,
        "attempts": job.attempts,
    }


def _dict_to_job(payload: dict) -> OutreachJob | None:
    try:
        created_at_raw = payload.get("created_at")
        created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.utcnow()
        return OutreachJob(
            user_id=int(payload.get("user_id")),
            chat_id=str(payload.get("chat_id")),
            text=str(payload.get("text")),
            created_at=created_at,
            template_id=payload.get("template_id"),
            template_version=payload.get("template_version"),
            idempotency_key=str(payload.get("idempotency_key") or uuid4().hex),
            attempts=int(payload.get("attempts") or 0),
        )
    except Exception:
        logger.exception("Failed to deserialize outreach job payload=%s", payload)
        return None


def _compute_idempotency_key(job: OutreachJob) -> str:
    base = f"{job.user_id}:{job.chat_id}:{job.template_id or 'custom'}:{job.template_version or 'v1'}"
    text_hash = sha256((job.text or '').strip().encode()).hexdigest()[:8]
    return f"{base}:{text_hash}"


def _bootstrap_pending_jobs() -> None:
    pending = oqs.load_pending_jobs()
    restored = 0
    for payload in pending:
        job = _dict_to_job(payload)
        if not job:
            continue
        if not job.idempotency_key:
            job.idempotency_key = _compute_idempotency_key(job)
        try:
            _queue.put_nowait(job)
            restored += 1
        except asyncio.QueueFull:
            logger.warning("Outreach queue is full during bootstrap; dropping restored job chat=%s", job.chat_id)
            break
    if restored:
        logger.info("Restored %s outreach job(s) from persistent queue", restored)


def enqueue_outreach(job: OutreachJob) -> None:
    if not is_outreach_enabled(job.user_id):
        logger.info(
            "Skip enqueue outreach: disabled user_id=%s chat=%s template=%s version=%s",
            job.user_id,
            job.chat_id,
            job.template_id,
            job.template_version,
        )
        return
    if not job.idempotency_key:
        job.idempotency_key = _compute_idempotency_key(job)
    if oqs.is_sent(job.idempotency_key):
        logger.info(
            "Skip enqueue outreach: already sent idempotency_key=%s user_id=%s chat=%s",
            job.idempotency_key,
            job.user_id,
            job.chat_id,
        )
        return
    if oqs.is_pending(job.idempotency_key):
        logger.info(
            "Skip enqueue outreach: already pending idempotency_key=%s user_id=%s chat=%s",
            job.idempotency_key,
            job.user_id,
            job.chat_id,
        )
        return
    try:
        _queue.put_nowait(job)
    except asyncio.QueueFull:
        logger.warning("Outreach queue is full; dropping job chat=%s", job.chat_id)
        return
    oqs.add_pending_job(_job_to_dict(job))
    logger.info(
        "Enqueued outreach job user_id=%s chat=%s template=%s version=%s idempotency_key=%s size=%s",
        job.user_id,
        job.chat_id,
        job.template_id,
        job.template_version,
        job.idempotency_key,
        _queue.qsize(),
    )


async def outreach_sender_loop(stop_event: asyncio.Event) -> None:
    global _last_send_at_monotonic
    _bootstrap_pending_jobs()
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
                oqs.remove_pending_job(job.idempotency_key)
                continue

            if not is_outreach_enabled(job.user_id):
                logger.info(
                    "Outreach disabled; skipping job user_id=%s chat=%s idempotency_key=%s",
                    job.user_id,
                    job.chat_id,
                    job.idempotency_key,
                )
                oqs.remove_pending_job(job.idempotency_key)
                continue

            if oqs.is_sent(job.idempotency_key):
                logger.info(
                    "Outreach idempotency skip (sent) user_id=%s chat=%s idempotency_key=%s",
                    job.user_id,
                    job.chat_id,
                    job.idempotency_key,
                )
                oqs.remove_pending_job(job.idempotency_key)
                continue

            now = monotonic()
            if _last_send_at_monotonic is not None:
                elapsed = now - _last_send_at_monotonic
                delay = GLOBAL_OUTREACH_INTERVAL_SECONDS - elapsed
                if delay > 0:
                    await asyncio.sleep(delay)

            logger.info(
                "Sending outreach user_id=%s chat=%s template=%s version=%s idempotency_key=%s",
                job.user_id,
                job.chat_id,
                job.template_id,
                job.template_version,
                job.idempotency_key,
            )
            ok, err, status = await send_outreach_message(
                chat_id=job.chat_id,
                text=job.text,
                idempotency_key=job.idempotency_key,
            )
            _last_send_at_monotonic = monotonic()
            if ok:
                logger.info(
                    "Outreach sent user_id=%s chat=%s idempotency_key=%s",
                    job.user_id,
                    job.chat_id,
                    job.idempotency_key,
                )
                oqs.remove_pending_job(job.idempotency_key)
                oqs.mark_sent(job.idempotency_key)
            else:
                retryable = status in (429, 500, 502, 503, 504)
                logger.warning(
                    "Outreach failed user_id=%s chat=%s idempotency_key=%s error=%s status=%s attempts=%s",
                    job.user_id,
                    job.chat_id,
                    job.idempotency_key,
                    err,
                    status,
                    job.attempts,
                )
                if retryable and job.attempts < len(RETRY_BACKOFF_SECONDS):
                    job.attempts += 1
                    oqs.update_pending_job(_job_to_dict(job), idempotency_key=job.idempotency_key)
                    backoff = RETRY_BACKOFF_SECONDS[job.attempts - 1]
                    logger.info(
                        "Retrying outreach user_id=%s chat=%s idempotency_key=%s backoff=%ss",
                        job.user_id,
                        job.chat_id,
                        job.idempotency_key,
                        backoff,
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                    if stop_event.is_set():
                        logger.info("Stop requested during backoff; dropping outreach job chat=%s", job.chat_id)
                        continue
                    try:
                        _queue.put_nowait(job)
                    except asyncio.QueueFull:
                        logger.warning("Outreach queue is full; dropping retry for chat=%s", job.chat_id)
                    continue
                oqs.remove_pending_job(job.idempotency_key)
                oqs.append_dead_letter(
                    {
                        "idempotency_key": job.idempotency_key,
                        "user_id": job.user_id,
                        "chat_id": job.chat_id,
                        "template_id": job.template_id,
                        "template_version": job.template_version,
                        "error": err,
                        "status": status,
                        "attempts": job.attempts,
                        "failed_at": datetime.utcnow().isoformat(),
                    }
                )
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
