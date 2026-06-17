# CursorLight — Claude Code 状态灯 一键安装脚本
# 用法: 双击 install.bat 或 终端运行:
#   powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "CursorLight Installer"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetDir = "$env:USERPROFILE\.cursor\hooks\cursor-light"
$SettingsFile = "$env:USERPROFILE\.claude\settings.json"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  CursorLight — Claude Code 状态灯安装" -ForegroundColor Cyan
Write-Host "  ESP32 BLE + 桌面圆点" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# =============================================
# Step 1: Check Python
# =============================================
Write-Host "[1/5] Checking Python ..." -ForegroundColor Yellow

$python = $null
foreach ($py in @("py", "python3", "python")) {
    try {
        $ver = & $py --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $python = $py
            Write-Host "  OK: $ver" -ForegroundColor Green
            break
        }
    } catch {}
}

if (-not $python) {
    Write-Host "  ERROR: Python not found!" -ForegroundColor Red
    Write-Host "  Please install Python 3.9+ from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  IMPORTANT: Check 'Add Python to PATH' during installation" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# =============================================
# Step 2: Install bleak
# =============================================
Write-Host "[2/5] Installing bleak (BLE library) ..." -ForegroundColor Yellow

try {
    & $python -m pip install bleak --quiet 2>&1 | Out-Null
    & $python -c "import bleak" 2>&1 | Out-Null
    Write-Host "  OK: bleak installed" -ForegroundColor Green
} catch {
    Write-Host "  WARNING: bleak install may have failed, continuing..." -ForegroundColor Yellow
}

# =============================================
# Step 3: Deploy files
# =============================================
Write-Host "[3/5] Deploying to $TargetDir ..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

$files = @(
    "cursor_light_ble_enhanced.py",
    "ble_daemon.py",
    "cc_state_bridge.py",
    "ble_gate.py",
    "state_helper.py",
    "startup.py",
    "traffic_light_desktop.py",
    "process_util.py"
)

foreach ($f in $files) {
    $src = Join-Path $ScriptDir $f
    $dst = Join-Path $TargetDir $f
    if (Test-Path $src) {
        Copy-Item -Force $src $dst
        Write-Host "  OK: $f" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $f (install package incomplete)" -ForegroundColor Red
    }
}

# =============================================
# Step 4: Configure Claude Code hooks
# =============================================
Write-Host "[4/5] Configuring Claude Code hooks ..." -ForegroundColor Yellow

$claudeDir = Split-Path $SettingsFile -Parent
New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null

# Read existing settings if any
$settings = $null
if (Test-Path $SettingsFile) {
    try {
        $raw = Get-Content $SettingsFile -Raw -Encoding UTF8
        if ($raw.Trim()) {
            $settings = $raw | ConvertFrom-Json -AsHashtable -Depth 20
        }
    } catch {
        Write-Host "  WARNING: Cannot parse settings.json, backing up and creating new" -ForegroundColor Yellow
        $backup = $SettingsFile + ".bak." + (Get-Date -Format "yyyyMMddHHmmss")
        Copy-Item $SettingsFile $backup
        Write-Host "  Backup: $backup" -ForegroundColor Gray
        $settings = $null
    }
}

if (-not $settings) {
    $settings = @{}
}

# Check if already installed
$alreadyInstalled = $false
if ($settings.ContainsKey("hooks") -and $settings.hooks.ContainsKey("SessionStart")) {
    foreach ($hook in $settings.hooks.SessionStart) {
        if ($hook -is [hashtable] -and $hook.ContainsKey("hooks")) {
            foreach ($h in $hook.hooks) {
                if ($h -is [hashtable] -and $h.command -like "*startup.py*") {
                    $alreadyInstalled = $true
                    break
                }
            }
        }
    }
}

if ($alreadyInstalled) {
    Write-Host "  SKIP: Already installed, hooks unchanged" -ForegroundColor Yellow
} else {
    # Ensure hooks hashtable exists
    if (-not $settings.ContainsKey("hooks")) {
        $settings["hooks"] = @{}
    }

    # SessionStart - auto-start daemon + desktop dots
    $settings.hooks["SessionStart"] = @(
        @{
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/startup.py`""
                    shell = "bash"
                }
            )
        }
    )

    # UserPromptSubmit - thinking (yellow breathing)
    $settings.hooks["UserPromptSubmit"] = @(
        @{
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" thinking"
                    shell = "bash"
                }
            )
        }
    )

    # PreToolUse - busy / alarm (AskUserQuestion)
    $settings.hooks["PreToolUse"] = @(
        @{
            matcher = ""
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" pre_tool"
                    shell = "bash"
                }
            )
        }
    )

    # PostToolUse - alarm (CreatePlan / EnterPlanMode)
    $settings.hooks["PostToolUse"] = @(
        @{
            matcher = ""
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" post_tool"
                    shell = "bash"
                }
            )
        }
    )

    # Stop - success / error / alarm
    $settings.hooks["Stop"] = @(
        @{
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" stop"
                    shell = "bash"
                }
            )
        }
    )

    # PostToolUseFailure - denied (permission_denied -> thinking, else -> error)
    $settings.hooks["PostToolUseFailure"] = @(
        @{
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" denied"
                    shell = "bash"
                }
            )
        }
    )

    # Notification - plan-detect (AI response text -> alarm if plan awaiting)
    $settings.hooks["Notification"] = @(
        @{
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" plan-detect"
                    shell = "bash"
                }
            )
        }
    )

    # SessionEnd - green / alarm (plan pending)
    $settings.hooks["SessionEnd"] = @(
        @{
            hooks = @(
                @{
                    type = "command"
                    command = "py -3 `"`$USERPROFILE/.cursor/hooks/cursor-light/cc_state_bridge.py`" idle"
                    shell = "bash"
                }
            )
        }
    )

    # Write back
    try {
        $json = $settings | ConvertTo-Json -Depth 20
        $json | Set-Content -Path $SettingsFile -Encoding UTF8
        Write-Host "  OK: Hooks configured in $SettingsFile" -ForegroundColor Green
    } catch {
        Write-Host "  ERROR: Failed to write settings.json" -ForegroundColor Red
        Write-Host "  $_" -ForegroundColor Red
    }
}

# =============================================
# Step 5: Test start
# =============================================
Write-Host "[5/5] Testing startup ..." -ForegroundColor Yellow

try {
    $proc = Start-Process -FilePath $python -ArgumentList "`"$TargetDir\startup.py`"" -NoNewWindow -Wait -PassThru
    if ($proc.ExitCode -eq 0) {
        Write-Host "  OK: Daemon started" -ForegroundColor Green
    }
} catch {
    Write-Host "  NOTE: Startup test skipped (BLE device may be out of range)" -ForegroundColor Yellow
}

# =============================================
# Done
# =============================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  CursorLight installed!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "The status light activates automatically when Claude Code starts." -ForegroundColor White
Write-Host ""
Write-Host "Manual commands:" -ForegroundColor Gray
Write-Host "  py -3 %USERPROFILE%\.cursor\hooks\cursor-light\ble_daemon.py status" -ForegroundColor Gray
Write-Host "  py -3 %USERPROFILE%\.cursor\hooks\cursor-light\ble_daemon.py send green" -ForegroundColor Gray
Write-Host "  py -3 %USERPROFILE%\.cursor\hooks\cursor-light\ble_daemon.py send alarm" -ForegroundColor Gray
Write-Host ""
Write-Host "Requirements: ESP32-C3 with CursorLight firmware + Bluetooth ON" -ForegroundColor DarkYellow
Write-Host ""
Read-Host "Press Enter to finish"
