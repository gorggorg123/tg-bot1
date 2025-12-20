from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from threading import Lock
from typing import List

from .schemas import ApprovedAnswer

logger = logging.getLogger(__name__)

APPROVED_MEMORY_DB_PATH_ENV = "APPROVED_MEMORY_DB_PATH"
LEGACY_MEMORY_DB_PATH_ENV = "ITOM_MEMORY_DB_PATH"
DEFAULT_DB_NAME = "data/approved_memory.sqlite"
DEFAULT_MAX_RECORDS = 20_000


def _default_path() -> Path:
    for env_name in (APPROVED_MEMORY_DB_PATH_ENV, LEGACY_MEMORY_DB_PATH_ENV):
        env_path = os.getenv(env_name)
        if env_path:
            try:
                return Path(env_path).expanduser().resolve()
            except Exception:
                continue
    base = Path(__file__).resolve().parents[2]
    return (base / DEFAULT_DB_NAME).resolve()


def _sanitize_text(text: str) -> str:
    s = (text or "").strip()
    # Remove phones/emails to avoid leaking PII
    s = re.sub(r"\b\+?\d[\d\s\-()]{6,}\b", "[номер скрыт]", s)
    s = re.sub(r"[\w.%-]+@[\w.-]+\.[A-Za-z]{2,6}", "[email скрыт]", s)
    return s[:2000]


class ApprovedMemoryStore:
    def __init__(self, path: str | Path | None = None, *, max_records: int = DEFAULT_MAX_RECORDS):
        self.path = Path(path or _default_path()).resolve()
        self.max_records = max_records
        self._lock = Lock()
        self._init_db()

    def _init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approved_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    ozon_entity_id TEXT NOT NULL,
                    product_id TEXT,
                    product_name TEXT,
                    rating INTEGER,
                    input_text TEXT NOT NULL,
                    answer_text TEXT NOT NULL,
                    meta TEXT,
                    sent_at TEXT NOT NULL,
                    hash TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approved_kind ON approved_answers(kind)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approved_sent_at ON approved_answers(sent_at)"
            )

    def _compute_hash(self, rec: ApprovedAnswer) -> str:
        base = "|".join(
            [
                (rec.kind or "").strip(),
                (rec.ozon_entity_id or "").strip(),
                (rec.answer_text or "").strip(),
            ]
        )
        return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()

    def add_approved_answer(self, rec: ApprovedAnswer) -> bool:
        rec.input_text = _sanitize_text(rec.input_text)
        rec.answer_text = _sanitize_text(rec.answer_text)
        rec.hash = rec.hash or self._compute_hash(rec)

        payload = (
            rec.kind,
            rec.ozon_entity_id,
            rec.product_id,
            rec.product_name,
            int(rec.rating) if rec.rating is not None else None,
            rec.input_text,
            rec.answer_text,
            json.dumps(rec.meta or {}),
            rec.ts,
            rec.hash,
        )

        with self._lock, sqlite3.connect(self.path) as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO approved_answers (
                        kind, ozon_entity_id, product_id, product_name, rating, input_text, answer_text, meta, sent_at, hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
            except Exception as exc:
                logger.warning("Failed to persist approved answer %s: %s", rec.hash, exc)
                return False

            if conn.total_changes == 0:
                return False

            self._enforce_limit(conn)

        logger.info(
            "Approved memory added kind=%s entity=%s hash=%s bytes=%d",
            rec.kind,
            rec.ozon_entity_id,
            rec.hash,
            len(rec.answer_text.encode("utf-8", errors="ignore")),
        )
        return True

    def _enforce_limit(self, conn: sqlite3.Connection) -> None:
        try:
            cur = conn.execute("SELECT COUNT(*) FROM approved_answers")
            total = cur.fetchone()[0]
            if total <= self.max_records:
                return
            to_delete = total - self.max_records
            conn.execute(
                "DELETE FROM approved_answers WHERE hash IN (SELECT hash FROM approved_answers ORDER BY sent_at ASC LIMIT ?)",
                (to_delete,),
            )
        except Exception as exc:
            logger.warning("Failed to enforce memory limit: %s", exc)

    def query_similar(
        self,
        *,
        kind: str,
        input_text: str,
        product_id: str | None = None,
        limit: int = 5,
    ) -> List[ApprovedAnswer]:
        text = _sanitize_text(input_text)
        if not text:
            return []

        tokens = self._tokenize(text)
        pid_clean = (product_id or "").strip()

        with self._lock, sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                "SELECT sent_at, kind, ozon_entity_id, product_id, product_name, rating, input_text, answer_text, meta, hash"
                " FROM approved_answers WHERE kind = ?",
                (kind,),
            )
            rows = cur.fetchall()

        scored: list[tuple[float, ApprovedAnswer]] = []
        for row in rows:
            meta = {}
            try:
                meta = json.loads(row[8]) if row[8] else {}
            except Exception:
                meta = {}
            rec = ApprovedAnswer(
                ts=row[0],
                kind=row[1],
                ozon_entity_id=row[2],
                product_id=row[3],
                product_name=row[4],
                rating=row[5],
                input_text=row[6],
                answer_text=row[7],
                meta=meta,
                hash=row[9],
            )
            score = self._score(tokens, rec, pid_clean)
            if score > 0:
                scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = [rec for _, rec in scored[: max(1, min(limit, 10))]]
        return out

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"[\wёЁа-яА-Я]+", text.lower()))

    def _score(self, tokens: set[str], rec: ApprovedAnswer, product_id: str) -> float:
        rec_tokens = self._tokenize(rec.input_text)
        if not rec_tokens:
            return 0.0
        overlap = tokens & rec_tokens
        score = float(len(overlap))

        triggers = {
            "упаков", "брак", "комплект", "инструкц", "доставка", "возврат", "гарант",
            "размер", "качество", "поврежден", "опоздал", "комплектн",
        }
        if triggers & tokens & rec_tokens:
            score += 2.5

        if product_id and rec.product_id and product_id == rec.product_id:
            score += 5.0

        return score


_approved_memory_store: ApprovedMemoryStore | None = None


def get_approved_memory_store() -> ApprovedMemoryStore:
    global _approved_memory_store
    if _approved_memory_store is None:
        _approved_memory_store = ApprovedMemoryStore()
    return _approved_memory_store


__all__ = [
    "ApprovedMemoryStore",
    "ApprovedAnswer",
    "get_approved_memory_store",
    "APPROVED_MEMORY_DB_PATH_ENV",
]
