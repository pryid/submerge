import json
import os
import tempfile
import unittest
from urllib.parse import parse_qsl, urlparse

os.environ.setdefault("SUB_BASES", "https://a.example")
os.environ.pop("SUB_LINK_REWRITES", None)
os.environ.pop("SUB_LINK_REWRITES_FILE", None)

import submerge  # noqa: E402


class LinkRewriteTests(unittest.TestCase):
    def setUp(self):
        self._rewrite_state = (
            submerge.SUB_LINK_REWRITES_FILE,
            dict(submerge.LINK_REWRITE_RULES),
            submerge.LINK_REWRITE_FILE_SIG,
            submerge.LINK_REWRITE_LAST_ERROR,
        )

    def tearDown(self):
        (
            submerge.SUB_LINK_REWRITES_FILE,
            submerge.LINK_REWRITE_RULES,
            submerge.LINK_REWRITE_FILE_SIG,
            submerge.LINK_REWRITE_LAST_ERROR,
        ) = self._rewrite_state

    def write_rules(self, path, sni):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "primary.example.com": {
                        "query": {"sni": sni},
                    }
                },
                f,
            )

    def test_rewrites_only_matching_host(self):
        rules = submerge.parse_link_rewrite_rules(
            {
                "primary.example.com": {
                    "resolve_host": True,
                    "query": {"sni": "front-primary.example.com"},
                }
            }
        )

        untouched = "vless://id@other.example.com:443?security=reality&sni=other.example.com&type=tcp#OTHER"

        self.assertEqual(
            submerge.rewrite_subscription_link(untouched, rules, resolver=lambda _host: "203.0.113.10"),
            untouched,
        )

    def test_rewrites_host_and_replaces_query_value(self):
        rules = submerge.parse_link_rewrite_rules(
            {
                "primary.example.com": {
                    "resolve_host": True,
                    "query": {"sni": "front-primary.example.com"},
                }
            }
        )
        link = "vless://id@primary.example.com:443?security=reality&sni=old.example.com&spx=%2Fabc&type=tcp#PRIMARY"

        out = submerge.rewrite_subscription_link(link, rules, resolver=lambda _host: "203.0.113.10")
        parsed = urlparse(out)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))

        self.assertEqual(parsed.netloc, "id@203.0.113.10:443")
        self.assertEqual(query["sni"], "front-primary.example.com")
        self.assertEqual(query["spx"], "/abc")
        self.assertEqual(parsed.fragment, "PRIMARY")

    def test_rewrite_removes_duplicate_query_keys(self):
        rules = submerge.parse_link_rewrite_rules(
            {
                "primary.example.com": {
                    "query": {"sni": "front-primary.example.com"},
                }
            }
        )
        link = "vless://id@primary.example.com:443?sni=one.example.com&sni=two.example.com&type=tcp#PRIMARY"

        out = submerge.rewrite_subscription_link(link, rules)
        pairs = parse_qsl(urlparse(out).query, keep_blank_values=True)

        self.assertEqual([k for k, _v in pairs].count("sni"), 1)
        self.assertIn(("sni", "front-primary.example.com"), pairs)

    def test_static_address_takes_precedence_over_dns(self):
        rules = submerge.parse_link_rewrite_rules(
            {
                "primary.example.com": {
                    "address": "198.51.100.7",
                    "resolve_host": True,
                    "query": {"sni": "front-primary.example.com"},
                }
            }
        )
        link = "vless://id@primary.example.com:443?type=tcp#PRIMARY"

        out = submerge.rewrite_subscription_link(
            link,
            rules,
            resolver=lambda _host: self.fail("DNS resolver should not be called"),
        )

        self.assertEqual(urlparse(out).netloc, "id@198.51.100.7:443")

    def test_rewrite_file_reloads_after_change(self):
        link = "vless://id@primary.example.com:443?sni=old.example.com&type=tcp#PRIMARY"

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "link_rewrites.json")
            self.write_rules(path, "front-a.example.com")

            submerge.SUB_LINK_REWRITES_FILE = path
            submerge.LINK_REWRITE_RULES, submerge.LINK_REWRITE_FILE_SIG = submerge.load_link_rewrite_rules()

            first = submerge.rewrite_subscription_link(link)
            self.write_rules(path, "front-reloaded.example.com")
            second = submerge.rewrite_subscription_link(link)

        self.assertIn("sni=front-a.example.com", first)
        self.assertIn("sni=front-reloaded.example.com", second)

    def test_invalid_rewrite_file_keeps_previous_rules(self):
        link = "vless://id@primary.example.com:443?sni=old.example.com&type=tcp#PRIMARY"

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "link_rewrites.json")
            self.write_rules(path, "front-valid.example.com")

            submerge.SUB_LINK_REWRITES_FILE = path
            submerge.LINK_REWRITE_RULES, submerge.LINK_REWRITE_FILE_SIG = submerge.load_link_rewrite_rules()

            with open(path, "w", encoding="utf-8") as f:
                f.write("{")

            out = submerge.rewrite_subscription_link(link)

        self.assertIn("sni=front-valid.example.com", out)


if __name__ == "__main__":
    unittest.main()
