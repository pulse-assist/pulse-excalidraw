import assert from "node:assert/strict";
import { test } from "node:test";

import { byId, canon, mergeElements, stamp } from "./merge.js";

const el = (id, version = 1, extra = {}) => ({ id, version, versionNonce: version * 10, x: 0, ...extra });

function setup(base) {
  return { baseStamp: stamp(base), baseRaw: byId(base) };
}

test("canon не зависит от порядка ключей", () => {
  assert.equal(canon({ a: 1, b: [1, { d: 2, c: 3 }] }), canon({ b: [1, { c: 3, d: 2 }], a: 1 }));
});

test("правка агента приходит, если владелец элемент не трогал", () => {
  const base = [el("a"), el("b")];
  const { baseStamp, baseRaw } = setup(base);
  const remote = [el("a"), el("b", 2, { x: 50 })];
  const out = mergeElements(base, baseStamp, baseRaw, remote);
  assert.deepEqual(out.taken, ["b"]);
  assert.equal(out.elements[1].x, 50);
});

test("правка владельца не перетирается неизменённым файлом", () => {
  const base = [el("a")];
  const { baseStamp, baseRaw } = setup(base);
  const local = [el("a", 2, { x: 99 })];
  const out = mergeElements(local, baseStamp, baseRaw, base);
  assert.equal(out.changed, false);
  assert.equal(out.elements[0].x, 99);
});

test("новые элементы с обеих сторон сохраняются, новые из файла встают за соседом", () => {
  const base = [el("a"), el("b")];
  const { baseStamp, baseRaw } = setup(base);
  const local = [el("a"), el("b"), el("mine")];
  const remote = [el("a"), el("agent"), el("b")];
  const out = mergeElements(local, baseStamp, baseRaw, remote);
  assert.deepEqual(out.elements.map((e) => e.id), ["a", "agent", "b", "mine"]);
});

test("удалённое в файле удаляется у владельца, если он его не менял", () => {
  const base = [el("a"), el("b")];
  const { baseStamp, baseRaw } = setup(base);
  const out = mergeElements(base, baseStamp, baseRaw, [el("a")]);
  assert.equal(out.elements.find((e) => e.id === "b").isDeleted, true);
});

test("удаление в файле не отменяет свежую правку владельца", () => {
  const base = [el("a")];
  const { baseStamp, baseRaw } = setup(base);
  const out = mergeElements([el("a", 3, { x: 5 })], baseStamp, baseRaw, []);
  assert.equal(out.elements[0].isDeleted, undefined);
  assert.equal(out.elements[0].x, 5);
});

test("конфликт: выигрывает большая версия", () => {
  const base = [el("a")];
  const { baseStamp, baseRaw } = setup(base);
  const out = mergeElements([el("a", 2, { x: 1 })], baseStamp, baseRaw, [el("a", 5, { x: 2 })]);
  assert.equal(out.elements[0].x, 2);
});
