"""Export approved AI memory to JSONL (stdout by default)."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from botapp.ai_memory.store import get_approved_memory_store


def _rows(store_path: Path):
    with sqlite3.connect(store_path) as conn:
        cur = conn.execute(
            """
            SELECT kind, ozon_entity_id, product_id, product_name, rating,
                   input_text, answer_text, meta, sent_at, hash
            FROM approved_answers
            ORDER BY sent_at DESC
            """
        )
        yield from cur.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser(description="Export approved AI memory to JSONL")
    parser.add_argument("--output", type=Path, default=None, help="Path to write JSONL; default stdout")
    args = parser.parse_args()

    store = get_approved_memory_store()
    records = _rows(store.path)

    sink = None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        sink = args.output.open("w", encoding="utf-8")

    def _emit(line: str) -> None:
        if sink:
            sink.write(line + "\n")
        else:
            print(line)

    try:
        for row in records:
            payload = {
                "kind": row[0],
                "ozon_entity_id": row[1],
                "product_id": row[2],
                "product_name": row[3],
                "rating": row[4],
                "input_text": row[5],
                "answer_text": row[6],
                "meta": json.loads(row[7]) if row[7] else {},
                "sent_at": row[8],
                "hash": row[9],
            }
            _emit(json.dumps(payload, ensure_ascii=False))
    finally:
        if sink:
            sink.close()


if __name__ == "__main__":
    main()
