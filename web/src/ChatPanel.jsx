// Чат с агентом рядом с доской. Это обычный чат Пульса «о файле» (/api/files/chats): агент видит путь доски,
// а к каждому сообщению дописывается, что сейчас выделено. Правки агента приходят в редактор сами (живое
// обновление), а весь разговор виден и в разделе «Чаты».

import { useCallback, useEffect, useRef, useState } from "react";

import { pulse, pulseLink } from "./api";

const POLL_MS = 2000;

// браузер отправляет переводы строк формы как \r\n — приписку ищем с любыми
const CONTEXT_TAIL = /\r?\n\r?\n— Доска: [\s\S]*$/;

function stripContext(text) {
  return text.replace(CONTEXT_TAIL, "");
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** Немного markdown для ответов: жирный, код, ссылки (разделы Пульса — во всё окно), списки, абзацы. */
function markdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/```([\s\S]*?)```/g, (_, code) => `<pre>${code.replace(/^\w*\n/, "")}</pre>`);
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  html = html.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    const internal = href.startsWith("/");
    const url = internal ? pulseLink(href) : href;
    if (!internal && !/^https?:/i.test(href)) return label;
    return `<a href="${url}" target="${internal ? "_top" : "_blank"}" rel="noreferrer">${label}</a>`;
  });
  html = html.replace(/^[-*] (.*)$/gm, "• $1");
  return html.replace(/\n/g, "<br>");
}

function time(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}.${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Ход работы агента, пока ответ готовится: последний шаг последнего запуска. */
function progressText(progress) {
  const run = (progress?.runs || []).at(-1);
  const step = (run?.steps || []).at(-1);
  if (!step) return "Агент думает…";
  return `${step.label}${step.detail ? ` · ${step.detail}` : ""}…`;
}

/** Приписка к сообщению агенту: какая доска открыта и что выделено. */
export function boardContext(path, selection) {
  const sel = selection || { ids: [], text: "" };
  return `— Доска: ${path} открыта в редакторе Excalidraw; `
    + (sel.ids.length ? `выделено: ${sel.text}` : "ничего не выделено")
    + ". Смотреть и менять — pulse excalidraw get/add/update/delete --file (скилл «Excalidraw»).";
}

/** Кто ответил: agent — {id, name} (раньше выводился «[object Object]»). */
function agentName(agent) {
  if (!agent) return "";
  return typeof agent === "string" ? agent : agent.name || "";
}

export default function ChatPanel({ path, getSelection, onClose }) {
  const [chat, setChat] = useState(null);
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [state, setState] = useState("loading");
  const [error, setError] = useState(null);
  const listRef = useRef(null);

  const load = useCallback(async (id) => {
    const items = await pulse(`/api/chats/${id}/messages`);
    setMessages(items || []);
    return items || [];
  }, []);

  useEffect(() => {
    let cancelled = false;
    setChat(null);
    setMessages([]);
    setState("loading");
    pulse("/api/chats", { params: { file_path: path } })
      .then(async (chats) => {
        if (cancelled) return;
        const latest = (chats || [])[0];
        if (latest) { setChat(latest); await load(latest.id); }
        setState("ready");
      })
      .catch((e) => { if (!cancelled) { setError(e.message); setState("ready"); } });
    return () => { cancelled = true; };
  }, [path, load]);

  const pending = messages.some((m) => m.status === "pending");
  useEffect(() => {
    if (!chat || !pending) return undefined;
    const timer = setInterval(() => load(chat.id).catch(() => {}), POLL_MS);
    return () => clearInterval(timer);
  }, [chat, pending, load]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages.length, pending]);

  const send = async (event) => {
    event?.preventDefault();
    const body = text.trim();
    if (!body || state === "sending") return;
    setState("sending");
    setError(null);
    try {
      let current = chat;
      if (!current) {
        const started = await pulse("/api/files/chats", { method: "POST", body: { path } });
        current = started.chat;
        setChat(current);
      }
      const form = new FormData();
      form.append("text", `${body}\n\n${boardContext(path, getSelection?.())}`);
      await pulse(`/api/chats/${current.id}/messages`, { method: "POST", form });
      setText("");
      await load(current.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setState("ready");
    }
  };

  const newChat = () => {
    setChat(null);
    setMessages([]);
    setError(null);
  };

  return (
    <aside className="xd-chat" aria-label="Чат о доске">
      <div className="xd-chat-head">
        <b>Чат о доске</b>
        <span className="xd-grow" />
        {chat && <a className="xd-link" href={pulseLink(`/chats/${chat.id}`)} target="_top">В «Чатах»</a>}
        <button type="button" className="xd-link" onClick={newChat} disabled={!chat}>Новый</button>
        <button type="button" className="xd-close" onClick={onClose} aria-label="Закрыть чат">×</button>
      </div>
      <div className="xd-chat-list" ref={listRef}>
        {state === "loading" && <div className="pl-empty">Загружаю…</div>}
        {state !== "loading" && !messages.length && (
          <div className="pl-empty">
            Попросите агента: «нарисуй схему процесса», «подпиши выделенное», «собери идеи с доски в задачи».
            Агент видит доску и то, что вы выделили; его правки появятся на доске сами.
          </div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`xd-msg xd-msg-${m.role === "pulse" ? "pulse" : "owner"}`}>
            {m.role === "pulse" && m.status === "pending" ? (
              <span className="pl-muted">{progressText(m.progress)}</span>
            ) : m.role === "pulse" ? (
              // eslint-disable-next-line react/no-danger
              <div dangerouslySetInnerHTML={{ __html: markdown(m.text || "") }} />
            ) : (
              <div className="xd-pre">{stripContext(m.text || "")}</div>
            )}
            <div className="xd-msg-meta">{agentName(m.agent) ? `${agentName(m.agent)} · ` : ""}{time(m.finished_at || m.created_at)}</div>
          </div>
        ))}
      </div>
      {error && <div className="pl-error xd-chat-error">Не получилось: {error}</div>}
      <form className="xd-chat-form" onSubmit={send}>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Сообщение агенту о доске"
                  rows={3} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(e); }} />
        <button type="submit" className="xd-btn xd-btn-primary" disabled={!text.trim() || state === "sending"}>
          {state === "sending" ? "Отправляю…" : "Отправить"}
        </button>
      </form>
    </aside>
  );
}
