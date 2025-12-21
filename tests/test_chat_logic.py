import unittest

from botapp.ozon_client import ChatListResponse
from botapp.sections.chats.logic import normalize_thread_messages


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


if __name__ == "__main__":
    unittest.main()
