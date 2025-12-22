import asyncio
import importlib
import os
import tempfile
import unittest
from datetime import datetime, timezone

from botapp.ozon_client import ChatListResponse
from botapp.sections.chats.logic import (
    NormalizedMessage,
    extract_media_urls_from_text,
    normalize_thread_messages,
    resolve_product_title_for_message,
)
from botapp.utils import storage
from botapp.sections.chats.logic import _bubble_text  # type: ignore


class ChatListNormalizationTest(unittest.TestCase):
    def test_nested_chat_block_is_flattened(self):
        payload = {
            "items": [
                {
                    "first_unread_message_id": 2,
                    "last_message_id": 5,
                    "unread_count": 1,
                    "chat": {
                        "chat_id": "chat-abc123",
                        "chat_type": "BUYER_SELLER",
                        "chat_status": "OPEN",
                        "created_at": "2024-01-01T10:00:00Z",
                    },
                }
            ]
        }

        resp = ChatListResponse.model_validate(payload)
        items = list(resp.iter_items())
        self.assertEqual(len(items), 1)
        item = items[0]

        self.assertEqual(item.chat_id, "chat-abc123")
        self.assertEqual(item.chat_type, "BUYER_SELLER")
        self.assertEqual(item.chat_status, "OPEN")
        self.assertEqual(item.first_unread_message_id, 2)
        self.assertEqual(item.last_message_id, 5)
        self.assertEqual(item.unread_count, 1)


class ChatHistoryFilterTest(unittest.TestCase):
    def test_notification_messages_are_skipped(self):
        messages = [
            {"message_id": 1, "text": "service", "user": {"type": "NotificationUser"}},
            {"message_id": 2, "text": "buyer text", "user": {"type": "Customer"}},
            {"message_id": 3, "text": "we reply", "user": {"type": "Seller"}},
        ]

        normalized = normalize_thread_messages(messages, customer_only=True, include_seller=True)
        texts = [m.text for m in normalized]

        self.assertEqual(texts, ["buyer text", "we reply"])

    def test_messages_sorted_by_time_and_id(self):
        messages = [
            {"message_id": 5, "created_at": "2024-01-02T10:00:00Z", "text": "later", "user": {"type": "Seller"}},
            {"message_id": 2, "created_at": "2024-01-01T10:00:00Z", "text": "first", "user": {"type": "Customer"}},
            {"message_id": 3, "created_at": "2024-01-01T11:00:00Z", "text": "second", "user": {"type": "Customer"}},
        ]

        normalized = normalize_thread_messages(messages, customer_only=True, include_seller=True)
        texts = [m.text for m in normalized]

        self.assertEqual(texts, ["first", "second", "later"])


class ChatProductTitleTest(unittest.TestCase):
    def test_bubble_contains_product_title(self):
        msg = NormalizedMessage(role="buyer", text="hi", product_title="Очень хороший товар 🔥")
        bubble = _bubble_text(msg)
        self.assertIn("Товар:", bubble)
        self.assertIn("Очень хороший товар", bubble)

    def test_resolve_uses_chat_title_fallback(self):
        msg = NormalizedMessage(role="buyer", text="hi")
        title = asyncio.run(resolve_product_title_for_message(1, msg, chat_title="Чудо-чайник"))
        self.assertEqual(title, "Чудо-чайник")


class MediaExtractionTest(unittest.TestCase):
    def test_media_urls_are_removed_from_text(self):
        text = "Фото тут ![](https://api-seller.ozon.ru/v2/chat/file/pic1.jpg) и ссылка https://api-seller.ozon.ru/v2/chat/file/pic2.jpg"
        clean, urls = extract_media_urls_from_text(text)
        self.assertEqual(clean, "Фото тут и ссылка")
        self.assertIn("pic1.jpg", "".join(urls))
        self.assertIn("pic2.jpg", "".join(urls))


class ChatAIStateStorageTest(unittest.TestCase):
    def test_chat_ai_state_persistence(self):
        prev = os.environ.get("STORAGE_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["STORAGE_DIR"] = tmp
            importlib.reload(storage)

            state = storage.ChatAIState(
                chat_id="cid-1",
                user_id=42,
                draft_text="Привет",
                draft_created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                last_user_prompt="будь дружелюбным",
                ui_message_id=777,
                last_opened_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
            )
            storage.save_chat_ai_state(user_id=state.user_id, chat_id=state.chat_id, state=state)

            loaded = storage.load_chat_ai_state(42, "cid-1")
            self.assertIsNotNone(loaded)
            assert loaded  # mypy/typing
            self.assertEqual(loaded.draft_text, "Привет")
            self.assertEqual(loaded.last_user_prompt, "будь дружелюбным")
            self.assertEqual(loaded.ui_message_id, 777)

            storage.clear_chat_ai_state(42, "cid-1")
            self.assertIsNone(storage.load_chat_ai_state(42, "cid-1"))
        if prev is not None:
            os.environ["STORAGE_DIR"] = prev
        importlib.reload(storage)


if __name__ == "__main__":
    unittest.main()
