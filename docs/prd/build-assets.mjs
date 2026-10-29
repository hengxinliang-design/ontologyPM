// Rebuild PRD assets with the local Google Chrome (headless, via the DevTools protocol).
//   node docs/prd/build-assets.mjs diagrams      render docs/prd/diagrams/*.mmd to images/diagram-*.png
//   node docs/prd/build-assets.mjs screenshots   capture page screenshots from the running preview
// Screenshots expect the frontend on http://localhost:5174 and a backend started with TSG_PM_AUTH_ENABLED=false.
import { spawn } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const IMAGES = join(HERE, "images");
const CHROME = process.env.CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const APP = process.env.PRD_APP_URL ?? "http://localhost:5174/";
const MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js";
const W = 1440, H = 900, SCALE = 1.5;
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function openChrome() {
  const port = 9300 + Math.floor(Math.random() * 500);
  const profile = mkdtempSync(join(tmpdir(), "prd-chrome-"));
  const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    "--no-first-run", "--hide-scrollbars", "--lang=zh-CN", "about:blank"], { stdio: "ignore" });
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t => t.type === "page"); } catch { await sleep(200); }
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener("open", r, { once: true }));
  let seq = 0; const pending = new Map();
  ws.addEventListener("message", e => { const m = JSON.parse(e.data); pending.get(m.id)?.(m); pending.delete(m.id); });
  const send = (method, params = {}) => new Promise((res, rej) => {
    const id = ++seq;
    pending.set(id, m => m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result));
    ws.send(JSON.stringify({ id, method, params }));
  });
  const run = async expr => {
    const { result, exceptionDetails } = await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (exceptionDetails) throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text);
    return result.value;
  };
  const size = height => send("Emulation.setDeviceMetricsOverride", { width: W, height, deviceScaleFactor: SCALE, mobile: false });
  await size(H);
  await send("Page.enable");
  return { send, run, size, close: () => { ws.close(); chrome.kill(); } };
}

async function diagrams() {
  const cdp = await openChrome();
  // Download Mermaid in Node (honours NODE_USE_ENV_PROXY=1 behind a proxy) and inject it, so headless
  // Chrome needs no network access of its own.
  const response = await fetch(MERMAID);
  if (!response.ok) throw new Error(`cannot download ${MERMAID}: HTTP ${response.status}`);
  const mermaidSource = await response.text();
  const html = `<html><body style="margin:0;background:#fff"><div id="out" style="display:inline-block;padding:24px"></div></body></html>`;
  await cdp.send("Page.navigate", { url: "data:text/html;charset=utf-8," + encodeURIComponent(html) });
  await sleep(500);
  await cdp.send("Runtime.evaluate", { expression: mermaidSource });
  await cdp.run(`mermaid.initialize({ startOnLoad: false, theme: "base", flowchart: { htmlLabels: true, curve: "basis", padding: 14 },
    themeVariables: { fontFamily: '"PingFang SC","Microsoft YaHei",sans-serif', fontSize: "15px",
      primaryColor: "#eef4ff", primaryBorderColor: "#2a5bb8", primaryTextColor: "#10213d", lineColor: "#5b6f8f",
      secondaryColor: "#fff7e8", tertiaryColor: "#f6f8fc", clusterBkg: "#f6f8fc", clusterBorder: "#c9d6e8",
      actorBkg: "#eef4ff", actorBorder: "#2a5bb8", noteBkgColor: "#fff7e8", noteBorderColor: "#e0a12f" } });`);
  const dir = join(HERE, "diagrams");
  for (const file of readdirSync(dir).filter(f => f.endsWith(".mmd")).sort()) {
    const source = readFileSync(join(dir, file), "utf8");
    const box = await cdp.run(`const { svg } = await mermaid.render("d" + Date.now(), ${JSON.stringify(source)});
      const out = document.getElementById("out"); out.innerHTML = svg;
      const s = out.querySelector("svg"); const vb = s.viewBox.baseVal;
      s.style.maxWidth = "none"; s.setAttribute("width", vb.width); s.setAttribute("height", vb.height);
      await new Promise(r => setTimeout(r, 300));
      const b = out.getBoundingClientRect(); return { width: Math.ceil(b.width), height: Math.ceil(b.height) };`);
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: Math.max(box.width, 200), height: Math.max(box.height, 200), deviceScaleFactor: 2, mobile: false });
    await sleep(200);
    const { data } = await cdp.send("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: box.width, height: box.height, scale: 1 } });
    const name = `diagram-${basename(file, ".mmd")}.png`;
    writeFileSync(join(IMAGES, name), Buffer.from(data, "base64"));
    console.log("rendered", name, `${box.width}x${box.height}`);
  }
  cdp.close();
}

async function screenshots() {
  const cdp = await openChrome();
  await cdp.send("Page.navigate", { url: APP });
  await sleep(2000);
  // The SPA only re-checks the session when a cached user exists; with auth disabled /auth/me answers directly.
  await cdp.run(`const me = await (await fetch("/api/v1/auth/me")).json(); localStorage.setItem("tsg-pm-session-user", JSON.stringify(me.data)); location.reload();`).catch(() => {});
  await sleep(4000);

  const nav = label => cdp.run(`[...document.querySelectorAll("button.nav-item")].find(b => b.innerText.includes(${JSON.stringify(label)})).click();
    await new Promise(r => setTimeout(r, 2500)); window.scrollTo(0, 0);`);
  const click = (selector, text) => cdp.run(`const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find(e => ${text ? `e.innerText.includes(${JSON.stringify(text)})` : "true"});
    if (!el) throw new Error("missing ${selector} ${text ?? ""}"); el.click(); await new Promise(r => setTimeout(r, 1800));`);
  // Full-page shots grow the viewport to the page height so the fixed sidebar spans the image,
  // and hide the floating assistant so it does not land mid-page.
  async function shot(name, { full = false, maxHeight = 3600 } = {}) {
    if (full) {
      await cdp.run(`document.querySelector(".pm-assistant")?.style.setProperty("display", "none");`);
      await cdp.size(Math.max(H, await cdp.run(`return Math.min(document.documentElement.scrollHeight, ${maxHeight});`)));
      await sleep(800);
    }
    const { data } = await cdp.send("Page.captureScreenshot", { format: "jpeg", quality: 82 });
    writeFileSync(join(IMAGES, `${name}.jpg`), Buffer.from(data, "base64"));
    if (full) {
      await cdp.size(H);
      await cdp.run(`document.querySelector(".pm-assistant")?.style.removeProperty("display");`);
      await sleep(300);
    }
    console.log("saved", name);
  }

  await nav("项目管理"); await shot("01-projects", { full: true, maxHeight: 1400 });
  await click("tr.clickable"); await shot("02-project-drawer");
  await cdp.run(`document.querySelector(".project-drawer")?.scrollTo(0, 1100); await new Promise(r => setTimeout(r, 500));`); await shot("03-project-drawer-plan");
  await click(".drawer-close");
  await nav("投入成本分析"); await shot("04-investment", { full: true, maxHeight: 4200 });
  await nav("部门经营"); await sleep(1500); await shot("15-department", { full: true, maxHeight: 5200 });
  await nav("资源运营监控"); await shot("05-resource", { full: true, maxHeight: 2000 });
  await nav("CRM 数据查询"); await shot("06-crm", { full: true, maxHeight: 1400 });
  await nav("员工工时数据"); await shot("07-worktime", { full: true, maxHeight: 1600 });
  await nav("项目/商机数据维护"); await shot("08-sync", { full: true, maxHeight: 1600 });
  await nav("本体浏览器"); await shot("09-ontology-model", { full: true, maxHeight: 1800 });
  await click("[role=tab]", "对象集查询"); await shot("10-ontology-objectsets", { full: true, maxHeight: 1800 });
  await click("[role=tab]", "元数据与版本"); await shot("11-ontology-metadata", { full: true, maxHeight: 1600 });
  await nav("设置"); await shot("12-settings", { full: true, maxHeight: 2600 });
  await nav("项目管理"); await click("button", "PM Assistant"); await shot("13-assistant");
  cdp.close();
}

const task = { diagrams, screenshots }[process.argv[2]];
if (!task) { console.error("usage: node docs/prd/build-assets.mjs diagrams|screenshots"); process.exit(1); }
await task();
