# Repository Guidelines

## Project Structure & Module Organization

`submerge/` contains the Python service: `server.py` handles HTTP, `service.py`
implements subscription processing, and `config.py` validates and reloads
configuration. Bundled HTML, translations, and Mihomo templates live in
`submerge/assets/`; there is no frontend build step. The root `submerge.py` is a
compatibility launcher. Tests live in `tests/`, CI helpers in `scripts/`, deployment
examples in `deploy/`, neutral configuration samples in `examples/`, and guides
in `docs/`.

## Build, Test, and Development Commands

Use Python 3.12+ from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp examples/sub_bases.example.json sub_bases.json
.venv/bin/python -m submerge
```

Edit the copied configuration before fetching subscriptions. The default port is
`18080`; `/healthz` checks the HTTP process.

- `make check PYTHON=.venv/bin/python`: run Ruff, formatting checks, shell syntax checks, and tests.
- `make format PYTHON=.venv/bin/python`: apply Ruff fixes and formatting.
- `make test PYTHON=.venv/bin/python`: run the unittest suite.
- `make image`: build `localhost/submerge:test` using Podman.
- `make test-image PYTHON=.venv/bin/python`: test an existing image with nginx.

Rebuild before testing image changes. Override `IMAGE=...` or `ENGINE=docker` as needed.

## Coding Style & Naming Conventions

Follow `.editorconfig`: four spaces for Python, two for HTML/JSON/YAML/TOML/Markdown,
and tabs for Makefile recipes. Use UTF-8, LF endings, and final newlines. Ruff
uses a 100-character line limit and checks import ordering. Use `snake_case` for
modules/functions, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.

## Testing Guidelines

Use standard-library `unittest`, with `test_*.py` files and `test_*` methods.
Add regression tests for behavior changes in the relevant config, service, HTTP,
or CI-policy suite. No numerical coverage threshold is configured. Use local
upstream fixtures; container tests require Linux host networking and engine access.
Run container tests for changes affecting packaging, HTTP behavior, or nginx integration.

## Commit & Pull Request Guidelines

Recent commits use prefixes such as `refactor:`, `build:`, `ci:`, and `docs:`.
Keep commits focused and describe the resulting behavior. PR descriptions should
explain the problem, changes, and validation; link relevant issues and include
screenshots for browser UI changes. Keep CI green and prefer rebase merging for
linear history.

## Security & Configuration

Never commit real upstream domains, credentials, or client profiles—even in tests.
Use example domains and ignored local configuration. Preserve the `.dockerignore`
allowlist, pinned Actions/base-image references, and tests before publication.
See `docs/deployment.md` for Quadlet deployment and release procedures.
