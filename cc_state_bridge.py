#!/usr/bin/env python3
"""Claude Code Hook → state_desktop.json 桥接。

由 settings.json hooks 调用，将 Claude Code 事件转换为状态灯模式。
与 ble_daemon.py（BLE 实体灯）+ traffic_light_desktop.py（桌面圆点）配合。

用法（由 hooks 自动调用）:
  py -3 cc_state_bridge.py thinking      # UserPromptSubmit
  py -3 cc_state_bridge.py pre_tool       # PreToolUse（从 stdin 读 JSON）
  py -3 cc_state_bridge.py post_tool      # PostToolUse（从 stdin 读 JSON）
  py -3 cc_state_bridge.py stop           # Stop（从 stdin 读 JSON）
  py -3 cc_state_bridge.py idle           # SessionEnd
"""
import json
import os
import sys
import time
from pathlib import Path

from process_util import (
    find_claude_pid,
    kill_process_by_pid_file,
)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "state_desktop.json"
LOCK_PATH = SCRIPT_DIR / "state_desktop.lock"
PRIMARY_FILE = SCRIPT_DIR / "primary_session.json"
DESKTOP_PID_FILE = SCRIPT_DIR / "traffic_light_desktop.pid"

ACTION = sys.argv[1] if len(sys.argv) > 1 else "thinking"

# 灯效策略：
#   ALARM_PRE_TOOLS：PreToolUse 直接 alarm（总是阻塞等用户）
#   PENDING_TOOLS：PreToolUse —
#     perm_mode="allow"（预授权/auto 模式）→ busy，不 escalation
#     perm_mode="ask"  （手动模式）→ 直接 alarm
#     无 perm_mode      （旧版兼容）→ 默认 alarm
#   ALARM_POST_TOOLS：PostToolUse 直接 alarm（计划等待审核）
ALARM_PRE_TOOLS = {"AskUserQuestion"}
PENDING_TOOLS = {"Bash", "Write", "Edit", "WebFetch", "WebSearch", "NotebookEdit", "Agent", "Read", "Glob", "Grep", "Task"}
ALARM_POST_TOOLS = {"CreatePlan", "EnterPlanMode"}
PLAN_CLEAR_TOOLS = {"ExitPlanMode"}  # 计划批准后清除 plan_touched，避免 Stop 误报 alarm
PENDING_ESCALATE_MS = 15000  # 15 秒后 pending 升级为 alarm


# ---- 工具分类辅助 ----------------------------------------------------------------


def is_exec_tool(tool_name: str) -> bool:
    """判断工具是否为「执行类」（会真正改动系统/运行命令），而非纯文件读写。"""
    return tool_name in {
        "Bash", "Shell", "Delete", "ApplyPatch", "EditNotebook",
        "NotebookEdit", "Task", "Agent",
    }


def looks_like_plan_awaiting(text: str) -> bool:
    """检测 Agent 回复文本中是否包含「等待 Build 计划」的模式。
    移植自 agent-light.sh looks_like_plan_awaiting() — 12组正则。"""
    import re
    if not text or not text.strip():
        return False

    # 排除：已完成/总结类文本（不是等 Build）
    if re.search(
        r"已全部|全部完成|实施完成|落地完成|搞定了吗|"
        r"completed all|todos.*completed|Do not create them again",
        text, re.I,
    ):
        return False

    # 检测 plan 产物引用（适配 CC 和 Cursor 两种路径）
    has_artifact = bool(re.search(
        r"[\w./-]+\.plan\.md|\.claude/plans/|\.cursor/plans/", text, re.I,
    ))

    # 检测 plan 模板用语
    has_plan_boilerplate = bool(re.search(
        r"Do NOT edit the plan file|attached for your reference|"
        r"Do NOT edit the plan|计划文件本身|"
        r"Plan mode|plan mode",
        text, re.I,
    ))

    # 检测「等待用户确认执行」CTA
    has_wait_cta = bool(re.search(
        r"回复[\「\s\*]*执行计划|若认可.*(?:方案|计划).{0,40}(?:回复|执行)|"
        r"说[\「\s]*执行计划[\」\s]*即可|方可开始.*(?:实施|落地)|"
        r"unambiguously.*execute the plan|"
        r"to execute the plan|execute the plan",
        text, re.I,
    ))

    # 检测「按计划实施」附件
    has_impl_attach = bool(
        re.search(r"Implement the plan as specified", text, re.I)
        and has_plan_boilerplate
    )

    if has_impl_attach and (has_artifact or has_plan_boilerplate):
        return True
    if (has_artifact or has_plan_boilerplate) and has_wait_cta:
        return True
    if has_artifact and re.search(r"执行计划", text) and re.search(
        r"回复|若认可|点\s*Build|Build\s*即可", text, re.I,
    ):
        return True

    # Plan mode / Review Plan / CreatePlan / EnterPlanMode
    if re.search(r"Review\s*Plan|CreatePlan|EnterPlanMode", text, re.I):
        return True

    # Build CTA
    if re.search(
        r"点\s*Build|点击\s*Build|Build\s*即可|等你.*Build|点击.*执行",
        text, re.I,
    ):
        return True

    # 中文行程攻略计划
    if re.search(r"(?:一日游|行程|路线|攻略).{0,80}(?:计划|方案)", text, re.I):
        return True

    return False


def has_recent_plan(turn_started_ms: int = 0) -> str | None:
    """检查最近 8 分钟内的 plan 文件。返回路径字符串，无返回 None。
    扫描 Claude Code projects 目录 + Cursor plans 目录（兼容）。"""
    import time as _time
    now_ms = int(_time.time() * 1000)
    window_ms = 8 * 60 * 1000

    search_dirs = []

    # Claude Code 全局 projects 目录
    global_projects = Path.home() / ".claude" / "projects"
    if global_projects.is_dir():
        for proj_dir in global_projects.iterdir():
            if proj_dir.is_dir():
                plans_dir = proj_dir / "plans"
                if plans_dir.is_dir():
                    search_dirs.append(plans_dir)

    # Cursor plans（兼容旧系统）
    cursor_plans = Path.home() / ".cursor" / "plans"
    if cursor_plans.is_dir():
        search_dirs.append(cursor_plans)

    # 当前工作目录下的 .claude/plans
    try:
        cwd_plans = Path(os.getcwd()) / ".claude" / "plans"
        if cwd_plans.is_dir():
            search_dirs.append(cwd_plans)
    except OSError:
        pass

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
            if turn_started_ms and mtime_ms < turn_started_ms - 3000:
                continue
            if mtime_ms > best_ms:
                best_ms = mtime_ms
                best_name = str(p)

    return best_name if best_ms else None


def find_session_id() -> str | None:
    """通过 claude.exe PID 查找当前 Agent 的 session_id。"""
    claude_pid = find_claude_pid()
    if claude_pid is None:
        return None
    session_file = SCRIPT_DIR / f"session_{claude_pid}.json"
    if session_file.exists():
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return data.get("session_id")
        except (json.JSONDecodeError, OSError):
            pass
    return None


def is_primary_agent() -> bool:
    """检查当前进程是否属于 primary Agent。无 primary 文件时默认允许（向后兼容）。"""
    if not PRIMARY_FILE.exists():
        return True
    try:
        primary_data = json.loads(PRIMARY_FILE.read_text(encoding="utf-8"))
        primary_id = primary_data.get("session_id", "")
    except (json.JSONDecodeError, OSError):
        return True

    if not primary_id:
        return True

    my_session = find_session_id()
    if my_session is None:
        # 找不到 claude.exe（非标准环境）→ 允许写入
        return True

    return my_session == primary_id


def kill_desktop_dots() -> bool:
    """终止桌面任务栏圆点进程。返回是否成功。"""
    return kill_process_by_pid_file(DESKTOP_PID_FILE)


def cleanup_session_files() -> None:
    """清理当前 Agent 的 session 标记文件（SessionEnd 时调用）。"""
    claude_pid = find_claude_pid()
    if claude_pid is None:
        return
    my_session_file = SCRIPT_DIR / f"session_{claude_pid}.json"
    my_session_id = None
    if my_session_file.exists():
        try:
            data = json.loads(my_session_file.read_text(encoding="utf-8"))
            my_session_id = data.get("session_id")
        except (json.JSONDecodeError, OSError):
            pass
        my_session_file.unlink(missing_ok=True)

    # 清理 primary（如果是我们）
    if my_session_id and PRIMARY_FILE.exists():
        try:
            primary_data = json.loads(PRIMARY_FILE.read_text(encoding="utf-8"))
            if primary_data.get("session_id") == my_session_id:
                PRIMARY_FILE.unlink(missing_ok=True)
        except (json.JSONDecodeError, OSError):
            pass


def acquire_lock() -> int | None:
    """跨平台非阻塞文件锁。写入 1 字节确保 msvcrt.locking 有区域可锁。"""
    lock_path = LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        # 确保文件至少有 1 字节，否则 msvcrt.locking 在空文件上可能失效
        os.write(fd, b'\x00')
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


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}


def update_state(**delta) -> dict:
    """增量更新状态：合并 delta 到现有状态，返回新状态。"""
    state = read_state()
    state.update(delta)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))
    return state


def read_stdin_json() -> dict:
    """从 stdin 读取 Hook 传入的 JSON，失败返回 {}。"""
    try:
        # DEBUG: 检查 stdin 是否有数据
        debug_log = SCRIPT_DIR / "debug.log"

        # 检查 stdin 是否可读
        if sys.stdin.isatty():
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stdin is TTY (no data)\n")
            return {}

        # 尝试读取
        raw = sys.stdin.read()
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stdin raw ({len(raw)} bytes): {raw[:300]!r}\n")

        if not raw.strip():
            return {}

        # 尝试解析 JSON，如果失败则使用正则提取关键字段
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 使用正则提取关键字段
            import re
            result = {}
            # 提取 tool_name
            m = re.search(r'"tool_name"\s*:\s*"([^"]*)"', raw)
            if m:
                result["tool_name"] = m.group(1)
            # 提取 permission_mode
            m = re.search(r'"permission_mode"\s*:\s*"([^"]*)"', raw)
            if m:
                result["permission_mode"] = m.group(1)
            # 提取 hook_event_name
            m = re.search(r'"hook_event_name"\s*:\s*"([^"]*)"', raw)
            if m:
                result["hook_event_name"] = m.group(1)
            # 提取 status（Stop 事件专用）
            m = re.search(r'"status"\s*:\s*"([^"]*)"', raw)
            if m:
                result["status"] = m.group(1)
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] regex parsed: {result}\n")
            return result
    except (json.JSONDecodeError, OSError) as e:
        with open(SCRIPT_DIR / "debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stdin error: {e}\n")
        return {}


def is_plan_file_path(path: str) -> bool:
    """检查路径是否为 .plan.md 文件（兼容 CC 和 Cursor 路径）。"""
    if not path:
        return False
    normalized = path.replace("\\", "/").lower()
    return (
        normalized.endswith(".plan.md")
        or "/.cursor/plans/" in normalized
        or "/.claude/plans/" in normalized
        or "/.claude/projects/" in normalized  # CC 全局 projects 目录
    )


now_ms = int(time.time() * 1000)

# 获取锁，失败重试（PostToolUse 必须成功清除 pending，否则 alarm 误报）
fd = None
for attempt in range(5):
    fd = acquire_lock()
    if fd is not None:
        break
    if attempt < 4:
        time.sleep(0.05)  # 等 50ms 再试

if fd is None:
    sys.exit(0)

try:
    # ---- 多 Agent 互锁：非 primary Agent 静默退出 ----
    if not is_primary_agent():
        # 另一个 Agent 已声明 primary，我们不写状态
        sys.exit(0)

    if ACTION == "thinking":
        # UserPromptSubmit → 新一轮开始，重置 turn 状态
        update_state(
            last_mode="thinking",
            turn_phase="thinking",
            last_ts=now_ms,
        )
        # 清除上一轮的标记
        state = read_state()
        state.pop("awaiting_build", None)
        state.pop("build_started", None)
        state.pop("plan_touched", None)
        state["turn_started_ms"] = now_ms
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

    elif ACTION == "pre_tool":
        hook_input = read_stdin_json()
        tool_name = hook_input.get("tool_name", "")
        perm_mode = hook_input.get("permission_mode", "")  # "auto" | "ask" | ""
        hook_event = hook_input.get("hook_event_name", "")  # "PreToolUse" | "PostToolUse"
        tool_input = hook_input.get("tool_input", {})
        file_path = tool_input.get("path") or tool_input.get("file_path") or tool_input.get("filePath") or ""

        # DEBUG: 写入日志
        debug_log = SCRIPT_DIR / "debug.log"
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] pre_tool: tool={tool_name}, perm_mode={perm_mode!r}, hook_event={hook_event}, stdin_keys={list(hook_input.keys())}\n")

        # 如果实际是 PostToolUse 事件，执行 post_tool 逻辑
        with open(debug_log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] checking hook_event={hook_event!r}\n")
        if hook_event == "PostToolUse":
            with open(debug_log, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] executing post_tool logic for tool={tool_name}\n")

            if tool_name in ALARM_PRE_TOOLS:
                # AskUserQuestion 用户已回答 → 清除 alarm → busy
                state = read_state()
                if state.get("turn_phase") == "awaiting_user":
                    update_state(
                        last_mode="busy",
                        turn_phase="busy",
                        last_ts=now_ms,
                    )
                    state = read_state()
                    state.pop("awaiting_build", None)
                    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

            elif tool_name in PENDING_TOOLS:
                # Bash/Write 等执行完毕 → 清除 pending → busy（无论是否升级过）
                state = read_state()
                phase = state.get("turn_phase", "")
                if phase in ("awaiting_user", "pending_auth"):
                    update_state(
                        last_mode="busy",
                        turn_phase="busy",
                        last_ts=now_ms,
                    )
                    state = read_state()
                    state.pop("awaiting_build", None)
                    state.pop("pending_since", None)
                    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

            elif tool_name in PLAN_CLEAR_TOOLS:
                # ExitPlanMode → 计划已批准，清除 alarm → busy
                state = read_state()
                if state.get("turn_phase") == "awaiting_user":
                    update_state(
                        last_mode="busy",
                        turn_phase="busy",
                        last_ts=now_ms,
                    )
                state = read_state()
                state.pop("awaiting_build", None)
                state.pop("plan_touched", None)
                state.pop("pending_since", None)
                STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

            elif tool_name in ALARM_POST_TOOLS:
                # CreatePlan / EnterPlanMode → 计划等用户审核 → 直接 alarm
                update_state(
                    last_mode="alarm",
                    turn_phase="awaiting_user",
                    awaiting_build=True,
                    plan_touched=True,
                    last_ts=now_ms,
                )

            # PostToolUse 处理完毕，退出
            sys.exit(0)

        # 以下是 PreToolUse 处理逻辑
        current = read_state()
        phase = current.get("turn_phase", "")
        awaiting = current.get("awaiting_build", False)

        # 强制绿灯窗口：Stop 后 2 秒内不覆盖绿灯（ESC 视觉反馈）
        force_green_until = current.get("force_green_until", 0)
        if force_green_until > 0 and now_ms < force_green_until:
            # 绿灯保护期内，不更新状态，但更新 last_ts 防止 alarm 超时误触发
            update_state(last_ts=now_ms)
            sys.exit(0)

        if tool_name in ALARM_PRE_TOOLS:
            # AskUserQuestion — 总是阻塞等用户回答 → 直接 alarm
            update_state(
                last_mode="alarm",
                turn_phase="awaiting_user",
                awaiting_build=True,
                last_ts=now_ms,
            )

        elif tool_name in PENDING_TOOLS:
            perm_mode = hook_input.get("permission_mode", "")
            if perm_mode == "auto":
                # 预授权 auto 模式 → busy，不 alarm
                update_state(
                    last_mode="busy",
                    turn_phase="busy",
                    last_ts=now_ms,
                )
            else:
                # default（手动）/ 旧版兼容 → alarm
                update_state(
                    last_mode="alarm",
                    turn_phase="awaiting_user",
                    awaiting_build=True,
                    pending_since=now_ms,
                    last_ts=now_ms,
                )

        elif phase == "awaiting_user":
            # alarm 状态中（等待授权/用户回答），不被任何后续工具覆盖
            pass

        elif phase == "pending_auth":
            # pending 状态中（旧版兼容），不被非 pending 工具覆盖
            pass

        else:
            # 其他未分类工具（不在 PENDING_TOOLS 也不在 ALARM_PRE_TOOLS）→ busy
            update_state(
                last_mode="busy",
                turn_phase="busy",
                last_ts=now_ms,
            )

    elif ACTION == "post_tool":
        hook_input = read_stdin_json()
        tool_name = hook_input.get("tool_name", "")

        # 强制绿灯窗口：Stop 后 2 秒内 PostToolUse 不覆盖绿灯（ESC 视觉反馈）
        current = read_state()
        force_green_until = current.get("force_green_until", 0)
        if force_green_until > 0 and now_ms < force_green_until:
            update_state(last_ts=now_ms)
            sys.exit(0)

        if tool_name in ALARM_PRE_TOOLS:
            # AskUserQuestion 用户已回答 → 清除 alarm → busy
            state = read_state()
            if state.get("turn_phase") == "awaiting_user":
                update_state(
                    last_mode="busy",
                    turn_phase="busy",
                    last_ts=now_ms,
                )
                state = read_state()
                state.pop("awaiting_build", None)
                STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

        elif tool_name in PENDING_TOOLS:
            # Bash/Write 等执行完毕 → 清除 alarm → busy
            state = read_state()
            phase = state.get("turn_phase", "")
            if phase in ("awaiting_user", "pending_auth"):
                update_state(
                    last_mode="busy",
                    turn_phase="busy",
                    last_ts=now_ms,
                )
                state = read_state()
                state.pop("awaiting_build", None)
                state.pop("pending_since", None)
                STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

        elif tool_name in PLAN_CLEAR_TOOLS:
            # ExitPlanMode → 计划已批准，清除 alarm → busy
            state = read_state()
            if state.get("turn_phase") == "awaiting_user":
                update_state(
                    last_mode="busy",
                    turn_phase="busy",
                    last_ts=now_ms,
                )
            state = read_state()
            state.pop("awaiting_build", None)
            state.pop("plan_touched", None)
            state.pop("pending_since", None)
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

        elif tool_name in ALARM_POST_TOOLS:
            # CreatePlan / EnterPlanMode → 计划等用户审核 → 直接 alarm
            update_state(
                last_mode="alarm",
                turn_phase="awaiting_user",
                awaiting_build=True,
                plan_touched=True,
                last_ts=now_ms,
            )

    elif ACTION == "plan-detect":
        # AgentResponse / Notification → 正则检测「等 Build」文本模式
        data = read_stdin_json()
        text = data.get("text", data.get("message", ""))
        current = read_state()

        # 已经进入执行阶段，跳过
        if current.get("build_started"):
            sys.exit(0)

        if text and looks_like_plan_awaiting(text):
            update_state(
                last_mode="alarm",
                turn_phase="awaiting_user",
                awaiting_build=True,
                plan_touched=True,
                last_ts=now_ms,
            )
            with open(SCRIPT_DIR / "debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] plan-detect awaiting_build=true -> alarm\n")

    elif ACTION == "plan-file":
        # afterFileEdit → 检测写入的是否为 plan 文件
        data = read_stdin_json()
        current = read_state()

        if current.get("build_started"):
            sys.exit(0)

        fpath = data.get("file_path", data.get("path", ""))
        if fpath and is_plan_file_path(fpath):
            update_state(
                last_mode="alarm",
                turn_phase="awaiting_user",
                awaiting_build=True,
                plan_touched=True,
                last_ts=now_ms,
            )
            with open(SCRIPT_DIR / "debug.log", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] plan-file awaiting_build=true path={fpath!r} -> alarm\n")

    elif ACTION == "denied":
        # postToolUseFailure → 根据 failure_type 决定灯效
        data = read_stdin_json()
        failure_type = data.get("failure_type", "")
        current = read_state()

        if failure_type == "permission_denied":
            # 用户拒绝了权限请求 → thinking（AI 继续思考替代方案）
            if current.get("turn_phase") != "busy":
                update_state(
                    last_mode="thinking",
                    turn_phase="thinking",
                    last_ts=now_ms,
                )
        else:
            # 其他失败（工具执行错误等）→ error
            update_state(
                last_mode="error",
                turn_phase="",
                last_ts=now_ms,
            )

    elif ACTION == "stop":
        data = read_stdin_json()
        status = data.get("status", "")
        current = read_state()
        build_started = current.get("build_started", False)
        awaiting_build = current.get("awaiting_build", False)
        plan_touched = current.get("plan_touched", False)

        # DEBUG: 记录 Stop 事件收到的原始 status
        with open(SCRIPT_DIR / "debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stop status={status!r} awaiting_build={awaiting_build} build_started={build_started} plan_touched={plan_touched}\n")

        if status == "completed":
            if build_started:
                mode = "success"
            elif awaiting_build or plan_touched:
                # 任务完成但计划还没 Build → alarm 提醒
                update_state(awaiting_build=True)
                mode = "alarm"
            else:
                # 检查是否有近期 plan 文件（未被标记但磁盘上有 plan）
                recent_plan = has_recent_plan(
                    current.get("turn_started_ms", 0)
                )
                if recent_plan:
                    update_state(awaiting_build=True, plan_touched=True)
                    mode = "alarm"
                    with open(SCRIPT_DIR / "debug.log", "a", encoding="utf-8") as f:
                        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stop -> alarm (recent plan={recent_plan!r})\n")
                else:
                    mode = "success"
        elif status == "error":
            mode = "error"
        elif status == "aborted":
            mode = "green"
        else:
            mode = "green"

        # 清除所有标记
        state = read_state()
        state.pop("awaiting_build", None)
        state.pop("build_started", None)
        state.pop("plan_touched", None)
        state.pop("pending_since", None)

        # 强制绿灯窗口：Stop 后 2 秒内 PreToolUse 不覆盖绿灯，给 ESC 视觉反馈
        if mode == "green":
            state["force_green_until"] = now_ms + 2000

        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

        update_state(
            last_mode=mode,
            turn_phase="",
            last_ts=now_ms,
        )

    elif ACTION == "idle":
        # SessionEnd → 绿色待机 + 清理 session 文件 + 销毁桌面圆点
        current = read_state()
        if current.get("awaiting_build"):
            update_state(
                last_mode="alarm",
                turn_phase="",
                last_ts=now_ms,
            )
        else:
            update_state(
                last_mode="green",
                turn_phase="",
                last_ts=now_ms,
            )
        cleanup_session_files()
        kill_desktop_dots()

    elif ACTION == "busy":
        current = read_state()
        if current.get("awaiting_build") or current.get("turn_phase") == "awaiting_user":
            pass  # alarm 状态中，不覆盖
        else:
            update_state(
                last_mode="busy",
                turn_phase="busy",
                last_ts=now_ms,
            )

    elif ACTION == "alarm":
        update_state(
            last_mode="alarm",
            turn_phase="awaiting_user",
            awaiting_build=True,
            last_ts=now_ms,
        )

    elif ACTION == "build":
        # 计划开始执行 / Build 事件 → 清除 alarm 标记 → busy
        state = read_state()
        update_state(
            last_mode="busy",
            turn_phase="busy",
            build_started=True,
            last_ts=now_ms,
        )
        state = read_state()
        state.pop("awaiting_build", None)
        state.pop("plan_touched", None)
        state.pop("pending_since", None)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

    else:
        # 未知 action → 当作 mode 直接设置
        update_state(
            last_mode=ACTION,
            turn_phase=ACTION,
            last_ts=now_ms,
        )

finally:
    release_lock(fd)
