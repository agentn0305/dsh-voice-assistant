#Requires -Version 5.1
<#
.SYNOPSIS
  dsh-voice-assistant Windows 安装器（幂等，可重复执行）。
.DESCRIPTION
  检查 Python/Node/ffmpeg，创建 .venv，安装依赖，下载模型，生成配置，
  最后打印把插件装进 DSH profile 的命令（profile 名需要你确认）。
.PARAMETER Python
  指定 Python 解释器（默认依次尝试 python / python3 / py -3.12 / py -3）。
.PARAMETER SkipModels
  跳过模型下载（模型已就绪时用）。
.PARAMETER SkipDeps
  跳过 pip install（.venv 已就绪时用）。
.PARAMETER NoUserEnv
  不写用户级环境变量，只生成 config\windows.env。
.PARAMETER InstallTools
  用 winget 自动安装 zstd（自动朗读需要解压会话日志）与 ffmpeg（可选，音频回退用 ffplay）。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param(
  [string]$Python = "",
  [switch]$SkipModels,
  [switch]$SkipDeps,
  [switch]$NoUserEnv,
  [switch]$InstallTools
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Root = (Resolve-Path $Root).Path

$script:Warnings = @()
function Say  { param([string]$m) Write-Host ""; Write-Host ("==> " + $m) -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host ("  OK " + $m) -ForegroundColor Green }
function Warn { param([string]$m) Write-Host ("  ! " + $m) -ForegroundColor Yellow; $script:Warnings += $m }
function Die  { param([string]$m) Write-Host ("错误: " + $m) -ForegroundColor Red; exit 1 }

function Get-PythonInfo {
  param([string]$Exe, [string[]]$PrefixArgs = @())
  try {
    $out = & $Exe @PrefixArgs -c "import sys,platform; print('%d.%d.%d|%d|%s' % (sys.version_info[0], sys.version_info[1], sys.version_info[2], sys.maxsize, platform.architecture()[0]))" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $line = ($out | Select-Object -First 1).ToString().Trim()
    $parts = $line.Split("|")
    if ($parts.Count -lt 3) { return $null }
    $v = $parts[0].Split(".")
    return [pscustomobject]@{
      Exe     = $Exe
      Args    = $PrefixArgs
      Version = $parts[0]
      Major   = [int]$v[0]
      Minor   = [int]$v[1]
      MaxSize = [long]$parts[1]
      Bits    = $parts[2]
    }
  } catch {
    return $null
  }
}

Say "0/7 项目检查"
foreach ($rel in @("src\voice_loop.py", "src\tts_server.py", "plugin\package.json", "scripts\download-models.ps1")) {
  if (-not (Test-Path (Join-Path $Root $rel))) { Die ("不是完整的仓库（缺少 " + $rel + "）: " + $Root) }
}
Ok ("项目根: " + $Root)

Say "1/7 检查 Python >= 3.11 (64-bit)"
$candidates = @()
if ($Python) { $candidates += @{ Exe = $Python; Args = @() } }
$candidates += @{ Exe = "python"; Args = @() }
$candidates += @{ Exe = "python3"; Args = @() }
$candidates += @{ Exe = "py"; Args = @("-3.12") }
$candidates += @{ Exe = "py"; Args = @("-3") }
$chosen = $null
foreach ($c in $candidates) {
  if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
  $info = Get-PythonInfo -Exe $c.Exe -PrefixArgs $c.Args
  if ($null -eq $info) { continue }
  if ($info.Major -lt 3 -or ($info.Major -eq 3 -and $info.Minor -lt 11)) {
    Warn ("跳过 " + $c.Exe + " " + ($c.Args -join " ") + "：版本 " + $info.Version + " < 3.11")
    continue
  }
  if ($info.MaxSize -le 2147483647) {
    Warn ("跳过 " + $c.Exe + "：32 位 Python，onnxruntime/sherpa-onnx 需要 64 位")
    continue
  }
  $chosen = $info
  break
}
if ($null -eq $chosen) {
  Die "未找到 Python >= 3.11 (64-bit)。安装: winget install --id Python.Python.3.12 -e ；然后重开终端。"
}
Ok ("Python " + $chosen.Version + " (" + $chosen.Bits + "): " + $chosen.Exe + " " + ($chosen.Args -join " "))

Say "2/7 创建 .venv"
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (Test-Path $VenvPy) {
  Ok (".venv 已存在: " + $VenvPy)
} else {
  if (Test-Path $Venv) {
    Warn "检测到非 Windows 的 .venv（可能来自 Linux 仓库），重建"
    Remove-Item -Recurse -Force $Venv
  }
  & $chosen.Exe @($chosen.Args) -m venv $Venv
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPy)) { Die ("创建 venv 失败: " + $Venv) }
  Ok (".venv 已创建: " + $VenvPy)
}

Say "3/7 安装 Python 依赖"
if ($SkipDeps) {
  Warn "已跳过 pip install"
} else {
  $deps = @(
    "edge-tts>=7.3",
    "sherpa-onnx>=2.0",
    "kokoro-onnx>=0.6",
    "misaki[zh]>=0.9",
    "numpy>=2.0",
    "sounddevice>=0.5",
    "miniaudio>=1.59",
    "pypinyin>=0.51"
  )
  & $VenvPy -m pip install --disable-pip-version-check --upgrade pip
  if ($LASTEXITCODE -ne 0) { Warn "pip 自升级失败，继续用现有 pip" }
  $indexes = @("https://pypi.tuna.tsinghua.edu.cn/simple", "https://pypi.org/simple")
  $installed = $false
  foreach ($idx in $indexes) {
    Write-Host ("  pip install -i " + $idx)
    & $VenvPy -m pip install --disable-pip-version-check -i $idx @deps
    if ($LASTEXITCODE -eq 0) { $installed = $true; break }
    Warn ("镜像失败，换下一个: " + $idx)
  }
  if (-not $installed) { Die "Python 依赖安装失败（检查网络/代理后重跑）" }
  Ok "Python 依赖已安装"
}

Say "4/7 检查音频工具 / node / Chrome"
$needFfplay = -not (Get-Command ffplay -ErrorAction SilentlyContinue)
$needZstd = -not (Get-Command zstd -ErrorAction SilentlyContinue)
$ffplayJustInstalled = $false
$zstdJustInstalled = $false
if ($InstallTools -and ($needFfplay -or $needZstd)) {
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    if ($needFfplay) {
      Write-Host "  winget install --id Gyan.FFmpeg -e ..."
      & winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
      if ($LASTEXITCODE -eq 0) { $ffplayJustInstalled = $true; Warn "ffmpeg 已安装；请重开终端让 PATH 生效" } else { Warn "winget 安装 ffmpeg 失败" }
    }
    if ($needZstd) {
      Write-Host "  winget install --id Meta.Zstandard -e ..."
      & winget install --id Meta.Zstandard -e --accept-source-agreements --accept-package-agreements
      if ($LASTEXITCODE -eq 0) { $zstdJustInstalled = $true; Warn "zstd 已安装；请重开终端让 PATH 生效" } else { Warn "winget 安装 zstd 失败" }
    }
  } else {
    Warn "未找到 winget，无法自动安装 ffmpeg / zstd"
  }
}
if (Get-Command ffplay -ErrorAction SilentlyContinue) {
  Ok "ffplay 可用（音频层的可选回退）"
} elseif ($ffplayJustInstalled) {
  Ok "ffplay 已通过 winget 安装（重开终端后生效）"
} else {
  Write-Host "  - 未找到 ffplay：音频层优先用 sounddevice/miniaudio，ffplay 只是回退，可不装"
}
if (Get-Command zstd -ErrorAction SilentlyContinue) {
  Ok "zstd 可用"
} elseif ($zstdJustInstalled) {
  Ok "zstd 已通过 winget 安装（重开终端后生效）"
} else {
  Warn "未找到 zstd（自动朗读解压会话日志需要）。执行: winget install --id Meta.Zstandard -e ，然后重开终端；或加 -InstallTools"
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Warn "未找到 node。注入桥需要 Node 22+（或设 DSH_VOICE_NODE 指向 node.exe）；本安装器的 Chrome 探测会跳过。"
} else {
  $nv = (& node -v).ToString().Trim()
  $nodeMajor = 0
  if ($nv -match "^v(\d+)") { $nodeMajor = [int]$Matches[1] }
  if ($nodeMajor -ge 22) { Ok ("node " + $nv) } else { Warn ("node " + $nv + " < 22，DSH 可能无法运行") }
}

$chrome = ""
if ($node) {
  $LASTEXITCODE = 0
  $probeRaw = & node (Join-Path $Root "src\cdp\launch.mjs") --print-path 2>$null
  if ($LASTEXITCODE -eq 0 -and $probeRaw) {
    try {
      $probe = (($probeRaw | Out-String).Trim() | ConvertFrom-Json)
      if ($probe.ok -and $probe.chrome) {
        $chrome = [string]$probe.chrome
        Ok ("Chrome: " + $chrome)
      }
    } catch {
      Warn "无法解析 Chrome 探测输出"
    }
  }
}
if (-not $chrome) {
  Warn "未找到 Google Chrome。安装后重跑，或设置 DSH_VOICE_CHROME=<path>\chrome.exe（注入通道需要）"
}

Say "5/7 下载模型"
if ($SkipModels) {
  Warn "已跳过模型下载"
} else {
  $LASTEXITCODE = 0
  & (Join-Path $Root "scripts\download-models.ps1") -Root $Root
  if ($LASTEXITCODE -ne 0) { Die "模型下载失败；可重跑 scripts\download-models.ps1（支持断点续传）" }
  Ok "模型就绪"
}

Say "6/7 生成配置"
$SenseDir = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".dsh\speech-to-text\sensevoice\models\sensevoice-onnx"
$cfg = [ordered]@{
  DSH_VOICE_ROOT           = $Root
  DSH_VOICE_PYTHON         = $VenvPy
  DSH_VOICE_CHROME         = $chrome
  DSH_VOICE_NODE           = $(if ($node) { $node.Source } else { "" })
  DSH_VOICE_CDP_URL        = "http://127.0.0.1:9222"
  DSH_VOICE_SENSEVOICE_DIR = $SenseDir
}
$cfgPath = Join-Path $Root "config\windows.env"
$lines = @("# generated by install.ps1 — 供人工查看；插件实际读取同名用户环境变量")
foreach ($kv in $cfg.GetEnumerator()) { $lines += ($kv.Key + "=" + $kv.Value) }
Set-Content -Path $cfgPath -Value $lines -Encoding UTF8
Ok ("已写入 " + $cfgPath)

if ($NoUserEnv) {
  Warn "已跳过用户环境变量（-NoUserEnv）；插件将退回自动探测"
} else {
  foreach ($kv in $cfg.GetEnumerator()) {
    if (-not $kv.Value) { continue }
    [Environment]::SetEnvironmentVariable($kv.Key, [string]$kv.Value, "User")
    Ok ("用户环境变量 " + $kv.Key)
  }
  Warn "用户环境变量只对之后新开的进程生效；正在运行的 DSH 需要重启才能读到"
}

Say "7/7 安装到 DSH profile（profile 名需要你确认）"
$profilesDir = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".dsh\profiles"
if (Test-Path $profilesDir) {
  $names = @(Get-ChildItem $profilesDir -Directory | Where-Object { $_.Name -ne "node_modules" -and -not $_.Name.StartsWith(".") } | Select-Object -ExpandProperty Name)
  if ($names.Count -gt 0) { Write-Host ("  现有 profile: " + ($names -join ", ")) } else { Write-Host "  现有 profile: （空）" }
} else {
  Write-Host ("  尚未创建 profile 目录: " + $profilesDir)
}
$linkPath = (Join-Path $Root "plugin").Replace("\", "/")
Write-Host ""
Write-Host "  第 1 步：确认真实 profile 名（Desktop 与 web 可能不同）"
Write-Host '    Get-ChildItem $HOME\.dsh\profiles -Directory | Where-Object { $_.Name -ne "node_modules" } | Select-Object -ExpandProperty Name'
Write-Host "  第 2 步：安装（把 <name> 换成上面列出的名字）"
Write-Host ("    dsh plugin --profile <name> add -w link:" + $linkPath)
Write-Host "  第 3 步：校验配置树（不会启动服务）"
Write-Host "    dsh --profile <name> --dump-config | Select-String voice-assistant"
Write-Host "  第 4 步：启动 DSH 宿主，插件会拉起 tts/autoread/bridge/loop 四个子进程"
Write-Host "           端口被其它实例占用时，插件保持被动（不会抢端口）"
Write-Host ""
Write-Host "  验证（新开一个 PowerShell）:"
Write-Host "    curl.exe -s http://127.0.0.1:8765/health"
Write-Host "    curl.exe -s http://127.0.0.1:8766/health"
Write-Host "    Get-Content $env:TEMP\dsh-voice-assistant\tts.log -Tail 20"
Write-Host ""
Write-Host "  说明：出声只依赖 Python 包 sounddevice/miniaudio；Windows 不需要 ffplay/ffmpeg。"
Write-Host "        zstd 只给 autoread 解压会话日志用，ffmpeg/ffplay 只是 POSIX 的流式回退。"

if ($script:Warnings.Count -gt 0) {
  Write-Host ""
  Write-Host "需人工处理:" -ForegroundColor Yellow
  foreach ($w in $script:Warnings) { Write-Host ("  - " + $w) -ForegroundColor Yellow }
}
Say "完成。下一步：按上面第 1-4 步安装插件并启动宿主。"
