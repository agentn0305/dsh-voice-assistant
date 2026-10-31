import { spawn } from "node:child_process"
import { createConnection } from "node:net"
import { createWriteStream, existsSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export const name = "voice-assistant"
export const inject = []

/** 子服务监听的端口；用于避免与 systemd 版本抢端口。 */
const PORT = { tts: 8765, bridge: 8766 }

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

const SCRIPT = {
  tts: "src/tts_server.py",
  autoread: "src/auto_read.py",
  bridge: "src/voice_bridge.py",
  loop: "src/voice_loop.py",
}

function serviceEnv(name, config) {
  const base = { ...process.env }
  if (name === "tts") {
    base.TTS_ENGINE = config.ttsEngine ?? "edge"
    base.TTS_VOICE = config.ttsVoice ?? "zh-CN-XiaoxiaoNeural"
    base.TTS_KOKORO_VOICE = config.kokoroVoice ?? "zf_xiaoyi"
  }
  if (name === "loop") {
    base.VL_CMD_URL = config.cmdUrl ?? "http://127.0.0.1:8766/inject"
    base.VL_WAKE = config.wakeWords ?? "小艺,小艺小艺,你好小艺,小艺助手"
  }
  return base
}

export function apply(ctx, config = {}) {
  const root = config.root ?? join(homedir(), "project/test/voice-assistant")
  const python = config.python ?? join(root, ".venv/bin/python")
  const logDir = config.logDir ?? join(homedir(), ".dsh/logs/voice-assistant")
  const wanted = config.services ?? ["tts", "autoread", "bridge"]
  const log = (m) => { try { ctx.logger?.info?.(m) } catch {} }

  if (!existsSync(python)) {
    try { ctx.logger?.warn?.("[voice-assistant] missing python: " + python) } catch {}
    return
  }
  mkdirSync(logDir, { recursive: true })

  const children = new Map()

  const start = (key) => {
    const script = join(root, SCRIPT[key])
    if (!existsSync(script)) { log("[voice-assistant] missing script " + script); return }
    const out = createWriteStream(join(logDir, key + ".log"), { flags: "a" })
    const child = spawn(python, [script], { cwd: root, env: serviceEnv(key, config) })
    child.stdout.pipe(out)
    child.stderr.pipe(out)
    child.on("exit", (code) => { children.delete(key); log("[voice-assistant] " + key + " exited " + code) })
    children.set(key, child)
    log("[voice-assistant] " + key + " started pid=" + child.pid)
  }

  const stopAll = () => {
    for (const [key, child] of children) {
      try { child.kill("SIGTERM") } catch {}
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
      if (PORT.tts && (await portBusy(PORT.tts))) {
        log("[voice-assistant] tts port busy -> whole group managed externally, plugin stays passive")
        return
      }
      for (const key of order) {
        if (cancelled) return
        const port = PORT[key]
        if (port && (await portBusy(port))) {
          log("[voice-assistant] port " + port + " busy, skip " + key)
          continue
        }
        start(key)
      }
    })()
    return () => { cancelled = true; stopAll() }
  }, "voice-assistant: supervise child services")
}
