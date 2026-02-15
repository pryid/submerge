# Submerge

Submerge is a small HTTP service that merges subscription responses from multiple upstream servers and serves:

- raw merged output for clients
- a browser-friendly HTML page with QR, copy actions, and traffic summary

## What It Does

- Requests `/sub/<id>` from each upstream in `SUB_BASES`
- Merges and de-duplicates links (when upstream response is plain base64 list)
- Aggregates `Subscription-Userinfo` across successful upstreams
- Returns raw response for non-browser clients
- Renders an HTML viewer for browser requests

## Project Files

- `submerge.py` - main service
- `web_template.html` - HTML/CSS/JS template (loaded on every request)
- `web_i18n.json` - UI localization dictionary (loaded on every request)
- `submerge.container` - example Quadlet container unit

## Requirements

- Python 3.12+ (3.10+ should also work)
- Optional: `qrcode` Python package (for QR in HTML view)
- Nginx in front of the service

## Configuration

Environment variables:

- `SUB_BASES` (required): comma-separated upstream base URLs, example:
  - `https://a.example.com,https://b.example.com`
- `LISTEN_HOST` (default: `0.0.0.0`)
- `LISTEN_PORT` (default: `18080`)
- `TIMEOUT` (default: `10`)
- `ALLOW_PARTIAL` (default: `1`)
- `PAGE_TITLE` (default: `Sub-merge`)
- `HTML_TEMPLATE_FILE` (default: `./web_template.html` next to `submerge.py`)
- `I18N_FILE` (default: `./web_i18n.json` next to `submerge.py`)

## Run Locally (Python)

```bash
export SUB_BASES="https://a.example.com,https://b.example.com"
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
```

2. Install the quadlet unit (adjust path depending on your setup), then reload and start:

```bash
sudo cp submerge.container /etc/containers/systemd/submerge.container
sudo systemctl daemon-reload
sudo systemctl enable --now submerge.service
```

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
    proxy_redirect off;

    proxy_intercept_errors on;
    error_page 400 404 =404 /__nginx_404;
}

location = /__nginx_404 { return 404; }
```

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
- Changes in `submerge.py` require service restart.

## Notes

- If an upstream returns a non-plain format, Submerge falls back to the first successful upstream response as-is.
- If all upstreams fail on network level, service returns `502`.
