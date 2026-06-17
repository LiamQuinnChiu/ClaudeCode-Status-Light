# 🚦 ClaudeCode-Status-Light

<p align="center">
  <img src="https://img.shields.io/badge/Platform-ESP32--C3%20SuperMini-00979D?style=for-the-badge&logo=espressif" alt="ESP32-C3">
  <img src="https://img.shields.io/badge/Connectivity-BLE%205.0-0082FC?style=for-the-badge&logo=bluetooth" alt="BLE">
  <img src="https://img.shields.io/badge/IDE-Cursor-6C4DFF?style=for-the-badge" alt="Cursor IDE">
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License">
</p>

<p align="center">
  <b>适用于ClaudeCode框架的实体状态指示器。</b><br>
  <sub>Entity State Indicator for the Agent Framework applicable to ClaudeCode.</sub>
</p>


---

## 📖 这是什么？

**Status-Light** 用一块 ESP32-C3 SuperMini 开发板 + 蓝牙 BLE，将淘宝十几块钱的红绿灯挂件改造成桌面状态灯，实时显示 Cursor Agent / AI 编程过程中的各种状态：

- 💡 **思考中** → 连贯跑马灯
- ⚡ **执行中** → 黄灯慢闪
- ✅ **成功** → 绿灯常亮
- ❌ **失败** → 红灯快闪
- 🚨 **等待/阻塞** → 红黄交替警灯

不需要 Wi-Fi，不占 5GHz 频段，ESP32 只管 BLE 通信和灯效。

---

## 🎬 效果速览

| 场景            | 灯效        | 说明                       |
| --------------- | ----------- | -------------------------- |
| AI 分析 / 规划  | 🟢🟡🔴 跑马灯  | 连贯滚动，一眼就知道在思考 |
| 执行命令 / 构建 | 🟡 慢闪      | 黄灯呼吸，正在干活         |
| 任务成功        | 🟢 常亮      | 绿灯，平安落地             |
| 任务失败        | 🔴 快闪      | 红灯，需要关注             |
| 等待用户操作    | 🔴🟡 警灯     | 红黄交替，不可忽略         |
| 空闲            | 🟢 常亮 / 灭 | 按需关闭                   |



---

## ⚡ 快速开始

### 1. 烧录固件

Arduino IDE 2.x → 安装 `esp32 by Espressif Systems` → 选择 `ESP32C3 Dev Module` → Upload `.ino`

```
USB CDC On Boot = Enabled
波特率 = 115200
```

### 2. 安装电脑端依赖

```bash
# macOS
python3 -m pip install bleak

# Windows
py -3 -m pip install bleak
```

### 3. 手动测试灯

```bash
# macOS
python3 cursor_light_ble_enhanced.py green     # 绿灯
python3 cursor_light_ble_enhanced.py thinking  # 思考
python3 cursor_light_ble_enhanced.py busy      # 执行中
python3 cursor_light_ble_enhanced.py alarm     # 警灯
python3 cursor_light_ble_enhanced.py off       # 关闭

# Windows
py -3 cursor_light_ble_enhanced.py green
py -3 cursor_light_ble_enhanced.py thinking
py -3 cursor_light_ble_enhanced.py busy
py -3 cursor_light_ble_enhanced.py alarm
py -3 cursor_light_ble_enhanced.py off
```

### 4. 接入 Cursor Hooks（自动化）

```bash
# macOS
bash install-cursor-light.sh

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File install-cursor-light-win.ps1
```

之后 Cursor Agent 的状态会自动映射到灯效，无需手动操作。

---

## 🎨 完整灯效列表

| mode       | 灯效       | 用途             |
| ---------- | ---------- | ---------------- |
| `thinking` | 跑马灯     | AI 分析、规划    |
| `ai`       | 慢速跑马灯 | AI 生成内容      |
| `busy`     | 黄灯慢闪   | 构建、测试、安装 |
| `success`  | 绿灯常亮   | 成功             |
| `error`    | 红灯快闪   | 失败             |
| `alarm`    | 红黄警灯   | 严重阻塞         |
| `yellow`   | 黄灯常亮   | 等待用户         |
| `traffic`  | 模拟红绿灯 | 展示过渡         |
| `off`      | 全灭       | 关闭             |

内置自动超时保护，防止灯长时间高亮忘记关。

---

## 🏗️ 架构

```
Cursor IDE 事件
  → hooks.json（Hook 注册）
    → agent-light.sh（状态路由）
      → ble_gate.py（去重防抖）
        → cursor_light_ble_enhanced.py（BLE 发送）
          → ESP32-C3（PWM 灯控）
```

- **BLE 不占 Wi-Fi**，电脑继续连 5GHz 路由器
- **原子去重门** 防止多 Hook 同时触发导致灯效乱跳
- **设备地址缓存** 加速蓝牙重连

---

## 🐛 常见问题

| 现象           | 检查                                 |
| -------------- | ------------------------------------ |
| 找不到设备     | ESP32 供电？蓝牙开？距离 < 10m？     |
| 上传失败       | 按住 BOOT → Upload → 松开            |
| 串口无输出     | `USB CDC On Boot = Enabled`          |
| macOS 蓝牙错误 | 系统设置 → 隐私 → 蓝牙 → 授权终端    |
| 灯效乱跳       | `ble_gate.py` 去重门正常工作，等几秒 |

---

## 📚 完整文档

详细接线、烧录、调试、Hook 配置请查阅：

- [灯效功能说明.md](./灯效功能说明.md) — 完整灯效与 Hook 映射
- [README.md](./README.md) — 完整用户手册（中英双语）
- [CLAUDE.md](./CLAUDE.md) — 开发者参考

---

创作声明：

*接口代码及硬件连接与烧录原作者直达    	↓ ：*

*https://github.com/JasonLam08/cursor_agent_status_light*

</mark>本代码段基于原作者JasonLam08的框架接口代码基础上重写以适配Claude Code终端，并添加新的灯效逻辑。</mark>
