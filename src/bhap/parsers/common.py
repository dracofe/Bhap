"""Hardening shared by all parsers: content sniffing, ZIP-bomb and XML-DTD defenses."""
import zipfile
from xml.etree import ElementTree as ET

from ..errors import ParseError

MAX_ZIP_UNCOMPRESSED = 512 * 1024 * 1024
MAX_ZIP_MEMBERS = 20000
MAX_MEMBER_RATIO = 250  # uncompressed / compressed for any sizeable member
MAX_XML_PART = 64 * 1024 * 1024

SUPPORTED = {".pdf", ".docx", ".pptx", ".md", ".markdown", ".txt"}
LEGACY = {".doc": ".docx", ".ppt": ".pptx"}


def block(text, *, page=None, slide=None, section="", kind="text", visual=False, ocr_conf=None,
          approx=False, line=None):
    return {"text": text, "page": page, "slide": slide, "section": section, "kind": kind,
            "visual": visual, "ocr_conf": ocr_conf, "approx": approx, "line": line}


def sniff(path):
    """Return the real format based on content, independent of the extension."""
    with open(path, "rb") as handle:
        head = handle.read(4096)
    if b"%PDF-" in head[:1024]:
        return ".pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return "zip-corrupt"
        if "word/document.xml" in names:
            return ".docx"
        if "ppt/presentation.xml" in names:
            return ".pptx"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "ole"  # legacy .doc/.ppt
    if b"\x00" in head and not head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "binary"
    return "text"


def check_type(path, suffix):
    actual = sniff(path)
    expected = ".txt" if suffix in {".md", ".markdown", ".txt"} else suffix
    if expected == ".txt" and actual == "text":
        return
    if actual == expected:
        return
    if actual == "ole":
        raise ParseError(f"{path.name} is a legacy Office file (.doc/.ppt); convert it to .docx/.pptx locally "
                         "first, e.g. `libreoffice --headless --convert-to docx`")
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
