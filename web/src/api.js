// Адреса и запросы. Страница живёт на /plugin-apps/excalidraw/… того же адреса, что и Пульс (право embed):
// к API Пульса она ходит с сессией владельца, к своему серверу — через /plugin-apps/excalidraw/….

const path = window.location.pathname;
const marker = path.indexOf("/plugin-apps/");
/** База Пульса: «» или «/pulse», если Пульс открыт не в корне сайта. */
export const PULSE = marker >= 0 ? path.slice(0, marker) : "";
/** Свой сервер плагина. */
export const APP = marker >= 0 ? path.slice(0, marker) + path.slice(marker).split("/").slice(0, 3).join("/") : "";

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(url, { method = "GET", params, body, headers, raw, form, keepalive } = {}) {
  const query = params ? `?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null))}` : "";
  const init = { method, credentials: "same-origin", headers: { ...(headers || {}) }, keepalive };
  if (form) init.body = form;
  else if (raw !== undefined) { init.body = raw; init.headers["Content-Type"] = "application/json"; }
  else if (body !== undefined) { init.body = JSON.stringify(body); init.headers["Content-Type"] = "application/json"; }
  const response = await fetch(url + query, init);
  const text = await response.text();
  let data = text;
  try { data = text ? JSON.parse(text) : null; } catch { /* не JSON */ }
  if (!response.ok) {
    const detail = data && typeof data === "object" && data.detail ? data.detail : `Ошибка ${response.status}`;
    throw new HttpError(response.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export const pulse = (p, opts) => request(`${PULSE}${p}`, opts);
export const own = (p, opts) => request(`${APP}${p}`, opts);

/** Билет на файл доски: так же, как «Файлы» открывают файл в обработчике плагина. */
export function openTicket(filePath, mode = "edit") {
  return pulse("/api/files/open", { method: "POST", body: { path: filePath, plugin_id: "excalidraw", handler: "edit", mode } });
}

export function pulseLink(p) {
  return `${PULSE}${p}`;
}

/** Ссылка на файл в «Файлах»: пробелы и скобки — кодом, как в ссылках Пульса. */
export function filesLink(filePath) {
  return pulseLink(`/files?path=${encodeURIComponent(filePath).replace(/%2F/g, "/")}`);
}
