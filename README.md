# Submerge

Submerge is a small HTTP service that merges subscription responses from multiple upstream servers and serves:

- raw merged output for clients
- a browser-friendly HTML page with QR, copy actions, and traffic summary

## What It Does

- Requests `/sub/<id>` from each upstream in `SUB_BASES_FILE`
- Merges and de-duplicates links (when upstream response is plain base64 list)
- Aggregates `Subscription-Userinfo` across successful upstreams
- Returns raw response for non-browser clients
- Renders an HTML viewer for browser requests

## Project Files

- `submerge.py` - main service
- `web_template.html` - HTML/CSS/JS template (loaded on every request)
- `web_i18n.json` - UI localization dictionary and language list (loaded on every request)
- `sub_bases.example.json` - example upstream source list
- `submerge.container` - example Quadlet container unit

## Requirements

- Python 3.12+ (3.10+ should also work)
- Optional: `qrcode` Python package (for QR in HTML view)
- Nginx in front of the service

## Configuration

Environment variables:

- `SUB_BASES_FILE` (required): path to a JSON file with upstream base URLs
- `LISTEN_HOST` (default: `0.0.0.0`)
- `LISTEN_PORT` (default: `18080`)
- `TIMEOUT` (default: `10`)
- `ALLOW_PARTIAL` (default: `1`)
- `PAGE_TITLE` (default: `Sub-merge`)
- `SUB_LINK_REWRITES` (optional): JSON object with link rewrite rules
- `SUB_LINK_REWRITES_FILE` (optional): path to a JSON file with link rewrite rules; takes precedence over `SUB_LINK_REWRITES`
- `SUB_REWRITE_DNS_TTL` (default: `300`): DNS cache TTL in seconds for host rewrites
- `HTML_TEMPLATE_FILE` (default: `./web_template.html` next to `submerge.py`)
- `I18N_FILE` (default: `./web_i18n.json` next to `submerge.py`)

## Subscription Sources

Sources are configured only through `SUB_BASES_FILE`. The old comma-separated `SUB_BASES` environment variable is not supported.

Example `/opt/submerge/sub_bases.json`:

```json
[
  "https://de.example.com",
  "https://fr.example.com"
]
```

Then add this to the container config:

```ini
Environment=SUB_BASES_FILE=/opt/submerge/sub_bases.json
```

Changes to this JSON file are picked up on the next subscription request without restarting the service. If the file becomes invalid while the service is running, Submerge keeps the last valid source list and logs a warning.

## Link Rewrites

Rewrites are applied only to decoded URL-style subscription links whose host matches a configured rule. They run before merge de-duplication, so raw output, HTML view, and duplicate handling all use the same final link.

Example `/opt/submerge/link_rewrites.json`:

```json
{
  "ru.example.com": {
    "resolve_host": true,
    "query": {
      "sni": "front-primary.example.com"
    }
  },
  "ru-backup.example.com": {
    "resolve_host": true,
    "query": {
      "sni": "front-backup.example.com"
    }
  }
}
```

Then add this to the container config:

```ini
Environment=SUB_LINK_REWRITES_FILE=/opt/submerge/link_rewrites.json
```

The JSON file is not auto-discovered. Rewrites are enabled only when `SUB_LINK_REWRITES_FILE` or `SUB_LINK_REWRITES` is present in the service environment. When `SUB_LINK_REWRITES_FILE` is used, changes to that file are picked up on the next subscription request without restarting the service.

Supported rule fields:

- `resolve_host`: when true, resolves the original link host and replaces it with the resolved IP address
- `address`: optional fixed address replacement; if set, it is used instead of DNS resolution
- `query`: object of query parameters to force, for example `{"sni": "example.com"}`

After changing the Quadlet container file or the rewrite environment variables, reload and restart the service:

```bash
sudo systemctl daemon-reload
sudo systemctl restart submerge.service
```

Check that the generated service contains the rewrite environment:

```bash
sudo systemctl cat submerge.service | grep SUB_LINK_REWRITES
```

If the rewrite JSON file becomes invalid while the service is running, Submerge keeps the last valid rewrite rules and logs a warning.

Keep deployment-specific source and rewrite files out of git. The repository ignores `sub_bases.json` and `link_rewrites.json` for this reason.

## Run Locally (Python)

```bash
cp sub_bases.example.json sub_bases.json
export SUB_BASES_FILE="$PWD/sub_bases.json"
export LISTEN_PORT=18080
python3 -m pip install qrcode || true
python3 submerge.py
```

Service will listen on `http://127.0.0.1:18080`.

## Deploy with Quadlet Container

This repository already includes `submerge.container`.

1. Place project files into `/opt/submerge`:

```bash
sudo mkdir -p /opt/submerge
sudo cp submerge.py web_template.html web_i18n.json /opt/submerge/
sudo cp sub_bases.example.json /opt/submerge/sub_bases.json
```

2. Install the Quadlet source file (adjust path depending on your setup), then reload generators and start the generated service:

```bash
sudo cp submerge.container /etc/containers/systemd/submerge.container
sudo systemctl daemon-reload
sudo systemctl start submerge.service
```

Do not use `systemctl enable --now` for the generated Quadlet service. The persistent source of truth is `/etc/containers/systemd/submerge.container`; its `[Install]` section defines the boot target, and `daemon-reload` regenerates the corresponding systemd service.

3. Check logs:

```bash
sudo systemctl status submerge.service
```

## Nginx Configuration (Required)

Add this block to your nginx config:

```nginx
location ~ ^/sub-merge/([A-Za-z0-9_-]+)$ {
    proxy_pass http://127.0.0.1:18080/sub/$1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Range $http_range;
    proxy_set_header If-Range $http_if_range;
    proxy_redirect off;

    proxy_intercept_errors on;
    limit_req zone=one burst=20 nodelay;
    error_page 400 404 =404 @nginx_404;
}

location @nginx_404 {
    return 404;
}
```

The `limit_req` line requires a matching `limit_req_zone` in the nginx `http` context.

Then reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## Verification

- Browser view:
  - `https://your-domain/sub-merge/<id>`
- Client/raw output:
  - request same URL with non-browser client (for example `curl`)

## Live Editing

- Changes in `web_template.html` and `web_i18n.json` are picked up on page refresh (no service restart needed).
- UI locales are not embedded in `submerge.py`; add or edit languages in `web_i18n.json`.
- Changes in the file referenced by `SUB_BASES_FILE` are picked up on the next subscription request.
- Changes in `submerge.py` require service restart.
- Changes in the file referenced by `SUB_LINK_REWRITES_FILE` are picked up on the next subscription request.
- Changing the value of `SUB_BASES_FILE`, `SUB_LINK_REWRITES`, `SUB_LINK_REWRITES_FILE`, or the Quadlet container file requires service restart.

## Notes

- If an upstream returns a non-plain format, Submerge falls back to the first successful upstream response as-is.
- If all upstreams fail on network level, service returns `502`.
