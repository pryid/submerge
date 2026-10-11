"""Read-only diagnostics; never print subscription URLs, profiles or exception text."""

import json
from pathlib import Path


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
