"""Subscription source compatibility."""

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
