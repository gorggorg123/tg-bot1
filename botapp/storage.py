"""Helpers for working with persistent storage on Render Disks or locally."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

DEFAULT_DATA_DIR = "./data"
QUESTIONS_FILE_NAME = "questions_dataset.jsonl"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """Return directory for persistent data, creating it on first access."""

    base = os.getenv("DATA_DIR") or "/var/data"
    target = Path(base)
    if not target.exists():
        # Fallback for local development
        target = Path(DEFAULT_DATA_DIR)

    return _ensure_dir(target)


def get_questions_dataset_path() -> Path:
    """Return path to the JSONL dataset with customer Q&A."""

    return get_data_dir() / QUESTIONS_FILE_NAME


def append_question_record(record: Dict) -> None:
    """Append a single JSON object as a line into dataset."""

    path = get_questions_dataset_path()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_all_records(path: Path) -> list[Dict]:
    if not path.exists():
        return []
    items: list[Dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items


def upsert_question_answer(question_id: str, **fields: object) -> None:
    """Update or create a record by ``question_id`` in JSONL dataset."""

    path = get_questions_dataset_path()
    items = _read_all_records(path)
    updated = False
    for item in items:
        if item.get("question_id") == question_id:
            item.update(fields)
            updated = True
            break
    if not updated:
        item: Dict[str, object] = {"question_id": question_id}
        item.update(fields)
        items.append(item)

    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8")


__all__ = [
    "get_data_dir",
    "get_questions_dataset_path",
    "append_question_record",
    "upsert_question_answer",
]
