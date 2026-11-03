#Requires -Version 5.1
<#
.SYNOPSIS
  下载 voice-assistant 需要的模型（断点续传 + 体积校验）。
.DESCRIPTION
  镜像策略：hf-mirror 优先，ghfast.top 代理 GitHub 备用，最后直连 GitHub。
  优先使用 Windows 自带的 curl.exe；没有时退回 Invoke-WebRequest（不支持续传）。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\download-models.ps1
.EXAMPLE
  .\scripts\download-models.ps1 -SkipSenseVoice
#>
[CmdletBinding()]
param(
  [string]$Root = "",
  [switch]$SkipSenseVoice,
  [string]$GhProxy = "https://ghfast.top/https://github.com"
)

$ErrorActionPreference = "Stop"
if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$Root = (Resolve-Path $Root).Path

$HF = "https://hf-mirror.com"
$GH = "https://github.com"
$KokoroRel = "thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
$SenseRepo = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
if ($env:DSH_VOICE_SENSEVOICE_DIR) {
  $SenseDir = $env:DSH_VOICE_SENSEVOICE_DIR
} else {
  $SenseDir = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".dsh\speech-to-text\sensevoice\models\sensevoice-onnx"
}

$script:Failed = 0
function Write-Say($message) { Write-Host ""; Write-Host ("==> " + $message) -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host ("  ! " + $message) -ForegroundColor Yellow }

function Get-RemoteFile {
  param(
    [Parameter(Mandatory = $true)][string]$Dest,
    [Parameter(Mandatory = $true)][long]$Size,
    [Parameter(Mandatory = $true)][string[]]$Urls
  )
  if (Test-Path $Dest) {
    $have = (Get-Item $Dest).Length
    if ($have -eq $Size) {
      Write-Host ("  OK 已存在 " + (Split-Path -Leaf $Dest) + " (" + $have + " bytes)")
      return $true
    }
    Write-Warn ((Split-Path -Leaf $Dest) + " 体积 " + $have + " != " + $Size + "，删除重下")
    Remove-Item -Force $Dest
  }
  $dir = Split-Path -Parent $Dest
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $tmp = $Dest + ".part"

  foreach ($url in $Urls) {
    Write-Host ("  -> " + $url)
    for ($attempt = 1; $attempt -le 4; $attempt++) {
      if ((Test-Path $tmp) -and ((Get-Item $tmp).Length -eq $Size)) {
        Move-Item -Force $tmp $Dest
        Write-Host ("  OK " + (Split-Path -Leaf $Dest) + " (" + $Size + " bytes, 来自上次的 .part)")
        return $true
      }
      $ok = $false
      if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -fL -C - --retry 3 --retry-delay 2 --retry-connrefused --connect-timeout 20 -m 900 -o $tmp $url
        $ok = ($LASTEXITCODE -eq 0)
      } else {
        try {
          Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
          $ok = $true
        } catch {
          Write-Warn ("下载失败: " + $_.Exception.Message)
        }
      }
      if ($ok -and (Test-Path $tmp)) {
        $got = (Get-Item $tmp).Length
        if ($got -eq $Size) {
          Move-Item -Force $tmp $Dest
          Write-Host ("  OK " + (Split-Path -Leaf $Dest) + " (" + $got + " bytes)")
          return $true
        }
        Write-Warn ("体积不符: " + $got + " != " + $Size + "（第 " + $attempt + " 次）")
        Remove-Item -Force $tmp -ErrorAction SilentlyContinue
      } else {
        Write-Warn ("第 " + $attempt + " 次失败（保留 .part 以便续传）")
      }
      Start-Sleep -Seconds 2
    }
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
  }
  Write-Warn ("全部源失败: " + (Split-Path -Leaf $Dest))
  return $false
}

Write-Say ("kokoro 模型 -> " + (Join-Path $Root "kokoro"))
$jobs = @(
  @{
    Dest = Join-Path $Root "kokoro\kokoro-v1.0.onnx"; Size = 325532387L
    Urls = @(
      "$HF/fastrtc/kokoro-onnx/resolve/main/kokoro-v1.0.onnx",
      "$GhProxy/$KokoroRel/kokoro-v1.0.onnx",
      "$GH/$KokoroRel/kokoro-v1.0.onnx"
    )
  },
  @{
    Dest = Join-Path $Root "kokoro\kokoro-v1.0.int8.onnx"; Size = 92361271L
    Urls = @(
      "$HF/speaches-ai/Kokoro-82M-v1.0-ONNX-int8/resolve/main/model.onnx",
      "$GhProxy/$KokoroRel/kokoro-v1.0.int8.onnx",
      "$GH/$KokoroRel/kokoro-v1.0.int8.onnx"
    )
  },
  @{
    Dest = Join-Path $Root "kokoro\voices-v1.0.bin"; Size = 28214398L
    Urls = @(
      "$HF/fastrtc/kokoro-onnx/resolve/main/voices-v1.0.bin",
      "$GhProxy/$KokoroRel/voices-v1.0.bin",
      "$GH/$KokoroRel/voices-v1.0.bin"
    )
  }
)
foreach ($job in $jobs) {
  if (-not (Get-RemoteFile -Dest $job.Dest -Size $job.Size -Urls $job.Urls)) { $script:Failed = 1 }
}

if (-not $SkipSenseVoice) {
  Write-Say ("SenseVoice（唤醒词/识别）-> " + $SenseDir)
  if (-not (Get-RemoteFile -Dest (Join-Path $SenseDir "model.int8.onnx") -Size 239233841L -Urls @("$HF/$SenseRepo/resolve/main/model.int8.onnx"))) { $script:Failed = 1 }
  if (-not (Get-RemoteFile -Dest (Join-Path $SenseDir "tokens.txt") -Size 315894L -Urls @("$HF/$SenseRepo/resolve/main/tokens.txt"))) { $script:Failed = 1 }
}

if ($script:Failed -ne 0) {
  Write-Host ""
  Write-Warn "有模型未下载成功。可重跑本脚本（断点续传），或换代理后重试："
  Write-Warn ("  .\scripts\download-models.ps1 -GhProxy " + $GhProxy)
  exit 1
}
Write-Say "模型就绪"
