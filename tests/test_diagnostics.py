"""Operator checks, using neutral configuration only."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from submerge import diagnostics, service

LINK = "vless://test-secret@node.example.com:443#Demo"
URL = "https://example.com/sub-merge/private-token"


class DoctorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.sources = self.root / "sources.json"
        self.metadata = self.root / "metadata.json"
        self.sources.write_text('["https://private.example.com"]')
        self.metadata.write_text('{"happ":{"routing":{"Name":"Private profile"}}}')
        patcher = patch.multiple(
            service,
            SUB_BASES_FILE=str(self.sources),
            SUB_METADATA_FILE=str(self.metadata),
            SUB_LINK_REWRITES_FILE="",
            SUB_LINK_REWRITES="",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        fetch = patch.object(service, "fetch", side_effect=AssertionError("doctor must be offline"))
        fetch.start()
        self.addCleanup(fetch.stop)

    def run_doctor(self, *required):
        output = io.StringIO()
        with redirect_stdout(output):
            code = diagnostics.doctor_report(required)
        value = output.getvalue()
        self.assertNotIn("private.example.com", value)
        self.assertNotIn("Private profile", value)
        return code, value

    def test_valid_config_and_bundled_templates(self):
        code, output = self.run_doctor("happ")
        self.assertEqual(code, 0)
        self.assertIn("happ routing configured", output)
        self.assertIn("WARN: v2raytun routing not configured", output)

    def test_missing_optional_metadata_can_be_required(self):
        self.metadata.unlink()
        self.assertEqual(self.run_doctor()[0], 0)
        code, output = self.run_doctor("happ")
        self.assertEqual(code, 1)
        self.assertIn("missing", output)
        self.assertIn("happ routing required", output)

    def test_reads_current_disk_even_if_service_has_cached_valid_config(self):
        with patch.object(service, "_sources", service.ReloadingJSON(service.parse_sub_bases)):
            service.current_sub_bases()
            self.sources.write_text('{"bad":"private.example.com"}')
            self.assertEqual(self.run_doctor()[0], 1)

    def test_bad_metadata_and_explicitly_missing_rewrites_fail(self):
        self.metadata.write_text('{"happ":{"routing":"Private profile"}}')
        with patch.object(service, "SUB_LINK_REWRITES_FILE", str(self.root / "missing.json")):
            code, output = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("FAIL: metadata", output)
        self.assertIn("FAIL: rewrites", output)

    def test_empty_html_and_mihomo_without_provider_fail(self):
        html = self.root / "page.html"
        html.write_text("")
        mihomo = self.root / "mihomo.yaml"
        mihomo.write_text("proxy-providers: {}")
        with patch.multiple(
            service, HTML_TEMPLATE_FILE=str(html), MIHOMO_TEMPLATE_FILE=str(mihomo)
        ):
            code, output = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("FAIL: HTML", output)
        self.assertIn("FAIL: Mihomo", output)
