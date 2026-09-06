from __future__ import annotations
import ctypes
from ctypes import wintypes as W
from pathlib import Path
import os

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CloseHandle.argtypes = [W.HANDLE]
kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel.OpenProcess.restype = W.HANDLE
user = ctypes.WinDLL("user32", use_last_error=True)
user.FindWindowW.argtypes=[W.LPCWSTR,W.LPCWSTR];user.FindWindowW.restype=W.HWND
user.ShowWindow.argtypes=[W.HWND,ctypes.c_int];user.SetForegroundWindow.argtypes=[W.HWND]

class SingleInstance:
    def __init__(self, name="Local\\RazorbeamTerrariaPatcher.SingleInstance"):
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
        kernel.CreateMutexW.restype = W.HANDLE
        self.handle = kernel.CreateMutexW(None, False, name)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183

    def close(self):
        if self.handle:
            kernel.CloseHandle(self.handle); self.handle = None

def activate_existing(title):
    hwnd = user.FindWindowW(None, title)
    if hwnd:
        user.ShowWindow(hwnd, 9)  # SW_RESTORE
        user.SetForegroundWindow(hwnd)
        return True
    return False

class OwnedJob:
    """Destroy only child processes assigned to this owned Windows job."""
    def __init__(self):
        class BASIC(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                        ("Flags", W.DWORD), ("MinWS", ctypes.c_size_t), ("MaxWS", ctypes.c_size_t),
                        ("Active", W.DWORD), ("Affinity", ctypes.c_size_t), ("Priority", W.DWORD), ("Scheduling", W.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemory", ctypes.c_size_t),
                        ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        kernel.CreateJobObjectW.restype = W.HANDLE
        kernel.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        info = EXTENDED(); info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close(); raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, pid):
        handle = kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
        if not handle: raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not kernel.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally: kernel.CloseHandle(handle)

    def close(self):
        if self.handle:
            kernel.CloseHandle(self.handle); self.handle = None

def assert_game_closed(exe: Path, label="Terraria"):
    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("pid", W.DWORD),
                    ("heap", ctypes.c_size_t), ("module", W.DWORD), ("threads", W.DWORD),
                    ("parent", W.DWORD), ("priority", W.LONG), ("flags", W.DWORD), ("name", W.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel.Process32FirstW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    kernel.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
    item = PROCESSENTRY32(); item.dwSize = ctypes.sizeof(item)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(item))
        while ok:
            if item.name.casefold() == exe.name.casefold():
                process = kernel.OpenProcess(0x1000, False, item.pid)
                same = True  # Fail closed when this Terraria process cannot be inspected.
                if process:
                    try:
                        buf = ctypes.create_unicode_buffer(32768); size = W.DWORD(len(buf))
                        if kernel.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
                            same = os.path.normcase(os.path.realpath(buf.value)) == os.path.normcase(str(exe.resolve()))
                    finally: kernel.CloseHandle(process)
                if same: raise RuntimeError(f"Close {label} before patching or restoring. No game processes were stopped.")
            ok = kernel.Process32NextW(snapshot, ctypes.byref(item))
    finally: kernel.CloseHandle(snapshot)

class GameLock:
    """Cross-instance lock, automatically removed even if the patch worker crashes."""
    def __init__(self, exe): self.path = str(exe.parent / ".razorbeam-patch.lock"); self.handle = None
    def __enter__(self):
        kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        kernel.CreateFileW.restype = W.HANDLE
        self.handle = kernel.CreateFileW(self.path, 0x40000000 | 0x10000, 0, None, 4, 0x04000000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            code = ctypes.get_last_error()
            if code in (5, 1314): raise PermissionError("Game folder needs administrator access. Use Restart as administrator, then patch again.")
            raise RuntimeError("Another patch operation holds this game folder, or the folder is not writable.")
        return self
    def __exit__(self, *args):
        kernel.CloseHandle(self.handle)
