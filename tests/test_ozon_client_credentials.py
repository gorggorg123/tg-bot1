import asyncio
import os
import unittest
from unittest.mock import patch

from botapp.api import ozon_client


class OzonCredentialCompatibilityTest(unittest.TestCase):
    def tearDown(self) -> None:
        asyncio.run(ozon_client.close_clients())

    def test_read_credentials_accept_seller_env_aliases(self):
        with patch.dict(
            os.environ,
            {
                "OZON_CLIENT_ID": "",
                "OZON_API_KEY": "",
                "OZON_SELLER_CLIENT_ID": "seller-read-id",
                "OZON_SELLER_API_KEY": "seller-read-key",
            },
            clear=False,
        ):
            client_id, api_key = ozon_client._env_read_credentials()

        self.assertEqual(client_id, "seller-read-id")
        self.assertEqual(api_key, "seller-read-key")

    def test_write_client_uses_dedicated_write_credentials(self):
        with patch.dict(
            os.environ,
            {
                "OZON_CLIENT_ID": "read-id",
                "OZON_API_KEY": "read-key",
                "OZON_WRITE_CLIENT_ID": "write-id",
                "OZON_WRITE_API_KEY": "write-key",
                "OZON_SELLER_CLIENT_ID": "",
                "OZON_SELLER_API_KEY": "",
                "OZON_SELLER_WRITE_CLIENT_ID": "",
                "OZON_SELLER_WRITE_API_KEY": "",
            },
            clear=False,
        ):
            read_client = ozon_client.get_client()
            write_client = ozon_client.get_write_client()

        self.assertIsNotNone(write_client)
        assert write_client is not None
        self.assertEqual(read_client.client_id, "read-id")
        self.assertEqual(write_client.client_id, "write-id")
        self.assertEqual(write_client.api_key, "write-key")
        self.assertIsNot(read_client, write_client)

    def test_write_client_falls_back_to_read_credentials(self):
        with patch.dict(
            os.environ,
            {
                "OZON_CLIENT_ID": "read-id",
                "OZON_API_KEY": "read-key",
                "OZON_WRITE_CLIENT_ID": "",
                "OZON_WRITE_API_KEY": "",
                "OZON_SELLER_CLIENT_ID": "",
                "OZON_SELLER_API_KEY": "",
                "OZON_SELLER_WRITE_CLIENT_ID": "",
                "OZON_SELLER_WRITE_API_KEY": "",
            },
            clear=False,
        ):
            read_client = ozon_client.get_client()
            write_client = ozon_client.get_write_client()

        self.assertIs(read_client, write_client)

    def test_write_client_accepts_seller_write_aliases(self):
        with patch.dict(
            os.environ,
            {
                "OZON_CLIENT_ID": "",
                "OZON_API_KEY": "",
                "OZON_SELLER_CLIENT_ID": "seller-read-id",
                "OZON_SELLER_API_KEY": "seller-read-key",
                "OZON_WRITE_CLIENT_ID": "",
                "OZON_WRITE_API_KEY": "",
                "OZON_SELLER_WRITE_CLIENT_ID": "seller-write-id",
                "OZON_SELLER_WRITE_API_KEY": "seller-write-key",
            },
            clear=False,
        ):
            read_client = ozon_client.get_client()
            write_client = ozon_client.get_write_client()

        self.assertIsNotNone(write_client)
        assert write_client is not None
        self.assertEqual(read_client.client_id, "seller-read-id")
        self.assertEqual(write_client.client_id, "seller-write-id")
        self.assertEqual(write_client.api_key, "seller-write-key")
        self.assertIsNot(read_client, write_client)

    def test_send_question_answer_refuses_invalid_sku_before_network_call(self):
        class DummyClient:
            def __init__(self) -> None:
                self.calls = 0

            async def _post_with_status(self, path, body):
                self.calls += 1
                raise AssertionError("_post_with_status should not be called for invalid SKU")

        dummy_client = DummyClient()
        with patch.object(ozon_client, "get_write_client", return_value=dummy_client):
            ok = asyncio.run(ozon_client.send_question_answer("qid-1", "ready draft", sku=0))

        self.assertFalse(ok)
        self.assertEqual(dummy_client.calls, 0)


if __name__ == "__main__":
    unittest.main()
