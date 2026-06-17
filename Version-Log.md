# Version-log

## [2.3] — 2026-06-15

### Added
- **多 Agent 互锁**：多个 Claude Code 会话同时运行时，仅首个 Agent 控制 BLE 实体灯，后续 Agent 自动静默。通过 `primary_session.json` + claude.exe PID 进程树追踪实现。
- **Pending 超时升级**：Bash/Write/Edit 等工具在 PreToolUse 设为 `pending_auth`，15 秒内若 PostToolUse 未到达则自动升级为 alarm 红灯闪烁，提醒用户有工具等待授权。
- **桌面圆点 PID 管理**：`traffic_light_desktop.py` 写入 PID 文件，支持 `stop`/`status` 命令，`startup.py` 通过 FindWindow + PID 双重检查防重复启动。
- **SessionEnd 清理**：`cc_state_bridge.py idle` 动作自动清理 session 标记文件 + 销毁桌面圆点窗口（TerminateProcess）。

### Changed
- `ble_daemon.py`：改为持久 BLE 连接模式，断线自动重连，150ms 轮询 `state_desktop.json`。
- `cc_state_bridge.py`：新增 `pending_auth` 相位 + `PENDING_ESCALATE_MS=15000` 超时逻辑。
- `startup.py`：桌面圆点每次 SessionStart 都确保运行（不受 primary 限制）。

### Fixed
- Alarm 退出零等待（`ALARM_EXIT_DEBOUNCE_MS=0`），用户确认后红灯立即熄灭。
- 桌面圆点 `stop_instance()` 残留 PID 文件自动清理。

---

## [2.0] — 2026-06 初

### Added
- **Claude Code 事件层（Python/Windows）**：完整的 `cc_state_bridge.py` + `ble_daemon.py` + `startup.py` 事件管线。
- **桌面任务栏圆点**：`traffic_light_desktop.py`（tkinter + 透明色键），Windows 任务栏左侧红黄绿三圆点。
- **安装包**：`install.bat` + `install.ps1` 一键安装，自动配置 `~/.claude/settings.json` 六个 Hook。
- **BLE 设备地址缓存**：`cursor_light_ble_enhanced.py` 缓存直连 ~1s，扫描降级 ~2-5s。

### Supported Hooks
| Hook | 灯效 |
|---|---|
| SessionStart | 自动启动守护进程 + 桌面圆点 |
| UserPromptSubmit | thinking（黄呼吸） |
| PreToolUse | busy / alarm（AskUserQuestion） |
| PostToolUse | alarm（CreatePlan/EnterPlanMode） |
| Stop | success / error / alarm |
| SessionEnd | green / alarm（计划待处理） |

---

## [1.0] — 2026-05

### Added
- **Cursor IDE 事件层（bash，macOS/Linux）**：`agent-light.sh` + `ble_gate.py` + 11 个 hook-*.sh。
- **ESP32-C3 固件**：Arduino 草图，12 种灯效模式（跑马灯、呼吸灯、警灯等），超时 5min→traffic，10min→off。
- **BLE 通信层**：`cursor_light_ble_enhanced.py`（bleak），扫描 CursorLight → GATT 写入 mode 字符串。
- 硬件：ESP32-C3 SuperMini + 共阳极 RGB LED（IO2=绿 IO3=黄 IO4=红）。
