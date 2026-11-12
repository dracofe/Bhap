"""ZIP / JAR / TAR(.gz/.bz2/.xz) archives: index every readable member as part of one document.

Nothing is extracted to a user-visible location. Members are read into memory (text) or into a
private temp file (PDF/Office/images) and parsed with the same parsers as standalone files.
Defences: member/size/ratio limits, no symlinks/devices, no absolute or ../ paths, nested archives
and binaries are skipped, likely secret files (.env, private keys) are never indexed.
"""
import re
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ..errors import ParseError
from .common import ARCHIVES, IMAGES, LEGACY_OFFICE, TEXTUAL, DOCUMENTS, kind_of, looks_secret, sniff_bytes

MAX_MEMBERS = 5000
MAX_TOTAL = 512 * 1024 * 1024
MAX_MEMBER = 50 * 1024 * 1024
MAX_RATIO = 250
IGNORED_PARTS = {"__macosx", ".git", ".svn", ".hg", "node_modules", "__pycache__", ".idea", ".vscode", ".gradle",
                 "target", "build", "dist", "out", "bin", "obj", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache",
                 ".next", ".nuxt", "coverage", "vendor", "pods", "deriveddata", ".dart_tool"}


def _members_zip(path):
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ParseError(f"{path.name} is not a valid ZIP archive: {exc}") from exc
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS * 4:
        raise ParseError(f"{path.name}: {len(infos)} entries — too many; refusing (possible zip bomb)")
    total = sum(i.file_size for i in infos)
    if total > MAX_TOTAL * 4:
        raise ParseError(f"{path.name} expands to {total // 2**20} MB; refusing (possible zip bomb)")
    for info in infos:
        if info.is_dir():
            continue
        is_link = (info.external_attr >> 16) & 0o170000 == 0o120000
        ratio = info.file_size / info.compress_size if info.compress_size else 0
        yield info.filename, info.file_size, is_link, ratio, (lambda i=info: archive.read(i))
    archive.close()


def _members_tar(path):
    try:
        archive = tarfile.open(path, "r:*")
    except (tarfile.TarError, OSError) as exc:
        raise ParseError(f"{path.name} is not a valid TAR archive: {exc}") from exc
    with archive:
        count = 0
        for member in archive:
            count += 1
            if count > MAX_MEMBERS * 4:
                raise ParseError(f"{path.name}: too many entries; refusing")
            if member.isdir():
                continue
            special = not member.isfile()  # symlinks, hardlinks, devices, fifos

            def read(m=member):
                handle = archive.extractfile(m)
                return handle.read(MAX_MEMBER + 1) if handle else b""
            yield member.name, member.size, special, 0, read


def _unsafe(name):
    pure = PurePosixPath(name.replace("\\", "/"))
    return pure.is_absolute() or ".." in pure.parts or (len(name) > 1 and name[1] == ":")


def parse_archive(path, settings):
    from . import _parse_bytes  # late import: avoid a cycle
    kind = kind_of(path.name)
    members = _members_zip(path) if kind in {".zip", ".jar", ".war"} else _members_tar(path)
    blocks, skipped, warnings = [], {}, []
    files = 0
    total = 0

    def skip(reason, name):
        skipped.setdefault(reason, []).append(name)

    for name, size, special, ratio, read in members:
        clean = re.sub(r"^(?:\./)+", "", name.replace("\\", "/"))
        parts = [p.lower() for p in PurePosixPath(clean).parts]
        base = parts[-1] if parts else ""
        if _unsafe(name):
            skip("unsafe path", name)
            continue
        if special:
            skip("link", name)
            continue
        if any(p in IGNORED_PARTS for p in parts[:-1]) or base.startswith(("._", "~$")) or base == ".ds_store":
            skip("ignored", name)
            continue
        if looks_secret(base):
            skip("secret-like", name)
            continue
        member_kind = kind_of(base)
        if member_kind in ARCHIVES:
            skip("nested archive", name)
            continue
        if member_kind and member_kind not in (TEXTUAL | DOCUMENTS | IMAGES | LEGACY_OFFICE) and member_kind != ".pdf":
            if member_kind in {".class", ".o", ".obj", ".so", ".dll", ".exe", ".pyc", ".a", ".lib", ".dylib", ".bin"}:
                skip("binary", name)
                continue
        if size > MAX_MEMBER:
            skip("too large", name)
            continue
        if ratio > MAX_RATIO and size > 10 * 1024 * 1024:
            raise ParseError(f"{path.name}: member {name} has a suspicious compression ratio (zip bomb?)")
        total += size
        if total > MAX_TOTAL:
            warnings.append(f"stopped after {MAX_TOTAL // 2**20} MB of archive content")
            break
        files += 1
        if files > MAX_MEMBERS:
            warnings.append(f"only the first {MAX_MEMBERS} files were indexed")
            break
        data = read()
        if not member_kind or member_kind not in (DOCUMENTS | IMAGES | LEGACY_OFFICE | {".pdf"}):
            if sniff_bytes(data[:8192]) != "text":
                skip("binary", name)
                files -= 1
                continue
        try:
            parsed = _parse_bytes(data, base, member_kind, settings)
        except ParseError as exc:
            skip("unreadable", f"{name} ({exc})")
            files -= 1
            continue
        for item in parsed["blocks"]:
            item["section"] = clean + (f" > {item['section']}" if item.get("section") else "")
            blocks.append(item)
        warnings.extend(f"{clean}: {w}" for w in parsed.get("warnings", [])[:3])
    for reason, names in skipped.items():
        sample = ", ".join(n.rsplit("/", 1)[-1] for n in names[:4])
        warnings.append(f"skipped {len(names)} {reason} file(s): {sample}{' …' if len(names) > 4 else ''}")
    if not blocks:
        raise ParseError(f"{path.name}: no readable files inside the archive")
    return {"blocks": blocks, "units": files, "unit": "file", "warnings": warnings, "parser": "bhap-archive/1"}


def write_temp(data, name):
    """Write member bytes to a private temp file (for parsers that need a path)."""
    directory = Path(tempfile.mkdtemp(prefix="bhap-member-"))
    target = directory / (Path(name).name or "member")
    target.write_bytes(data)
    return target
