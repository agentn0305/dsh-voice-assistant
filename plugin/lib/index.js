import { spawn } from "node:child_process"
import { createConnection } from "node:net"
import { createWriteStream, existsSync, mkdirSync } from "node:fs"
import { tmpdir } from "node:os"
import { basename, dirname, isAbsolute, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

export const name = "voice-assistant"
export const inject = []

/** plugin/lib/index.js -> <project root>; works for link: installs on every OS. */
const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..")

/** 子服务监听的端口；用于避免与 systemd 版本抢端口。 */
const PORT = { tts: 8765, bridge: 8766, loop: 8767 }

const SCRIPT = {
  tts: "src/tts_server.py",
  autoread: "src/auto_read.py",
  bridge: "src/voice_bridge.py",
  loop: "src/voice_loop.py",
}

const DEFAULT_SERVICES = ["tts", "autoread", "bridge", "loop"]

function portBusy(port) {
  return new Promise((resolve) => {
    const sock = createConnection({ host: "127.0.0.1", port })
    const done = (v) => { try { sock.destroy() } catch {} ; resolve(v) }
    sock.setTimeout(500)
    sock.once("connect", () => done(true))
    sock.once("timeout", () => done(false))
    sock.once("error", () => done(false))
  })
}

function nonEmpty(v) {
  return typeof v === "string" && v.trim() !== ""
}

/** 项目根：显式配置 > DSH_VOICE_ROOT > 插件自身所在仓库。 */
export function runtimeRoot(config = {}, env = process.env) {
  if (nonEmpty(config.root)) return resolve(config.root)
  if (nonEmpty(env.DSH_VOICE_ROOT)) return resolve(env.DSH_VOICE_ROOT)
  return PROJECT_ROOT
}

/** 解释器：显式配置 > DSH_VOICE_PYTHON > 仓库内 .venv（Windows 走 Scripts/）。 */
export function resolvePython(root, config = {}, env = process.env, platform = process.platform) {
  const explicit = nonEmpty(config.python) ? config.python : (nonEmpty(env.DSH_VOICE_PYTHON) ? env.DSH_VOICE_PYTHON : "")
  if (explicit) {
    if (isAbsolute(explicit)) return explicit
    return explicit.includes("/") || explicit.includes("\\") ? resolve(root, explicit) : explicit
  }
  const candidates = platform === "win32"
    ? [join(root, ".venv", "Scripts", "python.exe")]
    : [join(root, ".venv", "bin", "python3"), join(root, ".venv", "bin", "python")]
  return candidates.find(existsSync) ?? candidates[0]
}

/** 注入桥用的 node：显式配置 > DSH_VOICE_NODE > 宿主自身（确实是 node 时）> PATH 里的 node。 */
export function resolveNode(config = {}, env = process.env, execPath = process.execPath) {
  const explicit = nonEmpty(config.node) ? config.node : (nonEmpty(env.DSH_VOICE_NODE) ? env.DSH_VOICE_NODE : "")
  if (explicit) return explicit
  const base = basename(execPath).toLowerCase()
  return base === "node" || base === "node.exe" ? execPath : "node"
}

function serviceEnv(key, config, nodeExecPath) {
  const base = {
    ...process.env,
    // Python 子进程在中文 Windows 上默认用 cp936 写管道，日志会乱码/抛异常。
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    PYTHONUNBUFFERED: "1",
  }
  if (nonEmpty(config.inputDevice)) base.VL_INPUT_DEVICE = config.inputDevice
  if (nonEmpty(config.outputDevice)) base.VL_OUTPUT_DEVICE = config.outputDevice
  if (key === "tts") {
    base.TTS_ENGINE = config.ttsEngine ?? "edge"
    base.TTS_VOICE = config.ttsVoice ?? "zh-CN-XiaoxiaoNeural"
    base.TTS_KOKORO_VOICE = config.kokoroVoice ?? "zf_xiaoyi"
    if (nonEmpty(config.modelDir)) {
      base.TTS_KOKORO_MODEL = join(config.modelDir, "kokoro-v1.0.onnx")
      base.TTS_KOKORO_VOICES = join(config.modelDir, "voices-v1.0.bin")
    }
  }
  if (key === "autoread") {
    base.AR_TTS = config.ttsUrl ?? "http://127.0.0.1:8765/speak"
    if (nonEmpty(config.sessionsDir)) base.AR_SESSIONS = config.sessionsDir
    if (nonEmpty(config.workspace)) base.AR_WORKSPACE = config.workspace
  }
  if (key === "bridge") {
    // 宿主自身是 node 时直接复用；Electron 宿主（dsh.exe）下退回 PATH，必要时用 config.node/DSH_VOICE_NODE。
    base.VB_NODE = resolveNode(config, process.env, nodeExecPath)
    if (nonEmpty(config.cdpScript)) base.VB_CDP_SCRIPT = config.cdpScript
  }
  if (key === "loop") {
    base.VL_CMD_URL = config.cmdUrl ?? "http://127.0.0.1:8766/inject"
    base.VL_WAKE = config.wakeWords ?? "小艺,小艺小艺,你好小艺,小艺助手"
    if (nonEmpty(config.sensevoiceDir)) {
      base.VL_MODEL = join(config.sensevoiceDir, "model.int8.onnx")
      base.VL_TOKENS = join(config.sensevoiceDir, "tokens.txt")
    }
  }
  return base
}

export function apply(ctx, config = {}) {
  const root = runtimeRoot(config)
  const python = resolvePython(root, config)
  const logDir = nonEmpty(config.logDir) ? config.logDir : join(tmpdir(), "dsh-voice-assistant")
  const portOf = (value, fallback) => {
    const n = Number(value)
    return Number.isInteger(n) && n > 0 && n < 65536 ? n : fallback
  }
  const lockPort = config.lockPort !== undefined && config.lockPort !== null && config.lockPort !== "" ? config.lockPort : process.env.VL_LOCK_PORT
  const ports = { tts: portOf(config.ttsPort, PORT.tts), bridge: portOf(config.bridgePort, PORT.bridge), loop: portOf(lockPort, PORT.loop) }
  const wanted = Array.isArray(config.services) && config.services.length ? config.services : DEFAULT_SERVICES
  const log = (m) => { try { ctx.logger?.info?.(m) } catch {} }
  const warn = (m) => { try { ctx.logger?.warn?.(m) } catch {} }

  if (!existsSync(python)) {
    warn("[voice-assistant] missing python: " + python + " (set DSH_VOICE_PYTHON or run install.ps1/install.sh)")
    return
  }
  try { mkdirSync(logDir, { recursive: true }) } catch (exc) { warn("[voice-assistant] cannot create log dir " + logDir + ": " + exc) }

  const children = new Map()

  const start = (key) => {
    const script = join(root, SCRIPT[key])
    if (!existsSync(script)) { warn("[voice-assistant] missing script " + script); return }
    const out = createWriteStream(join(logDir, key + ".log"), { flags: "a" })
    out.on("error", (exc) => warn("[voice-assistant] log stream " + key + ": " + exc))
    const env = serviceEnv(key, config, process.execPath)
    if (key === "loop") env.VL_LOCK_PORT = String(ports.loop)
    const child = spawn(python, [script], {
      cwd: root,
      env,
      shell: false,          // 绝不经过 shell：路径里有空格也不会被拆开
      windowsHide: true,     // Windows 上不弹黑窗
      stdio: ["ignore", "pipe", "pipe"],
    })
    child.stdout.pipe(out)
    child.stderr.pipe(out)
    child.once("error", (exc) => {
      children.delete(key)
      warn("[voice-assistant] " + key + " failed to start: " + exc)
      try { out.end() } catch {}
    })
    child.once("exit", (code, signal) => {
      children.delete(key)
      log("[voice-assistant] " + key + " exited code=" + code + " signal=" + signal)
      try { out.end() } catch {}
    })
    children.set(key, child)
    log("[voice-assistant] " + key + " started pid=" + child.pid)
  }

  const stopAll = () => {
    for (const [key, child] of children) {
      try { child.kill() } catch {}
      log("[voice-assistant] " + key + " stopped")
    }
    children.clear()
  }

  ctx.effect(() => {
    const order = [...wanted].filter((k) => SCRIPT[k]).sort((a, b) => (a === "tts" ? -1 : b === "tts" ? 1 : 0))
    let cancelled = false
    ;(async () => {
      // TTS 是这一组的锚点：它被外部托管时，同组的 autoread 也必须跳过，
      // 否则会出现"两份 autoread 同时朗读"的重复播报。
      if (await portBusy(ports.tts)) {
        log("[voice-assistant] tts port " + ports.tts + " busy -> whole group managed externally, plugin stays passive")
        return
      }
      for (const key of order) {
        if (cancelled) return
        const port = ports[key]
        if (port && (await portBusy(port))) {
          if (key === "loop") log("loop already running externally (lock port " + port + " busy), skipping")
          else log("[voice-assistant] port " + port + " busy, skip " + key)
          continue
        }
        start(key)
      }
    })()
    return () => { cancelled = true; stopAll() }
  }, "voice-assistant: supervise child services")
}
