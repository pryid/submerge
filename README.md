# Submerge

A small HTTP service that merges subscriptions, deduplicates proxy links, applies
optional rewrites and aggregates traffic counters. It serves raw subscriptions,
Mihomo profiles, Happ/v2RayTun metadata and a browser page with QR codes.
Sources can use custom subscription paths and plaintext or base64 URI lists.
The browser shows per-source usage and expiry.

Production runs from a ready-to-use OCI image. The server needs only a Quadlet and
configuration files; code, Python dependencies and default templates live in the image.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv sync --locked
cp examples/sub_bases.example.json sub_bases.json
# Edit sub_bases.json with your upstream URLs before requesting a subscription.
uv run --locked python -m submerge
```

The service listens on port `18080`. `/healthz` checks the local HTTP server;
`/sub/<id>` fetches a subscription. Public links use `/sub-merge/<id>` through nginx.
The previous `python submerge.py` entry point remains available.

## Deploy and publish

The [deployment guide](docs/deployment.md) covers `main.pod`, nginx, migration,
updates and rollback. The [workflow](.github/workflows/ci.yml) checks code, runs
tests, builds native amd64/arm64 images with Red Hat Actions and tests each with
nginx before publishing one multiarch image to GHCR:

| Git event | Image tags |
| --- | --- |
| Pull request | Checks only |
| Push to `main` | No automatic workflow run |
| Release tag `vX.Y.Z` | `vX.Y.Z`, `sha-<commit>`; highest release also updates `stable` |
| Prerelease tag `vX.Y.Z-rc.N` | Version and commit tags only |
| Manual run | Checks; optional publication from `main` or a release tag |

After committing the project changes, push `main` and an unused release tag
together; only the tag triggers the release workflow:

```bash
git tag v1.0.0
git push --atomic origin main v1.0.0
```

The supplied Quadlet follows `ghcr.io/pryid/submerge:stable`. Podman selects
`linux/amd64` or `linux/arm64` automatically. Set the GHCR package to **Public** for
anonymous server pulls. Manual publication and release ordering are described in
the [deployment guide](docs/deployment.md#publish-the-image).

## Development

```bash
make check
make image
make test-image
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
| `scripts/` | CI release policy and live endpoint smoke test |
| `docs/` | Configuration, deployment and development guides |

Keep real upstream URLs, rewrite targets and client profiles in ignored local
files or `/etc/submerge` on the server. `.dockerignore` allows only runtime inputs
into the image build context.
