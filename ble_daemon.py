#!/usr/bin/env python3
"""CursorLight BLE 守护进程

保持到 ESP32 CursorLight 的 BLE 长连接，轮询 state_desktop.json，
检测状态变化后直接 GATT 写入（无扫描、无重连），延迟 < 200ms。

用法:
  py -3 ble_daemon.py              # 前台运行（调试）
  py -3 ble_daemon.py start        # 后台运行
  py -3 ble_daemon.py stop         # 停止后台进程
  py -3 ble_daemon.py status       # 查看运行状态
  py -3 ble_daemon.py send <mode>  # 手动发送模式
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from bleak import BleakScanner, BleakClient

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "state_desktop.json"
PID_FILE = SCRIPT_DIR / "ble_daemon.pid"
LOG_PATH = SCRIPT_DIR / "ble_daemon.log"

DEVICE_NAME = "CursorLight"
MODE_CHAR_UUID = "b8b7e002-7a6b-4f4f-9a8b-11c0ffee0001"

# ---- 配置 ----

POLL_INTERVAL = 0.15         # 状态轮询间隔（秒）— 高频保证 alarm→busy 低延迟
RECONNECT_DELAY = 2.0        # BLE 断线重连间隔
BLE_TIMEOUT = 8.0            # 单次 BLE 操作超时

DEBOUNCE_MS = {
    "thinking": 1200,
    "busy": 1200,
    "alarm": 300,
    "success": 1200,
    "error": 1200,
    "green": 2000,
    "red": 1000,
    "yellow": 1000,
}
ALARM_EXIT_DEBOUNCE_MS = 0    # alarm 退出零等待


# ---- 日志 ----

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


# ---- 状态 ----

def read_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


PENDING_ESCALATE_MS = 15000  # pending_auth 15 秒超时后升级为 alarm
ALARM_TIMEOUT_MS = 30000     # awaiting_user 30 秒超时后自动回落

def resolve_mode(data: dict, now_ms: int | None = None) -> str:
    awaiting = data.get("awaiting_build", False)
    phase = data.get("turn_phase", "")
    last_mode = data.get("last_mode", "")

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    # Stop 后强制绿灯窗口：ESC 中断的视觉反馈，2 秒内不被覆盖
    force_green_until = data.get("force_green_until", 0)
    if force_green_until > 0 and now_ms < force_green_until:
        return "green"

    # pending_auth：预授权快速清除不升级（auto bypass），需授权超时升级（手动模式 alarm）
    if phase == "pending_auth":
        pending_since = data.get("pending_since", 0)
        if now_ms - pending_since >= PENDING_ESCALATE_MS:
            return "alarm"
        else:
            return last_mode or "busy"

    if awaiting or phase == "awaiting_user":
        # 超时保护：alarm 超过 ALARM_TIMEOUT_MS 自动回落，防止 PostToolUse 未触发卡死
        pending_since = data.get("pending_since", 0)
        if pending_since > 0 and (now_ms - pending_since) >= ALARM_TIMEOUT_MS:
            return last_mode or "busy"
        return "alarm"

    if last_mode:
        return last_mode
    if phase == "thinking":
        return "thinking"
    if phase == "busy":
        return "busy"
    return "green"


# ---- BLE 持久连接 ----

class PersistentBLE:
    """保持到 CursorLight 的 BLE 长连接，自动重连。"""

    def __init__(self):
        self.client: BleakClient | None = None
        self.address: str | None = None
        self.connected = False

    async def connect(self) -> bool:
        """扫描并连接到 CursorLight，返回是否成功。"""
        # 先尝试扫描
        log(f"Scanning for {DEVICE_NAME} ...")
        try:
            device = await asyncio.wait_for(
                BleakScanner.find_device_by_name(DEVICE_NAME, timeout=5.0),
                timeout=6.0,
            )
        except asyncio.TimeoutError:
            log("BLE scan timeout")
            return False

        if device is None:
            log("Device not found")
            return False

        self.address = device.address
        log(f"Found: {self.address}")

        return await self._do_connect()

    async def connect_cached(self, address: str) -> bool:
        """用缓存地址直连。"""
        self.address = address
        return await self._do_connect()

    async def _do_connect(self) -> bool:
        """建立 BleakClient 连接。"""
        try:
            self.client = BleakClient(
                self.address,
                timeout=BLE_TIMEOUT,
                disconnected_callback=self._on_disconnect,
            )
            await asyncio.wait_for(self.client.connect(), timeout=BLE_TIMEOUT)
            if self.client.is_connected:
                self.connected = True
                log(f"BLE connected: {self.address}")
                return True
        except asyncio.TimeoutError:
            log("BLE connect timeout")
        except Exception as e:
            log(f"BLE connect error: {e}")
        self.connected = False
        return False

    def _on_disconnect(self, client: BleakClient):
        """断开回调。"""
        log("BLE disconnected")
        self.connected = False

    async def write_mode(self, mode: str) -> bool:
        """GATT 写入模式字符串，返回是否成功。"""
        if not self.client or not self.connected:
            return False
        try:
            await asyncio.wait_for(
                self.client.write_gatt_char(
                    MODE_CHAR_UUID, mode.encode("utf-8"), response=True
                ),
                timeout=3.0,
            )
            return True
        except asyncio.TimeoutError:
            log(f"BLE write timeout: {mode}")
        except Exception as e:
            log(f"BLE write error: {mode} | {e}")
        self.connected = False
        return False

    async def disconnect(self):
        if self.client and self.connected:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.connected = False


# ---- Main Loop ----

async def daemon_loop():
    """持久连接主循环。"""
    log("BLE daemon starting (persistent connection mode)")

    ble = PersistentBLE()

    # 扫描连接
    if not await ble.connect():
        log("Initial connection failed, will retry in loop")

    last_mode = ""
    last_sent_ms = 0
    last_sent_mode = ""
    reconnect_cooldown = 0

    while True:
        try:
            now_ms = int(time.time() * 1000)

            # ---- BLE 连接管理 ----
            if not ble.connected:
                if now_ms >= reconnect_cooldown:
                    reconnect_cooldown = now_ms + int(RECONNECT_DELAY * 1000)
                    if ble.address:
                        log(f"Reconnecting to {ble.address} ...")
                        await ble.connect_cached(ble.address)
                    else:
                        log("Scanning for device ...")
                        await ble.connect()
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # ---- 状态轮询 ----
            data = read_state()
            mode = resolve_mode(data, now_ms)

            if mode != last_mode:
                log(f"State: {last_mode} -> {mode}")
            last_mode = mode

            # ---- Debounce ----
            if mode != last_sent_mode:
                if last_sent_mode == "alarm" and mode != "alarm":
                    debounce = ALARM_EXIT_DEBOUNCE_MS
                else:
                    debounce = DEBOUNCE_MS.get(mode, 1500)
                elapsed = now_ms - last_sent_ms

                if elapsed >= debounce:
                    log(f"BLE send: {mode}")
                    ok = await ble.write_mode(mode)
                    if ok:
                        last_sent_ms = int(time.time() * 1000)
                        last_sent_mode = mode
                        log(f"BLE OK: {mode}")
                    else:
                        log(f"BLE fail: {mode}, will reconnect")
                else:
                    remaining = (debounce - elapsed) / 1000
                    if remaining > 0.5:  # 只记录明显的 debounce 阻塞
                        log(f"Debounce: {mode} blocked ({remaining:.1f}s)")

        except Exception as e:
            log(f"Loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


# ---- CLI 命令 ----

def cmd_start():
    """后台启动 daemon。"""
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                print(f"Daemon already running (PID={pid})")
                return
        except (ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)

    proc = subprocess.Popen(
        [sys.executable, __file__],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"BLE daemon started (PID={proc.pid})")
    log(f"Daemon started by cmd_start, PID={proc.pid}")


def cmd_stop():
    """停止 daemon。"""
    if not PID_FILE.exists():
        print("No daemon PID file found")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
            print(f"Daemon stopped (PID={pid})")
        else:
            print(f"Process not found (PID={pid})")
    except (ValueError, OSError) as e:
        print(f"Failed to stop: {e}")
    finally:
        PID_FILE.unlink(missing_ok=True)
        log("Daemon stopped")


def cmd_status():
    """检查 daemon 运行状态。"""
    if not PID_FILE.exists():
        print("Daemon not running")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            print(f"Daemon running (PID={pid})")
            data = read_state()
            mode = resolve_mode(data)
            print(f"Current mode: {mode}")
            if data:
                print(f"State data: {json.dumps(data, ensure_ascii=False)}")
        else:
            print("Daemon not running (stale PID file)")
            PID_FILE.unlink(missing_ok=True)
    except (ValueError, OSError):
        print("Daemon not running")


async def cmd_send_async(mode: str):
    """手动发送 BLE 命令（持久连接）。"""
    log(f"Manual send: {mode}")
    ble = PersistentBLE()
    if not await ble.connect():
        print("ERROR: Cannot connect to CursorLight")
        return
    ok = await ble.write_mode(mode)
    if ok:
        log(f"Manual BLE OK: {mode}")
        print(f"Sent: {mode}")
    else:
        print(f"Failed to send: {mode}")
    await ble.disconnect()


def cmd_send(mode: str):
    asyncio.run(cmd_send_async(mode))


def main():
    if len(sys.argv) < 2:
        asyncio.run(daemon_loop())
        return

    cmd = sys.argv[1].lower()

    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "status":
        cmd_status()
    elif cmd == "send" and len(sys.argv) > 2:
        cmd_send(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
