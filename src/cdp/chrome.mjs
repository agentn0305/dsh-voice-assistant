#!/usr/bin/env node
// Chrome 定位与 CDP 端点解析：Windows / macOS / Linux 共用。
// 路径优先级：DSH_VOICE_CHROME > 各平台默认安装位置 > PATH。
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import { homedir } from "node:os"
import { delimiter, join } from "node:path"

export const DEFAULT_CDP_PORT = 9222
export const DSH_URL = process.env.DSH_VOICE_DSH_URL || "http://127.0.0.1:3080"

/** CDP HTTP 端点（不带尾斜杠）；兼容旧的 VB_CDP_URL。 */
export function cdpBaseUrl(env = process.env) {
  const raw = env.DSH_VOICE_CDP_URL || env.VB_CDP_URL
  if (raw && raw.trim()) return raw.trim().replace(/\/+$/, "")
  return "http://127.0.0.1:" + DEFAULT_CDP_PORT
}

export function chromeUserDataDir(env = process.env) {
  return env.DSH_VOICE_CHROME_PROFILE || join(homedir(), ".dsh-voice-chrome")
}

function pathCandidates(env, platform) {
  const out = []
  if (platform === "win32") {
    const programFiles = env.PROGRAMFILES || "C:\\Program Files"
    const programFilesX86 = env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)"
    const localAppData = env.LOCALAPPDATA || join(homedir(), "AppData", "Local")
    out.push(
      join(programFiles, "Google", "Chrome", "Application", "chrome.exe"),
      join(programFilesX86, "Google", "Chrome", "Application", "chrome.exe"),
      join(localAppData, "Google", "Chrome", "Application", "chrome.exe"),
    )
  } else if (platform === "darwin") {
    out.push(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      join(homedir(), "Applications", "Google Chrome.app", "Contents", "MacOS", "Google Chrome"),
    )
  } else {
    out.push(
      "/usr/bin/google-chrome",
      "/usr/bin/google-chrome-stable",
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser",
      "/snap/bin/chromium",
    )
  }
  const names = platform === "win32" ? ["chrome.exe"] : ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
  for (const dir of String(env.PATH || "").split(delimiter)) {
    if (!dir) continue
    for (const name of names) out.push(join(dir, name))
  }
  return out
}

export function chromeCandidates(env = process.env, platform = process.platform) {
  const explicit = env.DSH_VOICE_CHROME && env.DSH_VOICE_CHROME.trim()
  return explicit ? [explicit, ...pathCandidates(env, platform)] : pathCandidates(env, platform)
}

/** 返回可执行文件绝对路径；找不到时抛出带排查清单的错误。 */
export function resolveChromePath(env = process.env, platform = process.platform) {
  const explicit = env.DSH_VOICE_CHROME && env.DSH_VOICE_CHROME.trim()
  if (explicit) {
    if (existsSync(explicit)) return explicit
    throw new Error("DSH_VOICE_CHROME points to a missing file: " + explicit)
  }
  const candidates = chromeCandidates(env, platform)
  const found = candidates.find((p) => p && existsSync(p))
  if (found) return found
  throw new Error(
    "Chrome not found. Set DSH_VOICE_CHROME to chrome.exe, or install Google Chrome. Tried:\n  " +
    candidates.join("\n  "),
  )
}

export async function cdpAlive(base = cdpBaseUrl(), timeoutMs = 1500) {
  try {
    const res = await fetch(base + "/json/version", { signal: AbortSignal.timeout(timeoutMs) })
    return res.ok
  } catch {
    return false
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

/** 启动专用 Chrome（独立 user-data-dir），等待 CDP 就绪。 */
export async function launchChrome(options = {}) {
  const port = options.port ?? DEFAULT_CDP_PORT
  const base = "http://127.0.0.1:" + port
  const url = options.url || DSH_URL
  const userDataDir = options.userDataDir || chromeUserDataDir()
  if (await cdpAlive(base)) return { ok: true, alreadyRunning: true, port, userDataDir, url }

  const chrome = resolveChromePath()
  const child = spawn(chrome, [
    "--remote-debugging-port=" + port,
    "--user-data-dir=" + userDataDir,
    "--no-first-run",
    "--no-default-browser-check",
    "--new-window",
    url,
  ], { detached: true, stdio: "ignore", windowsHide: false })
  child.once("error", () => {})
  child.unref()

  for (let i = 0; i < 30; i++) {
    if (await cdpAlive(base, 1000)) return { ok: true, chrome, port, userDataDir, url }
    await sleep(500)
  }
  return { ok: false, chrome, port, userDataDir, url, error: "CDP did not become ready on " + base + "/json/version" }
}
