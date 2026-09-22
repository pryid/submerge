"""JSON configuration with atomic replacement and last-known-good reloads."""

import json
import logging
import os
import threading

_UNSET = object()


class ReloadingJSON:
    def __init__(self, parser, default_factory=None):
        self.parser = parser
        self.default_factory = default_factory
        self.path = None
        self.value = _UNSET
        self.signature = None
        self.last_error = None
        self.lock = threading.Lock()

    def get(self, path):
        with self.lock:
            return self._get(path)

    def _get(self, path):
        if path != self.path:
            self.path = path
            self.value = _UNSET
            self.signature = None
            self.last_error = None

        try:
            if not path:
                raise FileNotFoundError("No configuration path configured")
            stat = os.stat(path)
            signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature == self.signature:
                self.last_error = None
                return self.value
            with open(path, encoding="utf-8") as stream:
                value = self.parser(json.load(stream), path)
        except (OSError, ValueError) as error:
            if (
                isinstance(error, FileNotFoundError)
                and self.default_factory
                and self.signature is None
            ):
                self.value = self.default_factory()
                return self.value
            if self.value is _UNSET:
                raise
            message = str(error)
            if message != self.last_error:
                logging.warning("Keeping previous configuration for %s: %s", path, error)
                self.last_error = message
            return self.value

        self.value = value
        self.signature = signature
        self.last_error = None
        return self.value
