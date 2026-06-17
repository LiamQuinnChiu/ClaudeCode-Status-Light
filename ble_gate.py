#!/usr/bin/env python3
"""原子判断是否需要向 BLE 发 mode，避免并行 Hook 重复扫描。"""
import json
import os
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "state_desktop.json"
LOCK_PATH = SCRIPT_DIR / "state_desktop.lock"

DEBOUNCE_MS = {
    "thinking": 5000,
    "busy": 8000,
    "alarm": 500,
    "success": 3000,
    "error": 3000,
    "green": 3000,
}


def acquire_lock(lock_path: Path) -> int | None:
    """跨平台文件锁。返回 fd，失败返回 None。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        os.close(fd)
        return None


def release_lock(fd: int) -> None:
    """释放跨平台文件锁。"""
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    os.close(fd)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(data: dict) -> None:
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False))


def main() -> None:
    if len(sys.argv) < 3:
        print("no")
        return

    action = sys.argv[1]
    mode = sys.argv[2].strip().lower()

    fd = acquire_lock(LOCK_PATH)
    if fd is None:
        print("no:locked")
        return

    try:
        data = load_state()
        now_ms = int(time.time() * 1000)
        last_mode = str(data.get("last_mode", ""))
        last_ts = int(data.get("last_ts", 0) or 0)
        phase = str(data.get("turn_phase", ""))

        send = True
        reason = ""

        if action == "turn-start":
            # 用户新 Prompt：开始新一轮，允许从 busy 回到 thinking
            data["turn_phase"] = "thinking"
            data.pop("awaiting_build", None)
            data.pop("build_started", None)
            data.pop("plan_touched", None)
            data["turn_started_ms"] = now_ms
            if mode == last_mode and (now_ms - last_ts) < DEBOUNCE_MS["thinking"]:
                send, reason = False, "turn-start debounce"

        elif action == "await-user":
            data["turn_phase"] = "awaiting_user"
            mode = "alarm"
            if last_mode == "alarm" and (now_ms - last_ts) < DEBOUNCE_MS["alarm"]:
                send, reason = False, "await-user debounce"

        elif action == "busy":
            data["turn_phase"] = "busy"
            if phase == "busy" and last_mode == "busy":
                send, reason = False, "sticky busy"
            elif mode == last_mode and (now_ms - last_ts) < DEBOUNCE_MS["busy"]:
                send, reason = False, "busy debounce"

        elif action == "thinking":
            if phase == "busy":
                send, reason = False, "thinking blocked by busy phase"
            elif mode == last_mode and (now_ms - last_ts) < DEBOUNCE_MS["thinking"]:
                send, reason = False, "thinking debounce"
            else:
                data["turn_phase"] = "thinking"

        elif action == "alarm":
            if mode == last_mode and (now_ms - last_ts) < DEBOUNCE_MS["alarm"]:
                send, reason = False, "alarm debounce"

        elif action in ("idle", "stop-success", "stop-error", "stop-alarm"):
            data["turn_phase"] = ""
            debounce_key = (
                "error"
                if "error" in action
                else "alarm"
                if "alarm" in action
                else "success"
                if "success" in action
                else "green"
            )
            if action == "stop-success":
                data.pop("awaiting_build", None)
            if mode == last_mode and (now_ms - last_ts) < DEBOUNCE_MS.get(
                debounce_key, 3000
            ):
                send, reason = False, f"{action} debounce"

        elif action == "plan-detect":
            print("no")
            return

        elif action == "denied-thinking":
            if phase == "busy":
                mode = "busy"
                if last_mode == "busy":
                    send, reason = False, "denied sticky busy"
            else:
                data["turn_phase"] = "thinking"
                mode = "thinking"

        elif action == "denied-error":
            if phase == "busy":
                send, reason = False, "denied-error during busy"
            else:
                data["turn_phase"] = ""
                if mode == last_mode and (now_ms - last_ts) < DEBOUNCE_MS["error"]:
                    send, reason = False, "denied-error debounce"

        if send:
            data["last_mode"] = mode
            data["last_ts"] = now_ms
            save_state(data)
            print(f"yes:{mode}")
        else:
            save_state(data)
            print(f"no:{reason}")
    finally:
        release_lock(fd)


if __name__ == "__main__":
    main()
