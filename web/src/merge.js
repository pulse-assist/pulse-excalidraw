// Слияние доски по элементам: что поменял владелец в редакторе (local) и что пришло в файле (remote — правки
// агента, другой вкладки, ручная правка файла) относительно общей основы — последней версии файла, с которой
// редактор синхронизировался. Без React и Excalidraw: тестируется в node (merge.test.mjs).

/** JSON с ключами по алфавиту: одинаковые элементы дают одинаковую строку независимо от порядка полей. */
export function canon(value) {
  if (value === undefined) return "";
  if (Array.isArray(value)) return `[${value.map(canon).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().filter((k) => value[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${canon(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function byId(elements) {
  const map = new Map();
  for (const e of elements || []) if (e && e.id) map.set(e.id, e);
  return map;
}

/** Отпечаток элементов сцены: id → версия. По нему видно, что владелец менял после синхронизации. */
export function stamp(elements) {
  const map = new Map();
  for (const e of elements || []) if (e && e.id) map.set(e.id, `${e.version}:${e.versionNonce}:${e.isDeleted ? 1 : 0}`);
  return map;
}

/**
 * local       — элементы сцены сейчас (с удалёнными isDeleted, как их держит Excalidraw);
 * baseStamp   — stamp(сцена сразу после прошлой синхронизации);
 * baseRaw     — byId(элементы файла прошлой синхронизации);
 * remote      — элементы файла сейчас.
 * Возвращает { elements, taken: id элементов, взятых из файла, changed: было ли что брать }.
 */
export function mergeElements(local, baseStamp, baseRaw, remote) {
  const localMap = byId(local);
  const remoteMap = byId(remote);
  const taken = new Set();
  const result = new Map();
  const ids = new Set([...localMap.keys(), ...remoteMap.keys(), ...baseRaw.keys()]);
  for (const id of ids) {
    const l = localMap.get(id);
    const r = remoteMap.get(id);
    const remoteChanged = canon(r) !== canon(baseRaw.get(id));
    const localChanged = baseStamp.has(id)
      ? !l || stamp([l]).get(id) !== baseStamp.get(id)
      : !!l;
    if (!remoteChanged) {
      if (l) result.set(id, l);
      continue;
    }
    if (!localChanged || !l) {
      if (r) { result.set(id, r); taken.add(id); }
      else if (l && !l.isDeleted) {
        // в файле элемента больше нет — удалили там; у владельца он не менялся — удаляем и у него
        result.set(id, { ...l, isDeleted: true, version: (l.version || 1) + 1, versionNonce: nonce() });
        taken.add(id);
      } else if (l) result.set(id, l);
      continue;
    }
    // поменяли с обеих сторон: свежее — у кого версия выше; удаление в файле правку владельца не отменяет
    if (r && (r.version || 0) > (l.version || 0)) { result.set(id, r); taken.add(id); }
    else result.set(id, l);
  }
  // порядок (z-order): как у владельца; новые из файла — сразу за своим соседом по файлу
  const order = (local || []).map((e) => e.id).filter((id) => result.has(id));
  const placed = new Set(order);
  const remoteOrder = (remote || []).map((e) => e.id);
  remoteOrder.forEach((id, i) => {
    if (placed.has(id) || !result.has(id)) return;
    let at = order.length;
    for (let j = i - 1; j >= 0; j -= 1) {
      const k = order.indexOf(remoteOrder[j]);
      if (k >= 0) { at = k + 1; break; }
    }
    order.splice(at, 0, id);
    placed.add(id);
  });
  return { elements: order.map((id) => result.get(id)), taken: [...taken], changed: taken.size > 0 };
}

/** Картинки: всё, что есть хотя бы с одной стороны. */
export function mergeFiles(localFiles, remoteFiles) {
  return { ...(remoteFiles || {}), ...(localFiles || {}) };
}

function nonce() {
  return Math.floor(Math.random() * 2 ** 31);
}
