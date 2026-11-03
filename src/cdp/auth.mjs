#!/usr/bin/env node
// 用给定的 token URL 给 CDP 专用 Chrome 窗口完成认证
// 用法: node auth.mjs "http://127.0.0.1:3080/?token=..."
import { cdpBaseUrl } from "./chrome.mjs"

const url = process.argv[2]
if (!url) { console.log(JSON.stringify({ ok: false, error: "usage: auth.mjs <token-url>" })); process.exit(1) }
const list = await (await fetch(cdpBaseUrl() + "/json/list")).json()
const page = list.find(t => t.type === "page" && t.url.includes("3080"))
if (!page) { console.log(JSON.stringify({ ok: false, error: "no dsh page in CDP", cdp: cdpBaseUrl(), hint: "start Chrome with: node src/cdp/launch.mjs" })); process.exit(2) }
const ws = new WebSocket(page.webSocketDebuggerUrl)
let id = 0; const pending = new Map()
const send = (m, p) => { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise(r => pending.set(mid, r)) }
ws.addEventListener("message", ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } })
await new Promise(r => ws.addEventListener("open", r))
await send("Page.enable", {}); await send("Runtime.enable", {})
await send("Page.navigate", { url })
await new Promise(r => setTimeout(r, 4000))
const res = await send("Runtime.evaluate", { expression: "document.body.innerText.slice(0,200)", returnByValue: true })
const text = String(res.result?.result?.value || "")
const authed = !/authentication required/i.test(text)
console.log(JSON.stringify({ ok: authed, head: text.replace(/\s+/g, " ").slice(0, 80) }))
ws.close()
process.exit(authed ? 0 : 3)
