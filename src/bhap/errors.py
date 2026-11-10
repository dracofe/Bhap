"""Exception hierarchy with stable process exit codes.

0 success · 1 generic failure · 2 invalid usage · 3 model/runtime unavailable ·
4 document parsing failure · 5 storage/database problem · 6 network blocked ·
130 interrupted by the user.
"""

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2
EXIT_MODEL = 3
EXIT_PARSE = 4
EXIT_STORAGE = 5
EXIT_NETWORK = 6
EXIT_INTERRUPTED = 130


class BhapError(Exception):
    exit_code = EXIT_FAILURE

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


class UsageError(BhapError):
    exit_code = EXIT_USAGE


class NotFoundError(UsageError):
    pass


class ModelError(BhapError):
    exit_code = EXIT_MODEL


class ParseError(BhapError):
    exit_code = EXIT_PARSE


class StorageError(BhapError):
    exit_code = EXIT_STORAGE


class NetworkBlockedError(PermissionError):
    """Raised by the network guard; subclasses OSError so HTTP clients surface it."""

    exit_code = EXIT_NETWORK
