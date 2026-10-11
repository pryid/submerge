"""Read-only diagnostics; never print subscription URLs, profiles or exception text."""

import base64
import json
import re
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def doctor_report(required_routing=()):
    # Import here so a bad numeric environment setting becomes a concise failure.
    try:
        from . import service
    except (ValueError, OSError) as error:
        print(f"FAIL: service environment ({type(error).__name__})")
        return 1

    failures = 0

    def check(label, operation):
        nonlocal failures
        try:
            value = operation()
        except (OSError, ValueError, TypeError) as error:
            failures += 1
            print(f"FAIL: {label} ({type(error).__name__}; contents omitted)")
            return None
        print(f"OK: {label}")
        return value

    def read_json(path, parser):
        with open(path, encoding="utf-8") as stream:
            return parser(json.load(stream), "configuration")

    # Read disk directly: unlike the running service, doctor must not use old cached values.
    sources = check(
        f"sources {service.SUB_BASES_FILE!r}",
        lambda: read_json(service.SUB_BASES_FILE, service.parse_sub_bases),
    )
    if sources is not None:
        print(f"  Sources: {len(sources)}")
    if service.SUB_LINK_REWRITES_FILE:
        check(
            f"rewrites {service.SUB_LINK_REWRITES_FILE!r}",
            lambda: read_json(service.SUB_LINK_REWRITES_FILE, service.parse_link_rewrite_rules),
        )
    elif service.SUB_LINK_REWRITES:
        check(
            "inline rewrites",
            lambda: service.parse_link_rewrite_rules_json(service.SUB_LINK_REWRITES, "inline"),
        )
    else:
        print("INFO: rewrites not configured")

    metadata = None
    path = service.SUB_METADATA_FILE
    try:
        missing = not path or not Path(path).exists()
    except OSError:
        missing = False  # Report unreadable paths as failures below.
    if missing:
        print(f"WARN: optional metadata {path!r} missing; client routing is not configured")
    else:
        metadata = check(
            f"metadata {path!r}", lambda: read_json(path, service.parse_raw_metadata_config)
        )
    for client in ("happ", "v2raytun"):
        configured = bool(metadata and metadata[client].get("routing"))
        if not configured and client in required_routing:
            failures += 1
            print(f"FAIL: {client} routing required but not configured")
        else:
            print(
                f"{'OK' if configured else 'WARN'}: {client} routing "
                f"{'configured' if configured else 'not configured'}"
            )

    def template(loader, required=None):
        value = loader()
        # HTML includes JavaScript ${expressions}; safe_substitute leaves those intact.
        if not value.template.strip() or (required and required not in value.get_identifiers()):
            raise ValueError("Invalid template")
        return value

    check(
        f"HTML template {service.HTML_TEMPLATE_FILE!r}",
        lambda: template(service.load_html_template),
    )
    check(f"translations {service.I18N_FILE!r}", service.load_i18n)
    check(
        f"Mihomo template {service.MIHOMO_TEMPLATE_FILE!r}",
        lambda: template(service.load_mihomo_template, "PROVIDER_URL"),
    )
    print(f"Doctor: {failures} failure(s). No upstream requests made.")
    return int(bool(failures))


def validate_routing(value, client):
    if client == "happ":
        prefixes = ("happ://routing/onadd/", "happ://routing/add/")
        prefix = next((p for p in prefixes if value.startswith(p)), None)
        if not prefix:
            raise ValueError("unsupported routing link")
        value = value[len(prefix) :]
    profile = json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
    name = "Name" if client == "happ" else "name"
    if (
        not isinstance(profile, dict)
        or not isinstance(profile.get(name), str)
        or not profile[name].strip()
    ):
        raise ValueError("missing profile name")


def check_public_subscription(url, client="happ", require_routing=False, min_links=1, timeout=30):
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in url)
        ):
            raise ValueError("invalid URL")
        request = Request(
            url,
            headers={
                "User-Agent": {
                    "happ": "Happ",
                    "v2raytun": "v2raytun",
                    "generic": "submerge-verify",
                }[client],
                "Accept": "*/*",
            },
        )
    except ValueError:
        print("FAIL: supply an HTTP(S) subscription URL without credentials or a fragment")
        return 1
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                print(f"FAIL: HTTP {response.status}; response body omitted")
                return 1
            headers = response.headers
            limit = 8 * 1024 * 1024
            body = response.read(limit + 1)
    except HTTPError as error:
        error.close()
        print(f"FAIL: HTTP {error.code}; response body omitted")
        return 1
    except (OSError, HTTPException, ValueError) as error:
        print(f"FAIL: HTTP request ({type(error).__name__}; details omitted)")
        return 1

    elapsed = (time.monotonic() - started) * 1000
    if len(body) > limit:
        print("FAIL: response exceeds 8 MiB")
        return 1
    try:
        length = headers.get("Content-Length")
        if length is not None and int(length) != len(body):
            raise ValueError("length mismatch")
        decoded = base64.b64decode(body.strip(), validate=True).decode("utf-8-sig")
        lines = [
            s.strip() for s in decoded.splitlines() if s.strip() and not s.lstrip().startswith("#")
        ]
        if not lines or not all(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>]+", s) for s in lines
        ):
            raise ValueError("not a URI list")
    except ValueError:
        print("FAIL: incomplete response, invalid base64 or unsupported URI list")
        return 1
    links = sum(not s.startswith(("happ://", "v2raytun://", "incy://")) for s in lines)
    if links < min_links:
        print(f"FAIL: {links} connection link(s); expected at least {min_links}")
        return 1
    print(
        f"OK: HTTP 200; base64 URI list; {links} connection link(s); {len(body)} bytes; {elapsed:.0f} ms"
    )
    print("OK: Content-Length matches" if length is not None else "WARN: Content-Length absent")
    if client != "generic":
        routing = headers.get("Routing", "")
        if not routing:
            print(f"{'FAIL' if require_routing else 'WARN'}: {client} Routing header absent")
            if require_routing:
                return 1
        else:
            try:
                validate_routing(routing, client)
            except (ValueError, TypeError):
                print(f"FAIL: invalid {client} Routing profile")
                return 1
            print(f"OK: {client} Routing profile decodes to a named JSON object")
        if client == "happ" and routing:
            enabled = headers.get("Routing-Enable", "").lower()
            if enabled not in {"1", "true"}:
                print(
                    f"{'FAIL' if require_routing else 'WARN'}: Happ Routing-Enable is not enabled"
                )
                if require_routing:
                    return 1
            else:
                print("OK: Happ Routing-Enable enabled")
    print("Verify passed. Client import and geofile downloads must be checked in the app.")
    return 0
