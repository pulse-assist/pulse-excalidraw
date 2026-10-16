// Редактор одной доски: Excalidraw, автосохранение, живое обновление (правки агента подтягиваются слиянием),
// резервная копия в браузере на случай закрытой вкладки, сведения для агента — что открыто и что выделено.

import {
  CaptureUpdateAction, Excalidraw, MainMenu, hashElementsVersion, restore, restoreElements, serializeAsJSON,
} from "@excalidraw/excalidraw";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { APP, HttpError, own } from "./api";
import { byId, canon, mergeElements, mergeFiles, stamp } from "./merge";

const MAX_WAIT_MS = 10000;          // автосохранение не позже, чем через 10 с после первой правки
export const BACKUP_PREFIX = "pulse-excalidraw:";
const KEEPALIVE_LIMIT = 60000;      // fetch keepalive при закрытии вкладки берёт до 64 КБ

function hhmm(date = new Date()) {
  return `${String(date.getHours()).padStart(2, "0")}.${String(date.getMinutes()).padStart(2, "0")}`;
}

function toParent(message) {
  if (window.parent !== window) window.parent.postMessage(message, window.location.origin);
}

function labelOf(element, all) {
  if (element.type === "text") return element.originalText || element.text || "";
  if (element.type === "frame" || element.type === "magicframe") return element.name || "";
  const bound = (element.boundElements || []).find((b) => b.type === "text");
  const text = bound && all.get(bound.id);
  return text ? text.originalText || text.text || "" : "";
}

const KIND = {
  rectangle: "прямоугольник", ellipse: "эллипс", diamond: "ромб", arrow: "стрелка", line: "линия", text: "текст",
  frame: "рамка", magicframe: "рамка", freedraw: "рисунок", image: "картинка", embeddable: "встройка", iframe: "встройка",
};

/** Выделение словами: «прямоугольник «Сервер» [id]» — для контекста чата. */
export function describeSelection(api) {
  if (!api) return { ids: [], text: "" };
  const appState = api.getAppState();
  const elements = api.getSceneElements();
  const all = new Map(elements.map((e) => [e.id, e]));
  const ids = Object.keys(appState.selectedElementIds || {}).filter((id) => appState.selectedElementIds[id] && all.has(id))
    .filter((id) => !(all.get(id).type === "text" && all.get(id).containerId && appState.selectedElementIds[all.get(id).containerId]));
  const parts = ids.slice(0, 20).map((id) => {
    const e = all.get(id);
    const label = labelOf(e, all).replace(/\s+/g, " ").trim().slice(0, 60);
    return `${KIND[e.type] || e.type}${label ? ` «${label}»` : ""} [${id}]`;
  });
  if (ids.length > 20) parts.push(`и ещё ${ids.length - 20}`);
  return { ids, text: parts.join(", ") };
}

export default function Editor({ ticket, path, access, mode, config, theme, onApi, onStatus, chatOpen, onToggleChat, onSelection }) {
  const readOnly = mode === "view" || access !== "write";
  const [initial, setInitial] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [status, setStatus] = useState({ state: "loading" });
  const [toasts, setToasts] = useState([]);
  const [backup, setBackup] = useState(null);
  const apiRef = useRef(null);
  // Последний снимок сцены из onChange. Сохраняем и сверяем всегда его, а не api.getScene…: при закрытии доски
  // Excalidraw очищает свою сцену раньше, чем мы успеваем сохранить, — так однажды записалась пустая доска.
  const sceneRef = useRef(null);
  const aliveRef = useRef(true);
  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false; }; }, []);
  const sync = useRef({
    version: null,             // версия файла, с которой синхронизированы
    baseStamp: new Map(),      // отпечаток сцены на момент синхронизации
    baseRaw: new Map(),        // элементы файла на момент синхронизации
    savedHash: null,           // hashElementsVersion сцены, совпадающей с файлом
    savedBg: null,
    savedFiles: 0,
    dirtySince: 0,
    saving: false,
    timer: null,
    since: Date.now() / 1000,  // правки агентов — после этого времени
    lastPayload: null,
  });

  const report = useCallback((state, message) => {
    const next = { state, message, at: state === "saved" ? hhmm() : undefined };
    setStatus(next);
    onStatus?.(next);
    toParent({ type: "pulse:file-status", status: state === "dirty" ? "ready" : state, at: next.at, message });
  }, [onStatus]);

  // --- загрузка ------------------------------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const meta = await own("/file/meta", { params: { ticket } });
        const raw = await own("/file/content", { params: { ticket } });
        const data = raw && typeof raw === "object" ? raw : {};
        const restored = restore({ elements: data.elements || [], appState: data.appState || {}, files: data.files || {} },
          null, null, { repairBindings: true });
        if (cancelled) return;
        const s = sync.current;
        s.version = meta.version;
        s.since = meta.now || s.since;
        s.baseRaw = byId(data.elements || []);
        s.baseStamp = stamp(restored.elements);
        s.savedHash = hashElementsVersion(restored.elements);
        s.savedBg = restored.appState.viewBackgroundColor;
        s.savedFiles = Object.keys(data.files || {}).length;
        sceneRef.current = { elements: restored.elements, appState: restored.appState, files: data.files || {} };
        setInitial({
          elements: restored.elements,
          appState: { ...restored.appState, viewModeEnabled: readOnly },
          files: data.files || {},
          scrollToContent: true,
        });
        // в браузере осталась несохранённая копия (вкладку закрыли до сохранения) — предложим вернуть
        try {
          const saved = JSON.parse(localStorage.getItem(BACKUP_PREFIX + path) || "null");
          if (saved && saved.elements && canon(saved.elements.filter((e) => !e.isDeleted))
              !== canon((data.elements || []).filter((e) => !e.isDeleted))) {
            setBackup(saved);
          } else if (saved) {
            localStorage.removeItem(BACKUP_PREFIX + path);
          }
        } catch { /* хранилище браузера недоступно */ }
        report(readOnly ? "ready" : "saved");
      } catch (e) {
        if (!cancelled) { setLoadError(e.message); report("error", e.message); }
      }
    })();
    return () => { cancelled = true; };
  }, [ticket, path, readOnly, report]);

  // --- сохранение ----------------------------------------------------------------------------------------
  const isDirty = useCallback(() => {
    const scene = sceneRef.current;
    const s = sync.current;
    if (!scene || readOnly) return false;
    return hashElementsVersion(scene.elements) !== s.savedHash
      || scene.appState.viewBackgroundColor !== s.savedBg
      || Object.keys(scene.files || {}).length !== s.savedFiles;
  }, [readOnly]);

  const payload = useCallback(() => {
    const { elements, appState, files = {} } = sceneRef.current;
    const used = new Set(elements.filter((e) => e.type === "image" && !e.isDeleted).map((e) => e.fileId));
    const kept = Object.fromEntries(Object.entries(files).filter(([id]) => used.has(id)));
    return { elements, bg: appState.viewBackgroundColor, json: serializeAsJSON(elements, appState, kept, "local"),
             filesCount: Object.keys(files).length };
  }, []);

  const takeSnapshot = useCallback(() => {
    const api = apiRef.current;
    if (api && aliveRef.current) {
      sceneRef.current = { elements: api.getSceneElementsIncludingDeleted(), appState: api.getAppState(), files: api.getFiles() };
    }
  }, []);

  const writeBackup = useCallback(() => {
    if (!sceneRef.current || readOnly) return;
    try {
      const { json } = payload();
      if (json.length > 4_000_000) return;            // большие доски с картинками — без копии в браузере
      const parsed = JSON.parse(json);
      localStorage.setItem(BACKUP_PREFIX + path, JSON.stringify({ at: Date.now(), version: sync.current.version,
                                                                 elements: parsed.elements, appState: parsed.appState }));
    } catch { /* нет места или хранилище недоступно */ }
  }, [path, payload, readOnly]);

  const applyRemote = useCallback(async () => {
    // файл поменялся не нами: берём его и сливаем со сценой
    const s = sync.current;
    const meta = await own("/file/meta", { params: { ticket } });
    const data = (await own("/file/content", { params: { ticket } })) || {};
    const api = apiRef.current;
    if (!api || !aliveRef.current) return null;   // доску уже закрыли — сливать не во что
    const remote = data.elements || [];
    const wasDirty = isDirty();
    const local = api.getSceneElementsIncludingDeleted();
    const merged = mergeElements(local, s.baseStamp, s.baseRaw, remote);
    const remoteFiles = data.files || {};
    const missing = Object.values(remoteFiles).filter((f) => !api.getFiles()[f.id]);
    if (missing.length) api.addFiles(missing);
    if (merged.changed) {
      api.updateScene({ elements: restoreElements(merged.elements, null, { repairBindings: true }),
                        captureUpdate: CaptureUpdateAction.NEVER });
    }
    if (data.appState?.viewBackgroundColor && !wasDirty && data.appState.viewBackgroundColor !== api.getAppState().viewBackgroundColor) {
      api.updateScene({ appState: { viewBackgroundColor: data.appState.viewBackgroundColor }, captureUpdate: CaptureUpdateAction.NEVER });
    }
    takeSnapshot();
    const now = sceneRef.current.elements;
    s.version = meta.version;
    s.baseRaw = byId(remote);
    s.baseStamp = stamp(now);
    if (!wasDirty) {
      s.savedHash = hashElementsVersion(now);
      s.savedBg = api.getAppState().viewBackgroundColor;
      s.savedFiles = Object.keys(api.getFiles()).length;
    }
    return { merged, files: mergeFiles(api.getFiles(), remoteFiles) };
  }, [ticket, isDirty, takeSnapshot]);

  const save = useCallback(async () => {
    const s = sync.current;
    if (!sceneRef.current || readOnly || s.saving) return;
    if (!isDirty()) { report("saved"); return; }
    s.saving = true;
    clearTimeout(s.timer);
    report("saving");
    try {
      for (let attempt = 0; attempt < 3; attempt += 1) {
        const { elements, json, filesCount, bg } = payload();
        const hash = hashElementsVersion(elements);
        try {
          const headers = s.version ? { "If-Match": s.version } : {};
          // все элементы удалены владельцем (очистил доску) — серверу можно записать пустую доску
          if (elements.length && elements.every((e) => e.isDeleted)) headers["X-Board-Cleared"] = "1";
          const result = await own("/file/content", { method: "PUT", params: { ticket }, raw: json, headers });
          s.version = result.version;
          s.baseRaw = byId(JSON.parse(json).elements);
          s.baseStamp = stamp(elements);
          s.savedHash = hash;
          s.savedBg = bg;
          s.savedFiles = filesCount;
          if (result.saved_as) report("error", `Сохранено копией: ${result.saved_as}`);
          break;
        } catch (e) {
          if (e instanceof HttpError && e.status === 409 && attempt < 2 && aliveRef.current) {
            await applyRemote();          // файл поменял агент — сливаем и пишем ещё раз
            continue;
          }
          throw e;
        }
      }
      if (isDirty()) {
        report("dirty");
        s.dirtySince = Date.now();
        s.timer = setTimeout(() => save(), config.autosave_ms);
      } else {
        s.dirtySince = 0;
        report("saved");
        try { localStorage.removeItem(BACKUP_PREFIX + path); } catch { /* ничего */ }
      }
    } catch (e) {
      if (e instanceof HttpError && e.status === 410) {      // доску удалили — писать некуда и незачем
        report("error", e.message);
        return;
      }
      report("error", `Не сохранилось: ${e.message}`);
      writeBackup();
      s.timer = setTimeout(() => save(), 15000);      // повторим сами; копия правок — в браузере
    } finally {
      s.saving = false;
    }
  }, [applyRemote, config.autosave_ms, isDirty, path, payload, readOnly, report, ticket, writeBackup]);

  const schedule = useCallback(() => {
    const s = sync.current;
    if (!s.dirtySince) s.dirtySince = Date.now();
    clearTimeout(s.timer);
    const wait = Math.max(0, Math.min(config.autosave_ms, s.dirtySince + MAX_WAIT_MS - Date.now()));
    s.timer = setTimeout(() => save(), wait);
  }, [config.autosave_ms, save]);

  // --- выделение и «доска открыта» — для агента ------------------------------------------------------------
  const selectionKey = useRef("");
  const sendPresence = useCallback((closed = false) => {
    const sel = describeSelection(apiRef.current);
    const body = JSON.stringify({ path, ticket, selected: sel.ids, mode: readOnly ? "view" : "edit",
                                  access: readOnly ? "read" : "write", closed });
    if (closed && navigator.sendBeacon) {
      navigator.sendBeacon(`${APP}/presence`, new Blob([body], { type: "application/json" }));
      return;
    }
    own("/presence", { method: "POST", raw: body }).catch(() => {});
  }, [path, readOnly, ticket]);

  const presenceTimer = useRef(null);
  const onChange = useCallback((elements, appState, files) => {
    const api = apiRef.current;
    if (!api || !aliveRef.current) return;
    // сцена без единого элемента (даже удалённых), когда только что они были, — это не правка владельца
    // (очистка доски помечает элементы удалёнными), а сброс сцены при закрытии: такое не сохраняем
    if (!elements.length && sceneRef.current?.elements.length) return;
    sceneRef.current = { elements, appState, files };
    const key = Object.keys(appState.selectedElementIds || {}).sort().join(",");
    if (key !== selectionKey.current) {
      selectionKey.current = key;
      clearTimeout(presenceTimer.current);
      presenceTimer.current = setTimeout(() => { sendPresence(); onSelection?.(describeSelection(api)); }, 500);
    }
    if (readOnly || sync.current.saving) {
      if (!readOnly && isDirty()) schedule();
      return;
    }
    if (isDirty()) {
      if (status.state !== "dirty" && status.state !== "saving") report("dirty");
      schedule();
      clearTimeout(sync.current.backupTimer);
      sync.current.backupTimer = setTimeout(writeBackup, 1000);
    }
  }, [isDirty, onSelection, readOnly, report, schedule, sendPresence, status.state, writeBackup]);

  useEffect(() => {
    if (!initial) return undefined;
    sendPresence();
    const beat = setInterval(() => sendPresence(), 30000);
    const bye = () => sendPresence(true);
    window.addEventListener("pagehide", bye);
    // отложенный сигнал о выделении после закрытия вернул бы в список плагина только что удалённую доску
    return () => { clearInterval(beat); clearTimeout(presenceTimer.current); window.removeEventListener("pagehide", bye); bye(); };
  }, [initial, sendPresence]);

  // --- живое обновление: файл поменялся (агент, другая вкладка) — подтягиваем ----------------------------
  useEffect(() => {
    if (!initial) return undefined;
    let tick = 0;
    const timer = setInterval(async () => {
      tick += 1;
      const s = sync.current;
      if (!apiRef.current || s.saving || s.polling) return;
      if (document.hidden && tick % 5) return;
      s.polling = true;
      try {
        const meta = await own("/file/meta", { params: { ticket, since: s.since } });
        const changes = meta.changes || [];
        if (changes.length) s.since = Math.max(...changes.map((c) => c.at));
        if (meta.version !== s.version && !s.saving) {
          await applyRemote();
          if (isDirty()) schedule();
        }
        if (changes.length) {
          setToasts((list) => [...list, ...changes.map((c) => ({ key: `${c.at}`, ...c }))].slice(-3));
        }
        if (status.state === "error" && /Билет|404/.test(status.message || "")) report("saved");
      } catch (e) {
        if (e instanceof HttpError && e.status === 404) report("error", "Доска недоступна: файл удалён или перенесён — откройте её заново");
      } finally {
        s.polling = false;
      }
    }, config.poll_ms);
    return () => clearInterval(timer);
  }, [initial, ticket, config.poll_ms, applyRemote, isDirty, schedule, report, status]);

  // --- защита от потери правок ---------------------------------------------------------------------------
  useEffect(() => {
    const beforeUnload = (event) => {
      if (!isDirty() && !sync.current.saving) return;
      writeBackup();
      try {
        const { json, elements } = payload();
        if (json.length < KEEPALIVE_LIMIT && sync.current.version) {
          const headers = { "If-Match": sync.current.version };
          if (elements.length && elements.every((e) => e.isDeleted)) headers["X-Board-Cleared"] = "1";
          own("/file/content", { method: "PUT", params: { ticket }, raw: json, keepalive: true, headers }).catch(() => {});
        }
      } catch { /* сцена недоступна */ }
      event.preventDefault();
      event.returnValue = "";
    };
    const hidden = () => { if (document.visibilityState === "hidden" && isDirty()) { writeBackup(); save(); } };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("visibilitychange", hidden);
      if (isDirty()) { writeBackup(); save(); }
    };
  }, [isDirty, payload, save, ticket, writeBackup]);

  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        save();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [save]);

  const restoreBackup = () => {
    const api = apiRef.current;
    if (!api || !backup) return;
    api.updateScene({ elements: restoreElements(backup.elements, api.getSceneElementsIncludingDeleted(), { repairBindings: true }),
                      captureUpdate: CaptureUpdateAction.IMMEDIATELY });
    setBackup(null);
    schedule();
  };
  const dropBackup = () => {
    try { localStorage.removeItem(BACKUP_PREFIX + path); } catch { /* ничего */ }
    setBackup(null);
  };

  const showChange = (change) => {
    const api = apiRef.current;
    if (!api) return;
    const ids = new Set(change.ids || []);
    const elements = api.getSceneElements().filter((e) => ids.has(e.id));
    if (elements.length) {
      api.scrollToContent(elements, { fitToContent: elements.length > 1, animate: true });
      api.updateScene({ appState: { selectedElementIds: Object.fromEntries(elements.filter((e) => !e.containerId).map((e) => [e.id, true])) },
                        captureUpdate: CaptureUpdateAction.NEVER });
    }
    setToasts((list) => list.filter((t) => t.key !== change.key));
  };

  useEffect(() => {
    if (!toasts.length) return undefined;
    const timer = setTimeout(() => setToasts((list) => list.slice(1)), 8000);
    return () => clearTimeout(timer);
  }, [toasts]);

  const name = useMemo(() => (path.split("/").pop() || "Доска").replace(/\.excalidraw$/, ""), [path]);

  if (loadError) return <div className="pl-error xd-message">Доска не открылась: {loadError}</div>;
  if (!initial) return <div className="pl-empty xd-message">Загружаю доску…</div>;

  return (
    <div className="xd-editor">
      <Excalidraw
        excalidrawAPI={(api) => { apiRef.current = api; onApi?.(api); }}
        initialData={initial}
        onChange={onChange}
        viewModeEnabled={readOnly}
        theme={theme}
        langCode="ru-RU"
        name={name}
        UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, toggleTheme: null } }}
        renderTopRightUI={() => (
          <div className="xd-top">
            <SaveBadge status={status} readOnly={readOnly} onRetry={save} />
            {config.chat && onToggleChat && (
              <button type="button" className={`xd-btn${chatOpen ? " xd-btn-on" : ""}`} onClick={onToggleChat}
                      title="Чат с агентом о доске">
                Чат
              </button>
            )}
          </div>
        )}
      >
        <MainMenu>
          <MainMenu.DefaultItems.Export />
          <MainMenu.DefaultItems.SaveAsImage />
          <MainMenu.DefaultItems.SearchMenu />
          <MainMenu.DefaultItems.Help />
          <MainMenu.DefaultItems.ClearCanvas />
          <MainMenu.Separator />
          <MainMenu.DefaultItems.ChangeCanvasBackground />
        </MainMenu>
      </Excalidraw>
      {backup && (
        <div className="xd-banner">
          В браузере осталась несохранённая версия доски от {hhmm(new Date(backup.at))}.
          <button type="button" className="xd-btn" onClick={restoreBackup}>Вернуть её</button>
          <button type="button" className="xd-btn" onClick={dropBackup}>Отбросить</button>
        </div>
      )}
      <div className="xd-toasts">
        {toasts.map((t) => (
          <div key={t.key} className="xd-toast">
            <span>{t.summary}</span>
            {t.ids?.length > 0 && <button type="button" className="xd-link" onClick={() => showChange(t)}>Показать</button>}
          </div>
        ))}
      </div>
    </div>
  );
}

function SaveBadge({ status, readOnly, onRetry }) {
  if (readOnly) return <span className="xd-badge">Только просмотр</span>;
  const text = {
    loading: "Загружаю…", ready: "Готово", dirty: "Есть изменения…", saving: "Сохраняю…",
    saved: status.at ? `Сохранено · ${status.at}` : "Сохранено", error: status.message || "Ошибка",
  }[status.state];
  return (
    <span className={`xd-badge${status.state === "error" ? " xd-badge-error" : ""}${status.state === "saved" ? " xd-badge-ok" : ""}`}
          title={status.message || ""} onClick={status.state === "error" ? onRetry : undefined}
          role={status.state === "error" ? "button" : undefined}>
      {text}
    </span>
  );
}
