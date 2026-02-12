import unittest

import botapp.catalog_cache as catalog_cache
from botapp.utils import section_refs_store
from botapp.utils.storage import ROOT


class PersistencePathsTest(unittest.TestCase):
    def test_catalog_cache_path_uses_storage_root(self):
        self.assertEqual(catalog_cache.SKU_TITLE_CACHE_PATH, ROOT / "sku_title_cache.json")

    def test_section_refs_path_uses_storage_root(self):
        self.assertTrue(str(section_refs_store.SECTION_REFS_FILE).startswith(str(ROOT)))


if __name__ == "__main__":
    unittest.main()
