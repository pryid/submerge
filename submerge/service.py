"""Subscription fetching, merging, client formats and page rendering."""

import base64
import html
import json
import os
import re
import socket
import time
import urllib.request
from dataclasses import dataclass, field
from http.client import HTTPException
from string import Template
from urllib.error import HTTPError
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

from .config import ReloadingJSON


# Configuration
def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if default is not None:
        return default
    raise SystemExit(f"Missing env: {name}")


ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
SUB_BASES_FILE = env("SUB_BASES_FILE", "sub_bases.json")
LISTEN_HOST = env("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(env("LISTEN_PORT", "18080"))
TIMEOUT = float(env("TIMEOUT", "10"))
ALLOW_PARTIAL = env("ALLOW_PARTIAL", "1").lower() not in ("0", "false", "no")
PAGE_TITLE = env("PAGE_TITLE", "Sub-merge")
SUB_LINK_REWRITES = os.environ.get("SUB_LINK_REWRITES", "").strip()
SUB_LINK_REWRITES_FILE = os.environ.get("SUB_LINK_REWRITES_FILE", "").strip()
SUB_REWRITE_DNS_TTL = float(env("SUB_REWRITE_DNS_TTL", "300"))
MIHOMO_AUTO = env("MIHOMO_AUTO", "1").lower() not in ("0", "false", "no", "off")
MIHOMO_TEMPLATE_FILE = env("MIHOMO_TEMPLATE_FILE", os.path.join(ASSET_DIR, "mihomo_template.yaml"))
MIHOMO_PROFILE_TITLE = env("MIHOMO_PROFILE_TITLE", f"{PAGE_TITLE} Mihomo")
MIHOMO_UPDATE_INTERVAL = env("MIHOMO_UPDATE_INTERVAL", "6")
SUB_METADATA_FILE = os.environ.get("SUB_METADATA_FILE", "sub_metadata.json").strip()

RAW_METADATA_DEFAULTS = {
    "profile_title": "",
    "profile_update_interval": "",
    "support_url": "",
    "announce_text": "",
    "announce_url": "",
    "info_text": "",
    "info_color": "blue",
    "info_button_text": "",
    "info_button_link": "",
    "expire": "",
    "expire_button_link": "",
    "body_comments": "0",
}

# Internal endpoint used by the reverse proxy.
INTERNAL_PREFIX = "/sub/"
# Public prefix used in browser and client import URLs.
PUBLIC_PREFIX = "/sub-merge/"

ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MIHOMO_UA_RE = re.compile(
    r"(mihomo|clash|clashmeta|clash\.meta|clash-verge|clashnyanpasu|"
    r"clashforwindows|clashx|stash|koala|koala-clash|"
    r"clashmetaforandroid|clashforandroid|cfw|cfa|flclash)",
    re.I,
)
HAPP_UA_RE = re.compile(r"(^|[^a-z0-9])(happ|happ-proxy)([^a-z0-9]|$)", re.I)
V2RAYTUN_UA_RE = re.compile(r"(v2raytun|v2ray-tun)", re.I)
RAW_SUB_UA_RE = re.compile(
    r"(nekobox|nekoray|sagernet|v2rayng|v2rayn|hiddify|shadowrocket|"
    r"streisand|throne|sing-box|singbox|foxray|karing|exclave|incy)",
    re.I,
)

BASE64_FORMATS = {"base64", "raw", "uri", "v2ray", "v2rayn", "plain"}
MIHOMO_FORMATS = {"mihomo", "clash", "clash-meta", "clashmeta", "yaml", "yml"}
HTML_FORMATS = {"html", "web"}
HAPP_FORMATS = {"happ", "happ-proxy"}
V2RAYTUN_FORMATS = {"v2raytun", "v2ray-tun"}

PASS_HEADERS = {
    "profile-update-interval",
    "support-url",
}


@dataclass
class LinkRewriteRule:
    resolve_host: bool = False
    address: str | None = None
    query: dict[str, str] = field(default_factory=dict)


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


def optional_bool(value, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return config_bool(value, default)


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


_sources = ReloadingJSON(parse_sub_bases)


def current_sub_bases() -> list[str]:
    return _sources.get(SUB_BASES_FILE)


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


_rewrites = ReloadingJSON(parse_link_rewrite_rules)
DNS_CACHE: dict[str, tuple[float, str]] = {}


def current_link_rewrite_rules() -> dict[str, LinkRewriteRule]:
    if SUB_LINK_REWRITES_FILE:
        return _rewrites.get(SUB_LINK_REWRITES_FILE)
    return (
        parse_link_rewrite_rules_json(SUB_LINK_REWRITES, "SUB_LINK_REWRITES")
        if SUB_LINK_REWRITES
        else {}
    )


try:
    import qrcode
    import qrcode.image.svg

    HAVE_QR = True
except Exception:
    HAVE_QR = False


# Subscription protocols and upstream requests
def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "submerge/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="ignore").strip()
            return r.status, body, dict(r.headers)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore").strip()
        return e.code, body, dict(e.headers)
    except (OSError, HTTPException):
        return 0, "", {}


def is_browser(h):
    accept = (h.get("Accept") or "").lower()
    ua = h.get("User-Agent") or ""
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
    if fmt in HAPP_FORMATS or fmt in V2RAYTUN_FORMATS:
        return "base64"
    if fmt in MIHOMO_FORMATS:
        return "mihomo"

    ua = headers.get("User-Agent") or ""
    if RAW_SUB_UA_RE.search(ua) or HAPP_UA_RE.search(ua) or V2RAYTUN_UA_RE.search(ua):
        return "base64"

    if is_browser(headers):
        return "html"

    if MIHOMO_AUTO and MIHOMO_UA_RE.search(ua):
        return "mihomo"

    return "base64"


def raw_client_kind(headers, raw_path: str) -> str:
    q = query_params(raw_path)
    fmt = (q.get("format") or q.get("target") or q.get("type") or "").strip().lower()

    if fmt in HAPP_FORMATS:
        return "happ"
    if fmt in V2RAYTUN_FORMATS:
        return "v2raytun"

    ua = headers.get("User-Agent") or ""
    if HAPP_UA_RE.search(ua):
        return "happ"
    if V2RAYTUN_UA_RE.search(ua):
        return "v2raytun"
    return "generic"


def scheme_host(self_headers):
    # nginx forwards the original scheme through X-Forwarded-Proto.
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


def url_with_query(url: str, query: dict[str, str]) -> str:
    parsed = urlparse(url)
    pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    pairs.update(query)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(pairs),
            parsed.fragment,
        )
    )


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
        suffix = hostport[end + 1 :] if end >= 0 else ""
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
            # Keep the available totals and mark the incomplete summary in the UI.
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
        b = link[len("vmess://") :].strip()
        pad = "=" * (-len(b) % 4)
        raw = base64.b64decode(b + pad, validate=False)
        obj = json.loads(raw.decode("utf-8", errors="strict"))
        return obj.get("ps") or obj.get("name")
    except Exception:
        return None


def amneziawg_config(link: str) -> str | None:
    """Decode the uncompressed .conf payload emitted by 3x-ui's vpn:// links."""
    payload = link.partition("#")[0]
    # Bound decoding/rendering work; compressed and JSON AmneziaVPN keys stay opaque.
    if len(payload) > 90_000:
        return None
    match = re.fullmatch(r"vpn://([A-Za-z0-9_-]+={0,2})", payload, re.I)
    if not match:
        return None
    encoded = match[1]
    if "=" in encoded and len(encoded) % 4:
        return None
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if len(raw) > 65_536:
            return None
        config = raw.decode("utf-8", errors="strict")
    except ValueError:
        return None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in config):
        return None
    if all(re.search(rf"^\[{section}\]\r?$", config, re.M) for section in ("Interface", "Peer")):
        return config
    return None


def item_name(link: str, idx: int, config: str | None = None):
    try:
        p = urlparse(link)
        if p.fragment:
            return unquote(p.fragment)
        config = config if config is not None else amneziawg_config(link)
        if config is not None:
            for line in config.splitlines():
                if line.startswith("#") and (remark := line[1:].strip()):
                    return remark
            return "AmneziaWG"
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
        qr = qrcode.make(
            text,
            image_factory=qrcode.image.svg.SvgPathFillImage,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=4,
        )
        return "data:image/svg+xml;base64," + base64.b64encode(qr.to_string()).decode("ascii")
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
    Return status, body, passthrough headers, parsed lines, note and source headers.
    """
    results = []

    for base in current_sub_bases():
        url = f"{base}/sub/{sub_id}"
        code, body, hdrs = fetch(url)
        results.append((base, code, body, hdrs))

    ok = [(b, c, body, h) for (b, c, body, h) in results if c == 200 and body.strip()]

    # Preserve upstream errors when no subscription could be fetched.
    if not ok:
        # Prefer the first actual HTTP error over a synthetic gateway error.
        for _b, c, body, hdrs in results:
            if c not in (0, 200):
                return c, (body or ""), (hdrs or {}), None, None, [hdrs]
        # Network failures and empty 200 responses provide no usable subscription.
        return 502, "", {}, None, "No usable upstream responses", []

    bad = [(b, c) for (b, c, body, _h) in results if c != 200 or not body.strip()]
    if bad and not ALLOW_PARTIAL:
        return 502, "Some sources unavailable", {}, None, None, []

    # Decode every successful response before merging.
    decoded_sets = []
    for _b, _c, body, _h in ok:
        lines, ok_dec = decode_b64_plain_list(body)
        if not ok_dec:
            # Preserve unsupported formats by returning the first successful body.
            h0 = ok[0][3]
            return (
                200,
                ok[0][2],
                h0,
                None,
                "Non-plain format detected, using first successful as-is",
                [x[3] for x in ok],
            )

        decoded_sets.append(rewrite_subscription_lines(lines))

    # Merge and deduplicate while preserving upstream order.
    seen = set()
    merged = []
    for lines in decoded_sets:
        for ln in lines:
            if ln not in seen:
                seen.add(ln)
                merged.append(ln)

    note = None
    # Explain partial results in the browser view.
    if bad and ALLOW_PARTIAL:
        note = "Some sources unavailable: " + ", ".join(
            [f"{b}({c if c else 'net'})" for b, c in bad]
        )

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


def clamp_text(value: str, limit: int) -> str:
    value = str(value or "").strip()
    if limit > 0 and len(value) > limit:
        return value[:limit]
    return value


def b64_utf8(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def b64_header(value: str) -> str:
    return "base64:" + b64_utf8(value)


def compact_json_b64(data) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return b64_utf8(raw)


def routing_profile_b64(profile) -> str | None:
    if not profile:
        return None
    if not isinstance(profile, dict):
        raise ValueError("routing profile must be a JSON object")
    return compact_json_b64(profile)


def happ_routing_link(profile) -> str | None:
    value = routing_profile_b64(profile)
    if not value:
        return None
    return "happ://routing/onadd/" + value


def v2raytun_routing_value(profile) -> str | None:
    return routing_profile_b64(profile)


def parse_raw_metadata_config(data, source: str) -> dict:
    config = {
        "metadata": dict(RAW_METADATA_DEFAULTS),
        "happ": {},
        "v2raytun": {},
    }
    if not isinstance(data, dict):
        raise ValueError(f"{source} must be a JSON object")

    metadata = data.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError(f"{source}.metadata must be a JSON object")

    for key in RAW_METADATA_DEFAULTS:
        if key in metadata and metadata[key] is not None:
            config["metadata"][key] = str(metadata[key]).strip()

    for section_name in ("happ", "v2raytun"):
        section = data.get(section_name, {})
        if section is None:
            section = {}
        if not isinstance(section, dict):
            raise ValueError(f"{source}.{section_name} must be a JSON object")
        routing_profile_b64(section.get("routing"))
        config[section_name] = section
    optional_bool(config["metadata"]["body_comments"])
    return config


_metadata = ReloadingJSON(
    parse_raw_metadata_config,
    default_factory=lambda: parse_raw_metadata_config({}, "defaults"),
)


def load_raw_metadata_config() -> dict:
    return _metadata.get(SUB_METADATA_FILE)


def raw_subscription_metadata(
    kind: str, userinfo_header: str, web_page_url: str
) -> tuple[dict[str, str], list[str]]:
    headers: dict[str, str] = {}
    body_lines: list[str] = []

    if kind == "generic":
        return headers, body_lines

    config = load_raw_metadata_config()
    metadata = config["metadata"]

    support_url = metadata.get("support_url", "")
    announce_text = metadata.get("announce_text", "")
    announce_url = metadata.get("announce_url", "") or support_url
    info_text = metadata.get("info_text", "") or announce_text
    info_button_link = metadata.get("info_button_link", "") or support_url
    body_comments = optional_bool(metadata.get("body_comments"), False)

    title = clamp_text(metadata.get("profile_title", ""), 25)
    if title:
        headers["Profile-Title"] = b64_header(title)

    if metadata.get("profile_update_interval"):
        headers["Profile-Update-Interval"] = metadata["profile_update_interval"]

    if web_page_url:
        headers["Profile-Web-Page-Url"] = web_page_url
    if support_url:
        headers["Support-Url"] = support_url
    if userinfo_header:
        headers["Subscription-Userinfo"] = userinfo_header

    announce = clamp_text(announce_text, 200)
    if announce:
        headers["Announce"] = b64_header(announce)
    if announce_url:
        headers["Announce-Url"] = announce_url

    clipped_info_text = clamp_text(info_text, 200)
    if clipped_info_text:
        headers["Sub-Info-Text"] = clipped_info_text
        info_color = metadata.get("info_color", "").lower()
        if info_color in {"red", "blue", "green"}:
            headers["Sub-Info-Color"] = info_color
        if metadata.get("info_button_text"):
            headers["Sub-Info-Button-Text"] = clamp_text(metadata["info_button_text"], 25)
        if info_button_link:
            headers["Sub-Info-Button-Link"] = info_button_link

    if metadata.get("expire"):
        headers["Sub-Expire"] = metadata["expire"]
        if metadata.get("expire_button_link"):
            headers["Sub-Expire-Button-Link"] = metadata["expire_button_link"]

    if kind == "happ":
        routing = happ_routing_link(config["happ"].get("routing"))
        if routing:
            headers["Routing"] = routing
            headers["Routing-Enable"] = "1"
    elif kind == "v2raytun":
        routing = v2raytun_routing_value(config["v2raytun"].get("routing"))
        if routing:
            headers["Routing"] = routing

    if body_comments:
        for key, value in headers.items():
            body_lines.append(f"#{key.lower()}: {value}")
        if kind == "happ" and "Routing" in headers:
            body_lines.append(headers["Routing"])

    return headers, body_lines


def web_routing_links() -> dict[str, str]:
    try:
        config = load_raw_metadata_config()
    except Exception:
        return {}

    out: dict[str, str] = {}
    try:
        happ = happ_routing_link(config.get("happ", {}).get("routing"))
        if happ:
            out["happ"] = happ
    except Exception:
        pass

    try:
        v2raytun = v2raytun_routing_value(config.get("v2raytun", {}).get("routing"))
        if v2raytun:
            out["v2raytun"] = "v2raytun://import_route/" + v2raytun
    except Exception:
        pass

    return out


def web_client_config(sub_id: str, sub_url: str) -> dict:
    return {
        "subId": sub_id,
        "urls": {
            "base": sub_url,
            "base64": url_with_query(sub_url, {"format": "base64"}),
            "happ": url_with_query(sub_url, {"format": "happ"}),
            "v2raytun": url_with_query(sub_url, {"format": "v2raytun"}),
            "mihomo": url_with_query(sub_url, {"format": "mihomo"}),
        },
        "routing": web_routing_links(),
    }


# Browser page
HTML_TEMPLATE_FILE = env("HTML_TEMPLATE_FILE", os.path.join(ASSET_DIR, "web_template.html"))
I18N_FILE = env("I18N_FILE", os.path.join(ASSET_DIR, "web_i18n.json"))


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
    qr_block = (
        f'<img src="{html.escape(qr)}" alt="QR"/>'
        if qr
        else '<div style="color:rgba(255,255,255,.62);font-size:12px">QR unavailable</div>'
    )

    items = []
    if lines:
        for i, ln in enumerate(lines, start=1):
            config = amneziawg_config(ln)
            nm = html.escape(item_name(ln, i, config))
            esc = html.escape(ln)
            copied = html.escape(config if config is not None else ln)
            copied = copied.replace("\r", "&#13;").replace("\n", "&#10;")
            label = (
                '<span class="row-action" data-i18n="copyConfig">Copy configuration</span>'
                if config is not None
                else ""
            )
            row = (
                f'<button class="row" type="button" data-copy="{copied}" '
                f'data-link="{esc}" title="{esc}">'
                f'<div class="name">{nm}</div><div class="mono">{esc}</div>{label}</button>'
            )
            if config is not None:
                download = base64.b64encode(config.encode("utf-8")).decode("ascii")
                row = (
                    f'<div class="config-row">{row}'
                    f'<a class="btn" href="data:text/plain;charset=utf-8;base64,{download}" '
                    f'download="amneziawg-{i}.conf" data-i18n="downloadConfig">'
                    "Download .conf</a></div>"
                )
            items.append(row)
    items_html = (
        "\n".join(items)
        if items
        else '<div style="color:rgba(255,255,255,.62);padding:6px 2px">(no parsed list, see raw below)</div>'
    )
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
        CLIENTS_CONFIG=json.dumps(web_client_config(sub_id, sub_url), ensure_ascii=False),
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
