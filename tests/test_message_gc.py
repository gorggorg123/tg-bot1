import asyncio
import tempfile
import unittest
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest

from botapp.utils import message_gc, section_refs_store


class MessageGCSafeClearTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        section_refs_store._reset_for_tests(Path(self.tmp_dir.name) / "section_refs.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_safe_clear_not_found_is_success(self):
        class DummyBot:
            async def edit_message_text(self, **kwargs):
                raise TelegramBadRequest(method=None, message="message to edit not found")

        ok = asyncio.run(message_gc._safe_clear(DummyBot(), 1, 2))
        self.assertTrue(ok)

    def test_safe_clear_not_modified_is_success(self):
        class DummyBot:
            async def edit_message_text(self, **kwargs):
                raise TelegramBadRequest(method=None, message="message is not modified")

        ok = asyncio.run(message_gc._safe_clear(DummyBot(), 1, 2))
        self.assertTrue(ok)

    def test_safe_delete_not_found_is_success(self):
        class DummyBot:
            async def delete_message(self, **kwargs):
                raise TelegramBadRequest(method=None, message="message to delete not found")

        ok = asyncio.run(message_gc._safe_delete(DummyBot(), 1, 2))
        self.assertTrue(ok)


class MessageGCMenuAnchorTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        section_refs_store._reset_for_tests(Path(self.tmp_dir.name) / "section_refs.json")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_menu_renders_on_trigger_without_creating_new_message(self):
        class DummyBot:
            def __init__(self):
                self.edit_called = 0
                self.send_called = 0

            async def edit_message_text(self, **kwargs):
                self.edit_called += 1
                # Симулируем edit на месте меню-триггера без отправки нового сообщения.
                raise TelegramBadRequest(method=None, message="message is not modified")

            async def send_message(self, **kwargs):
                self.send_called += 1
                raise AssertionError("send_message should not be used when edit succeeds on trigger")

        class DummyChat:
            def __init__(self, chat_id):
                self.id = chat_id

        class DummyMessage:
            def __init__(self, mid, chat_id):
                self.message_id = mid
                self.chat = DummyChat(chat_id)

        class DummyCallback:
            def __init__(self, message):
                self.message = message

        user_id = 7
        chat_id = 99
        trigger_mid = 555
        callback = DummyCallback(DummyMessage(trigger_mid, chat_id))
        bot = DummyBot()

        result = asyncio.run(
            message_gc.render_section(
                message_gc.SECTION_MENU,
                bot=bot,
                chat_id=chat_id,
                user_id=user_id,
                text="menu",
                callback=callback,
            )
        )

        self.assertIsNone(result)  # NOT_MODIFIED -> None
        self.assertEqual(bot.edit_called, 1)
        self.assertEqual(bot.send_called, 0)
        self.assertEqual(message_gc.get_section_message_id(user_id, message_gc.SECTION_MENU), trigger_mid)


if __name__ == "__main__":
    unittest.main()
