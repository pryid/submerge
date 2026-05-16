#!/usr/bin/env python3
import base64
import html
import json
import os
import re
import socket
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, HTTPServer
from string import Template
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse, unquote

# ---------------- config (минимум env) ----------------
def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if default is not None:
        return default
    raise SystemExit(f"Missing env: {name}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SUB_BASES_FILE = env("SUB_BASES_FILE")
LISTEN_HOST = env("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(env("LISTEN_PORT", "18080"))
TIMEOUT = float(env("TIMEOUT", "10"))
ALLOW_PARTIAL = env("ALLOW_PARTIAL", "1").lower() not in ("0", "false", "no")
PAGE_TITLE = env("PAGE_TITLE", "Sub-merge")
SUB_LINK_REWRITES = os.environ.get("SUB_LINK_REWRITES", "").strip()
SUB_LINK_REWRITES_FILE = os.environ.get("SUB_LINK_REWRITES_FILE", "").strip()
SUB_REWRITE_DNS_TTL = float(env("SUB_REWRITE_DNS_TTL", "300"))
MIHOMO_AUTO = env("MIHOMO_AUTO", "1").lower() not in ("0", "false", "no", "off")
MIHOMO_TEMPLATE_FILE = env("MIHOMO_TEMPLATE_FILE", os.path.join(BASE_DIR, "mihomo_template.yaml"))
MIHOMO_PROFILE_TITLE = env("MIHOMO_PROFILE_TITLE", f"{PAGE_TITLE} Mihomo")
MIHOMO_UPDATE_INTERVAL = env("MIHOMO_UPDATE_INTERVAL", "6")

# внутренний путь, на который nginx проксирует: /sub/<id>
INTERNAL_PREFIX = "/sub/"
# публичный префикс, который видят юзеры: /sub-merge/<id>
PUBLIC_PREFIX = "/sub-merge/"

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MIHOMO_UA_RE = re.compile(
    r"(mihomo|clash|clashmeta|clash\.meta|clash-verge|clashnyanpasu|"
    r"clashforwindows|clashx|stash|koala|koala-clash|"
    r"clashmetaforandroid|clashforandroid|cfw|cfa|flclash)",
    re.I,
)

BASE64_FORMATS = {"base64", "raw", "uri", "v2ray", "v2rayn", "plain"}
MIHOMO_FORMATS = {"mihomo", "clash", "clash-meta", "clashmeta", "yaml", "yml"}
HTML_FORMATS = {"html", "web"}

PASS_HEADERS = {
    "profile-update-interval",
    "profile-title",
    "routing-enable",
    "support-url",
}

@dataclass
class LinkRewriteRule:
    resolve_host: bool = False
    address: str | None = None
    query: dict[str, str] = field(default_factory=dict)


def config_file_signature(path: str) -> tuple[int, int, int]:
    st = os.stat(path)
    return (int(st.st_ino), int(st.st_size), int(st.st_mtime_ns))


def config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off", ""):
            return False
    raise ValueError(f"Invalid boolean value in link rewrites: {value!r}")


def parse_sub_bases(data, source: str = "SUB_BASES_FILE") -> list[str]:
    if not isinstance(data, list):
        raise ValueError(f"{source} must be a JSON array")

    bases: list[str] = []
    for idx, raw_base in enumerate(data):
        if not isinstance(raw_base, str):
            raise ValueError(f"{source}[{idx}] must be a string")
        base = raw_base.strip().rstrip("/")
        if not base:
            raise ValueError(f"{source}[{idx}] is empty")

        parsed = urlparse(base)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"{source}[{idx}] must be an http(s) base URL")
        bases.append(base)

    if not bases:
        raise ValueError(f"{source} must contain at least one source")
    return bases


def load_sub_bases_file(path: str) -> tuple[list[str], tuple[int, int, int]]:
    sig = config_file_signature(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return parse_sub_bases(data, path), sig


def load_sub_bases() -> tuple[list[str], tuple[int, int, int]]:
    try:
        return load_sub_bases_file(SUB_BASES_FILE)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        raise SystemExit(f"Cannot read SUB_BASES_FILE: {e}") from e


SUB_BASES, SUB_BASES_FILE_SIG = load_sub_bases()
SUB_BASES_LAST_ERROR: str | None = None


def warn_sub_bases_reload_error(message: str):
    global SUB_BASES_LAST_ERROR
    if message == SUB_BASES_LAST_ERROR:
        return
    SUB_BASES_LAST_ERROR = message
    print(f"submerge: keeping previous subscription sources: {message}", file=sys.stderr, flush=True)


def current_sub_bases() -> list[str]:
    global SUB_BASES, SUB_BASES_FILE_SIG, SUB_BASES_LAST_ERROR

    try:
        sig = config_file_signature(SUB_BASES_FILE)
    except OSError as e:
        warn_sub_bases_reload_error(f"cannot stat {SUB_BASES_FILE}: {e}")
        return SUB_BASES

    if sig == SUB_BASES_FILE_SIG:
        return SUB_BASES

    try:
        bases, loaded_sig = load_sub_bases_file(SUB_BASES_FILE)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        warn_sub_bases_reload_error(f"cannot reload {SUB_BASES_FILE}: {e}")
        return SUB_BASES

    SUB_BASES = bases
    SUB_BASES_FILE_SIG = loaded_sig
    SUB_BASES_LAST_ERROR = None
    return SUB_BASES


def normalize_host(host: str) -> str:
    host = str(host or "").strip().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.lower()


def parse_link_rewrite_rules(data, source: str = "SUB_LINK_REWRITES") -> dict[str, LinkRewriteRule]:
    if not data:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{source} must be a JSON object")

    rules: dict[str, LinkRewriteRule] = {}
    for raw_host, raw_rule in data.items():
        host = normalize_host(raw_host)
        if not host:
            raise ValueError(f"{source} contains an empty host")
        if not isinstance(raw_rule, dict):
            raise ValueError(f"{source}[{raw_host!r}] must be an object")

        address = raw_rule.get("address")
        if address is not None:
            address = str(address).strip() or None

        raw_query = raw_rule.get("query", {})
        if raw_query is None:
            raw_query = {}
        if not isinstance(raw_query, dict):
            raise ValueError(f"{source}[{raw_host!r}].query must be an object")

        query = {}
        for key, value in raw_query.items():
            key = str(key).strip()
            if not key:
                raise ValueError(f"{source}[{raw_host!r}].query contains an empty key")
            if value is not None:
                query[key] = str(value)

        rules[host] = LinkRewriteRule(
            resolve_host=config_bool(raw_rule.get("resolve_host"), False),
            address=address,
            query=query,
        )
    return rules


def parse_link_rewrite_rules_json(raw: str, source: str) -> dict[str, LinkRewriteRule]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {source}: {e}") from e
    return parse_link_rewrite_rules(data, source)


def load_link_rewrite_rules_file(path: str) -> tuple[dict[str, LinkRewriteRule], tuple[int, int, int]]:
    sig = config_file_signature(path)
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    return parse_link_rewrite_rules_json(raw, path), sig


def load_link_rewrite_rules() -> tuple[dict[str, LinkRewriteRule], tuple[int, int, int] | None]:
    if SUB_LINK_REWRITES_FILE:
        try:
            return load_link_rewrite_rules_file(SUB_LINK_REWRITES_FILE)
        except (OSError, ValueError) as e:
            raise SystemExit(f"Cannot read SUB_LINK_REWRITES_FILE: {e}") from e
    elif SUB_LINK_REWRITES:
        try:
            return parse_link_rewrite_rules_json(SUB_LINK_REWRITES, "SUB_LINK_REWRITES"), None
        except ValueError as e:
            raise SystemExit(str(e)) from e
    return {}, None


LINK_REWRITE_RULES, LINK_REWRITE_FILE_SIG = load_link_rewrite_rules()
LINK_REWRITE_LAST_ERROR: str | None = None

DNS_CACHE: dict[str, tuple[float, str]] = {}


def warn_link_rewrite_reload_error(message: str):
    global LINK_REWRITE_LAST_ERROR
    if message == LINK_REWRITE_LAST_ERROR:
        return
    LINK_REWRITE_LAST_ERROR = message
    print(f"submerge: keeping previous link rewrite rules: {message}", file=sys.stderr, flush=True)


def current_link_rewrite_rules() -> dict[str, LinkRewriteRule]:
    global LINK_REWRITE_FILE_SIG, LINK_REWRITE_LAST_ERROR, LINK_REWRITE_RULES

    if not SUB_LINK_REWRITES_FILE:
        return LINK_REWRITE_RULES

    try:
        sig = config_file_signature(SUB_LINK_REWRITES_FILE)
    except OSError as e:
        warn_link_rewrite_reload_error(f"cannot stat {SUB_LINK_REWRITES_FILE}: {e}")
        return LINK_REWRITE_RULES

    if sig == LINK_REWRITE_FILE_SIG:
        return LINK_REWRITE_RULES

    try:
        rules, loaded_sig = load_link_rewrite_rules_file(SUB_LINK_REWRITES_FILE)
    except (OSError, ValueError) as e:
        warn_link_rewrite_reload_error(f"cannot reload {SUB_LINK_REWRITES_FILE}: {e}")
        return LINK_REWRITE_RULES

    LINK_REWRITE_RULES = rules
    LINK_REWRITE_FILE_SIG = loaded_sig
    LINK_REWRITE_LAST_ERROR = None
    return LINK_REWRITE_RULES

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

def query_params(raw_path: str) -> dict[str, str]:
    return dict(parse_qsl(urlparse(raw_path).query, keep_blank_values=True))

def response_format(headers, raw_path: str) -> str:
    q = query_params(raw_path)
    fmt = (q.get("format") or q.get("target") or q.get("type") or "").strip().lower()

    if fmt in HTML_FORMATS:
        return "html"
    if fmt in BASE64_FORMATS:
        return "base64"
    if fmt in MIHOMO_FORMATS:
        return "mihomo"

    if is_browser(headers):
        return "html"

    ua = headers.get("User-Agent") or ""
    if MIHOMO_AUTO and MIHOMO_UA_RE.search(ua):
        return "mihomo"

    return "base64"

def scheme_host(self_headers):
    # nginx лучше прокидывать: proxy_set_header X-Forwarded-Proto $scheme;
    scheme = (self_headers.get("X-Forwarded-Proto") or "").strip().lower() or "https"
    host = (self_headers.get("Host") or "").strip() or "localhost"
    return scheme, host

def public_url(self_headers, sub_id: str) -> str:
    scheme, host = scheme_host(self_headers)
    return f"{scheme}://{host}{PUBLIC_PREFIX}{sub_id}"

def public_url_with_query(self_headers, sub_id: str, query: dict[str, str] | None = None) -> str:
    base = public_url(self_headers, sub_id)
    if query:
        return base + "?" + urlencode(query)
    return base

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

def resolve_host_ip(host: str) -> str | None:
    host = normalize_host(host)
    if not host:
        return None

    now = time.monotonic()
    cached = DNS_CACHE.get(host)
    if cached and cached[0] > now:
        return cached[1]

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return cached[1] if cached else None

    ip = None
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        if family == socket.AF_INET:
            ip = sockaddr[0]
            break
    if ip is None and infos:
        ip = infos[0][4][0]

    if ip and SUB_REWRITE_DNS_TTL > 0:
        DNS_CACHE[host] = (now + SUB_REWRITE_DNS_TTL, ip)
    return ip

def host_for_netloc(host: str) -> str:
    host = str(host).strip()
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host

def replace_netloc_host(netloc: str, new_host: str) -> str:
    head, sep, hostport = netloc.rpartition("@")
    prefix = f"{head}{sep}" if sep else ""

    if hostport.startswith("["):
        end = hostport.find("]")
        suffix = hostport[end + 1:] if end >= 0 else ""
    else:
        _host, sep2, rest = hostport.partition(":")
        suffix = f"{sep2}{rest}" if sep2 else ""

    return f"{prefix}{host_for_netloc(new_host)}{suffix}"

def rewrite_query_params(query: str, overrides: dict[str, str]) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    out = []
    replaced = set()

    for key, value in pairs:
        if key in overrides:
            if key not in replaced:
                out.append((key, overrides[key]))
                replaced.add(key)
            continue
        out.append((key, value))

    for key, value in overrides.items():
        if key not in replaced:
            out.append((key, value))

    return urlencode(out)

def rewrite_subscription_link(
    link: str,
    rules: dict[str, LinkRewriteRule] | None = None,
    resolver=resolve_host_ip,
) -> str:
    rules = current_link_rewrite_rules() if rules is None else rules
    if not rules:
        return link

    try:
        parsed = urlparse(link)
    except Exception:
        return link

    host = parsed.hostname
    if not host:
        return link

    rule = rules.get(normalize_host(host))
    if not rule:
        return link

    netloc = parsed.netloc
    if rule.address:
        netloc = replace_netloc_host(netloc, rule.address)
    elif rule.resolve_host:
        try:
            resolved = resolver(host)
        except Exception:
            resolved = None
        if resolved:
            netloc = replace_netloc_host(netloc, resolved)

    query = rewrite_query_params(parsed.query, rule.query) if rule.query else parsed.query
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, query, parsed.fragment))

def rewrite_subscription_lines(lines: list[str]) -> list[str]:
    rules = current_link_rewrite_rules()
    if not rules:
        return lines
    return [rewrite_subscription_link(ln, rules) for ln in lines]

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

    for base in current_sub_bases():
        url = f"{base}/sub/{sub_id}"
        code, body, hdrs = fetch(url)
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

        decoded_sets.append(rewrite_subscription_lines(lines))

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

def load_mihomo_template() -> Template:
    with open(MIHOMO_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return Template(f.read())

def sanitize_template_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)

def render_mihomo_config(sub_id: str, provider_url: str) -> str:
    tmpl = load_mihomo_template()
    safe_sub_id = sanitize_template_id(sub_id)
    return tmpl.safe_substitute(
        SUB_ID=safe_sub_id,
        PROVIDER_URL=provider_url,
        PROFILE_TITLE=MIHOMO_PROFILE_TITLE,
    )

# ---------------- HTML ----------------
HTML_TEMPLATE_FILE = env("HTML_TEMPLATE_FILE", os.path.join(BASE_DIR, "web_template.html"))
I18N_FILE = env("I18N_FILE", os.path.join(BASE_DIR, "web_i18n.json"))


def load_html_template() -> Template:
    with open(HTML_TEMPLATE_FILE, "r", encoding="utf-8") as f:
        return Template(f.read())


def load_i18n() -> dict:
    with open(I18N_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{I18N_FILE} must be a JSON object")

    out = {}
    for raw_lang, values in data.items():
        lang = str(raw_lang).strip()
        if not lang:
            raise ValueError(f"{I18N_FILE} contains an empty locale")
        if not isinstance(values, dict):
            raise ValueError(f"{I18N_FILE}[{raw_lang!r}] must be an object")

        strings = {}
        for key, value in values.items():
            if isinstance(value, str):
                strings[str(key)] = value
        if strings:
            out[lang] = strings

    if not out:
        raise ValueError(f"{I18N_FILE} must contain at least one locale")
    return out


def render_language_options(i18n: dict) -> str:
    items = []
    for lang, values in i18n.items():
        label = values.get("languageName") or lang
        items.append(f'<option value="{html.escape(lang)}">{html.escape(label)}</option>')
    return "\n".join(items)


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
        LANG_OPTIONS=render_language_options(i18n),
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
        self._handle_subscription(send_body=True)

    def do_HEAD(self):
        self._handle_subscription(send_body=False)

    def _handle_subscription(self, send_body: bool):
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

            fmt = response_format(self.headers, self.path)
            want_html = fmt == "html"

            status, body, any_hdrs, lines, note, hdrs_for_userinfo = merge_from_all(sub_id)

            # агрегируем userinfo по всем успешным (или хотя бы тем, где был header)
            userinfo_agg = aggregate_userinfo(hdrs_for_userinfo)

            # Браузер: несуществующая/невалидная подписка -> 404 пустой (nginx покажет свой 404)
            if want_html and status in (400, 404):
                self.send_response(404)
                self.end_headers()
                return

            if fmt == "mihomo":
                if status in (400, 404):
                    self.send_response(404)
                    self.end_headers()
                    return
                if status != 200:
                    out = (body or note or "upstream error").encode("utf-8")
                    self.send_response(status if status else 502)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Subscription-Userinfo", userinfo_agg["header"])
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    if send_body:
                        self.wfile.write(out)
                    return

                provider_url = public_url_with_query(self.headers, sub_id, {"format": "base64"})
                try:
                    yaml_body = render_mihomo_config(sub_id, provider_url)
                except Exception as e:
                    msg = f"Mihomo template error: {e}\n"
                    out = msg.encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    if send_body:
                        self.wfile.write(out)
                    return

                out = yaml_body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/yaml; charset=utf-8")
                self.send_header("Subscription-Userinfo", userinfo_agg["header"])
                self.send_header("Profile-Update-Interval", MIHOMO_UPDATE_INTERVAL)
                self.send_header("Profile-Title", MIHOMO_PROFILE_TITLE)
                self.send_header("Profile-Web-Page-Url", public_url(self.headers, sub_id))
                for hk, hv in pick_some_headers(any_hdrs).items():
                    if hk.lower() not in {"profile-title", "profile-update-interval"}:
                        self.send_header(hk, hv)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                if send_body:
                    self.wfile.write(out)
                return

            # Клиенты: всегда отдаём "сырой" ответ (base64 или ошибка)
            if fmt == "base64":
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
                if send_body:
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
            if send_body:
                self.wfile.write(out)

        except Exception:
            # чтобы обработчик не падал и не приводил к 502 из-за crash-loop
            self.send_response(500)
            self.end_headers()

if __name__ == "__main__":
    HTTPServer((LISTEN_HOST, LISTEN_PORT), H).serve_forever()
