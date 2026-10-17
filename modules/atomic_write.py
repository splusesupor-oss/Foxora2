"""Crash-safe atomic writers for small, low-change runtime files."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _publish(path, writer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_json(path, data, *, indent=None):
    return _publish(
        path,
        lambda stream: json.dump(
            data, stream, ensure_ascii=False,
            indent=indent,
            separators=None if indent is not None else (",", ":"),
        ),
    )


def write_text(path, text):
    return _publish(path, lambda stream: stream.write(str(text)))
