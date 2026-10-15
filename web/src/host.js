// Связь со страницей Пульса, в которую встроен редактор (контракт плагинов 1.10). Пульс 1.10 сам разворачивает
// страницу «Доски» на всё окно и показывает рядом полноценный чат — тот же, что в «Чатах»; редактор только говорит,
// о какой доске чат, и отвечает, что сейчас выделено. Пульс старше 1.10 на pulse:hello не отвечает — тогда
// редактор показывает свой чат, как раньше.

import { useEffect, useState } from "react";

const HELLO_WAIT_MS = 800;
const inFrame = window.parent && window.parent !== window;

let state = null;                       // последний pulse:host: {features, expanded, fullscreen, chat_open}
let settled = !inFrame;                 // ответ получен или ждать больше нечего
const listeners = new Set();
let contextProvider = null;

function emit() {
  for (const fn of listeners) fn();
}

function post(message) {
  if (inFrame) window.parent.postMessage(message, window.location.origin);
}

if (inFrame) {
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin || event.source !== window.parent) return;
    const m = event.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "pulse:host") {
      state = m;
      settled = true;
      emit();
    } else if (m.type === "pulse:chat-context-request") {
      const context = contextProvider ? contextProvider() : undefined;
      post({ type: "pulse:chat-context", rid: m.rid, context });
    }
  });
  post({ type: "pulse:hello" });
  setTimeout(() => { if (!settled) { settled = true; emit(); } }, HELLO_WAIT_MS);
}

/** Возможности Пульса: null — ещё не ясно; {chat: bool, expand: bool, …} — ясно. */
export function useHost() {
  const [, setTick] = useState(0);
  useEffect(() => {
    const fn = () => setTick((n) => n + 1);
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  if (!settled) return null;
  const features = state?.features || [];
  return {
    chat: features.includes("chat"),
    expand: features.includes("expand"),
    chatOpen: !!state?.chat_open,
    expanded: !!state?.expanded || !!state?.fullscreen,
  };
}

/** Чат Пульса — о доске path (null — убрать чат). */
export function setChatTarget(path) {
  post(path
    ? { type: "pulse:chat-target", path, title: "Чат о доске",
        hint: "Попросите агента: «нарисуй схему процесса», «подпиши выделенное», «собери идеи с доски в задачи». "
          + "Агент видит доску и то, что вы выделили; его правки появятся на доске сами.",
        placeholder: "Сообщение агенту о доске" }
    : { type: "pulse:chat-target", path: null });
}

export function toggleChat() {
  post({ type: "pulse:chat-toggle" });
}

export function toggleExpand() {
  post({ type: "pulse:expand" });
}

/** Приписка к сообщениям в чате Пульса: что открыто и выделено (вызывается перед каждой отправкой). */
export function provideContext(fn) {
  contextProvider = fn;
  return () => { if (contextProvider === fn) contextProvider = null; };
}
