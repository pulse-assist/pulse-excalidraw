// Редактор во всю вкладку: список досок, переключение досок (правка не теряется, доска не обнуляется), чат.
// API Пульса (сессия владельца) подменено в браузере; доски, билеты и сервер плагина — настоящие.
import { execFileSync } from "node:child_process";
import { chromium } from "playwright";

const [port, ticketA, ticketB, pathA, pathB] = process.argv.slice(2);
const base = `http://127.0.0.1:${port}`;
const out = "/root/pulse_data/workspace/xd-e2e";
const exe = execFileSync("bash", ["-c", "ls -d ~/.cache/ms-playwright/chromium_headless_shell-1194/*/headless_shell"]).toString().trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const count = (p) => JSON.parse(execFileSync("pulse", ["excalidraw", "get", "--file", p, "--format", "json"]).toString()).elements.length;

const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1500, height: 850 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const sent = [];
const messages = [];
await page.route("**/api/**", async (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  if (url.pathname === "/api/files" && req.method() === "GET") {
    return json({ items: [pathA, pathB].map((p) => ({ name: p.split("/").pop(), path: p, kind: "file", modified_at: new Date().toISOString() })) });
  }
  if (url.pathname === "/api/files/open") {
    const { path } = req.postDataJSON();
    return json({ ticket: path === pathA ? ticketA : ticketB, access: "write", url: "/plugin-apps/excalidraw/editor?mode=edit" });
  }
  if (url.pathname === "/api/chats" && req.method() === "GET") return json([]);
  if (url.pathname === "/api/files/chats") return json({ chat: { id: "c1" }, messages: [] }, 201);
  if (url.pathname === "/api/chats/c1/messages" && req.method() === "POST") {
    const text = req.postData() || "";
    sent.push(text);
    messages.push({ id: "m1", role: "owner", text: text.match(/name="text"\r\n\r\n([\s\S]*?)\r\n--/)?.[1] || "", status: "done", created_at: new Date().toISOString() });
    messages.push({ id: "m2", role: "pulse", text: "Готово: добавил **два** блока. [Доска](/files?path=x)", status: "done", agent: "Разработчик", created_at: new Date().toISOString() });
    return json({}, 202);
  }
  if (url.pathname === "/api/chats/c1/messages") return json(messages);
  return route.continue();
});

const result = {};
const nameA = pathA.split("/").pop().replace(".excalidraw", "");
const nameB = pathB.split("/").pop().replace(".excalidraw", "");
await page.goto(`${base}/editor`);
await page.waitForSelector(".xd-board-link");
result.list = await page.$$eval(".xd-board-link .pl-grow", (els) => els.map((e) => e.textContent));
await page.click(`.xd-board-link:has-text("${nameA}")`);
await page.waitForSelector(".xd-badge-ok", { timeout: 30000 });
const a0 = count(pathA);
const b0 = count(pathB);

// правка на A и сразу переход на B — до автосохранения
await page.mouse.click(900, 700);
await page.keyboard.press("r");
await page.mouse.move(500, 400); await page.mouse.down(); await page.mouse.move(650, 500, { steps: 6 }); await page.mouse.up();
await page.click(`.xd-board-link:has-text("${nameB}")`);
await sleep(3000);
await page.waitForSelector(".xd-badge-ok", { timeout: 30000 });
result.a_before = a0; result.a_after_switch = count(pathA);
result.b_before = b0; result.b_after_open = count(pathB);
await page.click(`.xd-board-link:has-text("${nameA}")`);
await page.waitForSelector(".xd-badge-ok", { timeout: 30000 });
await sleep(3000);
result.a_after_back = count(pathA); result.b_after_back = count(pathB);

// чат: выделить всё, написать агенту
await page.mouse.click(900, 700);
await page.keyboard.press("Control+a");
await sleep(700);
if (!(await page.$(".xd-chat"))) await page.click("button.xd-btn:has-text(\"Чат\")");
await page.waitForSelector(".xd-chat textarea");
await page.fill(".xd-chat textarea", "Подпиши выделенное");
await page.click(".xd-chat button[type=submit]");
await page.waitForSelector(".xd-msg-pulse", { timeout: 10000 });
result.owner_bubble = await page.textContent(".xd-msg-owner");
result.pulse_bubble_html = await page.innerHTML(".xd-msg-pulse");
result.sent_has_context = sent.length ? /— Доска: .*выделено: /s.test(sent[0]) : false;
await page.screenshot({ path: `${out}/5-app-with-chat.png` });
result.page_errors = errors;
console.log(JSON.stringify(result, null, 2));
await browser.close();
