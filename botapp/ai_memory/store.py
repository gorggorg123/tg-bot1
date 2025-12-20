from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Iterable, List

from .schemas import MemoryRecord

logger = logging.getLogger(__name__)

ITOM_MEMORY_DB_PATH_ENV = "ITOM_MEMORY_DB_PATH"
DEFAULT_DB_NAME = "data/itom_memory.sqlite"
DEFAULT_MAX_RECORDS = 20_000


def _default_path() -> Path:
    env_path = os.getenv(ITOM_MEMORY_DB_PATH_ENV)
    if env_path:
        try:
            return Path(env_path).expanduser().resolve()
        except Exception:
            pass
    base = Path(__file__).resolve().parents[2]
    return (base / DEFAULT_DB_NAME).resolve()


def _sanitize_text(text: str) -> str:
    s = (text or "").strip()
    # Remove phones/emails to avoid leaking PII
    s = re.sub(r"\b\+?\d[\d\s\-()]{6,}\b", "[номер скрыт]", s)
    s = re.sub(r"[\w.%-]+@[\w.-]+\.[A-Za-z]{2,6}", "[email скрыт]", s)
    return s[:2000]


class MemoryStore:
    def __init__(self, path: str | Path | None = None, *, max_records: int = DEFAULT_MAX_RECORDS):
        self.path = Path(path or _default_path()).resolve()
        self.max_records = max_records
        self._lock = Lock()
        self._init_db()
        self._maybe_seed_from_digest()

    def _init_db(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory (
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    input_text TEXT NOT NULL,
                    output_text TEXT NOT NULL,
                    sku TEXT,
                    product_title TEXT,
                    meta TEXT,
                    source TEXT NOT NULL,
                    hash TEXT PRIMARY KEY
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_ts ON memory(ts)")

    def _maybe_seed_from_digest(self) -> None:
        digest_path = Path(__file__).resolve().parents[2] / "data" / "itom_qna_digest.txt"
        if not digest_path.exists():
            return
        try:
            content = digest_path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return
        if not content:
            return

        added = 0
        for block in content.split("\n\n"):
            lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
            if len(lines) < 2:
                continue
            question = lines[0]
            answer = "\n".join(lines[1:])
            rec = MemoryRecord.now_iso(
                kind="question",
                entity_id=f"seed:{hashlib.sha1(question.encode('utf-8')).hexdigest()[:8]}",
                input_text=question,
                output_text=answer,
                meta={"source_file": digest_path.name},
                source="seed",
            )
            if self.add_record(rec, allow_seed=True):
                added += 1
        if added:
            logger.info("Seeded %s memory records from %s", added, digest_path)

    def _compute_hash(self, rec: MemoryRecord) -> str:
        base = "|".join(
            [
                (rec.kind or "").strip(),
                (rec.entity_id or "").strip(),
                (rec.output_text or "").strip(),
            ]
        )
        return hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()

    def add_record(self, rec: MemoryRecord, *, allow_seed: bool = False) -> bool:
        rec.input_text = _sanitize_text(rec.input_text)
        rec.output_text = _sanitize_text(rec.output_text)
        rec.hash = rec.hash or self._compute_hash(rec)

        if rec.source != "sent_to_ozon" and not allow_seed:
            logger.debug("Skip non-approved memory source=%s", rec.source)
            return False

        payload = (
            rec.ts,
            rec.kind,
            rec.entity_id,
            rec.input_text,
            rec.output_text,
            rec.sku,
            rec.product_title,
            json.dumps(rec.meta or {}),
            rec.source,
            rec.hash,
        )

        with self._lock, sqlite3.connect(self.path) as conn:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO memory (
                        ts, kind, entity_id, input_text, output_text, sku, product_title, meta, source, hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
            except Exception as exc:
                logger.warning("Failed to persist memory record %s: %s", rec.hash, exc)
                return False

            if conn.total_changes == 0:
                return False

            self._enforce_limit(conn)

        logger.info(
            "Memory record added kind=%s entity=%s hash=%s bytes=%d",
            rec.kind,
            rec.entity_id,
            rec.hash,
            len(rec.output_text.encode("utf-8", errors="ignore")),
        )
        return True

    def _enforce_limit(self, conn: sqlite3.Connection) -> None:
        try:
            cur = conn.execute("SELECT COUNT(*) FROM memory")
            total = cur.fetchone()[0]
            if total <= self.max_records:
                return
            to_delete = total - self.max_records
            conn.execute(
                "DELETE FROM memory WHERE hash IN (SELECT hash FROM memory ORDER BY ts ASC LIMIT ?)",
                (to_delete,),
            )
        except Exception as exc:
            logger.warning("Failed to enforce memory limit: %s", exc)

    def query_similar(
        self,
        *,
        kind: str,
        input_text: str,
        sku: str | None = None,
        limit: int = 5,
    ) -> List[MemoryRecord]:
        text = _sanitize_text(input_text)
        if not text:
            return []

        tokens = self._tokenize(text)
        sku_clean = (sku or "").strip()

        with self._lock, sqlite3.connect(self.path) as conn:
            cur = conn.execute(
                "SELECT ts, kind, entity_id, input_text, output_text, sku, product_title, meta, source, hash"
                " FROM memory WHERE kind = ? AND source IN ('sent_to_ozon', 'seed')",
                (kind,),
            )
            rows = cur.fetchall()

        scored: list[tuple[float, MemoryRecord]] = []
        for row in rows:
            meta = {}
            try:
                meta = json.loads(row[7]) if row[7] else {}
            except Exception:
                meta = {}
            rec = MemoryRecord(
                ts=row[0],
                kind=row[1],
                entity_id=row[2],
                input_text=row[3],
                output_text=row[4],
                sku=row[5],
                product_title=row[6],
                meta=meta,
                source=row[8],
                hash=row[9],
            )
            score = self._score(tokens, rec, sku_clean)
            if score > 0:
                scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        out = [rec for _, rec in scored[: max(1, min(limit, 10))]]
        return out

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"[\wёЁа-яА-Я]+", text.lower()))

    def _score(self, tokens: set[str], rec: MemoryRecord, sku: str) -> float:
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

        if sku and rec.sku and sku == rec.sku:
            score += 5.0

        if rec.source == "seed":
            score *= 0.65

        return score


_memory_store: MemoryStore | None = None


def get_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store


__all__ = ["MemoryStore", "MemoryRecord", "get_memory_store", "ITOM_MEMORY_DB_PATH_ENV"]
