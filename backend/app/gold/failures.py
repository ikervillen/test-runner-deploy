"""Exit codes that tell a scheduler whether retrying a failed command can help.

Values follow sysexits.h. Usage and data errors are permanent; the rest may clear.
"""

from __future__ import annotations

from portalocker.exceptions import LockException

from .common import IntegrityError

EXIT_USAGE = 2
EXIT_DATA = 65
EXIT_SOFTWARE = 70
EXIT_IO = 74
EXIT_TEMPORARY = 75
PERMANENT = frozenset({EXIT_USAGE, EXIT_DATA})


def exit_code(error: BaseException) -> int:
    if isinstance(error, IntegrityError):
        return EXIT_DATA
    if isinstance(error, LockException):
        return EXIT_TEMPORARY
    if isinstance(error, OSError):
        return EXIT_IO
    return EXIT_SOFTWARE
