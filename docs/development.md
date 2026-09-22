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
`make image` passes the current HEAD as the `BUILD_REVISION` build argument;
override it when building exported sources. It identifies the base commit, so
commit local changes before building a release. CI supplies its exact commit SHA.
The image stores this value in an environment variable; Git is not needed at runtime.
Source runs without a valid `BUILD_REVISION` hide the browser footer.
Set `SUBMERGE_TEST_REVISION=<full-sha>` to verify the footer in image HTTP tests;
CI sets this automatically.

## Tests

- `tests/test_config.py`: validation, reloads and recovery.
- `tests/test_service.py`: negotiation, metadata, rewrites and rendering.
- `tests/test_http.py`: actual HTTP process, local upstream fixtures and optional containers.
- `tests/test_amneziawg.py`: 3x-ui configuration decoding, names, browser copy/download and escaping.
- `tests/test_ci.py`: documentation filtering, manual publication and release ordering.

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

## CI and dependencies

The workflow runs for PRs, release tags and manual requests; pushes to `main`
do not trigger it. The `test` job runs on each workflow invocation.
`scripts/ci.py` skips image jobs only for PR changes confined to `README.md` and
`docs/`; release tags and manual runs always build/test images. Native amd64/arm64 jobs
build with `redhat-actions/buildah-build`, run the HTTP suite with nginx and export
OCI archives. A separate job loads both archives and publishes their manifest with
`redhat-actions/podman-login` and `redhat-actions/push-to-registry`. Images enter the
registry only after both architectures pass.

Pip downloads are cached. Tested image archives use an exact commit/architecture
cache key, with no fallback keys; only publishing runs save image caches. PR jobs
have no registry write permission. Artifacts expire after one day. If GitHub has
evicted an image cache, the workflow rebuilds it and repeats the same checks.

Actions are pinned to commits and the Python base to a multiarch digest.
Dependabot proposes weekly updates for Actions, the Containerfile and Python
dependencies. Review/merge these updates to receive base-image security fixes;
rerunning an unchanged commit does not update its pinned Python base. To update
it manually, obtain the **index** digest with
`skopeo inspect --raw docker://docker.io/library/python:3.12-alpine | sha256sum`,
replace the `FROM` digest and run image tests before releasing.

Publication uses `concurrency.queue: max` to serialize releases without dropping
pending jobs. This is [supported by GitHub](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency),
but actionlint 1.7.12 does not yet recognize the `queue` key. Until it supports
that field, lint the workflow with the narrow exception
`actionlint -ignore 'unexpected key "queue" for "concurrency" section'`.
Do not remove the queue just to satisfy the old linter.

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
