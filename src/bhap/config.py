"""Settings, data directory layout, and per-project workspace binding.

Data root (override with BHAP_HOME):
  Linux   $XDG_DATA_HOME/bhap  (~/.local/share/bhap)
  macOS   ~/Library/Application Support/Bhap
  Windows %LOCALAPPDATA%\\Bhap

  <root>/config/config.json   settings
  <root>/db/bhap.sqlite       metadata, chunks, vectors, chat, study
  <root>/workspaces/<id>/originals|exports
  <root>/cache  <root>/backups  <root>/logs
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

from .errors import UsageError

# key: (default, type, validator-or-choices, description)
SCHEMA = {
    "language": ("id", str, ("id", "en"), "UI language / bahasa antarmuka"),
    "workspace": ("personal", str, None, "Default workspace"),
    "runtime": ("ollama", str, ("ollama", "llamacpp"), "Local LLM runtime adapter"),
    "ollama_url": ("http://127.0.0.1:11434", str, "loopback", "Ollama endpoint (loopback only)"),
    "llamacpp_url": ("http://127.0.0.1:8080", str, "loopback", "llama.cpp server endpoint (loopback only)"),
    "auto_start_runtime": (True, bool, None, "Start a local `ollama serve` (loopback) when it is not running"),
    "llm_model": ("", str, None, "Chat/generation model"),
    "embedding_model": ("", str, None, "Embedding model"),
    "mode": ("strict", str, ("strict", "general", "brainstorm"), "Default answer mode"),
    "top_k": (6, int, (1, 30), "Evidence chunks per answer"),
    "max_chunks_per_doc": (4, int, (1, 30), "Diversity cap per document"),
    "num_ctx": (8192, int, (1024, 262144), "Context window requested from the model"),
    "temperature": (0.1, float, (0.0, 2.0), "Sampling temperature"),
    "min_similarity": (0.0, float, (-1.0, 1.0), "Abstain when best vector score is below this (0 = off)"),
    "history_turns": (3, int, (0, 20), "Previous exchanges sent as chat context"),
    "chunk_words": (220, int, (40, 2000), "Target words per chunk"),
    "chunk_overlap": (40, int, (0, 500), "Overlapping words between chunks"),
    "embed_batch": (16, int, (1, 256), "Texts per embedding request"),
    "max_file_mb": (100, int, (1, 4096), "Maximum import size in MB"),
    "store_originals": (True, bool, None, "Copy originals into Bhap (false = reference mode)"),
    "include_speaker_notes": (True, bool, None, "Index PPTX speaker notes"),
    "ocr": ("auto", str, ("auto", "off"), "OCR text-less PDF pages with local Tesseract"),
    "ocr_languages": ("ind+eng", str, None, "Tesseract language codes"),
    "isolate_parsing": (True, bool, None, "Parse files in a resource-limited worker process"),
    "parse_timeout": (300, int, (10, 7200), "Seconds before a parse worker is killed"),
    "parse_memory_mb": (3072, int, (256, 65536), "Address-space limit for parse worker (POSIX)"),
    "low_resource": (False, bool, None, "Smaller batches/context for weak hardware"),
    "save_history": (True, bool, None, "Persist conversations locally"),
    "allow_shell": (False, bool, None, "Allow !<command> in the interactive session (confirmed per run)"),
    "auto_switch": (False, bool, None, "Switch workspace on cd when folder is linked"),
    "pdf_viewer": ("", str, None, "Viewer template, e.g. 'zathura -P {page} {file}'"),
    "color": ("auto", str, ("auto", "always", "never"), "Terminal colors"),
}

LOOPBACK_URL = re.compile(r"^https?://(127(?:\.\d{1,3}){3}|localhost|\[::1\])(:\d{1,5})?/?$")


def home():
    override = os.environ.get("BHAP_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Bhap"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Bhap"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "bhap"


def path(*parts, create=True):
    """Return a path inside the data root, creating parent directories privately."""
    target = home().joinpath(*parts)
    if create:
        directory = target if not target.suffix else target.parent
        directory.mkdir(parents=True, exist_ok=True)
        _private(home())
    return target


def _private(directory):
    if os.name != "nt":
        try:
            directory.chmod(0o700)
        except OSError:
            pass


def config_file():
    return home() / "config" / "config.json"


def _readable_config():
    """Current config file, or the 0.1 location if not migrated yet (reading never moves files)."""
    current = config_file()
    if not current.exists() and (home() / "config.json").exists():
        return home() / "config.json"
    return current


def migrate_layout():
    """Move files from the 0.1 flat layout (<root>/config.json, <root>/bhap.sqlite)."""
    root = home()
    legacy_config = root / "config.json"
    if legacy_config.exists() and not config_file().exists():
        config_file().parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_config), str(config_file()))
    legacy_db = root / "bhap.sqlite"
    new_db = root / "db" / "bhap.sqlite"
    if legacy_db.exists() and not new_db.exists():
        new_db.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm", "-journal"):
            source = root / f"bhap.sqlite{suffix}"
            if source.exists():
                shutil.move(str(source), str(new_db.parent / source.name))


def defaults():
    return {key: spec[0] for key, spec in SCHEMA.items()}


def load():
    data = defaults()
    file = _readable_config()
    if file.exists():
        try:
            stored = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        for key, value in stored.items():
            if key in SCHEMA:
                try:
                    data[key] = coerce(key, value)
                except UsageError:
                    pass
    env_lang = os.environ.get("BHAP_LANG")
    if env_lang in ("id", "en"):
        data["language"] = env_lang
    for key, env in (("ollama_url", "BHAP_OLLAMA_URL"), ("llamacpp_url", "BHAP_LLAMACPP_URL")):
        if os.environ.get(env):
            data[key] = coerce(key, os.environ[env])
    if data["low_resource"]:
        data["top_k"] = min(data["top_k"], 4)
        data["num_ctx"] = min(data["num_ctx"], 4096)
        data["embed_batch"] = min(data["embed_batch"], 4)
    return data


def save(data):
    migrate_layout()
    file = config_file()
    file.parent.mkdir(parents=True, exist_ok=True)
    _private(home())
    clean = {key: data[key] for key in SCHEMA if key in data}
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if os.name != "nt":
        tmp.chmod(0o600)
    os.replace(tmp, file)


def update(**changes):
    data = stored()
    for key, value in changes.items():
        data[key] = coerce(key, value)
    save(data)
    return load()


def stored():
    """Settings as stored on disk (without env overrides or low-resource clamps)."""
    data = defaults()
    file = _readable_config()
    if file.exists():
        try:
            for key, value in json.loads(file.read_text(encoding="utf-8")).items():
                if key in SCHEMA:
                    data[key] = value
        except (OSError, ValueError):
            pass
    return data


def coerce(key, value):
    if key not in SCHEMA:
        raise UsageError(f"Unknown setting: {key}", hint="bhap config show")
    default, kind, rule, _ = SCHEMA[key]
    try:
        if kind is bool:
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                    raise ValueError
                value = lowered in {"true", "1", "yes", "on"}
            else:
                value = bool(value)
        elif kind is int:
            value = int(value)
        elif kind is float:
            value = float(value)
        else:
            value = str(value).strip()
    except (TypeError, ValueError):
        raise UsageError(f"Invalid value for {key}: {value!r} ({kind.__name__} expected)") from None
    if rule == "loopback":
        if not LOOPBACK_URL.match(value):
            raise UsageError(f"{key} must point to loopback (127.0.0.1/localhost); remote runtimes are not allowed")
        value = value.rstrip("/")
    elif isinstance(rule, tuple) and kind is str:
        if value not in rule:
            raise UsageError(f"{key} must be one of: {', '.join(rule)}")
    elif isinstance(rule, tuple):
        low, high = rule
        if not low <= value <= high:
            raise UsageError(f"{key} must be between {low} and {high}")
    return value


# --- project binding: <folder>/.bhap/config.toml -----------------------------

PROJECT_FILE = Path(".bhap") / "config.toml"


def _read_toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except ImportError:  # Python 3.10: tiny subset parser (key = "value" / true / false)
        result = {}
        for line in text.splitlines():
            match = re.match(r'^\s*([A-Za-z0-9_.]+)\s*=\s*(.+?)\s*$', line)
            if not match or line.lstrip().startswith("#"):
                continue
            raw = match.group(2)
            if raw[:1] in "\"'":
                result[match.group(1)] = raw[1:-1]
            elif raw in ("true", "false"):
                result[match.group(1)] = raw == "true"
        return result


def find_project(start):
    """Return (folder, settings) for the nearest .bhap/config.toml at or above start."""
    current = Path(start).resolve()
    for folder in (current, *current.parents):
        candidate = folder / PROJECT_FILE
        if candidate.is_file():
            try:
                return folder, _read_toml(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return folder, {}
    return None, {}


def link_project(folder, workspace_name):
    target = Path(folder) / PROJECT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    safe = workspace_name.replace("\\", "\\\\").replace('"', '\\"')
    target.write_text(f'# Bhap project binding\nworkspace = "{safe}"\n', encoding="utf-8")
    return target
