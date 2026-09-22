# Development

## Setup

Run commands from the repository root with Python 3.12+:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
make check PYTHON=.venv/bin/python
```

`requirements.txt` contains runtime dependencies. `requirements-dev.txt` adds the
pinned Ruff version used in CI. `pyproject.toml` defines Python formatting and
lint rules; `.editorconfig` defines basic whitespace settings.

## Commands

| Command | Purpose |
| --- | --- |
| `make check` | Lint, formatting check, shell syntax and tests |
| `make format` | Apply import sorting, safe lint fixes and Python formatting |
| `make test` | Unit and HTTP tests |
| `make image` | Build `localhost/submerge:test` with Podman |
| `make test-image` | Run HTTP tests against the image and nginx |

Pass `PYTHON=.venv/bin/python` for the virtual environment. `ENGINE=docker` switches
container commands to Docker. `IMAGE=...` selects another build/test tag.
`make test-image` tests an existing image; run `make image` after code changes.

## Tests

- `tests/test_config.py`: validation, reloads and recovery.
- `tests/test_service.py`: negotiation, metadata, rewrites and rendering.
- `tests/test_http.py`: actual HTTP process, local upstream fixtures and optional containers.

The HTTP suite checks merged links, traffic totals, partial-source policy,
formats, QR, HEAD/errors, config replacement and nginx query forwarding. All
upstreams are local fixtures. Container tests require Linux host networking,
temporary bind mounts and permission to run the chosen engine.

Run only the container integration tests directly:

```bash
SUBMERGE_TEST_IMAGE=localhost/submerge:test \
SUBMERGE_TEST_NGINX_IMAGE=docker.io/library/nginx:stable-alpine \
CONTAINER_ENGINE=podman .venv/bin/python -m unittest -v tests.test_http
```

## Code layout

`submerge/server.py` owns routes, responses and process startup/shutdown.
`submerge/service.py` implements subscriptions, rewrites, metadata and rendering.
`submerge/config.py` provides shared validation/reload behavior. Importing the
package does not read deployment JSON; startup validates it before accepting traffic.

`python -m submerge` is the primary entry point. The root `submerge.py` is a small
compatibility launcher. Bundled assets are resolved relative to the package;
local JSON defaults are relative to the working directory.

The server and upstream fetch loop are synchronous. Concurrency changes should
account for mutable config and DNS caches and retain stable merge order.

## Configuration and assets

Keep deployment data in ignored filenames or `local/`. Commit only neutral
examples. The build context allows package modules, named assets and runtime
requirements; tests, docs and local configs do not enter the image.

The browser template is plain HTML/CSS/JavaScript with Python `string.Template`
placeholders and no frontend build step. Translations live in
`submerge/assets/web_i18n.json`. Asset changes are included in the next image build.
