import asyncio
import unittest
from unittest.mock import patch

from botapp.jobs import outreach_sender
from botapp.jobs.outreach_sender import OutreachJob, _compute_idempotency_key, enqueue_outreach


class OutreachSenderEnqueueTest(unittest.TestCase):
    def setUp(self):
        outreach_sender._queue = asyncio.Queue()

    def test_idempotency_computed_when_missing(self):
        job = OutreachJob(user_id=1, chat_id="c1", text="hello", created_at=outreach_sender.datetime.now(outreach_sender.timezone.utc), idempotency_key="")
        added = {}

        with patch.object(outreach_sender, "is_outreach_enabled", return_value=True), patch.object(
            outreach_sender.oqs, "is_sent", return_value=False
        ), patch.object(outreach_sender.oqs, "is_pending", return_value=False), patch.object(
            outreach_sender.oqs, "add_pending_job", side_effect=lambda payload: added.update(payload)
        ):
            enqueue_outreach(job)

        expected_key = _compute_idempotency_key(job)
        self.assertEqual(added.get("idempotency_key"), expected_key)
        self.assertEqual(job.idempotency_key, expected_key)

    def test_already_sent_is_skipped(self):
        job = OutreachJob(user_id=2, chat_id="c2", text="bye", created_at=outreach_sender.datetime.now(outreach_sender.timezone.utc))
        with patch.object(outreach_sender, "is_outreach_enabled", return_value=True), patch.object(
            outreach_sender.oqs, "is_sent", return_value=True
        ), patch.object(outreach_sender.oqs, "is_pending", return_value=False), patch.object(
            outreach_sender.oqs, "add_pending_job"
        ) as mocked_add:
            enqueue_outreach(job)
            mocked_add.assert_not_called()
        self.assertEqual(outreach_sender._queue.qsize(), 0)


if __name__ == "__main__":
    unittest.main()
