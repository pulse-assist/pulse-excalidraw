// Страница редактора. Два режима:
// - ?ticket=…&mode=… — доска, открытая в «Файлах» (обработчик *.excalidraw): билет выдало ядро;
// - без билета — редактор во всю вкладку: список досок папки «Доски» (настройка folder), новая доска, ?path=… —
//   какая открыта. Билет на доску берём сами (POST /api/files/open) — страница работает с сессией владельца.

import "@excalidraw/excalidraw/index.css";
import "./style.css";

import { StrictMode, useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { APP, filesLink, openTicket, own, pulse } from "./api";
import ChatPanel, { boardContext } from "./ChatPanel";
import Editor, { BACKUP_PREFIX, describeSelection } from "./Editor";
import { provideContext, setChatTarget, toggleChat, useHost } from "./host";

// шрифты Excalidraw — со своего сервера (static/fonts), без CDN
window.EXCALIDRAW_ASSET_PATH = `${APP}/`;

const params = new URLSearchParams(window.location.search);

function useTheme() {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const [theme, setTheme] = useState(media.matches ? "dark" : "light");
  useEffect(() => {
    const onMessage = (event) => {
      if (event.origin === window.location.origin && event.data?.type === "pulse:theme") setTheme(event.data.theme);
    };
    const onMedia = () => setTheme(media.matches ? "dark" : "light");
    window.addEventListener("message", onMessage);
    media.addEventListener("change", onMedia);
    return () => { window.removeEventListener("message", onMessage); media.removeEventListener("change", onMedia); };
  }, [media]);
  return theme;
}

function BoardWithChat({ ticket, path, access, mode, config, theme, host }) {
  // host — страница «Доски» в Пульсе 1.10+: чат рядом показывает сам Пульс (полноценный, как в «Чатах»);
  // undefined — доска в «Файлах» или старый Пульс: свой чат; null — ещё не ясно, чат пока не показываем.
  const hostChat = config.chat && host?.chat;
  const [chatOpen, setChatOpen] = useState(() => config.chat && window.innerWidth >= 1100 && params.get("chat") !== "0");
  const apiRef = useRef(null);
  const getSelection = useCallback(() => describeSelection(apiRef.current), []);
  useEffect(() => {
    if (!hostChat) return undefined;
    setChatTarget(path);
    const stop = provideContext(() => boardContext(path, describeSelection(apiRef.current)));
    return () => { stop(); setChatTarget(null); };
  }, [hostChat, path]);
  const ownChat = config.chat && host !== null && !hostChat;
  return (
    <div className={`xd-board${ownChat && chatOpen ? " xd-with-chat" : ""}`}>
      <Editor key={ticket} ticket={ticket} path={path} access={access} mode={mode} config={config} theme={theme}
              onApi={(api) => { apiRef.current = api; }} chatOpen={hostChat ? host.chatOpen : chatOpen}
              onToggleChat={hostChat ? toggleChat : ownChat ? () => setChatOpen((v) => !v) : null} />
      {ownChat && chatOpen && <ChatPanel path={path} getSelection={getSelection} onClose={() => setChatOpen(false)} />}
    </div>
  );
}

function FilesMode({ config, theme }) {
  const ticket = params.get("ticket");
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    own("/file/meta", { params: { ticket } }).then(setMeta).catch((e) => setError(e.message));
  }, [ticket]);
  if (error) return <div className="pl-error xd-message">Доска не открылась: {error}</div>;
  if (!meta) return <div className="pl-empty xd-message">Загружаю доску…</div>;
  return <BoardWithChat ticket={ticket} path={meta.path} access={meta.access} mode={params.get("mode") || "edit"}
                        config={config} theme={theme} />;
}

function boardName(path) {
  return (path.split("/").pop() || "").replace(/\.excalidraw$/, "");
}

function deleteQuestion(path) {
  // в своих папках Пульс удаляет файл насовсем (и его прежние версии), в облачных хранилищах (@yadisk, @gdrive) —
  // в корзину диска
  const name = boardName(path);
  return path.startsWith("@")
    ? `Удалить доску «${name}»? Файл уйдёт в корзину облачного диска.`
    : `Удалить доску «${name}»? Это нельзя отменить.`;
}

function AppMode({ config, theme }) {
  const folder = config.folder;
  const host = useHost();
  const [boards, setBoards] = useState(null);
  const [error, setError] = useState(null);
  const [current, setCurrent] = useState(params.get("path"));
  const [opened, setOpened] = useState(null);
  const [listOpen, setListOpen] = useState(() => window.innerWidth >= 800 || !params.get("path"));

  const refresh = useCallback(async () => {
    try {
      const listing = await pulse("/api/files", { params: { path: folder } }).catch((e) => {
        if (e.status === 404) return { items: [] };
        throw e;
      });
      const inFolder = (listing.items || []).filter((i) => i.kind === "file" && i.name.endsWith(".excalidraw"))
        .map((i) => ({ path: i.path, modified_at: i.modified_at }));
      const known = await own("/boards").catch(() => ({ boards: [] }));
      const elsewhere = (known.boards || []).filter((b) => !inFolder.some((i) => i.path === b.path))
        .map((b) => ({ path: b.path, modified_at: b.changed_at || b.seen_at, elsewhere: true }));
      inFolder.sort((a, b) => String(b.modified_at).localeCompare(String(a.modified_at)));
      setBoards([...inFolder, ...elsewhere]);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, [folder]);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    if (!current) { setOpened(null); return; }
    let cancelled = false;
    setOpened(null);
    openTicket(current, "edit")
      .then((r) => { if (!cancelled) setOpened({ path: current, ticket: r.ticket, access: r.access, mode: r.url.includes("mode=view") ? "view" : "edit" }); })
      .catch((e) => { if (!cancelled) setOpened({ path: current, error: e.message }); });
    const url = new URL(window.location.href);
    url.searchParams.set("path", current);
    window.history.replaceState(null, "", url);
    return () => { cancelled = true; };
  }, [current]);

  const create = async () => {
    const name = window.prompt("Название новой доски", "Новая доска");
    if (!name) return;
    try {
      await pulse("/api/files/mkdir", { method: "POST", body: { path: folder } }).catch((e) => {
        if (e.status !== 409) throw e;            // папка уже есть
      });
      const made = await pulse("/api/files/create", { method: "POST",
        body: { folder, plugin_id: "excalidraw", template: "excalidraw", name: name.replace(/[:"?*<>|\\/]/g, "-") } });
      await refresh();
      setCurrent(made.path);
    } catch (e) {
      setError(`Доска не создалась: ${e.message}`);
    }
  };

  const remove = async (path) => {
    if (!window.confirm(deleteQuestion(path))) return;
    // сначала закрываем редактор: он дописывает несохранённые правки, а запись после удаления плагин отклонит
    if (path === current) setCurrent(null);
    try {
      await pulse("/api/files", { method: "DELETE", params: { path } }).catch((e) => {
        if (e.status !== 404) throw e;            // файла уже нет — доску осталось только забыть
      });
    } catch (e) {
      setError(`Доска «${boardName(path)}» не удалилась: ${e.message}`);
      return;
    }
    await own("/boards", { method: "DELETE", params: { path } }).catch(() => {});
    try { localStorage.removeItem(BACKUP_PREFIX + path); } catch { /* хранилище браузера недоступно */ }
    await refresh();
  };

  useEffect(() => {
    if (current) return;
    const url = new URL(window.location.href);
    if (!url.searchParams.has("path")) return;
    url.searchParams.delete("path");
    window.history.replaceState(null, "", url);
  }, [current]);

  return (
    <div className={`xd-app${listOpen ? " xd-app-list" : ""}`}>
      {listOpen && (
        <nav className="xd-list" aria-label="Доски">
          <div className="xd-list-head">
            <b>Доски</b>
            <span className="xd-grow" />
            <button type="button" className="xd-btn xd-btn-primary" onClick={create}>+ Доска</button>
          </div>
          <div className="pl-small pl-muted xd-list-folder">
            Папка <a href={filesLink(folder)} target="_top">{folder}/</a>
          </div>
          {error && <div className="pl-error">{error}</div>}
          {boards === null && <div className="pl-empty">Загружаю…</div>}
          {boards && !boards.length && (
            <div className="pl-empty">Досок пока нет. «+ Доска» создаст первую в папке «{folder}».</div>
          )}
          <ul className="pl-list">
            {(boards || []).map((b) => (
              <li key={b.path} className={`xd-list-row${b.path === current ? " xd-active" : ""}`}>
                <button type="button" className="xd-board-link" onClick={() => { setCurrent(b.path); if (window.innerWidth < 800) setListOpen(false); }}
                        title={b.path}>
                  <span className="pl-grow">{boardName(b.path)}</span>
                  {b.elsewhere && <span className="pl-small pl-muted">{b.path.split("/").slice(0, -1).join("/") || "/"}</span>}
                </button>
                <button type="button" className="xd-close xd-row-delete" onClick={() => remove(b.path)}
                        title={`Удалить доску «${boardName(b.path)}»`} aria-label={`Удалить доску «${boardName(b.path)}»`}>×</button>
              </li>
            ))}
          </ul>
        </nav>
      )}
      <main className="xd-main">
        <div className="xd-main-head">
          <button type="button" className="xd-btn" onClick={() => setListOpen((v) => !v)}>{listOpen ? "‹ Скрыть список" : "☰ Доски"}</button>
          {current && <span className="xd-title" title={current}>{boardName(current)}</span>}
          {current && <a className="xd-link" href={filesLink(current)} target="_top">в «Файлах»</a>}
          {current && <button type="button" className="xd-link xd-link-danger" onClick={() => remove(current)}>Удалить</button>}
        </div>
        {!current && <div className="pl-empty xd-message">Выберите доску слева или создайте новую.</div>}
        {current && !opened && <div className="pl-empty xd-message">Открываю доску…</div>}
        {opened?.error && <div className="pl-error xd-message">Доска не открылась: {opened.error}</div>}
        {opened?.ticket && (
          <BoardWithChat ticket={opened.ticket} path={opened.path} access={opened.access} mode={opened.mode}
                         config={config} theme={theme} host={host} />
        )}
      </main>
    </div>
  );
}

function Root() {
  const theme = useTheme();
  const [config, setConfig] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    own("/config").then(setConfig).catch((e) => setError(e.message));
  }, []);
  if (error) return <div className="pl-error xd-message">Плагин не ответил: {error}</div>;
  if (!config) return <div className="pl-empty xd-message">Загружаю…</div>;
  return (
    <div className={`xd-root xd-theme-${theme}`}>
      {params.get("ticket") ? <FilesMode config={config} theme={theme} /> : <AppMode config={config} theme={theme} />}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<StrictMode><Root /></StrictMode>);
