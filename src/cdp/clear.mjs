#!/usr/bin/env node
// 清空 DSH 输入框：聚焦 → Ctrl+A → Backspace（真实键盘事件，React 受控组件才认）
const list = await (await fetch(process.env.VB_CDP_URL || "http://127.0.0.1:9222/json/list")).json()
const page = list.find(t => t.type === "page" && t.url.includes("3080"))
if (!page) { console.log(JSON.stringify({ ok: false, error: "no dsh page" })); process.exit(1) }
const ws = new WebSocket(page.webSocketDebuggerUrl)
let id = 0; const pending = new Map()
const send = (m, p) => { const mid = ++id; ws.send(JSON.stringify({ id: mid, method: m, params: p })); return new Promise(r => pending.set(mid, r)) }
ws.addEventListener("message", ev => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id) } })
await new Promise(r => ws.addEventListener("open", r))
await send("Runtime.enable", {})
const evalJs = async e => (await send("Runtime.evaluate", { expression: e, returnByValue: true })).result?.result?.value
await evalJs("(() => { const c=[...document.querySelectorAll('textarea,[contenteditable=true],[role=textbox]')].filter(e=>e.offsetParent!==null); if(c.length) c[c.length-1].focus(); return c.length })()")
const key = (type, k, code, vk, mods = 0) => send("Input.dispatchKeyEvent", { type, key: k, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, modifiers: mods })
await key("rawKeyDown", "a", "KeyA", 65, 2); await key("keyUp", "a", "KeyA", 65, 2)
await new Promise(r => setTimeout(r, 120))
await key("rawKeyDown", "Backspace", "Backspace", 8); await key("keyUp", "Backspace", "Backspace", 8)
await new Promise(r => setTimeout(r, 200))
const left = String(await evalJs("(() => { const a=document.activeElement; return (a.isContentEditable?a.innerText:a.value||'').trim() })()") || "")
console.log(JSON.stringify({ ok: left.length === 0, remaining: left.slice(0, 60) }))
ws.close()
