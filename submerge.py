"""Compatibility launcher; prefer python -m submerge."""

from submerge.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
