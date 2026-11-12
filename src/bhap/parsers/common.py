"""Hardening shared by all parsers: content sniffing, ZIP-bomb and XML-DTD defenses."""
import zipfile
from xml.etree import ElementTree as ET

from ..errors import ParseError

MAX_ZIP_UNCOMPRESSED = 512 * 1024 * 1024
MAX_ZIP_MEMBERS = 20000
MAX_MEMBER_RATIO = 250  # uncompressed / compressed for any sizeable member
MAX_XML_PART = 64 * 1024 * 1024

# --------------------------------------------------------------------------- format registry
CODE = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".java", ".kt", ".kts", ".scala", ".groovy", ".gradle",
        ".py", ".pyw", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".go", ".rs", ".cs", ".fs", ".vb", ".swift",
        ".m", ".mm", ".php", ".rb", ".pl", ".pm", ".lua", ".r", ".jl", ".dart", ".zig", ".nim", ".hs", ".ml",
        ".ex", ".exs", ".erl", ".clj", ".lisp", ".scm", ".sql", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat",
        ".cmd", ".asm", ".s", ".v", ".sv", ".vhd", ".vhdl", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
        ".proto", ".graphql", ".tf", ".dockerfile", ".makefile", ".cmake", ".mk", ".ino", ".sol", ".pas", ".f90",
        ".cu", ".cl", ".glsl", ".hlsl", ".mat", ".nix"}
TEXT = {".txt", ".text", ".log", ".ini", ".cfg", ".conf", ".toml", ".yaml", ".yml", ".properties", ".tex", ".bib",
        ".rst", ".adoc", ".asciidoc", ".org", ".srt", ".vtt", ".diff", ".patch", ".csv.txt", ".nfo"}
MARKDOWN = {".md", ".markdown", ".mdx"}
DATA = {".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".geojson", ".xml", ".html", ".htm", ".xhtml", ".ipynb", ".rtf"}
DOCUMENTS = {".pdf", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".epub"}
LEGACY_OFFICE = {".doc", ".ppt", ".xls"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
ARCHIVES = {".zip", ".jar", ".war", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}
SPECIAL_NAMES = {"makefile": ".makefile", "dockerfile": ".dockerfile", "cmakelists.txt": ".cmake", "readme": ".md",
                 "license": ".txt", "jenkinsfile": ".groovy", "vagrantfile": ".rb", "gemfile": ".rb", "rakefile": ".rb"}

TEXTUAL = CODE | TEXT | MARKDOWN | DATA
SUPPORTED = TEXTUAL | DOCUMENTS | LEGACY_OFFICE | IMAGES | ARCHIVES
LEGACY = {".doc": ".docx", ".ppt": ".pptx", ".xls": ".xlsx"}
# Never pick these up from folder scans or archives: they usually hold secrets.
SECRET_NAMES = {".env", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "id_ecdsa", "credentials",
                "secrets.json", "secrets.yaml", "secrets.yml"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".kdbx", ".gpg", ".asc"}


def kind_of(name):
    """Normalised extension used for dispatch ('.tar.gz', '.makefile', …)."""
    lowered = str(name).lower().replace("\\", "/").rsplit("/", 1)[-1]
    for double in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lowered.endswith(double):
            return double
    if lowered in SPECIAL_NAMES:
        return SPECIAL_NAMES[lowered]
    dot = lowered.rfind(".")
    return lowered[dot:] if dot > 0 else ""


def looks_secret(name):
    lowered = str(name).lower().rsplit("/", 1)[-1]
    return (lowered in SECRET_NAMES or lowered.startswith(".env") or kind_of(lowered) in SECRET_SUFFIXES)


def block(text, *, page=None, slide=None, section="", kind="text", visual=False, ocr_conf=None,
          approx=False, line=None):
    return {"text": text, "page": page, "slide": slide, "section": section, "kind": kind,
            "visual": visual, "ocr_conf": ocr_conf, "approx": approx, "line": line}


def sniff_bytes(head):
    if b"%PDF-" in head[:1024]:
        return ".pdf"
    if head.startswith(b"\x89PNG"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return ".tiff"
    if head.startswith(b"BM"):
        return ".bmp"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"BZh"):
        return "bzip2"
    if head.startswith(b"\xfd7zXZ\x00"):
        return "xz"
    if len(head) > 262 and head[257:262] == b"ustar":
        return ".tar"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"  # legacy .doc/.ppt/.xls
    if head.startswith(b"{\\rtf"):
        return ".rtf"
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "binary"
    return "text"


def sniff(path):
    """Return the real format based on content, independent of the extension."""
    with open(path, "rb") as handle:
        head = handle.read(8192)
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                mimetype = archive.read("mimetype").decode("ascii", "ignore").strip() if "mimetype" in names else ""
        except (zipfile.BadZipFile, OSError):
            return "zip-corrupt"
        if "word/document.xml" in names:
            return ".docx"
        if "ppt/presentation.xml" in names:
            return ".pptx"
        if "xl/workbook.xml" in names:
            return ".xlsx"
        odf = {"application/vnd.oasis.opendocument.text": ".odt",
               "application/vnd.oasis.opendocument.spreadsheet": ".ods",
               "application/vnd.oasis.opendocument.presentation": ".odp", "application/epub+zip": ".epub"}
        if mimetype in odf:
            return odf[mimetype]
        return "zip"
    return sniff_bytes(head)


COMPATIBLE = {  # extension -> accepted sniff results
    ".zip": {"zip", ".docx", ".pptx", ".xlsx", ".odt", ".ods", ".odp", ".epub"},
    ".jar": {"zip"}, ".war": {"zip"},
    ".tar": {".tar"}, ".tgz": {"gzip"}, ".tar.gz": {"gzip"}, ".tar.bz2": {"bzip2"}, ".tbz2": {"bzip2"},
    ".tar.xz": {"xz"}, ".txz": {"xz"},
    ".jpeg": {".jpg"}, ".jpg": {".jpg"}, ".tif": {".tiff"}, ".tiff": {".tiff"},
    ".doc": {"ole", ".docx", ".rtf"}, ".ppt": {"ole", ".pptx"}, ".xls": {"ole", ".xlsx"},
    ".rtf": {".rtf", "text"},
}


def check_type(path, suffix):
    actual = sniff(path)
    if suffix in TEXTUAL and suffix != ".rtf":
        if actual == "text":
            return actual
        if actual in ("binary", "zip", "zip-corrupt", ".pdf") or actual in IMAGES or actual in DOCUMENTS:
            raise ParseError(f"{path.name}: extension {suffix} does not match its content ({actual}); refusing to parse")
        return actual
    if actual == suffix or actual in COMPATIBLE.get(suffix, ()):
        return actual
    if actual == "zip-corrupt":
        raise ParseError(f"{path.name} is a corrupt Office/ZIP file")
    raise ParseError(f"{path.name}: extension {suffix} does not match its content ({actual}); refusing to parse")


def open_zip(path):
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ParseError(f"{path.name} is not a valid Office file: {exc}") from exc
    members = archive.infolist()
    total = sum(item.file_size for item in members)
    if len(members) > MAX_ZIP_MEMBERS or total > MAX_ZIP_UNCOMPRESSED:
        archive.close()
        raise ParseError(f"{path.name} expands to {total // 2**20} MB / {len(members)} parts; refusing (possible zip bomb)")
    for item in members:
        if item.file_size > 10 * 1024 * 1024 and item.compress_size and item.file_size / item.compress_size > MAX_MEMBER_RATIO:
            archive.close()
            raise ParseError(f"{path.name}: part {item.filename} has a suspicious compression ratio (zip bomb?)")
    return archive


def read_xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_PART:
        raise ParseError(f"XML part {name} too large")
    data = archive.read(name)
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data[:4096]:
        raise ParseError(f"XML part {name} declares a DTD/entities; refusing (XXE/billion-laughs defense)")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ParseError(f"Malformed XML in {name}: {exc}") from exc


def local(tag):
    return tag.rsplit("}", 1)[-1]
