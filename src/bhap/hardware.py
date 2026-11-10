"""Local hardware inspection used for warnings and model recommendations."""
import ctypes
import os
import platform
import shutil
import subprocess
import sys

GIB = 1024 ** 3

# Suggested (not guaranteed) Ollama model names per profile. Users must benchmark.
RECOMMENDATIONS = {
    "lite": {"llm": ["qwen2.5:1.5b", "gemma3:1b", "llama3.2:1b"],
             "embedding": ["paraphrase-multilingual", "granite-embedding:278m"]},
    "standard": {"llm": ["qwen2.5:7b", "gemma3:4b", "llama3.1:8b"],
                 "embedding": ["bge-m3", "paraphrase-multilingual"]},
    "power": {"llm": ["qwen2.5:14b", "gemma3:12b", "qwen2.5:7b"],
              "embedding": ["bge-m3"]},
}


def memory():
    """Return (total_bytes, available_bytes) or (None, None) if unknown."""
    try:
        if sys.platform.startswith("linux"):
            info = {}
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    key, value = line.split(":", 1)
                    info[key] = int(value.split()[0]) * 1024
            return info.get("MemTotal"), info.get("MemAvailable", info.get("MemFree"))
        if os.name == "nt":
            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                            ("tpage", ctypes.c_ulonglong), ("apage", ctypes.c_ulonglong),
                            ("tvirt", ctypes.c_ulonglong), ("avirt", ctypes.c_ulonglong),
                            ("aext", ctypes.c_ulonglong)]
            status = Status()
            status.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.total, status.avail
        if sys.platform == "darwin":
            total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                       text=True, timeout=5).stdout.strip())
            return total, None
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        return pages * size, None
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return None, None


def gpu():
    """Best-effort local GPU description; never required."""
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            if out:
                name, mem = [part.strip() for part in out.splitlines()[0].split(",")]
                return {"name": name, "vram_bytes": int(float(mem)) * 1024 ** 2}
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return {"name": "Apple Silicon (unified memory)", "vram_bytes": None}
    return None


def disk_free(directory):
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(directory).free
    except OSError:
        return None


def profile(total_bytes):
    if not total_bytes:
        return "standard"
    if total_bytes < 12 * GIB:
        return "lite"
    if total_bytes < 28 * GIB:
        return "standard"
    return "power"


def summary(data_dir):
    total, available = memory()
    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "ram_total_gb": round(total / GIB, 1) if total else None,
        "ram_available_gb": round(available / GIB, 1) if available else None,
        "gpu": gpu(),
        "disk_free_gb": round(disk_free(data_dir) / GIB, 1) if disk_free(data_dir) else None,
        "profile": profile(total),
    }
