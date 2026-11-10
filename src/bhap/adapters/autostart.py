"""Start a local Ollama on demand, so opening `bhap` just works.

If the configured runtime is Ollama, its loopback URL is not answering, and an `ollama`
binary is installed, Bhap starts `ollama serve` as a child process bound to that loopback
address and stops it again when Bhap exits. Nothing is installed or left running.
Disable with `bhap config set auto_start_runtime false`.
"""
import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import config
from . import runtime

_started = {"proc": None}


def find_binary():
    found = shutil.which("ollama")
    if found:
        return found
    for candidate in (Path.home() / ".local/bin/ollama", Path.home() / ".local/opt/ollama/bin/ollama",
                      Path("/usr/local/bin/ollama"), Path("/opt/ollama/bin/ollama")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _reachable(settings):
    try:
        runtime(settings).version()
        return True
    except Exception:  # noqa: BLE001 - any failure means "not up"
        return False


def ensure_running(settings=None, wait=30, notify=None):
    """Return "running" (already up), "started", "unavailable" (no binary) or "failed"."""
    settings = settings or config.load()
    if (settings["runtime"] != "ollama" or not settings.get("auto_start_runtime", True)
            or os.environ.get("BHAP_NO_AUTOSTART")):
        return "skipped"
    if _reachable(settings):
        return "running"
    binary = find_binary()
    if not binary:
        return "unavailable"
    host = settings["ollama_url"].split("://", 1)[1]
    log_path = config.path("logs") / "ollama.log"
    if notify:
        notify(host)
    log = open(log_path, "ab")  # noqa: SIM115 - handed to the child process
    proc = subprocess.Popen([binary, "serve"], env={**os.environ, "OLLAMA_HOST": host}, stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    _started["proc"] = proc
    atexit.register(stop)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _started["proc"] = None
            return "failed"
        if _reachable(settings):
            return "started"
        time.sleep(0.3)
    stop()
    return "failed"


def stop():
    proc = _started["proc"]
    if proc is None:
        return
    _started["proc"] = None
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def started_by_bhap():
    return _started["proc"] is not None
