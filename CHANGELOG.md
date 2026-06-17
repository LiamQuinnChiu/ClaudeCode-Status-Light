# Changelog — v2.4

## [2.4] — 2026-06-17

### Added
- **灯效映射同步**：桌面圆点（`traffic_light_desktop.py`）与 BLE 物理灯灯效完全对齐，五种模式统一映射表：
  - `alarm` → 红黄交替快闪（警灯），间隔 350ms
  - `error` → 红灯闪烁，间隔 500ms（BLE：快闪→常亮）
  - `thinking` → 黄灯慢闪（呼吸感），间隔 800ms
  - `busy` → 黄灯快闪，间隔 400ms
  - `green` / `success` → 绿灯常亮
- **最小保持时间（MIN_HOLD_MS）**：防止快速连续状态转换导致中间帧被跳过。
  - `busy` ≥ 600ms
  - `success` ≥ 400ms
  - `error` ≥ 400ms
  - `alarm` 不受限制，永远立即生效。

### Changed
- `traffic_light_desktop.py`：灯效逻辑重构，从简单颜色切换升级为与 BLE 固件一致的 5 模式动画系统。
- `ble_daemon.py`：`resolve_mode()` 融入 MIN_HOLD_MS 约束，确保状态切换不丢帧。

### Fixed
- 修复快速连续状态转换（如 alarm → busy → success 在 400ms 内）时桌面圆点跳过中间动画帧的问题。

---

> 上一版本 [2.3] 主要新增了多 Agent 互锁、Pending 超时升级、桌面圆点 PID 管理、SessionEnd 清理，以及 BLE 持久连接重构。
