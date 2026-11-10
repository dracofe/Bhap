"""Structured local logs (JSON lines). Nothing leaves the machine."""
import json
import os
import re
import time

from .config import home

MAX_BYTES = 2 * 1024 * 1024


def _file():
    directory = home() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "bhap.log"


def log(event, level="info", **fields):
    try:
        target = _file()
        if target.exists() and target.stat().st_size > MAX_BYTES:
            os.replace(target, target.with_suffix(".log.1"))
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event": event, **fields}
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # logging must never break the app


def read(limit=50):
    target = _file()
    if not target.exists():
        return []
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


_PATH = re.compile(r"(?:[A-Za-z]:\\|/)[^\s\"']+")
SENSITIVE = {"path", "file", "query", "question", "text", "name", "target", "error"}


def redact(record):
    clean = {}
    for key, value in record.items():
        if key in SENSITIVE and isinstance(value, str):
            clean[key] = "<redacted>" if key in {"query", "question", "text"} else _PATH.sub("<path>", value)
        else:
            clean[key] = value
    return clean
