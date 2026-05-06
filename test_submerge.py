import os
import unittest
from urllib.parse import parse_qsl, urlparse

os.environ.setdefault("SUB_BASES", "https://a.example")
os.environ.pop("SUB_LINK_REWRITES", None)
os.environ.pop("SUB_LINK_REWRITES_FILE", None)

import submerge  # noqa: E402


class LinkRewriteTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
