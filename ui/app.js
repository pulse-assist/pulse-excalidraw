// Страница меню «Доски». Страницы меню плагинов живут в песочнице без сессии владельца, а редактору она нужна
// (читать и создавать доски в «Файлах», вести чат), поэтому сам редактор — встроенная страница
// /plugin-apps/excalidraw/editor: здесь — список известных плагину досок и вход в редактор (новая вкладка)
// или в «Файлы» (доска откроется в панели просмотра, «Развернуть» — во весь экран).

const app = document.getElementById("app");
const base = location.pathname.split("/api/plugins/")[0];
const editorUrl = (path) => `${base}/plugin-apps/excalidraw/editor${path ? `?path=${encodeURIComponent(path)}` : ""}`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const filesPath = (p) => `/files?path=${encodeURIComponent(p).replace(/%2F/g, "/")}`;

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "только что";
  if (diff < 3600) return `${Math.round(diff / 60)} мин назад`;
  if (diff < 86400) return `${Math.round(diff / 3600)} ч назад`;
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

async function render() {
  let settings = {};
  let data;
  try {
    settings = (await pulse.settings()) || {};
    data = await pulse.plugin("/boards");
  } catch (e) {
    app.innerHTML = `<div class="pl-error">Не получилось загрузить: ${esc(e.message || e)}</div>`;
    return;
  }
  const folder = settings.folder || "Доски";
  const boards = data.boards || [];
  const rows = boards.map((b) => `
    <li>
      <span class="pl-dot" style="background:${b.open ? "var(--pulse-ok)" : "var(--pulse-divider)"}"></span>
      <span class="pl-grow"><a class="xd-link" href="${esc(editorUrl(b.path))}" target="_blank" rel="noopener">${esc(b.name.replace(/\.excalidraw$/, ""))}</a>
        <span class="pl-muted xd-path">${esc(b.path)}</span></span>
      ${b.open ? `<span class="pl-badge pl-ok">открыта${b.selected?.length ? ` · выделено ${b.selected.length}` : ""}</span>` : ""}
      <span class="pl-muted pl-small">${esc(when(b.changed_at || b.seen_at))}</span>
      <button class="xd-link" data-go="${esc(filesPath(b.path))}">в «Файлах»</button>
    </li>`).join("");
  app.innerHTML = `
    <div class="pl-card">
      <div class="xd-actions">
        <a class="xd-btn xd-btn-primary" href="${esc(editorUrl(""))}" target="_blank" rel="noopener">Открыть редактор досок</a>
        <button class="xd-btn" data-go="${esc(filesPath(folder))}">Папка «${esc(folder)}» в «Файлах»</button>
      </div>
      <p class="pl-muted pl-small">Редактор открывается в новой вкладке: список досок, «+ Доска», чат с агентом рядом с доской.
        Любая доска .excalidraw открывается и прямо в «Файлах». Доски сохраняются сами через 2 с после правки.</p>
    </div>
    <div class="pl-card">
      <div class="pl-section">Недавние доски</div>
      ${boards.length ? `<ul class="pl-list">${rows}</ul>`
        : `<div class="pl-empty">Плагин ещё не видел ни одной доски. Откройте редактор и создайте первую — или попросите агента: «нарисуй схему на доске».</div>`}
    </div>
    <div class="pl-muted pl-small">Данные: плагин «Excalidraw», обновлено ${esc(when(new Date().toISOString()))}</div>`;
}

app.addEventListener("click", (event) => {
  const el = event.target.closest("[data-go]");
  if (el) { event.preventDefault(); pulse.navigate(el.dataset.go); }
});

render();
setInterval(render, 15000);
