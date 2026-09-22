"""Source compatibility, expiry accounting and native WireGuard export."""

import base64
import json
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlencode

from submerge import service
from tests.fixtures import AWG_CONFIG, SubscriptionHTML, vpn_link

PRIVATE = base64.b64encode(bytes(range(32))).decode()
PUBLIC = base64.b64encode(bytes(range(32, 64))).decode()
LINK = "vless://demo@node.example.com:443#Demo"
OTHER = "trojan://demo@other.example.com:443#Other"


def wg_link(**overrides):
    params = {
        "publickey": PUBLIC,
        "address": "192.0.2.2/32,2001:db8::2/128",
        "dns": "192.0.2.53",
        "mtu": "1420",
        "keepalive": "25",
    }
    params.update(overrides)
    return (
        f"wireguard://{quote(PRIVATE, safe='')}@[2001:db8::1]:51820?{urlencode(params)}#Demo%20WG"
    )


def render(links, sources=None, userinfo=None):
    return service.render_html(
        "demo",
        "https://example.com/sub-merge/demo",
        service.lines_to_b64(links),
        links,
        userinfo or service.aggregate_userinfo([]),
        None,
        sources,
    )


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
                side_effect=lambda url: (
                    200,
                    service.lines_to_b64([LINK])
                    if url.startswith("https://a.example.com/")
                    else LINK + "\n" + OTHER,
                    {},
                ),
            ) as fetch,
            patch.object(service, "current_link_rewrite_rules", return_value={}),
        ):
            result = service.merge_from_all("demo")
        self.assertEqual(result[3], [LINK, OTHER])
        self.assertEqual(service.decode_subscription_body(result[1]), ([LINK, OTHER], True))
        fetch.assert_any_call("https://b.example.com/custom/demo")
        self.assertEqual([s["index"] for s in result[6]], [1, 2])
        self.assertEqual([s["name"] for s in result[6]], ["Demo", "Demo, Other"])
        self.assertEqual([s["items"] for s in result[6]], [[1], [1, 2]])
        self.assertTrue(all(s["available"] for s in result[6]))

    def test_failed_source_has_no_fabricated_usage_and_does_not_leak_url(self):
        with (
            patch.object(
                service,
                "current_sub_bases",
                return_value=["https://a.example.com", "https://b.example.com/{id}?secret=hidden"],
            ),
            patch.object(
                service,
                "fetch",
                side_effect=lambda url: (
                    (200, LINK, {})
                    if url.startswith("https://a.example.com/")
                    else (503, "error", {})
                ),
            ),
            patch.object(service, "current_link_rewrite_rules", return_value={}),
            patch.object(service, "ALLOW_PARTIAL", True),
        ):
            result = service.merge_from_all("demo")
        self.assertIsNone(result[6][1]["userinfo"])
        self.assertIsNone(result[6][1]["name"])
        self.assertEqual(result[6][1]["items"], [])
        self.assertNotIn("hidden", result[4])
        self.assertNotIn("example.com", json.dumps(result[6]))

    def test_source_names_match_connections_and_are_safe_in_html(self):
        name = '🇳🇱 NL Demo </script><script>alert("test")</script>'
        links = [
            LINK.split("#")[0] + "#" + quote(name),
            OTHER.split("#")[0] + "#" + quote(name),
            "vmess://" + base64.b64encode(json.dumps({"ps": "Demo VMess"}).encode()).decode(),
            vpn_link(),
            wg_link().split("#")[0],
        ]
        with (
            patch.object(service, "current_sub_bases", return_value=["https://a.example.com"]),
            patch.object(service, "fetch", return_value=(200, service.lines_to_b64(links), {})),
            patch.object(service, "current_link_rewrite_rules", return_value={}),
        ):
            result = service.merge_from_all("demo")
        self.assertEqual(result[6][0]["name"], name + ", Demo VMess, Demo AWG, WireGuard")
        page = render(result[3], result[6])
        self.assertNotIn(name, page)
        sources = json.loads(page.split("const SOURCES = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual(sources[0]["name"], result[6][0]["name"])

    def test_connection_usage_tracks_rewritten_duplicates_after_failed_source(self):
        with (
            patch.object(
                service,
                "current_sub_bases",
                return_value=["https://a.example.com", "https://b.example.com"],
            ),
            patch.object(service, "ALLOW_PARTIAL", True),
            patch.object(
                service,
                "fetch",
                side_effect=lambda url: (
                    (503, "unavailable", {})
                    if url.startswith("https://a.example.com/")
                    else (200, LINK + "\n" + OTHER, {})
                ),
            ),
            patch.object(service, "rewrite_subscription_lines", return_value=[OTHER, OTHER]),
        ):
            result = service.merge_from_all("demo")
        self.assertEqual(result[3], [OTHER])
        self.assertEqual([s["items"] for s in result[6]], [[], [1]])
        page = SubscriptionHTML(render(result[3], result[6]))
        usage = [attrs for _, attrs in page.tags if attrs.get("class") == "connection-usage"]
        self.assertEqual([attrs["data-item"] for attrs in usage], ["1"])
        self.assertEqual([row["data-link"] for row in page.rows], [OTHER])
        self.assertFalse(any(attrs.get("id") == "sourceDetails" for _, attrs in page.tags))
        self.assertTrue(any(attrs.get("id") == "listStatus" for _, attrs in page.tags))


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


class WireGuardTests(unittest.TestCase):
    def test_native_export_preserves_addresses_keys_and_optional_fields(self):
        config = service.wireguard_config(wg_link(presharedkey=PRIVATE))
        self.assertIn(f"PrivateKey = {PRIVATE}", config)
        self.assertIn(f"PublicKey = {PUBLIC}", config)
        self.assertIn(f"PresharedKey = {PRIVATE}", config)
        self.assertIn("Address = 192.0.2.2/32, 2001:db8::2/128", config)
        self.assertIn("Endpoint = [2001:db8::1]:51820", config)
        self.assertIn("DNS = 192.0.2.53", config)
        self.assertIn("MTU = 1420", config)
        self.assertIn("PersistentKeepalive = 25", config)
        self.assertIn("AllowedIPs = 0.0.0.0/0, ::/0", config)

    def test_aliases_encoded_plus_keys_and_minimal_config(self):
        key = base64.b64encode(b"\xfb" * 32).decode()
        link = f"wg://{quote(key, safe='')}@node.example.com:51820?" + urlencode(
            {"public_key": key, "ip": "192.0.2.2/32", "allowed_ips": "192.0.2.0/24", "psk": key}
        )
        config = service.wireguard_config(link)
        self.assertIn(f"PrivateKey = {key}", config)
        self.assertIn("AllowedIPs = 192.0.2.0/24", config)
        self.assertNotIn("DNS =", config)
        self.assertNotIn("MTU =", config)

    def test_invalid_fields_and_config_injection_keep_original_link(self):
        for overrides in [
            {"publickey": "bad"},
            {"address": ""},
            {"address": "bad"},
            {"mtu": "abc"},
            {"mtu": "0"},
            {"keepalive": "-1"},
            {"dns": "192.0.2.53\nPostUp = injected"},
            {"presharedkey": "bad"},
            {"allowedips": "bad"},
        ]:
            link = wg_link(**overrides)
            with self.subTest(overrides=overrides):
                self.assertIsNone(service.wireguard_config(link))
                page = SubscriptionHTML(render([link]))
                self.assertEqual(page.rows[0]["data-copy"], link)
                self.assertEqual(page.downloads, [])
        for link in ["wg://bad", wg_link().replace(":51820", ":99999"), LINK]:
            self.assertIsNone(service.wireguard_config(link))

    def test_copy_file_and_qr_use_same_configuration_bulk_keeps_uri(self):
        link = wg_link()
        config = service.wireguard_config(link)
        with patch.object(
            service, "qr_svg_data_uri", return_value="data:image/svg+xml;base64,PHN2Zy8+"
        ) as qr:
            page = SubscriptionHTML(render([link, vpn_link(), LINK]))
        self.assertEqual([row["data-link"] for row in page.rows], [link, vpn_link(), LINK])
        self.assertEqual(page.rows[0]["data-copy"], config)
        self.assertEqual(page.downloads[0]["download"], "wireguard-1.conf")
        self.assertEqual(base64.b64decode(page.downloads[0]["href"].split(",")[1]).decode(), config)
        self.assertIn((config,), [call.args for call in qr.call_args_list])
        self.assertIn((AWG_CONFIG,), [call.args for call in qr.call_args_list])
        self.assertEqual(len([attrs for tag, attrs in page.tags if tag == "img"]), 3)

    def test_large_awg_config_keeps_download_without_qr(self):
        config = AWG_CONFIG + "\n#" + "x" * 2000
        with patch.object(service, "qr_svg_data_uri", return_value=None) as qr:
            page = SubscriptionHTML(render([vpn_link(config)]))
        self.assertEqual(qr.call_count, 1)  # Only the subscription URL.
        self.assertEqual(len(page.downloads), 1)
        self.assertTrue(any(a.get("data-i18n") == "configQrUnavailable" for _, a in page.tags))
