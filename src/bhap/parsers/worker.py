"""Parse worker: reads {"path", "settings"} JSON on stdin, writes the parse result on stdout."""
import json
import sys


def main():
    from .. import netguard
    netguard.install()  # documents never get network access, even via a parser bug
    from ..errors import ParseError, UsageError
    from . import parse_direct
    request = json.loads(sys.stdin.read())
    try:
        result = parse_direct(request["path"], request.get("settings") or {})
    except UsageError as exc:
        result = {"error": str(exc), "kind": "usage"}
    except ParseError as exc:
        result = {"error": str(exc), "kind": "parse"}
    except MemoryError:
        result = {"error": "parser ran out of memory (limit parse_memory_mb)", "kind": "parse"}
    except Exception as exc:  # noqa: BLE001 - report, never crash silently
        result = {"error": f"{type(exc).__name__}: {exc}", "kind": "parse"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
