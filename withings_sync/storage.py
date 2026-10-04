"""Durable local state shared by authentication and sync commands."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator


class Storage:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextmanager
    def lock(self) -> Iterator[None]:
        lock_path = self.directory / "withings-sync.lock"
        with lock_path.open("a", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def read_json(self, name: str) -> dict[str, Any]:
        path = self.directory / name
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"Invalid state file: {path}")
        return data

    def write_json(self, name: str, data: dict[str, Any]) -> None:
        path = self.directory / name
        fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", dir=self.directory)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, path)
            os.chmod(path, 0o600)
            dir_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
