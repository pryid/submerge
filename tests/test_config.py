import json
import tempfile
import unittest
from pathlib import Path

from submerge.config import ReloadingJSON


class ConfigTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "config.json"
        self.cache = ReloadingJSON(lambda data, _: data)

    def test_invalid_initial_configuration_fails(self):
        self.path.write_text("{")
        with self.assertRaises(ValueError):
            self.cache.get(self.path)

    def test_missing_required_configuration_fails(self):
        with self.assertRaises(FileNotFoundError):
            self.cache.get(self.path)

    def test_missing_optional_configuration_can_be_created_later(self):
        cache = ReloadingJSON(lambda data, _: data, default_factory=dict)
        self.assertEqual(cache.get(self.path), {})
        self.path.write_text('{"enabled": true}')
        self.assertEqual(cache.get(self.path), {"enabled": True})

    def test_deletion_keeps_previous_configuration(self):
        self.path.write_text('["first"]')
        self.cache.get(self.path)
        self.path.unlink()
        with self.assertLogs(level="WARNING"):
            self.assertEqual(self.cache.get(self.path), ["first"])

    def test_invalid_reload_warns_once_and_recovers(self):
        self.path.write_text('["first"]')
        self.cache.get(self.path)
        self.path.write_text("{")
        with self.assertLogs(level="WARNING") as logs:
            self.assertEqual(self.cache.get(self.path), ["first"])
            self.assertEqual(self.cache.get(self.path), ["first"])
        self.assertEqual(len(logs.output), 1)
        self.path.write_text(json.dumps(["recovered"]))
        self.assertEqual(self.cache.get(self.path), ["recovered"])

    def test_validation_failure_does_not_replace_previous_value(self):
        def parse(data, _):
            if not isinstance(data, list):
                raise ValueError("Expected an array")
            return data

        cache = ReloadingJSON(parse)
        self.path.write_text('["first"]')
        cache.get(self.path)
        self.path.write_text("{}")
        with self.assertLogs(level="WARNING"):
            self.assertEqual(cache.get(self.path), ["first"])


if __name__ == "__main__":
    unittest.main()
