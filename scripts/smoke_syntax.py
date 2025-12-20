#!/usr/bin/env python3
"""Lightweight syntax smoke test runner.

Runs compileall for the repository and tabnanny for the reviews module.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _smoke_imports() -> None:
    """Ensure critical modules import without side effects or network calls."""

    import botapp.sections.reviews.logic  # noqa: F401
    import botapp.sections.questions.logic  # noqa: F401
    import botapp.sections.chats.logic  # noqa: F401


def _smoke_tables() -> None:
    """Build minimal tables to catch regressions in list formatting."""

    from botapp.sections.reviews.logic import ReviewCard, build_reviews_table
    from botapp.sections.questions.logic import build_questions_table

    sample_review = ReviewCard(
        id="r1",
        rating=5,
        text="test",
        product_name="Product",
        offer_id=None,
        product_id="p1",
        created_at=None,
    )

    build_reviews_table(
        cards=[sample_review],
        pretty_period="",
        category="all",
        page=0,
        page_size=1,
    )

    class _Q:
        def __init__(self, *, id: str):
            self.id = id
            self.created_at = None
            self.product_name = "Product"
            self.sku = None
            self.product_id = "p1"
            self.answer_text = None
            self.has_answer = False

    build_questions_table(
        cards=[_Q(id="q1")],
        pretty_period="",
        category="all",
        page=0,
        page_size=1,
    )


def _smoke_chat_payloads() -> None:
    """Verify chat payload helpers safely handle unexpected shapes."""

    from botapp.ozon_client import (
        _normalize_chat_history_payload,
        _normalize_chat_list_payload,
    )

    assert _normalize_chat_list_payload(None) is None
    payload, weird = _normalize_chat_history_payload(None)
    assert payload is None
    assert weird is True


def main() -> None:
    _run([sys.executable, "-m", "compileall", "-q", str(REPO_ROOT)], cwd=REPO_ROOT)
    _run([sys.executable, "-m", "tabnanny", "-v", str(REPO_ROOT / "botapp" / "reviews.py")], cwd=REPO_ROOT)
    _smoke_imports()
    _smoke_tables()
    _smoke_chat_payloads()


if __name__ == "__main__":
    main()
