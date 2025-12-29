import asyncio
import unittest

from aiogram.exceptions import TelegramBadRequest

from botapp.utils import message_gc


class MessageGCSafeClearTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
