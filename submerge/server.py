"""HTTP transport and process lifecycle."""

import logging
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from . import service


class SubscriptionHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._handle_subscription()

    def do_HEAD(self):
        self._handle_subscription()

    def respond(self, status, body="", content_type="text/plain; charset=utf-8", headers=None):
        out = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(out)

    def _handle_subscription(self):
        try:
            path = urlparse(self.path).path
            if path == "/healthz":
                return self.respond(200, "ok\n")
            if not path.startswith(service.INTERNAL_PREFIX):
                return self.respond(404)
            sub_id = path[len(service.INTERNAL_PREFIX) :]
            if not service.ID_RE.fullmatch(sub_id):
                return self.respond(404)

            fmt = service.response_format(self.headers, self.path)
            status, body, any_hdrs, lines, note, hdrs_for_userinfo, sources = (
                service.merge_from_all(sub_id)
            )
            userinfo = service.aggregate_userinfo(hdrs_for_userinfo)
            userinfo["expiry_complete"] &= all(source["available"] for source in sources)
            if fmt in {"html", "mihomo"} and status in (400, 404):
                return self.respond(404)

            page_url = service.public_url(self.headers, sub_id)
            headers = {
                key.lower(): value for key, value in service.pick_some_headers(any_hdrs).items()
            }
            headers["subscription-userinfo"] = userinfo["header"]
            headers["profile-web-page-url"] = page_url

            if fmt == "mihomo":
                if status != 200:
                    return self.respond(status, body or note or "upstream error", headers=headers)
                provider_url = service.url_with_query(page_url, {"format": "base64"})
                body = service.render_mihomo_config(sub_id, provider_url)
                headers.update(
                    {
                        "profile-update-interval": service.MIHOMO_UPDATE_INTERVAL,
                        "profile-title": service.MIHOMO_PROFILE_TITLE,
                    }
                )
                return self.respond(200, body, "application/yaml; charset=utf-8", headers)

            if fmt == "base64":
                kind = service.raw_client_kind(self.headers, self.path)
                extra_headers, prefix = service.raw_subscription_metadata(
                    kind, userinfo["header"], page_url
                )
                headers.update({key.lower(): value for key, value in extra_headers.items()})
                if kind in {"happ", "v2raytun"}:
                    headers["content-disposition"] = 'attachment; filename="sub"'
                if status == 200 and prefix and lines is not None:
                    body = service.lines_to_b64(prefix + lines)
                return self.respond(status, body or "", headers=headers)

            page = service.render_html(sub_id, page_url, body, lines or [], userinfo, note, sources)
            return self.respond(status, page, "text/html; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            logging.exception("Subscription request failed")
            self.respond(500, "Internal server error\n")


def main():
    logging.basicConfig(level=logging.INFO, format="submerge: %(levelname)s: %(message)s")
    # Validate deployment configuration before accepting traffic, not on import.
    try:
        service.current_sub_bases()
        service.current_link_rewrite_rules()
        service.load_raw_metadata_config()
        service.load_html_template()
        service.load_i18n()
        service.load_mihomo_template()
    except (OSError, ValueError) as error:
        raise SystemExit(f"Invalid configuration: {error}") from error
    # Python is PID 1 in the image; explicitly handle container stop signals.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    with HTTPServer((service.LISTEN_HOST, service.LISTEN_PORT), SubscriptionHandler) as server:
        server.serve_forever()
