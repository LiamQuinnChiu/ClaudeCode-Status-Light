#!/usr/bin/env python3
"""Claude Code Startup Hook — 自启动守护进程 + 多 Agent 互锁。

首个 Agent 声明 primary 并控制灯光，后续 Agent 自动静默（不干扰）。
通过 claude.exe PID 区分不同 Agent 会话。
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from process_util import find_claude_pid, is_process_running_by_pid_file

SCRIPT_DIR = Path(__file__).resolve().parent
PRIMARY_FILE = SCRIPT_DIR / "primary_session.json"


def find_window(title_substring: str) -> bool:
    """检查是否有窗口标题包含指定字符串。（Windows 特有）"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, title_substring)
        return hwnd != 0
    except Exception:
        return False


def start_daemon() -> None:
    """启动 BLE 守护进程（如果未运行）。"""
    pid_file = SCRIPT_DIR / "ble_daemon.pid"
    if is_process_running_by_pid_file(pid_file):
        print("[CursorLight] BLE daemon already running")
        return

    print("[CursorLight] Starting BLE daemon...")
    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "ble_daemon.py"), "start"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def start_desktop_dots() -> None:
    """启动桌面任务栏圆点。先杀旧实例再启动，确保每轮对话干净重启。"""
    # 杀掉旧实例（如果有）
    pid_file = SCRIPT_DIR / "traffic_light_desktop.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            import ctypes as ct
            handle = ct.windll.kernel32.OpenProcess(0x0001, False, pid)
            if handle:
                ct.windll.kernel32.TerminateProcess(handle, 0)
                ct.windll.kernel32.CloseHandle(handle)
                print(f"[CursorLight] Stopped old desktop dots (PID={pid})")
        except (ValueError, OSError):
            pass
        pid_file.unlink(missing_ok=True)

    print("[CursorLight] Starting desktop dots...")
    subprocess.Popen(
        [sys.executable, str(SCRIPT_DIR / "traffic_light_desktop.py")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def main() -> None:
    print("[CursorLight] Auto-starting...")

    session_id = uuid.uuid4().hex
    claude_pid = find_claude_pid()

    # 写入 session 标记文件（用 claude.exe PID 区分不同 Agent）
    if claude_pid is not None:
        session_file = SCRIPT_DIR / f"session_{claude_pid}.json"
        session_file.write_text(
            json.dumps({"session_id": session_id, "claude_pid": claude_pid}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[CursorLight] Session: {session_id[:8]}... (claude PID={claude_pid})")
    else:
        # 降级：用自身 PID
        session_file = SCRIPT_DIR / f"session_{os.getpid()}.json"
        session_file.write_text(
            json.dumps({"session_id": session_id, "pid": os.getpid()}, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[CursorLight] Session (fallback): {session_id[:8]}...")

    # 判断是否为首个 Agent（primary）
    daemon_pid_file = SCRIPT_DIR / "ble_daemon.pid"
    daemon_running = is_process_running_by_pid_file(daemon_pid_file)

    if not daemon_running:
        PRIMARY_FILE.write_text(
            json.dumps({"session_id": session_id, "claude_pid": claude_pid}, ensure_ascii=False),
            encoding="utf-8",
        )
        print("[CursorLight] Primary agent claimed")
        start_daemon()
    else:
        print("[CursorLight] BLE daemon already running, reusing")

    # 桌面圆点独立于 BLE daemon，每次 SessionStart 都确保运行
    start_desktop_dots()

    # 重置状态灯为绿色，避免上一个会话残留的 alarm/error 在新会话一开始就亮红灯
    state_path = SCRIPT_DIR / "state_desktop.json"
    import time
    state_path.write_text(
        json.dumps({
            "last_mode": "green",
            "turn_phase": "",
            "last_ts": int(time.time() * 1000),
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    print("[CursorLight] Ready.")


if __name__ == "__main__":
    main()
