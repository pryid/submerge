"""Operator checks, using neutral configuration only."""

import base64
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from submerge import diagnostics, service
from submerge.cli import main

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


class Response(io.BytesIO):
    def __init__(self, body, headers=None, status=200):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value


class VerifyTests(unittest.TestCase):
    def response(self, client="happ"):
        body = base64.b64encode(LINK.encode())
        profile = {"Name" if client == "happ" else "name": "Private profile"}
        value = base64.b64encode(json.dumps(profile).encode()).decode()
        if client == "happ":
            value = "happ://routing/onadd/" + value
        return Response(
            body, {"Content-Length": str(len(body)), "Routing": value, "Routing-Enable": "1"}
        )

    def run_verify(self, response=None, error=None, **kwargs):
        output = io.StringIO()
        with (
            patch.object(diagnostics, "urlopen", return_value=response, side_effect=error) as fetch,
            redirect_stdout(output),
        ):
            code = diagnostics.check_public_subscription(URL, **kwargs)
        value = output.getvalue()
        for secret in ("private-token", "test-secret", "Private profile", "node.example.com"):
            self.assertNotIn(secret, value)
        return code, value, fetch

    def test_happ_and_v2raytun_profiles_and_correct_client_headers(self):
        for client, ua in (("happ", "Happ"), ("v2raytun", "v2raytun")):
            with self.subTest(client=client):
                code, output, fetch = self.run_verify(
                    self.response(client), client=client, require_routing=True
                )
                self.assertEqual(code, 0)
                self.assertIn("1 connection link(s)", output)
                self.assertEqual(fetch.call_args.args[0].get_header("User-agent"), ua)

    def test_missing_routing_is_optional_or_required(self):
        body = base64.b64encode(LINK.encode())
        self.assertEqual(self.run_verify(Response(body))[0], 0)
        code, output, _ = self.run_verify(Response(body), require_routing=True)
        self.assertEqual(code, 1)
        self.assertIn("Routing header absent", output)

    def test_invalid_routing_and_disabled_happ_fail(self):
        for value in (
            "bad",
            "happ://routing/off",
            "happ://routing/onadd/W10=",
            "happ://routing/onadd/e30=",
        ):
            response = self.response()
            response.headers.replace_header("Routing", value)
            self.assertEqual(self.run_verify(response, require_routing=True)[0], 1)
        response = self.response()
        response.headers.replace_header("Routing-Enable", "false")
        self.assertEqual(self.run_verify(response, require_routing=True)[0], 1)

    def test_html_plaintext_and_double_encoded_responses_fail(self):
        for body in (
            b"<html>Error</html>",
            LINK.encode(),
            base64.b64encode(b"<html>Error</html>"),
            base64.b64encode(base64.b64encode(LINK.encode())),
        ):
            self.assertEqual(self.run_verify(Response(body))[0], 1)

    def test_length_mismatch_minimum_links_and_oversized_response_fail(self):
        response = self.response()
        response.headers.replace_header("Content-Length", "99999")
        self.assertEqual(self.run_verify(response)[0], 1)
        self.assertEqual(self.run_verify(self.response(), min_links=2)[0], 1)
        self.assertEqual(self.run_verify(Response(b"x" * (8 * 1024 * 1024 + 1)))[0], 1)

    def test_network_errors_never_print_exception_urls_or_body(self):
        for error in (
            URLError(URL),
            IncompleteRead(b"test-secret"),
            HTTPError(URL, 404, "private-token", {}, io.BytesIO(b"test-secret")),
        ):
            code, _, _ = self.run_verify(error=error)
            self.assertEqual(code, 1)

    def test_url_from_stdin_never_enters_output(self):
        output = io.StringIO()
        with (
            patch("sys.stdin", io.StringIO(URL + "\n")),
            patch.object(diagnostics, "urlopen", return_value=self.response()),
            redirect_stdout(output),
        ):
            code = main(["verify", "--url-stdin", "--require-routing"])
        self.assertEqual(code, 0)
        self.assertNotIn(URL, output.getvalue())
