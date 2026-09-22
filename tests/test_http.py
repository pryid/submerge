"""Exercise the real HTTP service, locally or in a built image (Linux host network)."""

import base64
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from tests.fixtures import AWG_CONFIG, WG_LINK, SubscriptionHTML, vpn_link

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("SUBMERGE_TEST_IMAGE")
ENGINE = os.environ.get("CONTAINER_ENGINE", "podman")
NGINX_IMAGE = os.environ.get("SUBMERGE_TEST_NGINX_IMAGE")
LINK = "vless://demo@node.example.com:443?sni=old.example.com#Demo"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(url, method="GET", headers=None):
    opener = build_opener(ProxyHandler({}))
    try:
        response = opener.open(Request(url, method=method, headers=headers or {}), timeout=3)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


class Upstream(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self):
        type(self).calls += 1
        source, _, sub_id = urlparse(self.path).path.strip("/").split("/")
        status = 200
        body = base64.b64encode(LINK.encode())
        if sub_id == "awg":
            body = base64.b64encode((LINK + "\n" + vpn_link()).encode())
        elif sub_id == "wg":
            body = WG_LINK.encode()
        elif sub_id == "mixed" and source == "b":
            body = (LINK + "\n" + LINK.replace("node.example.com", "other.example.com")).encode()
        elif sub_id == "missing":
            status, body = 404, b"missing"
        elif sub_id == "broken" or (sub_id == "partial" and source == "b"):
            status, body = 503, b"unavailable"
        elif sub_id == "empty" and source == "b":
            body = b""
        self.send_response(status)
        userinfo = "upload=1; download=2; total=100"
        if sub_id == "mixed":
            userinfo += "; expire=" + ("1900000000" if source == "a" else "1800000000")
        self.send_header("Subscription-Userinfo", userinfo)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.config = Path(cls.directory.name)
        cls.config.chmod(0o755)  # The image runs as an unprivileged user.
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.addClassCleanup(cls.upstream.server_close)
        cls.addClassCleanup(cls.upstream.shutdown)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        port = cls.upstream.server_port
        cls.sources = [f"http://127.0.0.1:{port}/a", f"http://127.0.0.1:{port}/b"]
        cls.write_config()
        cls.url = cls.start_app()
        cls.strict_url = cls.start_app(strict=True)
        if IMAGE and NGINX_IMAGE:
            port = free_port()
            snippet = (
                (ROOT / "deploy" / "nginx-submerge.conf")
                .read_text()
                .replace("127.0.0.1:18080", cls.url.removeprefix("http://"))
            )
            conf = cls.config / "nginx.conf"
            conf.write_text(
                f"events {{}} http {{ server {{ listen 127.0.0.1:{port}; {snippet} }} }}"
            )
            cls.start_process(
                [
                    ENGINE,
                    "run",
                    "--rm",
                    "--network=host",
                    "-v",
                    f"{conf}:/etc/nginx/nginx.conf:ro,z",
                    NGINX_IMAGE,
                ],
                f"http://127.0.0.1:{port}/sub-merge/demo",
            )
            cls.proxy_url = f"http://127.0.0.1:{port}/sub-merge/demo"

    @classmethod
    def write_json(cls, name, data):
        # Replace within the mounted directory, as editors/deployment tools do.
        staged = cls.config / (name + ".new")
        staged.write_text(json.dumps(data))
        target = cls.config / name
        if target.exists():
            # Preserve permissions and SELinux labels across atomic replacement.
            shutil.copystat(target, staged)
        staged.replace(target)

    @classmethod
    def write_config(cls):
        cls.write_json("sub_bases.json", cls.sources)
        cls.write_json(
            "link_rewrites.json", {"node.example.com": {"query": {"sni": "front.example.com"}}}
        )
        cls.write_json(
            "sub_metadata.json",
            {
                "metadata": {"profile_title": "Demo"},
                "happ": {"routing": {"Name": "Example"}},
                "v2raytun": {"routing": {"name": "Example"}},
            },
        )

    def setUp(self):
        self.write_config()

    @classmethod
    def start_app(cls, strict=False):
        port = free_port()
        env = {
            "LISTEN_HOST": "127.0.0.1",
            "LISTEN_PORT": str(port),
            "ALLOW_PARTIAL": "0" if strict else "1",
            "TIMEOUT": "1",
            "SUB_BASES_FILE": str(cls.config / "sub_bases.json"),
            "SUB_LINK_REWRITES_FILE": str(cls.config / "link_rewrites.json"),
            "SUB_METADATA_FILE": str(cls.config / "sub_metadata.json"),
        }
        if IMAGE:
            cmd = [
                ENGINE,
                "run",
                "--rm",
                "--network=host",
                "--read-only",
                "-v",
                f"{cls.config}:/config:ro,z",
            ]
            for key, value in env.items():
                if key.endswith("_FILE"):
                    value = "/config/" + Path(value).name
                cmd.extend(["-e", f"{key}={value}"])
            cmd.append(IMAGE)
            process_env = None
        else:
            cmd = [sys.executable, "-m", "submerge"]
            process_env = {
                k: v for k, v in os.environ.items() if not k.startswith(("SUB_", "MIHOMO_"))
            }
            process_env.update(env)
        url = f"http://127.0.0.1:{port}"
        cls.start_process(cmd, url + "/healthz", process_env)
        return url

    @classmethod
    def start_process(cls, cmd, ready_url, env=None):
        name = None
        if cmd[0] == ENGINE:
            name = "submerge-test-" + uuid.uuid4().hex
            cmd[2:2] = ["--name", name]
        log = tempfile.TemporaryFile()
        cls.addClassCleanup(log.close)
        process = subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=log, stderr=log)

        def stop():
            if name:
                subprocess.run(
                    [ENGINE, "rm", "-f", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=20,
                )
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        cls.addClassCleanup(stop)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and process.poll() is None:
            try:
                if request(ready_url)[0] == 200:
                    return
            except (OSError, URLError):
                pass
            time.sleep(0.1)
        log.seek(0)
        raise RuntimeError("Service failed to start:\n" + log.read().decode(errors="replace"))

    def test_health_does_not_fetch_upstreams(self):
        before = Upstream.calls
        status, _, body = request(self.url + "/healthz")
        self.assertEqual((status, body), (200, b"ok\n"))
        self.assertEqual(Upstream.calls, before)

    def test_raw_merge_rewrite_dedup_and_traffic(self):
        status, headers, body = request(
            self.url + "/sub/demo?format=base64", headers={"User-Agent": "mihomo"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            base64.b64decode(body).decode(), LINK.replace("old.example.com", "front.example.com")
        )
        self.assertIn("total=200", headers["Subscription-Userinfo"])

    def test_amneziawg_mixed_subscription_and_browser_export(self):
        status, _, raw = request(self.url + "/sub/awg?format=base64")
        self.assertEqual(status, 200)
        links = base64.b64decode(raw).decode().splitlines()
        self.assertEqual(len(links), 2)  # Both upstreams return the same links.
        self.assertEqual(links[1], vpn_link())
        status, _, body = request(self.url + "/sub/awg?format=html")
        self.assertEqual(status, 200)
        page = SubscriptionHTML(body.decode())
        self.assertEqual([row["data-link"] for row in page.rows], links)
        self.assertEqual(page.rows[1]["data-copy"], AWG_CONFIG)
        download = page.downloads[0]["href"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(download).decode(), AWG_CONFIG)

    def test_formats_and_metadata(self):
        for fmt in ("html", "mihomo", "happ", "v2raytun"):
            with self.subTest(format=fmt):
                status, headers, body = request(self.url + "/sub/demo?format=" + fmt)
                self.assertEqual(status, 200)
                if fmt == "html":
                    self.assertIn(b"<!doctype html", body.lower())
                    if revision := os.environ.get("SUBMERGE_TEST_REVISION"):
                        self.assertIn(
                            f'<footer class="build-revision" title="{revision}">'
                            f"{revision[:7]}</footer>".encode(),
                            body,
                        )
                elif fmt == "mihomo":
                    self.assertIn(b"proxy-providers:", body)
                    self.assertIn(b"/sub-merge/demo?format=base64", body)
                else:
                    self.assertEqual(headers["Profile-Title"], "base64:RGVtbw==")
                    self.assertIn("Routing", headers)
                    self.assertTrue(base64.b64decode(body).startswith(b"vless://"))

    def test_template_plaintext_sources_and_expiry(self):
        self.write_json(
            "sub_bases.json", [self.sources[0], self.sources[1] + "/custom/{id}?format=plain"]
        )
        status, headers, body = request(self.url + "/sub/mixed?format=base64")
        self.assertEqual(status, 200)
        self.assertEqual(len(base64.b64decode(body).decode().splitlines()), 2)
        self.assertIn("expire=1800000000", headers["Subscription-Userinfo"])
        self.assertIn("total=200", headers["Subscription-Userinfo"])
        status, _, page = request(self.url + "/sub/mixed?format=html")
        self.assertEqual(status, 200)
        sources = json.loads(page.decode().split("const SOURCES = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual([s["userinfo"]["expire"] for s in sources], [1900000000, 1800000000])
        self.assertTrue(all(s["available"] for s in sources))
        self.assertEqual([s["name"] for s in sources], ["Demo", "Demo"])
        self.assertEqual([s["items"] for s in sources], [[1], [1, 2]])
        self.assertIn(b'class="connection-usage" data-item="1"', page)
        self.assertIn(b'class="connection-usage" data-item="2"', page)
        self.assertNotIn(b'id="sourceDetails"', page)
        self.assertIn(b'"expire": 1800000000, "complete": true', page)
        _, _, partial_page = request(self.url + "/sub/partial?format=html")
        partial_sources = json.loads(
            partial_page.decode().split("const SOURCES = ", 1)[1].split(";\n", 1)[0]
        )
        self.assertFalse(partial_sources[1]["available"])
        self.assertIsNone(partial_sources[1]["userinfo"])
        self.assertIsNone(partial_sources[1]["name"])

    def test_wireguard_native_export_and_qr(self):
        status, _, body = request(self.url + "/sub/wg?format=html")
        self.assertEqual(status, 200)
        page = SubscriptionHTML(body.decode())
        self.assertEqual(page.downloads[0]["download"], "wireguard-1.conf")
        config = page.rows[0]["data-copy"]
        self.assertIn("[Interface]", config)
        self.assertIn("Endpoint = node.example.com:51820", config)
        self.assertEqual(base64.b64decode(page.downloads[0]["href"].split(",")[1]).decode(), config)
        self.assertEqual(page.rows[0]["data-link"], WG_LINK + "&sni=front.example.com")
        if IMAGE or importlib.util.find_spec("qrcode"):
            self.assertTrue(
                any(
                    tag == "img" and attrs.get("alt") == "wireguard configuration QR"
                    for tag, attrs in page.tags
                )
            )

    @unittest.skipUnless(
        IMAGE or importlib.util.find_spec("qrcode"), "qrcode is not installed locally"
    )
    def test_html_contains_qr(self):
        _, _, body = request(self.url + "/sub/demo?format=html")
        self.assertIn(b"data:image/svg+xml;base64,", body)
        self.assertNotIn(b">QR unavailable</div>", body)

    def test_head_and_errors(self):
        for path in (
            "/healthz",
            "/sub/demo?format=html",
            "/sub/demo?format=mihomo",
            "/sub/demo?format=happ",
        ):
            with self.subTest(path=path):
                status, headers, body = request(self.url + path, method="HEAD")
                self.assertEqual((status, body), (200, b""))
                self.assertGreater(int(headers["Content-Length"]), 0)
        for path, expected in (
            ("/sub/missing", 404),
            ("/sub/broken", 503),
            ("/sub/demo/extra", 404),
        ):
            self.assertEqual(request(self.url + path)[0], expected)

    def test_partial_policy_including_empty_response(self):
        for sub_id in ("partial", "empty"):
            self.assertEqual(request(self.url + "/sub/" + sub_id)[0], 200)
            self.assertEqual(request(self.strict_url + "/sub/" + sub_id)[0], 502)

    def test_configuration_reload_keeps_last_good_values(self):
        request(self.url + "/sub/demo?format=happ")
        self.write_json("sub_bases.json", self.sources[:1])
        self.write_json("sub_metadata.json", {"metadata": {"profile_title": "Updated"}})
        self.write_json("link_rewrites.json", {"node.example.com": {"address": "192.0.2.1"}})
        status, headers, body = request(self.url + "/sub/demo?format=happ")
        self.assertEqual(status, 200)
        self.assertIn("total=100", headers["Subscription-Userinfo"])
        self.assertEqual(headers["Profile-Title"], "base64:VXBkYXRlZA==")
        self.assertIn(b"@192.0.2.1:", base64.b64decode(body))
        for name in ("sub_bases.json", "sub_metadata.json", "link_rewrites.json"):
            (self.config / name).write_text("{")
        new_status, new_headers, new_body = request(self.url + "/sub/demo?format=happ")
        self.assertEqual((new_status, new_body), (status, body))
        self.assertEqual(new_headers["Profile-Title"], headers["Profile-Title"])

    @unittest.skipUnless(IMAGE and NGINX_IMAGE, "nginx container smoke test not enabled")
    def test_nginx_preserves_format_query(self):
        status, headers, body = request(
            self.proxy_url + "?format=base64", headers={"User-Agent": "mihomo"}
        )
        self.assertEqual(status, 200)
        self.assertTrue(base64.b64decode(body).startswith(b"vless://"))
        status, headers, body = request(
            self.proxy_url + "?format=mihomo", headers={"Accept": "text/html"}
        )
        self.assertEqual(status, 200)
        self.assertIn(b"proxy-providers:", body)


if __name__ == "__main__":
    unittest.main()
