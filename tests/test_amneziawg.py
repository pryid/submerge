import base64
import unittest
import zlib

from submerge import service
from tests.fixtures import AWG_CONFIG, SubscriptionHTML, vpn_link


class AmneziaWGTests(unittest.TestCase):
    def render(self, links):
        page = service.render_html(
            "demo",
            "https://example.com/sub-merge/demo",
            service.lines_to_b64(links),
            links,
            service.aggregate_userinfo([]),
            None,
        )
        return SubscriptionHTML(page)

    def test_panel_format_preserves_config_and_obfuscation(self):
        self.assertEqual(service.amneziawg_config(vpn_link()), AWG_CONFIG)

    def test_padding_case_fragment_and_line_endings(self):
        for config in [AWG_CONFIG, AWG_CONFIG.replace("\n", "\r\n")]:
            encoded = base64.urlsafe_b64encode(config.encode()).decode()
            for payload in [encoded, encoded.rstrip("=")]:
                with self.subTest(payload=payload[:20], crlf="\r" in config):
                    self.assertEqual(
                        service.amneziawg_config("VPN://" + payload + "#Demo%20AWG"), config
                    )

    def test_invalid_and_unsupported_payloads_are_not_decoded(self):
        compressed = len(AWG_CONFIG.encode()).to_bytes(4, "big") + zlib.compress(
            AWG_CONFIG.encode()
        )
        unsupported = [
            "vpn://x",
            "vpn://%%%",
            "vpn://YWJj===",
            "vpn://YW=Jj",
            "vpn://YWJj?x=1",
            vpn_link("[Interface]\nPrivateKey=x"),
            vpn_link("[Peer]\nPublicKey=x"),
            vpn_link('{"containers": []}'),
            vpn_link(AWG_CONFIG + "\0"),
            "vpn://" + base64.urlsafe_b64encode(b"\xff").decode(),
            "vpn://" + base64.urlsafe_b64encode(compressed).decode(),
            "vless://demo@node.example.com:443#Demo",
        ]
        for link in unsupported:
            with self.subTest(link=link[:35]):
                self.assertIsNone(service.amneziawg_config(link))

    def test_config_size_is_bounded(self):
        oversized = AWG_CONFIG + "\n#" + "x" * 65_536
        self.assertIsNone(service.amneziawg_config(vpn_link(oversized)))
        self.assertIsNone(service.amneziawg_config("vpn://" + "A" * 100_000))

    def test_names_use_fragment_then_comment_then_default(self):
        self.assertEqual(service.item_name(vpn_link() + "#%D0%A2%D0%B5%D1%81%D1%82", 1), "Тест")
        self.assertEqual(service.item_name(vpn_link(), 1), "Demo AWG")
        self.assertEqual(
            service.item_name(vpn_link(AWG_CONFIG.replace("# Demo AWG\n", "")), 1), "AmneziaWG"
        )

    def test_copy_and_download_preserve_utf8_crlf_and_html_characters(self):
        config = AWG_CONFIG.replace("Demo AWG", 'Тест <img src=x onerror="alert(1)"> & $&')
        config = config.replace("\n", "\r\n")
        link = vpn_link(config)
        page = self.render([link])
        self.assertEqual(page.rows[0]["data-copy"], config)
        self.assertEqual(page.rows[0]["data-link"], link)
        self.assertEqual(page.downloads[0]["download"], "amneziawg-1.conf")
        data = page.downloads[0]["href"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(data).decode(), config)
        self.assertFalse(any("onerror" in attrs for _, attrs in page.tags))

    def test_bulk_copy_keeps_uris_in_mixed_subscription(self):
        links = ["vless://demo@node.example.com:443#Demo", vpn_link(), "vpn://unsupported"]
        page = self.render(links)
        self.assertEqual([row["data-link"] for row in page.rows], links)
        self.assertEqual(page.rows[0]["data-copy"], links[0])
        self.assertEqual(page.rows[2]["data-copy"], links[2])
        self.assertEqual(len(page.downloads), 1)
