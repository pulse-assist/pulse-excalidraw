// Проверка редактора в настоящем Chromium: загрузка, рисование, автосохранение, живое обновление от агента.
import { execFileSync } from "node:child_process";
import { chromium } from "playwright";

const [port, ticket, board] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const out = "/root/pulse_data/workspace/xd-e2e";
const exe = execFileSync("bash", ["-c", "ls -d ~/.cache/ms-playwright/chromium_headless_shell-1194/*/headless_shell"]).toString().trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pulse = (...args) => JSON.parse(execFileSync("pulse", ["excalidraw", ...args]).toString());
const count = () => pulse("get", "--file", board, "--format", "json").elements.length;

const result = {};
const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
const ctx = await browser.newContext({ viewport: { width: 1400, height: 850 } });
const page = await ctx.newPage();
page.on("dialog", (d) => { result.unload_dialog = d.type(); d.accept(); });
const external = [];
const errors = [];
page.on("request", (r) => { const u = new URL(r.url()); if (!["127.0.0.1", ""].includes(u.hostname) && !u.protocol.startsWith("data") && !u.protocol.startsWith("blob")) external.push(r.url()); });
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(`${base}/editor?ticket=${ticket}&mode=edit&chat=0`);
await page.waitForSelector(".excalidraw canvas", { timeout: 30000 });
await page.waitForSelector(".xd-badge-ok", { timeout: 15000 });
result.loaded_badge = await page.textContent(".xd-badge");
await page.screenshot({ path: `${out}/1-loaded.png` });
const before = count();

// рисуем прямоугольник: инструмент «r», протянуть мышью
await page.mouse.click(700, 600);
await page.keyboard.press("r");
await page.mouse.move(250, 450); await page.mouse.down(); await page.mouse.move(450, 560, { steps: 8 }); await page.mouse.up();
await sleep(300);
result.after_draw_badge = await page.textContent(".xd-badge");
await page.waitForSelector(".xd-badge-ok", { timeout: 15000 });
result.saved_badge = await page.textContent(".xd-badge");
result.elements_before = before;
result.elements_after_autosave = count();

// живое обновление: агент добавляет элемент — редактор подтягивает его без перезагрузки
pulse("add", "--file", board, "--elements", JSON.stringify([{ type: "sticky", text: "От агента", x: 700, y: 420 }]));
await page.waitForSelector(".xd-toast", { timeout: 10000 });
result.toast = await page.textContent(".xd-toast");
await sleep(500);
await page.screenshot({ path: `${out}/2-agent-live.png` });

// одновременно: владелец рисует, агент правит, пока автосохранение ещё не прошло — ничего не теряется
await page.keyboard.press("Escape");
await page.keyboard.press("o");
await page.mouse.move(1000, 600); await page.mouse.down(); await page.mouse.move(1150, 700, { steps: 6 }); await page.mouse.up();
pulse("add", "--file", board, "--elements", JSON.stringify([{ type: "text", text: "Агент во время правки", x: 700, y: 700 }]));
await sleep(6000);
await page.waitForSelector(".xd-badge-ok", { timeout: 15000 });
const final = pulse("get", "--file", board, "--format", "json").elements;
result.final_types = final.map((e) => e.type + (e.text ? `:${e.text}` : "")).sort();
await page.screenshot({ path: `${out}/3-merged.png` });

// выделение уходит агенту: выделить всё (Ctrl+A) — pulse excalidraw get видит выделение
await page.mouse.click(1300, 150);
await page.keyboard.press("Control+a");
await sleep(1500);
result.selected_seen_by_agent = pulse("get", "--file", board).selected.length;
result.open_boards = pulse("list").open.map((b) => b.path);

// закрытие вкладки с несохранённой правкой: копия остаётся в браузере (или успела сохраниться keepalive)
await page.keyboard.press("Escape");
await page.keyboard.press("r");
await page.mouse.move(250, 650); await page.mouse.down(); await page.mouse.move(400, 740, { steps: 5 }); await page.mouse.up();
result.count_before_close = count();
result.dirty_badge_before_close = await page.textContent(".xd-badge");
await page.close({ runBeforeUnload: true });
await sleep(1500);
const page2 = await ctx.newPage();
await page2.goto(`${base}/editor?ticket=${ticket}&mode=edit`);
await page2.waitForSelector(".excalidraw canvas", { timeout: 30000 });
await sleep(2500);
result.backup_keys_after_reopen = await page2.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("pulse-excalidraw:")));
result.banner_on_reopen = await page2.$(".xd-banner") ? await page2.textContent(".xd-banner") : null;
result.elements_after_close = count();
await page2.screenshot({ path: `${out}/4-reopen-with-chat.png` });

result.external_requests = external;
result.console_errors = errors.filter((e) => !e.includes("/api/chats")).slice(0, 10);
console.log(JSON.stringify(result, null, 2));
await browser.close();
