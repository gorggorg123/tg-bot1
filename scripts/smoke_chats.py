from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


async def _smoke_chat_list_and_history() -> None:
    from botapp.api.ozon_client import ChatListItem, ChatListResponse
    from botapp.sections.chats import logic

    logic._USER_CACHE.clear()
    logic._USER_THREADS.clear()

    history_called: dict[str, str] = {}

    async def fake_list(*, limit: int = 10, offset: int = 0, **_: object) -> ChatListResponse:
        return ChatListResponse(
            chats=[
                ChatListItem(
                    chat_id="c1",
                    title="Проверочный чат",
                    unread_count=2,
                    last_message={
                        "text": "Привет!",
                        "created_at": "2024-01-01T10:00:00Z",
                        "author_type": "customer",
                    },
                )
            ]
        )

    async def fake_history(chat_id: str, *, limit: int = 30) -> list[dict]:
        history_called["chat_id"] = chat_id
        history_called["limit"] = str(limit)
        return [
            {"text": "Покупатель пишет", "created_at": "2024-01-01T10:00:00Z", "author_type": "customer"},
            {"text": "Ответ продавца", "created_at": "2024-01-01T10:05:00Z", "author_type": "seller"},
            {"attachments": [{"file_name": "cheque.pdf", "size": 2048}]},
        ]

    orig_list = logic.ozon_chat_list
    orig_history = logic.ozon_chat_history
    logic.ozon_chat_list = fake_list
    logic.ozon_chat_history = fake_history

    try:
        text, items, _page, _total_pages = await logic.get_chats_table(user_id=1, page=0, force_refresh=True)
        assert items, "Chat list should have one item"
        assert "Проверочный" in text or "Чаты" in text

        bubbles = await logic.get_chat_bubbles_for_ui(
            user_id=1,
            chat_id="c1",
            force_refresh=True,
            customer_only=False,
            include_seller=True,
            max_messages=10,
        )
        assert any("Покупатель" in b for b in bubbles)
        assert any("Вы:" in b for b in bubbles)
        assert any("📎" in b for b in bubbles), "Attachments should be surfaced"
        assert history_called.get("chat_id") == "c1"
    finally:
        logic.ozon_chat_list = orig_list
        logic.ozon_chat_history = orig_history


async def _smoke_empty_chat_list() -> None:
    from botapp.api.ozon_client import ChatListResponse
    from botapp.sections.chats import logic

    logic._USER_CACHE.clear()
    logic._USER_THREADS.clear()

    async def fake_empty_list(*, limit: int = 10, offset: int = 0, **_: object) -> ChatListResponse:
        return ChatListResponse(chats=[])

    orig_list = logic.ozon_chat_list
    logic.ozon_chat_list = fake_empty_list
    try:
        text, items, _page, _total_pages = await logic.get_chats_table(user_id=2, page=0, force_refresh=True)
        assert not items
        assert "пустые" in text.lower()
    finally:
        logic.ozon_chat_list = orig_list


async def main() -> None:
    await _smoke_chat_list_and_history()
    await _smoke_empty_chat_list()
    print("smoke_chats: ok")


if __name__ == "__main__":
    asyncio.run(main())
