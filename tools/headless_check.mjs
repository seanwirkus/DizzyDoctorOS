// Load the chart screen in headless Chrome, run optional steps, and report console errors.
// Usage: node tools/headless_check.mjs <url> [--shot out.png] [--click "css"]... [--wait ms] [--eval "js"]
// Exit code 1 when the page logged errors or a step failed. Needs Google Chrome.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const args = process.argv.slice(2);
const url = args.shift();
if (!url) { console.error("usage: headless_check.mjs <url> [--shot out.png] [--click css]... [--type css text] [--wait ms] [--eval js] [--width w] [--height h]"); process.exit(2); }
const steps = [];
let shot = "", width = 1480, height = 940;
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--shot") shot = args[++i];
  else if (a === "--width") width = +args[++i];
  else if (a === "--height") height = +args[++i];
  else if (a === "--click") steps.push({ click: args[++i] });
  else if (a === "--type") steps.push({ type: args[++i], text: args[++i] });
  else if (a === "--wait") steps.push({ wait: +args[++i] });
  else if (a === "--eval") steps.push({ eval: args[++i] });
}
const CHROME = process.env.CHROME || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  : process.platform === "win32" ? "C:/Program Files/Google/Chrome/Application/chrome.exe" : "google-chrome");
const PORT = 9400 + Math.floor(Math.random() * 400);
const profile = mkdtempSync(path.join(tmpdir(), "ddos-headless-"));
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, "--no-first-run", "--hide-scrollbars",
  `--window-size=${width},${height}`, "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function waitFor(u, tries = 80) { for (let i = 0; i < tries; i++) { try { const r = await fetch(u); if (r.ok) return r; } catch {} await sleep(150); } throw new Error("chrome did not start"); }
class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.console = []; this.errors = [];
    ws.addEventListener("message", (m) => { const msg = JSON.parse(m.data);
      if (msg.id && this.pending.has(msg.id)) { const { res, rej } = this.pending.get(msg.id); this.pending.delete(msg.id); msg.error ? rej(new Error(msg.error.message)) : res(msg.result); }
      else if (msg.method === "Runtime.consoleAPICalled" && (msg.params.type === "error" || msg.params.type === "warning")) this.console.push(msg.params.type + ": " + msg.params.args.map(a => a.value ?? a.description ?? "").join(" "));
      else if (msg.method === "Runtime.exceptionThrown") this.errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      else if (msg.method === "Network.responseReceived" && msg.params.response.status >= 400) this.errors.push(`HTTP ${msg.params.response.status} ${msg.params.response.url}`); }); }
  send(method, params = {}) { const id = ++this.id; this.ws.send(JSON.stringify({ id, method, params })); return new Promise((res, rej) => this.pending.set(id, { res, rej })); }
  async eval(expr) { const r = await this.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || "eval failed"); return r.result.value; }
}
let code = 0;
try {
  await waitFor(`http://127.0.0.1:${PORT}/json/version`);
  const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r));
  const cdp = new Cdp(ws);
  await cdp.send("Page.enable"); await cdp.send("Runtime.enable"); await cdp.send("Network.enable");
  await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await cdp.send("Page.navigate", { url });
  await sleep(1500);
  for (const s of steps) {
    if (s.wait) await sleep(s.wait);
    else if (s.click) { const ok = await cdp.eval(`(() => { const el = document.querySelector(${JSON.stringify(s.click)}); if (!el) return false; el.click(); return true; })()`); if (!ok) { console.error("not found: " + s.click); code = 1; } await sleep(600); }
    else if (s.type) { const ok = await cdp.eval(`(() => { const el = document.querySelector(${JSON.stringify(s.type)}); if (!el) return false; el.focus(); el.value = ${JSON.stringify(s.text)}; el.dispatchEvent(new Event('input', {bubbles:true})); return true; })()`); if (!ok) { console.error("not found: " + s.type); code = 1; } await sleep(600); }
    else if (s.eval) { try { console.log("eval:", JSON.stringify(await cdp.eval(s.eval))); } catch (e) { console.error("eval failed: " + e.message); code = 1; } }
  }
  const info = await cdp.eval(`({ title: document.title, text: document.body.innerText.slice(0, 1200), width: document.documentElement.scrollWidth, overflow: document.documentElement.scrollWidth > ${width} })`);
  console.log("title:", info.title);
  if (info.overflow) { console.error("horizontal overflow: page is " + info.width + "px wide"); code = 1; }
  if (shot) { const png = await cdp.send("Page.captureScreenshot", { format: "png" }); writeFileSync(shot, Buffer.from(png.data, "base64")); console.log("screenshot:", shot); }
  if (cdp.errors.length) { console.error("page errors:\n  " + cdp.errors.join("\n  ")); code = 1; }
  if (cdp.console.length) console.log("console:\n  " + cdp.console.join("\n  "));
  console.log("text:\n" + info.text);
  ws.close();
} catch (e) { console.error("check failed:", e.message); code = 1; }
finally { chrome.kill(); await sleep(300); try { rmSync(profile, { recursive: true, force: true }); } catch {} }
process.exit(code);
