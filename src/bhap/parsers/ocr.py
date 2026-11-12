"""Optional local OCR via the Tesseract and Poppler (pdftoppm) command-line tools.

Nothing is installed or downloaded by Bhap; if the binaries are missing, OCR is reported
as unavailable and scanned pages get an explicit warning instead.
"""
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


def available():
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


@lru_cache(maxsize=1)
def installed_languages():
    if not shutil.which("tesseract"):
        return ()
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(line.strip() for line in out.splitlines()[1:] if line.strip())


def usable_languages(requested):
    have = set(installed_languages())
    chosen = [code for code in requested.split("+") if code in have]
    return "+".join(chosen) if chosen else ("eng" if "eng" in have else "")


def ocr_pdf_page(pdf_path, page_no, languages="ind+eng", dpi=200, timeout=120):
    """Return (text, mean_confidence 0..100) for one 1-based page, or ('', None)."""
    langs = usable_languages(languages)
    if not available() or not langs:
        return "", None
    with tempfile.TemporaryDirectory(prefix="bhap-ocr-") as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(["pdftoppm", "-f", str(page_no), "-l", str(page_no), "-r", str(dpi), "-gray", "-png",
                        str(pdf_path), str(prefix)], capture_output=True, timeout=timeout, check=False)
        images = sorted(Path(tmp).glob("page*.png"))
        if not images:
            return "", None
        result = subprocess.run(["tesseract", str(images[0]), "stdout", "-l", langs, "tsv"],
                                capture_output=True, text=True, timeout=timeout, check=False)
    return parse_tsv(result.stdout)


def parse_tsv(tsv):
    lines, confidences = {}, []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        word = cols[11].strip()
        if conf < 0 or not word:
            continue
        key = (int(cols[2]), int(cols[3]), int(cols[4]))  # block, paragraph, line
        lines.setdefault(key, []).append(word)
        confidences.append(conf)
    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    mean = round(sum(confidences) / len(confidences), 1) if confidences else None
    return text, mean


def available_image():
    return bool(shutil.which("tesseract"))


def ocr_image(path, languages="ind+eng", timeout=180):
    """Return (text, mean_confidence 0..100) for an image file."""
    langs = usable_languages(languages)
    if not langs:
        return "", None
    result = subprocess.run(["tesseract", str(path), "stdout", "-l", langs, "tsv"],
                            capture_output=True, text=True, timeout=timeout, check=False)
    return parse_tsv(result.stdout)
