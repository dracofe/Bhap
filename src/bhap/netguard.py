"""Process-wide egress guard.

Bhap only ever needs loopback (the local model runtime). Installing this guard makes
any attempt by Bhap or one of its libraries to resolve or connect to a non-loopback
address fail loudly instead of silently reaching the internet.
"""
import ipaddress
import socket

from .errors import NetworkBlockedError

_installed = False
_blocked_attempts = []
_original = {}

LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _is_loopback(host):
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    host = str(host).strip("[]").split("%", 1)[0]
    if host.lower() in LOCAL_NAMES or host == "":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _check(address, family=None):
    if family == getattr(socket, "AF_UNIX", object()):
        return
    if isinstance(address, tuple) and address:
        host = address[0]
    elif isinstance(address, (str, bytes)):
        return  # unix socket path
    else:
        host = None
    if not _is_loopback(host):
        _blocked_attempts.append(str(host))
        raise NetworkBlockedError(
            f"Network access to {host} blocked: Bhap is offline-only (loopback is allowed)")


def install():
    global _installed
    if _installed:
        return
    _original["connect"] = socket.socket.connect
    _original["connect_ex"] = socket.socket.connect_ex
    _original["getaddrinfo"] = socket.getaddrinfo

    def connect(self, address):
        _check(address, self.family)
        return _original["connect"](self, address)

    def connect_ex(self, address):
        _check(address, self.family)
        return _original["connect_ex"](self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            _blocked_attempts.append(str(host))
            raise NetworkBlockedError(f"DNS lookup for {host} blocked: Bhap is offline-only")
        return _original["getaddrinfo"](host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    _installed = True


def uninstall():
    global _installed
    if not _installed:
        return
    socket.socket.connect = _original["connect"]
    socket.socket.connect_ex = _original["connect_ex"]
    socket.getaddrinfo = _original["getaddrinfo"]
    _installed = False


def active():
    return _installed


def blocked_attempts():
    return list(_blocked_attempts)


def self_test():
    """Verify the guard refuses a public address without sending any packet."""
    if not _installed:
        return False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
    except NetworkBlockedError:
        return True
    except OSError:
        return False
    finally:
        probe.close()
        if _blocked_attempts and _blocked_attempts[-1] == "192.0.2.1":
            _blocked_attempts.pop()
    return False
