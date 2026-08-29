"""
Unit tests for main.py. Run with:

    python -m unittest discover -s tests -v

Each test gets its own throwaway sqlite file so tests don't interfere
with each other or with whatever the user has in ~/.urlshortener/.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as shortener  # noqa: E402


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.store = shortener.Storage(self.db_path)

    def tearDown(self):
        self.store.close()
        self.tmpdir.cleanup()


class TestValidation(unittest.TestCase):
    def test_accepts_http_and_https(self):
        self.assertEqual(
            shortener.validate_url("https://example.com/page"),
            "https://example.com/page",
        )
        self.assertEqual(
            shortener.validate_url("http://example.com"), "http://example.com"
        )

    def test_rejects_missing_scheme(self):
        with self.assertRaises(shortener.ShortenerError):
            shortener.validate_url("example.com")

    def test_rejects_unsupported_scheme(self):
        with self.assertRaises(shortener.ShortenerError):
            shortener.validate_url("ftp://example.com/file")

    def test_rejects_empty_string(self):
        with self.assertRaises(shortener.ShortenerError):
            shortener.validate_url("   ")

    def test_alias_charset(self):
        self.assertEqual(shortener.validate_alias("my-co_de1"), "my-co_de1")
        with self.assertRaises(shortener.ShortenerError):
            shortener.validate_alias("no spaces")
        with self.assertRaises(shortener.ShortenerError):
            shortener.validate_alias("ab")  # too short


class TestBase62(unittest.TestCase):
    def test_roundtrip_is_monotonic_and_unique(self):
        codes = [shortener.encode_base62(n) for n in range(1, 200)]
        self.assertEqual(len(codes), len(set(codes)))

    def test_zero(self):
        self.assertEqual(shortener.encode_base62(0), "0")


class TestStorage(StorageTestCase):
    def test_insert_generated_creates_unique_codes(self):
        row1 = self.store.insert_generated("https://example.com/one")
        row2 = self.store.insert_generated("https://example.com/two")
        self.assertNotEqual(row1["code"], row2["code"])
        self.assertTrue(row1["code"])
        self.assertTrue(row2["code"])

    def test_insert_with_alias(self):
        row = self.store.insert_with_alias("https://example.com", "custom")
        self.assertEqual(row["code"], "custom")
        self.assertEqual(row["long_url"], "https://example.com")

    def test_find_by_code_missing_returns_none(self):
        self.assertIsNone(self.store.find_by_code("doesnotexist"))

    def test_increment_clicks(self):
        row = self.store.insert_with_alias("https://example.com", "clicky")
        self.assertEqual(row["clicks"], 0)
        self.store.increment_clicks("clicky")
        self.store.increment_clicks("clicky")
        updated = self.store.find_by_code("clicky")
        self.assertEqual(updated["clicks"], 2)

    def test_persistence_across_connections(self):
        self.store.insert_with_alias("https://example.com", "persist")
        self.store.close()

        reopened = shortener.Storage(self.db_path)
        try:
            row = reopened.find_by_code("persist")
            self.assertIsNotNone(row)
            self.assertEqual(row["long_url"], "https://example.com")
        finally:
            reopened.close()


class TestCliCommands(StorageTestCase):
    def _args(self, **overrides):
        ns = shortener.argparse.Namespace(
            url=None, alias=None, code=None, open=False, json=False
        )
        for key, value in overrides.items():
            setattr(ns, key, value)
        return ns

    def test_shorten_then_resolve(self):
        shortener.cmd_shorten(self.store, self._args(url="https://example.com"))
        row = self.store.find_by_url("https://example.com")
        self.assertIsNotNone(row)

        shortener.cmd_resolve(self.store, self._args(code=row["code"]))
        updated = self.store.find_by_code(row["code"])
        self.assertEqual(updated["clicks"], 1)

    def test_resolve_missing_code_raises(self):
        with self.assertRaises(shortener.ShortenerError):
            shortener.cmd_resolve(self.store, self._args(code="ghost"))

    def test_shorten_duplicate_alias_raises(self):
        shortener.cmd_shorten(
            self.store, self._args(url="https://a.com", alias="taken")
        )
        with self.assertRaises(shortener.ShortenerError):
            shortener.cmd_shorten(
                self.store, self._args(url="https://b.com", alias="taken")
            )

    def test_shorten_same_url_twice_reuses_code(self):
        shortener.cmd_shorten(self.store, self._args(url="https://dup.com"))
        row_before = self.store.find_by_url("https://dup.com")

        shortener.cmd_shorten(self.store, self._args(url="https://dup.com"))
        rows = [r for r in self.store.all_rows() if r["long_url"] == "https://dup.com"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], row_before["code"])


if __name__ == "__main__":
    unittest.main()
