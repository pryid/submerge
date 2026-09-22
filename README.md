# Submerge

A small HTTP service that merges subscriptions, deduplicates proxy links, applies
optional rewrites and aggregates traffic counters. It serves raw subscriptions,
Mihomo profiles, Happ/v2RayTun metadata and a browser page with QR codes.

Production runs from a ready-to-use OCI image. The server needs only a Quadlet and
configuration files; code, Python dependencies and default templates live in the image.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp examples/sub_bases.example.json sub_bases.json
# Edit sub_bases.json with your upstream URLs before requesting a subscription.
.venv/bin/python -m submerge
```

The service listens on port `18080`. `/healthz` checks the local HTTP server;
`/sub/<id>` fetches a subscription. Public links use `/sub-merge/<id>` through nginx.
The previous `python submerge.py` entry point remains available.

## Deploy and publish

The [deployment guide](docs/deployment.md) covers `main.pod`, nginx, migration,
updates and rollback. The [workflow](.github/workflows/ci.yml) checks code, runs
tests, builds the image and tests it with nginx before publishing to GHCR:

| Git event | Image tags |
| --- | --- |
| Pull request or manual run | Checks only |
| Push to `main` | `main`, `sha-<commit>` |
| Release tag `vX.Y.Z` | `vX.Y.Z`, `sha-<commit>`, `stable` |
| Prerelease tag `vX.Y.Z-rc.N` | Version and commit tags only |

After pushing the project changes, publish with an unused release tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

The supplied Quadlet follows `ghcr.io/pryid/submerge:stable`. The workflow currently
builds `linux/amd64`. Set the GHCR package to **Public** for anonymous server pulls.

## Development

```bash
make check PYTHON=.venv/bin/python
make image
make test-image PYTHON=.venv/bin/python
```

See [development and testing](docs/development.md) and the
[configuration reference](docs/configuration.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `submerge/` | HTTP server, subscription logic and configuration loader |
| `submerge/assets/` | Browser template, translations and default Mihomo profile |
| `tests/` | Unit, HTTP and container integration tests |
| `deploy/` | Quadlet and nginx examples |
| `examples/` | Neutral JSON configuration examples |
| `scripts/` | Live endpoint smoke test |
| `docs/` | Configuration, deployment and development guides |

Keep real upstream URLs, rewrite targets and client profiles in ignored local
files or `/etc/submerge` on the server. `.dockerignore` allows only runtime inputs
into the image build context.
