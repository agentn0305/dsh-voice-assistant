#!/usr/bin/env node
// 启动（或复用）CDP 专用 Chrome。
// 用法: node launch.mjs [--print-path] [--port 9222] [--url http://127.0.0.1:3080]
import { chromeCandidates, launchChrome, resolveChromePath } from "./chrome.mjs"

const argv = process.argv.slice(2)
const arg = (name, fallback) => {
  const i = argv.indexOf(name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}
const json = (obj, code = 0) => { console.log(JSON.stringify(obj)); process.exit(code) }

if (argv.includes("--print-path")) {
  try {
    json({ ok: true, chrome: resolveChromePath() })
  } catch (exc) {
    json({ ok: false, error: String(exc.message || exc), tried: chromeCandidates() }, 1)
  }
}

const port = Number(arg("--port", "9222"))
const url = arg("--url", "http://127.0.0.1:3080")
try {
  const res = await launchChrome({ port, url })
  json(res, res.ok ? 0 : 1)
} catch (exc) {
  json({ ok: false, error: String(exc.message || exc), tried: chromeCandidates() }, 1)
}
