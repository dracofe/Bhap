"""Document parsing front door.

`parse_file` validates the file (extension, real content type, size) and runs the
format parser — by default inside a separate, resource-limited worker process so a
malicious or pathological document cannot hang or exhaust the main process.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from ..errors import ParseError, UsageError
from .common import LEGACY, SUPPORTED, check_type

PARSER_VERSION = "1"
__all__ = ["SUPPORTED", "LEGACY", "parse_file", "parse_direct", "is_supported"]


def is_supported(path):
    return Path(path).suffix.lower() in SUPPORTED


def parse_direct(path, settings):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in LEGACY:
        raise ParseError(f"{path.name}: legacy {suffix} is not supported; convert it to {LEGACY[suffix]} locally")
    if suffix not in SUPPORTED:
        raise UsageError(f"Unsupported format: {suffix or path.name} (supported: {', '.join(sorted(SUPPORTED))})")
    check_type(path, suffix)
    if suffix == ".pdf":
        from .pdf import parse
        return parse(path, settings)
    if suffix == ".docx":
        from .ooxml import parse_docx
        return parse_docx(path)
    if suffix == ".pptx":
        from .ooxml import parse_pptx
        return parse_pptx(path, include_notes=settings.get("include_speaker_notes", True))
    from .text import parse
    return parse(path)


def _limits(memory_mb, cpu_seconds):
    def apply():
        try:
            import resource
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):
            pass
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        except (ImportError, ValueError, OSError):
            pass
    return apply


def parse_file(path, settings):
    if not settings.get("isolate_parsing", True):
        return parse_direct(path, settings)
    package_parent = str(Path(__file__).resolve().parents[2])
    # -I keeps the worker from importing anything from the current directory or env
    # vars; we hand it the parent's import path explicitly (minus '' / cwd).
    cwd = os.getcwd()
    search_path = [package_parent] + [p for p in sys.path if p and p != cwd and p != package_parent]
    bootstrap = ("import sys, json; sys.path[:] = json.loads(sys.argv[1]); "
                 "from bhap.parsers.worker import main; sys.exit(main())")
    package_parent = json.dumps(search_path)
    payload = json.dumps({"path": str(path), "settings": {
        key: settings.get(key) for key in ("ocr", "ocr_languages", "include_speaker_notes")}})
    kwargs = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _limits(settings.get("parse_memory_mb", 3072), settings.get("parse_timeout", 300))
    else:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run([sys.executable, "-I", "-c", bootstrap, package_parent], input=payload,
                                capture_output=True, text=True, encoding="utf-8",
                                timeout=settings.get("parse_timeout", 300), **kwargs)
    except subprocess.TimeoutExpired:
        raise ParseError(f"{Path(path).name}: parsing exceeded {settings.get('parse_timeout', 300)}s and was stopped") from None
    except OSError as exc:
        raise ParseError(f"cannot start parse worker: {exc}") from exc
    try:
        data = json.loads(result.stdout)
    except ValueError:
        tail = (result.stderr or "").strip().splitlines()[-1:] or [f"exit code {result.returncode}"]
        if result.returncode < 0 or "MemoryError" in (result.stderr or ""):
            raise ParseError(f"{Path(path).name}: parser exceeded its resource limits ({tail[0]})") from None
        raise ParseError(f"{Path(path).name}: parser crashed ({tail[0]})") from None
    if "error" in data:
        cls = UsageError if data.get("kind") == "usage" else ParseError
        raise cls(data["error"])
    return data
