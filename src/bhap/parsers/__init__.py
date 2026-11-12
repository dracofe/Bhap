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
from .common import (ARCHIVES, CODE, DOCUMENTS, IMAGES, LEGACY, LEGACY_OFFICE, MARKDOWN, SUPPORTED, TEXTUAL,
                     check_type, kind_of, sniff)

PARSER_VERSION = "2"
__all__ = ["SUPPORTED", "LEGACY", "parse_file", "parse_direct", "is_supported", "kind_of"]
FORMATS_HELP = "text & source code, Markdown, PDF, DOCX/PPTX/XLSX, ODT/ODS/ODP, EPUB, CSV/JSON/HTML, notebooks, images (OCR), ZIP/TAR"


def is_supported(path):
    return kind_of(Path(path).name) in SUPPORTED


def _text_result(text, name, kind):
    """Parse decoded text according to its kind."""
    from . import data
    from .code import parse_code
    from .text import parse_markdown, parse_plain
    text = text.replace("\r\n", "\n")
    if kind in MARKDOWN:
        blocks, parser = parse_markdown(text), "bhap-text/1"
    elif kind in CODE:
        blocks, parser = parse_code(text, name), "bhap-code/1"
    elif kind in (".csv", ".tsv"):
        blocks, parser = data.parse_table(text, "\t" if kind == ".tsv" else ","), "bhap-csv/1"
    elif kind in (".json", ".jsonl", ".ndjson", ".geojson"):
        blocks, parser = data.parse_json(text, name), "bhap-json/1"
    elif kind in (".html", ".htm", ".xhtml"):
        blocks, parser = data.parse_html(text), "bhap-html/1"
    elif kind == ".xml":
        blocks, parser = data.parse_xml(text), "bhap-xml/1"
    elif kind == ".ipynb":
        blocks, parser = data.parse_notebook(text), "bhap-ipynb/1"
    elif kind == ".rtf":
        blocks, parser = data.parse_rtf(text), "bhap-rtf/1"
    else:
        blocks, parser = parse_plain(text), "bhap-text/1"
    unit = "row" if kind in (".csv", ".tsv") else "line"
    units = max(text.count("\n") + 1, 1) - (1 if unit == "row" else 0)
    return {"blocks": blocks, "units": units, "unit": unit, "warnings": [], "parser": parser}


def _parse_bytes(data, name, kind, settings):
    """Parse an in-memory file (used for archive members)."""
    from .text import decode
    if kind in DOCUMENTS | IMAGES | LEGACY_OFFICE or kind == ".pdf":
        from .archive import write_temp
        temp = write_temp(data, name)
        try:
            return parse_direct(temp, settings)
        finally:
            import shutil
            shutil.rmtree(temp.parent, ignore_errors=True)
    return _text_result(decode(data), name, kind)


def parse_direct(path, settings):
    path = Path(path)
    kind = kind_of(path.name)
    if kind not in SUPPORTED:
        actual = sniff(path)
        if actual == "text":  # any other text file (.log variants, no extension, …)
            from .text import decode
            return _text_result(decode(path.read_bytes()), path.name, ".txt")
        if actual in IMAGES or actual in DOCUMENTS or actual == ".tar":
            kind = actual
        else:
            raise UsageError(f"{path.name}: unsupported binary file (supported: {FORMATS_HELP})")
    actual = check_type(path, kind)
    if kind in LEGACY_OFFICE:
        target = LEGACY[kind]
        if actual in (".docx", ".pptx", ".xlsx"):  # a modern file with an old extension
            kind = actual
        elif actual == ".rtf":
            kind = ".rtf"
        elif actual == "ole" and path.stat().st_size < 1536:
            raise ParseError(f"{path.name}: corrupt legacy Office file (too small to be a real {kind})")
        else:
            from .office_more import convert_legacy
            converted, workdir = convert_legacy(path, target)
            try:
                result = parse_direct(converted, settings)
                if not any((b.get("text") or "").strip() for b in result["blocks"]):
                    raise ParseError(f"{path.name}: no readable text after converting the legacy {kind} file")
                result["parser"] += "+libreoffice"
                result.setdefault("warnings", []).append(f"converted from {kind} with local LibreOffice")
                return result
            finally:
                import shutil
                shutil.rmtree(workdir, ignore_errors=True)
    if kind == ".pdf":
        from .pdf import parse
        return parse(path, settings)
    if kind == ".docx":
        from .ooxml import parse_docx
        return parse_docx(path)
    if kind == ".pptx":
        from .ooxml import parse_pptx
        return parse_pptx(path, include_notes=settings.get("include_speaker_notes", True))
    if kind == ".xlsx":
        from .office_more import parse_xlsx
        return parse_xlsx(path)
    if kind in (".odt", ".ods", ".odp"):
        from .office_more import parse_odf
        return parse_odf(path, kind)
    if kind == ".epub":
        from .office_more import parse_epub
        return parse_epub(path)
    if kind in IMAGES:
        from .office_more import parse_image
        return parse_image(path, settings)
    if kind in ARCHIVES:
        from .archive import parse_archive
        return parse_archive(path, settings)
    if kind in TEXTUAL:
        from .text import decode
        return _text_result(decode(path.read_bytes()), path.name, kind)
    raise UsageError(f"{path.name}: unsupported format (supported: {FORMATS_HELP})")


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
