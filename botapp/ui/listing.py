"""Shared helpers for paginated list rendering."""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple, TypeVar

T = TypeVar("T")


def slice_page(items: Sequence[T] | Iterable[T], page: int, page_size: int) -> tuple[list[T], int, int]:
    items_list = list(items)
    total = len(items_list)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * page_size
    end = start + page_size
    return items_list[start:end], safe_page, total_pages


def format_period_header(title: str, pretty_period: str, page: int, total_pages: int) -> str:
    """Форматирует заголовок списка с красивым оформлением."""
    lines = [
        f"📂 <b>{title}</b>",
        f"📅 Период: {pretty_period}",
        f"📄 Страница: {page + 1}/{max(total_pages, 1)}",
    ]
    return "\n".join(lines).strip() or " "


__all__ = ["format_period_header", "slice_page"]
