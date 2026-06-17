#!/usr/bin/env python3
"""CursorLight 共享工具模块 — 进程树遍历、进程管理。

被 cc_state_bridge.py、startup.py、traffic_light_desktop.py 复用。
避免进程树工具代码在多个文件中重复。
"""
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path


# ---- 进程查询 ----------------------------------------------------------------

def get_process_name(pid: int) -> str:
    """获取进程名（小写），失败返回空字符串。"""
    try:
        hProc = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
        if not hProc:
            return ""
        name_buf = ctypes.create_unicode_buffer(260)
        size = ctypes.windll.psapi.GetModuleBaseNameW(hProc, None, name_buf, 260)
        ctypes.windll.kernel32.CloseHandle(hProc)
        return name_buf.value.lower() if size else ""
    except Exception:
        return ""


def get_parent_pid(pid: int | None = None) -> int | None:
    """Windows：通过 CreateToolhelp32Snapshot 获取父进程 PID。"""
    if pid is None:
        pid = os.getpid()

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = -1

    hSnapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if hSnapshot == INVALID_HANDLE_VALUE:
        return None

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(wintypes.ULONG)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    parent_pid = None
    try:
        if ctypes.windll.kernel32.Process32First(hSnapshot, ctypes.byref(pe)):
            while True:
                if pe.th32ProcessID == pid:
                    parent_pid = pe.th32ParentProcessID
                    break
                if not ctypes.windll.kernel32.Process32Next(hSnapshot, ctypes.byref(pe)):
                    break
    finally:
        ctypes.windll.kernel32.CloseHandle(hSnapshot)

    return parent_pid


def find_claude_pid() -> int | None:
    """向上遍历父进程链，找到 claude.exe 或 node.exe 的 PID。"""
    pid = os.getpid()
    for _ in range(12):
        pid = get_parent_pid(pid)
        if pid is None:
            break
        name = get_process_name(pid)
        if name in ("claude.exe", "claude", "node.exe", "node"):
            return pid
    return None


# ---- 进程管理 ----------------------------------------------------------------

def is_process_running_by_pid_file(pid_file: Path) -> bool:
    """检查 PID 文件中记录的进程是否存活。"""
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        if sys.platform == "win32":
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        return False
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return False


def kill_process_by_pid_file(pid_file: Path) -> bool:
    """通过 PID 文件终止进程并清理 PID 文件。返回是否成功。"""
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
            pid_file.unlink(missing_ok=True)
            return True
        else:
            # 进程不存在，清理残留 PID 文件
            pid_file.unlink(missing_ok=True)
            return False
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return False
