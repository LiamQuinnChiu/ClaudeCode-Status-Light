#!/usr/bin/env python3
# 控制 ESP32-C3 CursorLight BLE 状态灯
#
# 首次安装：
#   python3 -m pip install bleak
#
# 用法：
#   python3 cursor_light_ble_enhanced.py demo
#   python3 cursor_light_ble_enhanced.py thinking
#   python3 cursor_light_ble_enhanced.py busy
#   python3 cursor_light_ble_enhanced.py alarm

import asyncio
import json
import sys
from pathlib import Path

from bleak import BleakScanner, BleakClient

DEVICE_NAME = "CursorLight"
MODE_CHAR_UUID = "b8b7e002-7a6b-4f4f-9a8b-11c0ffee0001"

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_PATH = SCRIPT_DIR / "ble_device_cache"

VALID_MODES = {
    "red", "yellow", "green", "busy", "error",
    "thinking", "ai", "success", "traffic", "alarm", "demo", "off",
}


def _read_cache() -> str | None:
    """读取缓存的设备地址。"""
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return data.get("address")
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return None


def _write_cache(address: str) -> None:
    """缓存设备地址。"""
    try:
        CACHE_PATH.write_text(json.dumps({"address": address}), encoding="utf-8")
    except OSError:
        pass


async def _try_direct_connect(address: str, mode: str) -> bool:
    """尝试直接连接（跳过扫描），成功返回 True。"""
    try:
        async with BleakClient(address, timeout=5.0) as client:
            if client.is_connected:
                await client.write_gatt_char(
                    MODE_CHAR_UUID, mode.encode("utf-8"), response=True
                )
                print(f"直接连接成功: {address}, mode={mode}")
                return True
    except Exception:
        pass
    return False


async def main():
    if len(sys.argv) < 2:
        print("用法: python3 cursor_light_ble_enhanced.py <mode>")
        print("可用 mode:", ", ".join(sorted(VALID_MODES)))
        sys.exit(1)

    mode = sys.argv[1].strip().lower()
    if mode not in VALID_MODES:
        print(f"未知 mode: {mode}")
        print("可用 mode:", ", ".join(sorted(VALID_MODES)))
        sys.exit(1)

    # 1. 尝试缓存地址直连（快，约 1s）
    cached = _read_cache()
    if cached:
        if await _try_direct_connect(cached, mode):
            return  # 直连成功，结束

    # 2. 直连失败，回退到扫描（慢，约 2-5s）
    print(f"正在扫描 BLE 设备：{DEVICE_NAME} ...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=5.0)

    if device is None:
        print("没有找到 CursorLight。请确认：")
        print("1. ESP32 已通电")
        print("2. 代码已刷入 BLE 增强版")
        print("3. 距离足够近")
        print("4. 蓝牙已打开")
        sys.exit(2)

    print(f"找到设备: {device.address}")

    async with BleakClient(device) as client:
        if not client.is_connected:
            print("连接失败")
            sys.exit(3)

        print(f"已连接，发送 mode={mode}")
        await client.write_gatt_char(MODE_CHAR_UUID, mode.encode("utf-8"), response=True)
        print("发送完成")

    # 3. 缓存地址，下次直连更快
    _write_cache(device.address)


if __name__ == "__main__":
    asyncio.run(main())
