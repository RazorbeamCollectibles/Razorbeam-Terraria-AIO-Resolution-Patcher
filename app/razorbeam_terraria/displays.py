from __future__ import annotations
import ctypes
from ctypes import wintypes
from .models import Monitor

QDC_ONLY_ACTIVE_PATHS = 0x2
DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2

class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

class _RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]

class _PATH_SOURCE(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("statusFlags", wintypes.UINT)]

class _PATH_TARGET(ctypes.Structure):
    _fields_ = [("adapterId", _LUID), ("id", wintypes.UINT),
                ("modeInfoIdx", wintypes.UINT), ("outputTechnology", wintypes.UINT),
                ("rotation", wintypes.UINT), ("scaling", wintypes.UINT),
                ("refreshRate", _RATIONAL), ("scanLineOrdering", wintypes.UINT),
                ("targetAvailable", wintypes.BOOL), ("statusFlags", wintypes.UINT)]

class _PATH_INFO(ctypes.Structure):
    _fields_ = [("sourceInfo", _PATH_SOURCE), ("targetInfo", _PATH_TARGET),
                ("flags", wintypes.UINT)]

class _DEVICE_HEADER(ctypes.Structure):
    _fields_ = [("type", wintypes.UINT), ("size", wintypes.UINT),
                ("adapterId", _LUID), ("id", wintypes.UINT)]

class _SOURCE_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]

class _TARGET_NAME(ctypes.Structure):
    _fields_ = [("header", _DEVICE_HEADER), ("flags", wintypes.UINT),
                ("outputTechnology", wintypes.UINT), ("edidManufactureId", wintypes.USHORT),
                ("edidProductCodeId", wintypes.USHORT), ("connectorInstance", wintypes.UINT),
                ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
                ("monitorDevicePath", wintypes.WCHAR * 128)]

def _settings_number_order(records):
    """Return Windows Settings-style labels while retaining GDI names for control.

    CCD exposes connector type and connector instance separately. Windows Settings
    groups the active targets by adapter/connector topology; the GDI DISPLAY suffix
    is an unrelated source number and can disagree with Identify.
    """
    adapter_rank = {}
    for adapter, *_ in records:
        if adapter not in adapter_rank:
            adapter_rank[adapter] = len(adapter_rank)
    ordered = sorted(records, key=lambda r: (adapter_rank[r[0]], -r[1], r[2], r[3], r[4]))
    result = {}
    for number, record in enumerate(ordered, 1):
        result.setdefault(record[5].upper(), number)
    return result

def windows_display_numbers(user=None):
    user = user or ctypes.windll.user32
    paths = None
    for _attempt in range(3):
        path_count = wintypes.UINT(); mode_count = wintypes.UINT()
        if user.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), ctypes.byref(mode_count)):
            return {}
        # DISPLAYCONFIG_MODE_INFO is 64 bytes on supported Windows architectures.
        mode_buffer = (ctypes.c_byte * (max(1, mode_count.value) * 64))()
        paths = (_PATH_INFO * max(1, path_count.value))()
        result = user.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(path_count), paths,
                                         ctypes.byref(mode_count), mode_buffer, None)
        if result == 122: # ERROR_INSUFFICIENT_BUFFER: topology changed between the two calls.
            continue
        if result:return {}
        break
    else:return {}
    records = []
    for path_index, path in enumerate(paths[:path_count.value]):
        source = _SOURCE_NAME(); source.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
        source.header.size = ctypes.sizeof(source); source.header.adapterId = path.sourceInfo.adapterId
        source.header.id = path.sourceInfo.id
        target = _TARGET_NAME(); target.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
        target.header.size = ctypes.sizeof(target); target.header.adapterId = path.targetInfo.adapterId
        target.header.id = path.targetInfo.id
        if user.DisplayConfigGetDeviceInfo(ctypes.byref(source.header)) or user.DisplayConfigGetDeviceInfo(ctypes.byref(target.header)):
            return {}
        adapter = (path.targetInfo.adapterId.HighPart, path.targetInfo.adapterId.LowPart)
        records.append((adapter, int(target.outputTechnology), int(target.connectorInstance),
                        int(path.targetInfo.id), path_index, source.viewGdiDeviceName))
    return _settings_number_order(records)

def detect_monitors() -> list[Monitor]:
    # Use physical Win32 pixels, never QScreen's DPI-scaled logical geometry.
    user = ctypes.windll.user32
    user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous = user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]
    try: display_numbers = windows_display_numbers(user)
    except (OSError, ValueError, ctypes.ArgumentError): display_numbers = {}
    monitors = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
    @callback_type
    def callback(handle, hdc, rect, data):
        info = MONITORINFOEX(); info.cbSize = ctypes.sizeof(info)
        if user.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append(Monitor(info.szDevice, r.left, r.top, r.right-r.left, r.bottom-r.top,
                                    bool(info.dwFlags & 1), display_numbers.get(info.szDevice.upper())))
        return True
    try:
        if not user.EnumDisplayMonitors(None, None, callback, 0):
            raise ctypes.WinError()
    finally:
        if previous: user.SetThreadDpiAwarenessContext(previous)
    return sorted(monitors, key=lambda m: (m.y, m.x))
