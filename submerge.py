#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import urllib.request
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, HTTPServer
from string import Template
from urllib.parse import urlparse, unquote

# ---------------- config (минимум env) ----------------
def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if default is not None:
        return default
    raise SystemExit(f"Missing env: {name}")

SUB_BASES = [x.strip().rstrip("/") for x in env("SUB_BASES").split(",") if x.strip()]
if not SUB_BASES:
    raise SystemExit("SUB_BASES is empty")

LISTEN_HOST = env("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(env("LISTEN_PORT", "18080"))
TIMEOUT = float(env("TIMEOUT", "10"))
ALLOW_PARTIAL = env("ALLOW_PARTIAL", "1").lower() not in ("0", "false", "no")
PAGE_TITLE = env("PAGE_TITLE", "Sub-merge")

# внутренний путь, на который nginx проксирует: /sub/<id>
INTERNAL_PREFIX = "/sub/"
# публичный префикс, который видят юзеры: /sub-merge/<id>
PUBLIC_PREFIX = "/sub-merge/"

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

PASS_HEADERS = {
    "profile-update-interval",
    "profile-title",
    "routing-enable",
    "support-url",
}

try:
    import qrcode  # type: ignore
    HAVE_QR = True
except Exception:
    HAVE_QR = False

# ---------------- helpers ----------------
def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "submerge/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="ignore").strip()
            return r.status, body, dict(r.headers)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore").strip()
        return e.code, body, dict(e.headers)
    except URLError:
        return 0, "", {}

def is_browser(h):
    accept = (h.get("Accept") or "").lower()
    ua = (h.get("User-Agent") or "")
    if "text/html" in accept:
        return True
    return any(x in ua for x in ("Mozilla", "Chrome", "Safari", "Firefox", "Edg"))

def scheme_host(self_headers):
    # nginx лучше прокидывать: proxy_set_header X-Forwarded-Proto $scheme;
    scheme = (self_headers.get("X-Forwarded-Proto") or "").strip().lower() or "https"
    host = (self_headers.get("Host") or "").strip() or "localhost"
    return scheme, host

def public_url(self_headers, sub_id: str) -> str:
    scheme, host = scheme_host(self_headers)
    return f"{scheme}://{host}{PUBLIC_PREFIX}{sub_id}"

def decode_b64_plain_list(s: str):
    s = (s or "").strip()
    if not s:
        return [], False
    pad = "=" * (-len(s) % 4)
    try:
        raw = base64.b64decode(s + pad, validate=False)
        txt = raw.decode("utf-8", errors="strict")
        if "://" in txt:
            lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
            return lines, True
    except Exception:
        pass
    return [], False

def lines_to_b64(lines):
    return base64.b64encode(("\n".join(lines)).encode("utf-8")).decode("ascii")

def parse_userinfo_one(h: str):
    # upload=...; download=...; total=...
    d = {"upload": 0, "download": 0, "total": None}
    if not h:
        return d
    for p in re.split(r"[;,]\s*", h.strip()):
        m = re.match(r"([A-Za-z_]+)\s*=\s*(\d+)", p)
        if not m:
            continue
        k = m.group(1).lower()
        v = int(m.group(2))
        if k in ("upload", "download"):
            d[k] = v
        elif k == "total":
            d["total"] = v
    return d

def aggregate_userinfo(headers_list: list[dict]):
    uploads = 0
    downloads = 0
    totals = []
    missing_total = False
    unlimited = False

    for hdr in headers_list:
        h = hdr.get("subscription-userinfo") or hdr.get("Subscription-Userinfo") or ""
        if not h:
            missing_total = True
            continue
        ui = parse_userinfo_one(h)
        uploads += int(ui.get("upload", 0))
        downloads += int(ui.get("download", 0))
        t = ui.get("total", None)
        if t is None:
            missing_total = True
        elif int(t) == 0:
            unlimited = True
        else:
            totals.append(int(t))

    used = uploads + downloads

    if unlimited:
        kind = "unlimited"
        total = 0
        remain = 0
        hdr_out = f"upload={uploads}; download={downloads}; total=0"
    elif totals:
        total = sum(totals)
        remain = max(total - used, 0)
        kind = "limited"
        hdr_out = f"upload={uploads}; download={downloads}; total={total}"
        if missing_total:
            # не ломаем, но отметим в UI
            kind = "limited_partial"
    else:
        kind = "no_total"
        total = None
        remain = 0
        hdr_out = f"upload={uploads}; download={downloads}"

    return {
        "kind": kind,
        "upload": uploads,
        "download": downloads,
        "used": used,
        "total": total,
        "remain": remain,
        "header": hdr_out,
        "missing_total": missing_total,
    }

def vmess_name(link: str):
    try:
        if not link.lower().startswith("vmess://"):
            return None
        b = link[len("vmess://"):].strip()
        pad = "=" * (-len(b) % 4)
        raw = base64.b64decode(b + pad, validate=False)
        obj = json.loads(raw.decode("utf-8", errors="strict"))
        return obj.get("ps") or obj.get("name")
    except Exception:
        return None

def item_name(link: str, idx: int):
    try:
        p = urlparse(link)
        if p.fragment:
            return unquote(p.fragment)
        vn = vmess_name(link)
        if vn:
            return vn
        host = p.hostname or p.netloc
        if host:
            return f"{(p.scheme or 'link').upper()} {host}"
    except Exception:
        pass
    return f"ITEM {idx}"

def qr_svg_data_uri(text: str):
    if not HAVE_QR:
        return None
    try:
        qr = qrcode.QRCode(  # type: ignore
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,  # type: ignore
            box_size=1,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        m = qr.get_matrix()
        n = len(m)
        rects = []
        for y, row in enumerate(m):
            for x, v in enumerate(row):
                if v:
                    rects.append(f'<rect x="{x}" y="{y}" width="1" height="1"/>')
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="white"/>'
            f'<g fill="black">{"".join(rects)}</g></svg>'
        )
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        return "data:image/svg+xml;base64," + b64
    except Exception:
        return None

def pick_some_headers(h: dict):
    out = {}
    for k, v in h.items():
        if k.lower() in PASS_HEADERS:
            out[k] = v
    return out

def merge_from_all(sub_id: str):
    """
    Возвращает:
      (status, body, headers_for_passthru, merged_lines_or_None, note, headers_list_for_userinfo)
    """
    results = []
    any_network = False

    for base in SUB_BASES:
        url = f"{base}/sub/{sub_id}"
        code, body, hdrs = fetch(url)
        if code == 0:
            any_network = True
        results.append((base, code, body, hdrs))

    ok = [(b, c, body, h) for (b, c, body, h) in results if c == 200 and body.strip()]

    # ничего не нашлось
    if not ok:
        # если хоть кто-то ответил кодом (не 0) — вернём этот код (в порядке списка), чтобы не было 502
        for (_b, c, body, hdrs) in results:
            if c != 0:
                return c, (body or ""), (hdrs or {}), None, None, [hdrs]
        # все в сети упали
        return 502, "", {}, None, "All upstreams network-failed", []

    # пытаемся декодировать все успешные ответы
    decoded_sets = []
    for (_b, _c, body, _h) in ok:
        lines, ok_dec = decode_b64_plain_list(body)
        if not ok_dec:
            # не ломаем — отдаём первый успешный как есть
            h0 = ok[0][3]
            return 200, ok[0][2], h0, None, "Non-plain format detected, using first successful as-is", [x[3] for x in ok]

        decoded_sets.append(lines)

    # merge + dedup preserving order
    seen = set()
    merged = []
    for lines in decoded_sets:
        for ln in lines:
            if ln not in seen:
                seen.add(ln)
                merged.append(ln)

    note = None
    # пометка если какие-то апстримы отвалились (но ALLOW_PARTIAL=1)
    bad = [(b, c) for (b, c, _body, _h) in results if c != 200]
    if bad and ALLOW_PARTIAL:
        note = "Some sources unavailable: " + ", ".join([f"{b}({c if c else 'net'})" for b, c in bad])

    h0 = ok[0][3]
    return 200, lines_to_b64(merged), h0, merged, note, [x[3] for x in ok]

# ---------------- HTML ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_TEMPLATE_FILE = env("HTML_TEMPLATE_FILE", os.path.join(BASE_DIR, "web_template.html"))
I18N_FILE = env("I18N_FILE", os.path.join(BASE_DIR, "web_i18n.json"))

DEFAULT_I18N = {
    "ru": {
        "title": "Просмотр подписки (merge)",
        "scan": "Сканируй, чтобы добавить подписку",
        "list": "Список",
        "hint": "клик по пункту → копировать",
        "details": "Объединенный RAW + subscription-userinfo",
        "copyUrl": "Скопировать URL",
        "copyAll": "Скопировать всё",
        "copyRaw": "Скопировать merged RAW",
        "links": "Ссылок: {n}",
        "no": "Осталось: —",
        "unlim": "Осталось: безлимит",
        "lim": "Осталось: {remain} GB из {total} GB (исп.: {used} GB)",
        "ok": "Скопировано",
        "fail": "Не удалось",
    },
    "en": {
        "title": "Subscription viewer (merge)",
        "scan": "Scan to add subscription",
        "list": "List",
        "hint": "click item → copy",
        "details": "Merged raw + subscription-userinfo",
        "copyUrl": "Copy URL",
        "copyAll": "Copy all",
        "copyRaw": "Copy merged RAW",
        "links": "Links: {n}",
        "no": "Remaining: —",
        "unlim": "Remaining: unlimited",
        "lim": "Remaining: {remain} GB of {total} GB (used: {used} GB)",
        "ok": "Copied",
        "fail": "Copy failed",
    },
}


def load_html_template() -> Template:
    with open(HTML_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return Template(f.read())


def load_i18n() -> dict:
    out = {lang: dict(values) for lang, values in DEFAULT_I18N.items()}
    try:
        with open(I18N_FILE, "r", encoding="utf-8") as f:
            custom = json.load(f)
        if not isinstance(custom, dict):
            return out
        for lang, values in custom.items():
            if lang not in out or not isinstance(values, dict):
                continue
            for key, value in values.items():
                if isinstance(value, str):
                    out[lang][key] = value
    except Exception:
        return out
    return out


def render_html(sub_id: str, sub_url: str, merged_b64: str, lines, userinfo_agg, note: str | None):
    html_template = load_html_template()
    i18n = load_i18n()
    qr = qr_svg_data_uri(sub_url)
    qr_block = f'<img src="{html.escape(qr)}" alt="QR"/>' if qr else '<div style="color:rgba(255,255,255,.62);font-size:12px">QR unavailable</div>'

    items = []
    if lines:
        for i, ln in enumerate(lines, start=1):
            nm = html.escape(item_name(ln, i))
            esc = html.escape(ln)
            items.append(
                f'<button class="row" type="button" data-copy="{esc}" title="{esc}">'
                f'<div class="name">{nm}</div><div class="mono">{esc}</div></button>'
            )
    items_html = "\n".join(items) if items else '<div style="color:rgba(255,255,255,.62);padding:6px 2px">(no parsed list, see raw below)</div>'
    note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""

    return html_template.safe_substitute(
        TITLE=html.escape(PAGE_TITLE),
        SID=html.escape(sub_id),
        SUBURL=html.escape(sub_url),
        SUBURL_JS=json.dumps(sub_url),
        QR=qr_block,
        NOTE=note_html,
        ITEMS=items_html,
        RAW=html.escape(merged_b64 or ""),
        USERINFO=html.escape(userinfo_agg["header"]),
        LINKS=str(len(lines) if lines else 0),
        I18N_JSON=json.dumps(i18n, ensure_ascii=False),
        KIND=json.dumps(
            userinfo_agg["kind"]
            if userinfo_agg["kind"] in ("unlimited", "limited", "limited_partial")
            else "no_total"
        ),
        TOTAL=("null" if userinfo_agg["total"] is None else str(int(userinfo_agg["total"]))),
        USED=str(int(userinfo_agg["used"])),
        REMAIN=str(int(userinfo_agg["remain"])),
    )

# ---------------- HTTP handler ----------------
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            path = urlparse(self.path).path

            # expect /sub/<id>
            if not path.startswith(INTERNAL_PREFIX):
                self.send_response(404)
                self.end_headers()
                return

            sub_id = path[len(INTERNAL_PREFIX):].strip().split("/", 1)[0].strip()
            if not sub_id or not ID_RE.match(sub_id):
                self.send_response(404)
                self.end_headers()
                return

            want_html = is_browser(self.headers)

            status, body, any_hdrs, lines, note, hdrs_for_userinfo = merge_from_all(sub_id)

            # агрегируем userinfo по всем успешным (или хотя бы тем, где был header)
            userinfo_agg = aggregate_userinfo(hdrs_for_userinfo)

            # Браузер: несуществующая/невалидная подписка -> 404 пустой (nginx покажет свой 404)
            if want_html and status in (400, 404):
                self.send_response(404)
                self.end_headers()
                return

            # Клиенты: всегда отдаём "сырой" ответ (base64 или ошибка)
            if not want_html:
                out = (body or "").encode("utf-8")
                self.send_response(status if status else 502)
                self.send_header("Content-Type", "text/plain; charset=utf-8")

                # важное: агрегированный трафик
                self.send_header("Subscription-Userinfo", userinfo_agg["header"])

                # остальные полезные заголовки — с любого нормального hdrs
                for hk, hv in pick_some_headers(any_hdrs).items():
                    self.send_header(hk, hv)

                # web-page-url на merge
                self.send_header("Profile-Web-Page-Url", public_url(self.headers, sub_id))

                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return

            # Браузер: страница
            sub_url = public_url(self.headers, sub_id)
            page = render_html(sub_id, sub_url, body, lines or [], userinfo_agg, note)
            out = page.encode("utf-8")

            self.send_response(200 if status == 200 else status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        except Exception:
            # чтобы обработчик не падал и не приводил к 502 из-за crash-loop
            self.send_response(500)
            self.end_headers()

if __name__ == "__main__":
    HTTPServer((LISTEN_HOST, LISTEN_PORT), H).serve_forever()
