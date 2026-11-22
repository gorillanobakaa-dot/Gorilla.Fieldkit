"""Windows performance counters read in-process through pdh.dll: no PowerShell, no child, no console host.

2026-11-22: `thermal watch --forever` read two counters with `powershell Get-Counter` every ~6 s - 41 console
hosts in a 120 s trace, ~1,200 an hour, each a powershell.exe + conhost.exe start (~60 MB, ~0.5 s of CPU). The
same numbers come from the Performance Data Helper API that Get-Counter itself calls. `values()` returns None on any
failure (no counter, no pdh.dll, not Windows); the caller reports a missing reading (no PowerShell fallback).
"""
import ctypes
import sys
import time

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_CSTATUS_VALID_DATA = 0x0
PDH_CSTATUS_NEW_DATA = 0x1


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("doubleValue", ctypes.c_double)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


def _pdh():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("pdh.dll")
    except OSError:
        return None


def values(path, rate=False, interval=1.0, sleep=time.sleep, pdh=None):
    """-> [float] for every instance the (English) counter path matches, or None. rate=True takes two samples
    `interval` apart (rate counters such as '% Processor Performance' need two, as Get-Counter does)."""
    pdh = pdh or _pdh()
    if pdh is None:
        return None
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        return None
    try:
        if pdh.PdhAddEnglishCounterW(query, ctypes.c_wchar_p(path), None, ctypes.byref(counter)) != 0:
            return None
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        if rate:
            sleep(interval)
            if pdh.PdhCollectQueryData(query) != 0:
                return None
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        st = pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), None)
        if (st & 0xFFFFFFFF) != PDH_MORE_DATA or not size.value:
            return None
        buf = (ctypes.c_byte * size.value)()
        if pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
        out = [items[i].FmtValue.doubleValue for i in range(count.value)
               if items[i].FmtValue.CStatus in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA)]
        return out or None
    except (OSError, AttributeError, ValueError):
        return None
    finally:
        pdh.PdhCloseQuery(query)
