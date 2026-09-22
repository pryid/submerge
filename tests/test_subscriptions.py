"""Subscription source compatibility and expiry accounting."""

import base64
import json
import unittest
from unittest.mock import patch

from submerge import service

LINK = "vless://demo@node.example.com:443#Demo"
OTHER = "trojan://demo@other.example.com:443#Other"


class SourcesTests(unittest.TestCase):
    def test_legacy_bases_and_url_templates(self):
        sources = service.parse_sub_bases(
            [
                " https://a.example.com/root/ ",
                "https://b.example.com/custom/{id}/?format=raw",
                "https://c.example.com/feed?token={id}",
            ]
        )
        self.assertEqual(
            [service.source_url(s, "test_-1") for s in sources],
            [
                "https://a.example.com/root/sub/test_-1",
                "https://b.example.com/custom/test_-1/?format=raw",
                "https://c.example.com/feed?token=test_-1",
            ],
        )
        with self.assertRaises(ValueError):
            service.source_url(sources[0], "../escape")

    def test_invalid_templates_are_rejected(self):
        for source in [
            "https://{id}.example.com",
            "https://example.com/{id}/{id}",
            "https://example.com/{unknown}",
            "https://example.com/{id}#part",
            "https://example.com/path?token=x",
            "https://example.com/\n{id}",
        ]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                service.parse_sub_bases([source])

    def test_plain_and_both_base64_alphabets_with_comments(self):
        text = "\ufeff#profile-title: Demo\r\n" + LINK + "\r\n\n" + OTHER
        encodings = [
            text,
            base64.b64encode(text.encode()).decode(),
            base64.urlsafe_b64encode(text.encode()).decode().rstrip("="),
        ]
        for text in encodings:
            with self.subTest(text=text[:20]):
                self.assertEqual(service.decode_subscription_body(text), ([LINK, OTHER], True))

    def test_html_json_and_garbage_are_not_link_lists(self):
        for body in [
            '<a href="' + LINK + '">error</a>',
            json.dumps({"link": LINK}),
            "error\n" + LINK,
            "#comment only",
            "YW=Jj",
            "not base64",
        ]:
            with self.subTest(body=body):
                self.assertEqual(service.decode_subscription_body(body), ([], False))

    def test_mixed_sources_merge_rewrite_and_deduplicate(self):
        with (
            patch.object(
                service,
                "current_sub_bases",
                return_value=["https://a.example.com", "https://b.example.com/custom/{id}"],
            ),
            patch.object(
                service,
                "fetch",
                side_effect=[
                    (200, service.lines_to_b64([LINK]), {}),
                    (200, LINK + "\n" + OTHER, {}),
                ],
            ) as fetch,
            patch.object(service, "current_link_rewrite_rules", return_value={}),
        ):
            result = service.merge_from_all("demo")
        self.assertEqual(result[3], [LINK, OTHER])
        self.assertEqual(service.decode_subscription_body(result[1]), ([LINK, OTHER], True))
        self.assertEqual(fetch.call_args_list[1].args, ("https://b.example.com/custom/demo",))
        self.assertEqual([s["index"] for s in result[6]], [1, 2])
        self.assertTrue(all(s["available"] for s in result[6]))

    def test_failed_source_has_no_fabricated_usage_and_does_not_leak_url(self):
        with (
            patch.object(
                service,
                "current_sub_bases",
                return_value=["https://a.example.com", "https://b.example.com/{id}?secret=hidden"],
            ),
            patch.object(service, "fetch", side_effect=[(200, LINK, {}), (503, "error", {})]),
            patch.object(service, "current_link_rewrite_rules", return_value={}),
            patch.object(service, "ALLOW_PARTIAL", True),
        ):
            result = service.merge_from_all("demo")
        self.assertIsNone(result[6][1]["userinfo"])
        self.assertNotIn("hidden", result[4])
        self.assertNotIn("example.com", json.dumps(result[6]))


class ExpiryTests(unittest.TestCase):
    def aggregate(self, *values):
        return service.aggregate_userinfo([{"SUBSCRIPTION-USERINFO": value} for value in values])

    def test_earliest_expiry_and_traffic_are_preserved(self):
        result = self.aggregate(
            "upload=1; download=2; total=100; expire=1900000000",
            "upload=3; download=4; total=200; expire=1800000000",
        )
        self.assertEqual(result["expire"], 1800000000)
        self.assertEqual(result["header"], "upload=4; download=6; total=300; expire=1800000000")
        self.assertTrue(result["expiry_complete"])

    def test_no_expiry_is_distinct_from_missing_or_invalid(self):
        for invalid in [
            "",
            "expire=-1",
            "expire=garbage",
            "expire=123oops",
            "expire=9999999999999999",
        ]:
            with self.subTest(invalid=invalid):
                result = self.aggregate(invalid)
                self.assertIsNone(result["expire"])
                self.assertFalse(result["expiry_complete"])
                self.assertNotIn("expire=", result["header"])
        result = self.aggregate("expire=0", "expire=0")
        self.assertEqual(result["expire"], 0)
        self.assertIn("expire=0", result["header"])
        self.assertTrue(result["expiry_complete"])
        self.assertIsNone(self.aggregate()["expire"])

    def test_mixed_lifetimes_and_partial_metadata(self):
        result = self.aggregate("expire=1900000000", "expire=0", "")
        self.assertEqual(result["expire"], 1900000000)
        self.assertFalse(result["expiry_complete"])
        self.assertIsNone(self.aggregate("expire=0", "")["expire"])
