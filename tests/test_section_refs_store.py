import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botapp.utils import section_refs_store as store


class SectionRefsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp_dir.name) / "section_refs.json"
        store._reset_for_tests(self.file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_set_and_get_ref_are_persistent(self):
        store.set_ref(1, "menu", 11, 22)
        ref = store.get_ref(1, "menu")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.chat_id, 11)
        self.assertEqual(ref.message_id, 22)
        store.flush(force=True)

        store._reset_for_tests(self.file)
        ref_again = store.get_ref(1, "menu")
        self.assertIsNotNone(ref_again)
        self.assertEqual(ref_again.chat_id, 11)
        self.assertEqual(ref_again.message_id, 22)

    def test_prune_removes_expired_refs(self):
        old_payload = {
            "2": {
                "menu": {
                    "chat_id": 33,
                    "message_id": 44,
                    "updated_at": (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
                }
            }
        }
        self.file.write_text(json.dumps(old_payload), encoding="utf-8")
        store._reset_for_tests(self.file)

        ref = store.get_ref(2, "menu")
        self.assertIsNone(ref)
        # After prune the store file should contain no refs
        content = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(content, {})

    def test_mark_stale_removes_ref(self):
        store.set_ref(3, "chat", 55, 66)
        store.mark_stale(3, "chat")
        self.assertIsNone(store.get_ref(3, "chat"))


if __name__ == "__main__":
    unittest.main()
