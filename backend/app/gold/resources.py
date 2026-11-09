"""Measure the actual Python worker, not the Windows virtualenv launcher."""

from __future__ import annotations

import sys


def peak_memory() -> dict[str, int | None]:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("faults", wintypes.DWORD),
                ("peak_working_set", ctypes.c_size_t),
                ("working_set", ctypes.c_size_t),
                ("peak_paged", ctypes.c_size_t),
                ("paged", ctypes.c_size_t),
                ("peak_nonpaged", ctypes.c_size_t),
                ("nonpaged", ctypes.c_size_t),
                ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t),
            ]

        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        if not read(process(), ctypes.byref(counters), counters.cb):
            return {"peak_working_set_bytes": None, "peak_private_commit_bytes": None}
        return {
            "peak_working_set_bytes": counters.peak_working_set,
            "peak_private_commit_bytes": counters.peak_pagefile,
        }
    import resource

    size = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "peak_working_set_bytes": size if sys.platform == "darwin" else size * 1024,
        "peak_private_commit_bytes": None,
    }
