import base64
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qsl, urlparse

_CONFIG_DIR = tempfile.TemporaryDirectory()
_SUB_BASES_FILE = os.path.join(_CONFIG_DIR.name, "sub_bases.json")
with open(_SUB_BASES_FILE, "w", encoding="utf-8") as f:
    json.dump(["https://a.example"], f)

os.environ["SUB_BASES_FILE"] = _SUB_BASES_FILE
os.environ.pop("SUB_BASES", None)
os.environ.pop("SUB_LINK_REWRITES", None)
os.environ.pop("SUB_LINK_REWRITES_FILE", None)

import submerge  # noqa: E402


def base64_urlsafe_decode(value):
    value = submerge.unquote(value)
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value).decode("utf-8")


class SubBasesTests(unittest.TestCase):
    def setUp(self):
        self._bases_state = (
            submerge.SUB_BASES_FILE,
            list(submerge.SUB_BASES),
            submerge.SUB_BASES_FILE_SIG,
            submerge.SUB_BASES_LAST_ERROR,
        )

    def tearDown(self):
        (
            submerge.SUB_BASES_FILE,
            submerge.SUB_BASES,
            submerge.SUB_BASES_FILE_SIG,
            submerge.SUB_BASES_LAST_ERROR,
        ) = self._bases_state

    def write_bases(self, path, bases):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(bases, f)

    def test_parse_sub_bases_strips_trailing_slashes(self):
        self.assertEqual(
            submerge.parse_sub_bases([" https://a.example/ ", "http://b.example//"]),
            ["https://a.example", "http://b.example"],
        )

    def test_parse_sub_bases_rejects_empty_list(self):
        with self.assertRaises(ValueError):
            submerge.parse_sub_bases([])

    def test_sub_bases_file_reloads_after_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub_bases.json")
            self.write_bases(path, ["https://first.example"])

            submerge.SUB_BASES_FILE = path
            submerge.SUB_BASES, submerge.SUB_BASES_FILE_SIG = submerge.load_sub_bases()

            first = submerge.current_sub_bases()
            self.write_bases(path, ["https://second.example", "https://third.example"])
            second = submerge.current_sub_bases()

        self.assertEqual(first, ["https://first.example"])
        self.assertEqual(second, ["https://second.example", "https://third.example"])

    def test_invalid_sub_bases_file_keeps_previous_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub_bases.json")
            self.write_bases(path, ["https://valid.example"])

            submerge.SUB_BASES_FILE = path
            submerge.SUB_BASES, submerge.SUB_BASES_FILE_SIG = submerge.load_sub_bases()

            with open(path, "w", encoding="utf-8") as f:
                f.write("{")

            out = submerge.current_sub_bases()

        self.assertEqual(out, ["https://valid.example"])


class I18nTests(unittest.TestCase):
    def setUp(self):
        self._i18n_file = submerge.I18N_FILE

    def tearDown(self):
        submerge.I18N_FILE = self._i18n_file

    def write_i18n(self, path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def test_load_i18n_accepts_arbitrary_locale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "web_i18n.json")
            self.write_i18n(path, {"de": {"languageName": "Deutsch", "title": "Titel"}})

            submerge.I18N_FILE = path
            i18n = submerge.load_i18n()
            options = submerge.render_language_options(i18n)

        self.assertEqual(i18n["de"]["title"], "Titel")
        self.assertIn('value="de"', options)
        self.assertIn("Deutsch", options)


class FormatRoutingTests(unittest.TestCase):
    def setUp(self):
        self._mihomo_auto = submerge.MIHOMO_AUTO
        self._mihomo_template_file = submerge.MIHOMO_TEMPLATE_FILE
        self._mihomo_profile_title = submerge.MIHOMO_PROFILE_TITLE
        self._raw_meta_state = (
            submerge.SUB_METADATA_FILE,
            dict(submerge.RAW_METADATA_DEFAULTS),
        )

    def tearDown(self):
        submerge.MIHOMO_AUTO = self._mihomo_auto
        submerge.MIHOMO_TEMPLATE_FILE = self._mihomo_template_file
        submerge.MIHOMO_PROFILE_TITLE = self._mihomo_profile_title
        (
            submerge.SUB_METADATA_FILE,
            raw_defaults,
        ) = self._raw_meta_state
        submerge.RAW_METADATA_DEFAULTS.clear()
        submerge.RAW_METADATA_DEFAULTS.update(raw_defaults)

    def test_response_format_explicit_base64_beats_mihomo_user_agent(self):
        headers = {"User-Agent": "mihomo/1.19.0"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo?format=base64"), "base64")

    def test_response_format_explicit_mihomo_beats_browser_accept(self):
        headers = {"Accept": "text/html", "User-Agent": "Mozilla/5.0"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo?format=mihomo"), "mihomo")

    def test_response_format_browser_html(self):
        headers = {"Accept": "text/html"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo"), "html")

    def test_response_format_known_raw_client_beats_browser_accept(self):
        headers = {"Accept": "text/html", "User-Agent": "NekoBoxForAndroid/1.3.9 Mozilla/5.0"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo"), "base64")

    def test_response_format_mihomo_ua_when_auto_enabled(self):
        submerge.MIHOMO_AUTO = True
        headers = {"User-Agent": "Koala"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo"), "mihomo")

    def test_response_format_mihomo_ua_ignored_when_auto_disabled(self):
        submerge.MIHOMO_AUTO = False
        headers = {"User-Agent": "Clash.Meta"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo"), "base64")

    def test_response_format_happ_is_raw_subscription(self):
        headers = {"Accept": "text/html", "User-Agent": "Mozilla/5.0"}

        self.assertEqual(submerge.response_format(headers, "/sub/demo?format=happ"), "base64")
        self.assertEqual(submerge.raw_client_kind(headers, "/sub/demo?format=happ"), "happ")

    def test_raw_client_kind_from_user_agent(self):
        self.assertEqual(submerge.raw_client_kind({"User-Agent": "Happ/2.0"}, "/sub/demo"), "happ")
        self.assertEqual(submerge.raw_client_kind({"User-Agent": "v2RayTun/6.0"}, "/sub/demo"), "v2raytun")

    def test_public_url_with_query(self):
        headers = {"Host": "example.com", "X-Forwarded-Proto": "https"}

        self.assertEqual(
            submerge.public_url_with_query(headers, "abc_123", {"format": "base64"}),
            "https://example.com/sub-merge/abc_123?format=base64",
        )

    def test_render_mihomo_config_uses_safe_id_and_provider_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mihomo.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write("url: '${PROVIDER_URL}'\npath: provider-${SUB_ID}.txt\n# ${PROFILE_TITLE}\n")

            submerge.MIHOMO_TEMPLATE_FILE = path
            submerge.MIHOMO_PROFILE_TITLE = "Test Profile"
            out = submerge.render_mihomo_config("bad/id", "https://example.com/sub-merge/bad_id?format=base64")

        self.assertIn("url: 'https://example.com/sub-merge/bad_id?format=base64'", out)
        self.assertIn("path: provider-bad_id.txt", out)
        self.assertIn("# Test Profile", out)

    def test_happ_routing_link_encodes_json_profile(self):
        link = submerge.happ_routing_link({"Name": "Demo", "GlobalProxy": "true"})

        self.assertTrue(link.startswith("happ://routing/onadd/"))
        payload = link.rsplit("/", 1)[1]
        decoded = json.loads(base64_urlsafe_decode(payload))
        self.assertEqual(decoded["Name"], "Demo")

    def test_raw_subscription_metadata_uses_happ_and_v2raytun_routing_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub_metadata.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "happ": {"routing": {"Name": "Happ"}},
                        "v2raytun": {"routing": {"name": "v2RayTun"}},
                    },
                    f,
                )

            submerge.SUB_METADATA_FILE = path
            happ_headers, happ_body = submerge.raw_subscription_metadata(
                "happ",
                "upload=1; download=2; total=3",
                "https://example.com/sub-merge/demo",
            )
            v2_headers, _v2_body = submerge.raw_subscription_metadata(
                "v2raytun",
                "upload=1; download=2; total=3",
                "https://example.com/sub-merge/demo",
            )

        self.assertTrue(happ_headers["Routing"].startswith("happ://routing/onadd/"))
        self.assertEqual(happ_body, [])
        self.assertFalse(v2_headers["Routing"].startswith("happ://"))
        self.assertEqual(json.loads(base64_urlsafe_decode(v2_headers["Routing"]))["name"], "v2RayTun")

    def test_raw_subscription_metadata_hot_reloads_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub_metadata.json")
            submerge.SUB_METADATA_FILE = path

            with open(path, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"profile_title": "First", "announce_text": "One"}}, f)
            first, _first_body = submerge.raw_subscription_metadata("happ", "", "https://example.com")

            with open(path, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"profile_title": "Second", "announce_text": "Two"}}, f)
            second, _second_body = submerge.raw_subscription_metadata("happ", "", "https://example.com")

        self.assertEqual(first["Profile-Title"], "base64:Rmlyc3Q=")
        self.assertEqual(first["Announce"], "base64:T25l")
        self.assertEqual(second["Profile-Title"], "base64:U2Vjb25k")
        self.assertEqual(second["Announce"], "base64:VHdv")

    def test_raw_subscription_metadata_body_comments_are_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub_metadata.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"metadata": {"profile_title": "Demo", "body_comments": "1"}}, f)

            submerge.SUB_METADATA_FILE = path
            headers, body = submerge.raw_subscription_metadata("happ", "", "https://example.com")

        self.assertEqual(headers["Profile-Title"], "base64:RGVtbw==")
        self.assertIn("#profile-title: base64:RGVtbw==", body)


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
