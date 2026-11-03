#!/usr/bin/env node
// 用法: node eval.mjs "<js表达式>" [--ws <wsUrl>]
import { cdpBaseUrl } from "./chrome.mjs"

const expr = process.argv[2]
const wsArgIdx = process.argv.indexOf("--ws")
const wsUrl = wsArgIdx > 0 ? process.argv[wsArgIdx + 1] : (await targetWs())

async function targetWs() {
  const list = await (await fetch(cdpBaseUrl() + "/json/list")).json()
  const page = list.find(t => t.type === "page" && t.url.includes("3080"))
  if (!page) throw new Error("no dsh page target in " + cdpBaseUrl() + " (run: node src/cdp/launch.mjs)")
  return page.webSocketDebuggerUrl
}

const ws = new WebSocket(wsUrl)
let id = 0
const pending = new Map()
function send(method, params) {
  const mid = ++id
  ws.send(JSON.stringify({ id: mid, method, params }))
  return new Promise((res, rej) => pending.set(mid, { res, rej }))
}
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data)
  if (m.id && pending.has(m.id)) {
    const { res } = pending.get(m.id)
    pending.delete(m.id)
    res(m)
  }
})
await new Promise(r => ws.addEventListener("open", r))
await send("Runtime.enable", {})
const out = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true })
if (out.result?.exceptionDetails) console.log("EXCEPTION:", JSON.stringify(out.result.exceptionDetails.exception?.description || out.result.exceptionDetails).slice(0, 400))
else console.log(JSON.stringify(out.result?.result?.value ?? null).slice(0, 1500))
ws.close()
