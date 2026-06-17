#!/usr/bin/env python3
"""CursorLight 桌面状态灯 — 三个小圆点嵌入 Windows 任务栏左侧。

用法:
  py -3 traffic_light_desktop.py           # 监控 state_desktop.json
  py -3 traffic_light_desktop.py thinking  # 手动测试
  py -3 traffic_light_desktop.py demo      # 循环演示

右键圆点 → 退出
"""

import atexit
import ctypes
import json
import os
import sys
import time
import threading
from pathlib import Path

import tkinter as tk

from process_util import kill_process_by_pid_file

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "state_desktop.json"
PID_FILE = SCRIPT_DIR / "traffic_light_desktop.pid"
CONFIG_PATH = SCRIPT_DIR / "desktop_dots_config.json"

# ---- 颜色 ----------------------------------------------------------------

OFF = {"red": "#3a1018", "yellow": "#3a3410", "green": "#0c2e15"}
ON  = {"red": "#ff1744", "yellow": "#ffd600", "green": "#00e676"}
GLOW_C = {"red": "#ff5252", "yellow": "#ffe940", "green": "#69f0ae"}
TRANS = "#010203"   # 透明色键（罕见色，几乎不会和 UI 冲突）

DIAMETER = 16
GAP = 22           # 圆心间距
PAD = 6
W = PAD * 2 + GAP * 2 + DIAMETER
H = PAD * 2 + DIAMETER

# 每种模式的闪烁间隔（ms），不在表中的模式不闪烁
BLINK_INTERVAL = {
    "alarm": 350,     # 警灯红黄交替快闪
    "error": 500,     # 红灯闪烁
    "thinking": 800,  # 黄灯慢闪（呼吸感）
    "busy": 400,      # 黄灯快闪
    "traffic": 600,   # 红→黄→绿循环
}

# 模式最小保持时间（ms）— 防止快速切换导致中间帧被跳过
# alarm 不在此列表中：alarm 永远立即生效（最高优先级）
MIN_HOLD_MS = {
    "busy": 600,      # 从 alarm 退出后，黄灯至少闪一下
    "success": 400,   # 成功绿灯至少保持片刻
    "error": 400,     # 失败红灯至少保持片刻
}


# ---- Win32 获取任务栏位置 ------------------------------------------------

def get_taskbar_rect():
    """返回任务栏 (left, top, right, bottom)，失败返回 None"""
    try:
        hwnd = ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return None


def find_window(title_substring: str) -> bool:
    """检查是否有窗口标题包含指定字符串。"""
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title_substring)
        return hwnd != 0
    except Exception:
        return False


# ---- 主窗口 ----------------------------------------------------------------

class TaskbarDots:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.current_mode = "green"
        self.blink_state = False
        self._traffic_step = 0      # traffic 模式循环计数: 0=红,1=黄,2=绿
        self.manual_mode = None
        self._mode_set_at = 0       # 当前模式设置时间戳 (ms)
        self._pending_mode = None   # 延迟切换的目标 mode
        self._pending_after_id = None  # root.after id for deferred switch

        self._setup_window()
        self._build_lights()
        self._apply(self.current_mode)   # 初始点亮绿灯
        self._mode_set_at = int(time.time() * 1000)
        self._start_monitor()
        self._start_blink()

    # ---- window ----------------------------------------------------------

    def _setup_window(self):
        root = self.root
        root.title("CL")

        # PID 文件
        PID_FILE.write_text(str(os.getpid()))
        atexit.register(lambda: PID_FILE.unlink(missing_ok=True))
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 透明色键：背景会被抠掉
        root.overrideredirect(True)
        root.configure(bg=TRANS)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        try:
            root.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        # 周期性刷新 topmost，防止被其他窗口覆盖
        def _reassert_topmost():
            try:
                root.attributes("-topmost", True)
            except tk.TclError:
                pass
            root.after(3000, _reassert_topmost)
        root.after(3000, _reassert_topmost)

        # 定位：优先使用上次保存的位置，否则自动放到任务栏左侧上方
        saved = self._load_position()
        if saved:
            x, y = saved
        else:
            tb = get_taskbar_rect()
            if tb:
                tb_left, tb_top, _, tb_bottom = tb
                x = tb_left + 8
                y = tb_top - H - 4   # 任务栏上方 4px
            else:
                # fallback：屏幕左下角
                x = 8
                y = root.winfo_screenheight() - 68

        root.geometry(f"{W}x{H}+{x}+{y}")

        # 右键退出
        root.bind("<Button-3>", lambda e: root.destroy())
        root.bind("<Escape>", lambda e: root.destroy())

        # 左键拖拽 + 松手保存位置
        self._drag_ofs = 0, 0
        root.bind("<Button-1>", self._drag_start)
        root.bind("<B1-Motion>", self._drag_move)
        root.bind("<ButtonRelease-1>", self._drag_end)

    def _drag_start(self, e):
        self._drag_ofs = e.x, e.y

    def _drag_move(self, e):
        nx = self.root.winfo_x() + e.x - self._drag_ofs[0]
        ny = self.root.winfo_y() + e.y - self._drag_ofs[1]
        self.root.geometry(f"+{nx}+{ny}")

    def _drag_end(self, e):
        """鼠标松开时保存当前位置。"""
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self._save_position(x, y)

    @staticmethod
    def _save_position(x: int, y: int):
        try:
            CONFIG_PATH.write_text(json.dumps({"x": x, "y": y}))
        except OSError:
            pass

    @staticmethod
    def _load_position():
        if not CONFIG_PATH.exists():
            return None
        try:
            data = json.loads(CONFIG_PATH.read_text())
            return data.get("x"), data.get("y")
        except (json.JSONDecodeError, OSError):
            return None

    def _on_close(self):
        """清理 PID 文件并销毁窗口。"""
        if self._pending_after_id is not None:
            self.root.after_cancel(self._pending_after_id)
            self._pending_after_id = None
        PID_FILE.unlink(missing_ok=True)
        self.root.destroy()

    # ---- canvas & dots ---------------------------------------------------

    def _build_lights(self):
        c = tk.Canvas(self.root, width=W, height=H,
                      bg=TRANS, highlightthickness=0, bd=0)
        c.pack()
        self.canvas = c
        self.dots = {}

        centers = {
            "red":    (PAD + DIAMETER // 2,           H // 2),
            "yellow": (PAD + GAP + DIAMETER // 2,     H // 2),
            "green":  (PAD + GAP * 2 + DIAMETER // 2, H // 2),
        }
        for color, (cx, cy) in centers.items():
            r = DIAMETER // 2
            glow = c.create_oval(cx - r - 2, cy - r - 2,
                                 cx + r + 2, cy + r + 2,
                                 fill=OFF[color], outline="", state="hidden")
            dot = c.create_oval(cx - r, cy - r, cx + r, cy + r,
                                fill=OFF[color], outline="")
            hl = c.create_oval(cx - r + 3, cy - r + 2,
                               cx - r + 6, cy - r + 5,
                               fill="#fff", outline="", state="hidden")
            self.dots[color] = {
                "glow": glow, "dot": dot, "highlight": hl,
                "on": False,
            }

    def _set_dot(self, color: str, on: bool):
        d = self.dots[color]
        if d["on"] == on:
            return
        d["on"] = on
        c = self.canvas
        if on:
            c.itemconfig(d["dot"], fill=ON[color])
            c.itemconfig(d["glow"], fill=GLOW_C[color], state="normal")
            c.itemconfig(d["highlight"], state="normal")
        else:
            c.itemconfig(d["dot"], fill=OFF[color])
            c.itemconfig(d["glow"], state="hidden")
            c.itemconfig(d["highlight"], state="hidden")

    # ---- mode → dots -----------------------------------------------------

    @staticmethod
    def _mode_map(mode: str):
        r = y = g = False
        if mode in ("green", "success"):
            g = True
        elif mode in ("thinking", "busy", "traffic"):
            y = True
        elif mode in ("alarm", "error"):
            r = True
        return r, y, g

    def _apply(self, mode: str):
        r, y, g = self._mode_map(mode)

        if mode == "alarm":
            # 红黄交替快闪（警灯效果，同步 BLE 物理灯）
            if self.blink_state:
                r, y = True, False   # 红灯亮
            else:
                r, y = False, True   # 黄灯亮
        elif mode == "error":
            # 红灯闪烁
            if not self.blink_state:
                r = False
        elif mode == "thinking":
            # 黄灯慢闪（呼吸感）
            if not self.blink_state:
                y = False
        elif mode == "busy":
            # 黄灯快闪
            if not self.blink_state:
                y = False
        elif mode == "traffic":
            # 红→黄→绿循环（同步 BLE traffic 灯效）
            r = y = g = False
            if self._traffic_step == 0:
                r = True
            elif self._traffic_step == 1:
                y = True
            else:
                g = True

        self._set_dot("red", r)
        self._set_dot("yellow", y)
        self._set_dot("green", g)

    def _do_set_mode(self, mode: str):
        """实际执行模式切换。"""
        self._pending_after_id = None
        self._pending_mode = None
        if mode == self.current_mode:
            return
        self.current_mode = mode
        self._mode_set_at = int(time.time() * 1000)
        self.blink_state = True    # 新 mode 先亮灯（不是先灭）
        self._traffic_step = 0
        self._apply(mode)

    def set_mode(self, mode: str):
        """切换灯效模式。alarm 立即生效；非 alarm 模式遵守最小保持时间，防止快速
        连续状态转换（如 alarm→busy→success 在 400ms 内完成）导致中间帧被跳过。"""
        if mode == self.current_mode:
            return

        # 取消之前的延迟切换
        if self._pending_after_id is not None:
            self.root.after_cancel(self._pending_after_id)
            self._pending_after_id = None
        self._pending_mode = None

        now = int(time.time() * 1000)

        # alarm 总是立即生效（最高优先级），不被最小保持时间阻塞
        if mode != "alarm" and self._mode_set_at > 0:
            min_hold = MIN_HOLD_MS.get(self.current_mode, 0)
            if min_hold > 0:
                elapsed = now - self._mode_set_at
                if elapsed < min_hold:
                    # 还没到最小保持时间，延迟到时间到了再切换
                    remaining = int(min_hold - elapsed)
                    self._pending_mode = mode
                    self._pending_after_id = self.root.after(
                        remaining, self._do_set_mode, mode
                    )
                    return

        self._do_set_mode(mode)

    # ---- blink timer -----------------------------------------------------

    def _start_blink(self):
        """动态闪烁定时器：根据当前模式自适应间隔和闪烁效果。"""
        def tick():
            mode = self.current_mode
            interval = BLINK_INTERVAL.get(mode)

            if interval:
                if mode == "traffic":
                    # 红→黄→绿 三态循环
                    self._traffic_step = (self._traffic_step + 1) % 3
                else:
                    self.blink_state = not self.blink_state
                self._apply(mode)

            # 根据当前模式重新调度下一次 tick
            next_interval = BLINK_INTERVAL.get(self.current_mode, 500)
            self.root.after(next_interval, tick)

        self.root.after(500, tick)

    # ---- state.json monitor (后台线程) -----------------------------------

    PENDING_ESCALATE_MS = 15000  # pending 15 秒超时升级 alarm
    ALARM_TIMEOUT_MS = 30000     # awaiting_user 30 秒超时自动回落

    def _read_state(self) -> str | None:
        if not STATE_PATH.exists():
            return None
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        last_mode = data.get("last_mode", "")
        phase = data.get("turn_phase", "")
        awaiting = data.get("awaiting_build", False)
        now = int(time.time() * 1000)

        if phase == "pending_auth":
            pending_since = data.get("pending_since", 0)
            if now - pending_since >= self.PENDING_ESCALATE_MS:
                return "alarm"
            return last_mode or "busy"

        if awaiting or phase == "awaiting_user":
            # 超时保护：alarm 超过 ALARM_TIMEOUT_MS 自动回落
            pending_since = data.get("pending_since", 0)
            if pending_since > 0 and (now - pending_since) >= self.ALARM_TIMEOUT_MS:
                return last_mode or "busy"
            return "alarm"

        # Stop 后强制绿灯窗口：ESC 中断的视觉反馈
        force_green_until = data.get("force_green_until", 0)
        if force_green_until > 0 and now < force_green_until:
            return "green"

        if last_mode:
            return last_mode
        if phase == "thinking":
            return "thinking"
        if phase == "busy":
            return "busy"
        return "green"

    def _start_monitor(self):
        def loop():
            while True:
                if self.manual_mode is not None:
                    mode = self.manual_mode
                else:
                    mode = self._read_state() or "green"
                if mode != self.current_mode:
                    self.root.after(0, self.set_mode, mode)
                time.sleep(0.4)
        t = threading.Thread(target=loop, daemon=True)
        t.start()


# ---- demo ----------------------------------------------------------------

DEMO = [
    ("green", 1.5), ("thinking", 2.0), ("busy", 2.5), ("alarm", 1.8),
    ("success", 1.5), ("thinking", 1.2), ("busy", 1.5), ("error", 1.8),
    ("green", 1.5),
]


def stop_instance():
    """终止正在运行的桌面圆点实例。"""
    if not PID_FILE.exists():
        print("Desktop dots not running (no PID file)")
        return False

    pid_str = ""
    try:
        pid_str = PID_FILE.read_text().strip()
    except OSError:
        pass

    ok = kill_process_by_pid_file(PID_FILE)
    if ok:
        print(f"Desktop dots stopped (PID={pid_str})")
    else:
        print(f"Process not found (PID={pid_str}), cleaned up stale PID file")
    return ok


def is_running() -> bool:
    """检查桌面圆点是否已在运行（通过 FindWindow + PID 双重检查）。"""
    if not find_window("CL"):
        # 窗口不存在但 PID 文件残留 → 清理
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                import ctypes as ct
                handle = ct.windll.kernel32.OpenProcess(0x0400, False, pid)
                if not handle:
                    PID_FILE.unlink(missing_ok=True)
                else:
                    ct.windll.kernel32.CloseHandle(handle)
            except (ValueError, OSError):
                PID_FILE.unlink(missing_ok=True)
        return False
    return True


def main():
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        if arg == "stop":
            stop_instance()
            return
        if arg == "status":
            if is_running():
                print("Desktop dots running")
            else:
                print("Desktop dots not running")
            return

    # 已运行则跳过（避免重复启动）
    if is_running():
        print("Desktop dots already running")
        return

    root = tk.Tk()
    app = TaskbarDots(root)

    if len(sys.argv) > 1:
        arg = sys.argv[1].strip().lower()
        if arg == "demo":
            i = [0]
            def step():
                mode, dur = DEMO[i[0] % len(DEMO)]
                app.set_mode(mode)
                i[0] += 1
                root.after(int(dur * 1000), step)
            root.after(300, step)
        elif arg in ("green", "thinking", "busy", "alarm", "success", "error"):
            app.manual_mode = arg
            app.set_mode(arg)
        else:
            print(f"未知: {arg}")
            sys.exit(1)

    root.mainloop()


if __name__ == "__main__":
    main()
