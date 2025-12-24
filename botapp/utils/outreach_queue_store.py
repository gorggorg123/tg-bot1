from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DATA_DIR = (Path(__file__).resolve().parent.parent / "data").resolve()
QUEUE_PATH = DATA_DIR / "outreach_queue.json"
SENT_PATH = DATA_DIR / "outreach_sent.json"
DEAD_PATH = DATA_DIR / "outreach_dead.json"

SENT_TTL_DAYS = 30


def _ensure_dir(path: Path) -> None:
    os.makedirs(path.parent, exist_ok=True)


def _atomic_write(path: Path, payload: Any) -> None:
    _ensure_dir(path)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2, default=str)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _read_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("Failed to read JSON list from %s", path)
    return []


def _read_json_dict(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        logger.exception("Failed to read JSON dict from %s", path)
    return {}


def load_pending_jobs() -> list[dict]:
    return _read_json_list(QUEUE_PATH)


def save_pending_jobs(jobs: Iterable[dict]) -> None:
    payload = [job for job in jobs if isinstance(job, dict)]
    _atomic_write(QUEUE_PATH, payload)


def add_pending_job(job: dict) -> None:
    jobs = load_pending_jobs()
    jobs.append(job)
    save_pending_jobs(jobs)


def update_pending_job(job: dict, *, idempotency_key: str) -> None:
    jobs = load_pending_jobs()
    updated = False
    for idx, existing in enumerate(jobs):
        if not isinstance(existing, dict):
            continue
        if str(existing.get("idempotency_key")) == str(idempotency_key):
            jobs[idx] = job
            updated = True
            break
    if not updated:
        jobs.append(job)
    save_pending_jobs(jobs)


def remove_pending_job(idempotency_key: str) -> None:
    jobs = load_pending_jobs()
    filtered = [job for job in jobs if str(job.get("idempotency_key")) != str(idempotency_key)]
    save_pending_jobs(filtered)


def is_pending(idempotency_key: str) -> bool:
    return any(str(job.get("idempotency_key")) == str(idempotency_key) for job in load_pending_jobs())


def _purge_sent(data: dict) -> dict:
    now = datetime.now(timezone.utc)
    cleaned: dict[str, str] = {}
    for key, iso_exp in data.items():
        try:
            exp_dt = datetime.fromisoformat(iso_exp)
        except Exception:
            continue
        if exp_dt > now:
            cleaned[key] = iso_exp
    if cleaned != data:
        _atomic_write(SENT_PATH, cleaned)
    return cleaned


def is_sent(idempotency_key: str) -> bool:
    data = _purge_sent(_read_json_dict(SENT_PATH))
    return str(idempotency_key) in data


def mark_sent(idempotency_key: str, *, ttl_days: int = SENT_TTL_DAYS) -> None:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=ttl_days)
    data = _purge_sent(_read_json_dict(SENT_PATH))
    data[str(idempotency_key)] = exp.isoformat()
    _atomic_write(SENT_PATH, data)


def append_dead_letter(entry: dict) -> None:
    letters = _read_json_list(DEAD_PATH)
    letters.append(entry)
    _atomic_write(DEAD_PATH, letters)


__all__ = [
    "add_pending_job",
    "append_dead_letter",
    "is_pending",
    "is_sent",
    "load_pending_jobs",
    "mark_sent",
    "remove_pending_job",
    "save_pending_jobs",
    "update_pending_job",
]
