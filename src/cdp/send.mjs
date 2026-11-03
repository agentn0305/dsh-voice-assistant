#!/usr/bin/env node
// 用法: node cdp_send.mjs "<要注入的文本>" [--no-enter]
// 通过 CDP 把文本真实键入 DSH 输入框，并回车发送。
import { cdpBaseUrl } from "./chrome.mjs"

const text = process.argv[2] || ""
const pressEnter = !process.argv.includes("--no-enter")

const list = await (await fetch(cdpBaseUrl() + "/json/list")).json()
const page = list.find(t => t.type === "page" && t.url.includes("3080"))
if (!page) { console.log(JSON.stringify({ ok: false, error: "no dsh page", cdp: cdpBaseUrl(), hint: "start Chrome with: node src/cdp/launch.mjs" })); process.exit(1) }

const ws = new WebSocket(page.webSocketDebuggerUrl)
let id = 0; const pending = new Map()
const send2 = (method, params) => { const mid = ++id; ws.send(JSON.stringify({ id: mid, method, params })); return new Promise(res => pending.set(mid, res)) }
ws.addEventListener("message", ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } })
await new Promise(r => ws.addEventListener("open", r))
await send2("Runtime.enable", {})

const evalJs = async (expression) => {
  const r = await send2("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })
  if (r.result?.exceptionDetails) return { __err: r.result.exceptionDetails.exception?.description }
  return r.result?.result?.value
}

// 1) 定位输入框（先找框：认证失败时整个页面只有一行提示，找不到框才判未认证）
// 2) 定位输入框：优先 textarea，其次 contenteditable
const found = await evalJs(`(() => {
  const cands = [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 40 && r.height > 10 && el.offsetParent !== null })
  if (!cands.length) return null
  const el = cands[cands.length - 1]
  el.focus()
  const sel = window.getSelection(); 
  if (el.isContentEditable) { const r = document.createRange(); r.selectNodeContents(el); r.collapse(false); sel.removeAllRanges(); sel.addRange(r) }
  else { el.setSelectionRange(el.value.length, el.value.length) }
  return { tag: el.tagName, editable: el.isContentEditable, placeholder: (el.placeholder || el.getAttribute('aria-label') || '').slice(0, 40) }
})()`)
if (!found) {
  const body = String(await evalJs("document.body.innerText.slice(0,300)") || "")
  if (/authentication required/i.test(body)) {
    console.log(JSON.stringify({ ok: false, error: "page not authenticated", hint: "open the token URL printed by dsh web in this window" }))
    process.exit(2)
  }
  console.log(JSON.stringify({ ok: false, error: "composer not found", hint: "page may still be loading" }))
  process.exit(3)
}

// 3) 真实键入（Input.insertText 比改 DOM 更可靠，React 能感知）
await send2("Input.insertText", { text })
await new Promise(r => setTimeout(r, 250))
const typed = await evalJs(`(() => { const a = document.activeElement; return a.isContentEditable ? a.innerText.slice(-60) : (a.value||'').slice(-60) })()`)

// 4) 回车发送
if (pressEnter) {
  for (const type of ["rawKeyDown", "char", "keyUp"]) {
    await send2("Input.dispatchKeyEvent", { type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: type === "char" ? "\r" : undefined })
    await new Promise(r => setTimeout(r, 60))
  }
}
console.log(JSON.stringify({ ok: true, composer: found, typedTail: typed, sent: pressEnter }))
ws.close()
