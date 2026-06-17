#!/usr/bin/env python3
"""跨平台 state_desktop.json 操作辅助（Windows/macOS 自适应文件锁）。

Claude Code 版：状态文件为 state_desktop.json，plan 检测适配 .claude 路径。
被 cc_state_bridge.py 和手动调试使用。

用法:
  py -3 state_helper.py get <key>
  py -3 state_helper.py set <key> <value>
  py -3 state_helper.py set-json <key> <json>
  py -3 state_helper.py delete <key>
  py -3 state_helper.py has <key>
  py -3 state_helper.py has-recent-plan       # 检查近期 plan 文件
  py -3 state_helper.py reset-turn            # 重置 turn 状态
  py -3 state_helper.py set-phase <phase>     # 设置 turn_phase
"""
import json
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "state_desktop.json"


def acquire_lock():
    """跨平台阻塞文件锁。返回 fd。"""
    lock_path = SCRIPT_DIR / "state_desktop.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def release_lock(fd):
    """释放文件锁。"""
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


def load():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save(data):
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False))


def cmd_set(args):
    """set <key> <value>  — 写入字符串值"""
    key, value = args[0], args[1]
    fd = acquire_lock()
    try:
        data = load()
        data[key] = value
        save(data)
    finally:
        release_lock(fd)


def cmd_set_json(args):
    """set-json <key> <json_literal>  — 写入 JSON 值（true/false/数字等）"""
    key, raw = args[0], args[1]
    value = json.loads(raw)
    fd = acquire_lock()
    try:
        data = load()
        data[key] = value
        save(data)
    finally:
        release_lock(fd)


def cmd_delete(args):
    """delete <key>  — 删除 key"""
    key = args[0]
    fd = acquire_lock()
    try:
        data = load()
        data.pop(key, None)
        save(data)
    finally:
        release_lock(fd)


def cmd_get(args):
    """get <key>  — 打印值（不存在则空）"""
    key = args[0]
    data = load()
    val = data.get(key, "")
    if val is True:
        print("yes")
    elif val is False:
        print("no")
    elif val == "":
        print("")
    else:
        print(str(val))


def cmd_has(args):
    """has <key>  — 打印 yes 或 no"""
    key = args[0]
    data = load()
    val = data.get(key)
    if val:
        print("yes")
    else:
        print("no")


def cmd_has_recent_plan():
    """检查是否有最近（8 分钟内）的 plan 文件。

    扫描路径（按优先级）：
      1. ~/.claude/projects/*/plans/*.plan.md   — 全局 projects 目录
      2. $PWD/.claude/plans/*.plan.md           — 当前工作目录
      3. %USERPROFILE%/.claude/plans/*.plan.md  — Windows 用户目录
    """
    import time as _time

    try:
        data = load()
    except Exception:
        data = {}

    now_ms = int(_time.time() * 1000)
    turn_ms = int(data.get("turn_started_ms") or 0)
    window_ms = 8 * 60 * 1000  # 8 分钟

    # 搜索目录列表
    search_dirs = []

    # 1. Claude Code 全局 projects 目录
    global_projects = Path.home() / ".claude" / "projects"
    if global_projects.is_dir():
        for proj_dir in global_projects.iterdir():
            if proj_dir.is_dir():
                plans_dir = proj_dir / "plans"
                if plans_dir.is_dir():
                    search_dirs.append(plans_dir)

    # 2. 当前工作目录下的 .claude/plans
    try:
        cwd_plans = Path(os.getcwd()) / ".claude" / "plans"
        if cwd_plans.is_dir():
            search_dirs.append(cwd_plans)
    except OSError:
        pass

    # 3. 用户目录下的 .claude/plans（非 projects 子目录）
    user_plans = Path.home() / ".claude" / "plans"
    if user_plans.is_dir():
        search_dirs.append(user_plans)

    if not search_dirs:
        sys.exit(1)

    best_ms = 0
    best_name = ""
    for plans_dir in search_dirs:
        if not plans_dir.is_dir():
            continue
        for p in plans_dir.glob("*.plan.md"):
            try:
                mtime_ms = int(p.stat().st_mtime * 1000)
            except OSError:
                continue
            if now_ms - mtime_ms > window_ms:
                continue
            if turn_ms and mtime_ms < turn_ms - 3000:
                continue
            if mtime_ms > best_ms:
                best_ms = mtime_ms
                best_name = p.name

    if best_ms:
        print(str(Path(best_name).parent / best_name) if "/" in best_name else best_name)
        sys.exit(0)
    sys.exit(1)


def cmd_reset_turn():
    """turn-start 时重置一轮状态"""
    import time as _time
    fd = acquire_lock()
    try:
        data = load()
        data["turn_phase"] = "thinking"
        data.pop("awaiting_build", None)
        data.pop("build_started", None)
        data.pop("plan_touched", None)
        data.pop("pending_since", None)
        data["turn_started_ms"] = int(_time.time() * 1000)
        save(data)
    finally:
        release_lock(fd)


def cmd_set_phase(args):
    """set-phase <phase>"""
    phase = args[0]
    fd = acquire_lock()
    try:
        data = load()
        data["turn_phase"] = phase
        save(data)
    finally:
        release_lock(fd)


def main():
    if len(sys.argv) < 2:
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "set":
        cmd_set(args)
    elif cmd == "set-json":
        cmd_set_json(args)
    elif cmd == "delete":
        cmd_delete(args)
    elif cmd == "get":
        cmd_get(args)
    elif cmd == "has":
        cmd_has(args)
    elif cmd == "has-recent-plan":
        cmd_has_recent_plan()
    elif cmd == "reset-turn":
        cmd_reset_turn()
    elif cmd == "set-phase":
        cmd_set_phase(args)
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
