"""Плагин «Excalidraw» для Пульса: редактор досок, автосохранение, команды агентов, живое обновление.

Страницы:
- /editor?ticket=…&mode=edit|view — редактор доски в «Файлах» (обработчик *.excalidraw, билет от ядра);
- /editor?path=Доски/План.excalidraw и /editor без параметров — редактор во всю вкладку: список досок, чат.
Обе страницы — встроенные (`embed`): живут на адресе Пульса с сессией владельца, так что сами берут билеты
(POST /api/files/open), читают папку досок и ведут чат о доске (/api/chats…).

API страниц (тот же путь и через /plugin-apps/excalidraw/…, и через мост /api/plugins/excalidraw/api/…):
- GET  /file/meta?ticket=&since=   — версия файла и правки агентов после since (живое обновление);
- GET  /file/content?ticket=       — файл доски; PUT — сохранить (If-Match — версия, с которой редактор начинал);
- POST /presence                    — какая доска открыта и что выделено: агент видит это в pulse excalidraw get;
- GET  /boards                      — доски, которые плагин знает (для страницы меню);
- DELETE /boards?path=              — доску удалили (файл страница удаляет сама, DELETE /api/files): плагин
                                      забывает её — список, «открыта сейчас», правки агентов.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pulse_plugin import CommandError, Plugin, PulseApiError

from . import render, scene

plugin = Plugin()
app = plugin.app

STATIC = Path(__file__).parent / "static"
PRESENCE_TTL = 90                      # сек: редактор шлёт сигнал «открыта» каждые 30 с
CHANGES_KEEP = 50
MAX_ELEMENTS_IN_ANSWER = 400

_presence: dict[str, dict] = {}        # путь доски → {ticket, selected, at, mode}
_changes: dict[str, deque] = {}        # путь доски → последние правки агентов
_lock = threading.Lock()
_registry_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


# --- реестр досок ---------------------------------------------------------------------------------------
# Небольшой JSON в служебной папке плагина (PULSE_PLUGIN_DATA): досок — десятки, база для этого не нужна.

REGISTRY_LIMIT = 200


def _registry_file() -> Path:
    return plugin.data_dir / "boards.json"


def _read_registry() -> dict[str, dict]:
    try:
        data = json.loads(_registry_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_registry(data: dict[str, dict]) -> None:
    if len(data) > REGISTRY_LIMIT:
        keep = sorted(data.values(), key=lambda d: d.get("seen_at", 0), reverse=True)[:REGISTRY_LIMIT]
        data = {d["path"]: d for d in keep}
    target = _registry_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


def remember(path: str, **fields: Any) -> None:
    """Плагин помнит доски, которые открывали и меняли: из них — pulse excalidraw list и страница меню."""
    if not path:
        return
    with _registry_lock:
        data = _read_registry()
        data.setdefault(path, {}).update({"path": path, "name": Path(path).name, "seen_at": _now(), **fields})
        try:
            _write_registry(data)
        except OSError:
            pass


def known_boards(limit: int = 50) -> list[dict]:
    with _registry_lock:
        items = sorted(_read_registry().values(), key=lambda d: d.get("seen_at", 0), reverse=True)[:limit]
    items = [dict(item) for item in items]
    for item in items:
        live = _live(item["path"])
        item["open"] = bool(live)
        if live:
            item["selected"] = live.get("selected") or []
        for key in ("seen_at", "changed_at", "opened_at", "created_at"):
            if isinstance(item.get(key), (int, float)):
                item[key] = _iso(item[key])
    return items


def forget(path: str) -> bool:
    with _registry_lock:
        data = _read_registry()
        found = data.pop(path, None) is not None
        _write_registry(data)
    return found


def drop_board(path: str) -> bool:
    """Доска удалена: убрать её из списка, из открытых и из журнала правок агентов."""
    with _lock:
        was_open = _presence.pop(path, None) is not None
        _changes.pop(path, None)
    return forget(path) or was_open


def _live(path: str) -> dict | None:
    with _lock:
        entry = _presence.get(path)
        if entry and _now() - entry["at"] < PRESENCE_TTL:
            return dict(entry)
    return None


def open_boards() -> list[dict]:
    with _lock:
        return [{"path": p, **{k: v for k, v in e.items() if k != "ticket"}} for p, e in _presence.items()
                if _now() - e["at"] < PRESENCE_TTL]


def note_change(path: str, summary: str, ids: list[str]) -> None:
    with _lock:
        log = _changes.setdefault(path, deque(maxlen=CHANGES_KEEP))
        log.append({"at": _now(), "summary": summary, "ids": ids[:200]})
    remember(path, changed_at=_now(), changed_by="agent")


# --- файл доски по билету --------------------------------------------------------------------------------

def _error(exc: PulseApiError) -> HTTPException:
    text = str(exc)
    detail = text.split(": ", 1)[1] if re.match(r"^\d{3}: ", text) else text
    return HTTPException(exc.status or 502, detail)


def _meta(ticket: str) -> dict:
    try:
        return plugin.api.get(f"/api/plugin-files/{ticket}")
    except PulseApiError as exc:
        raise _error(exc) from None


def _read(ticket: str) -> bytes:
    try:
        return plugin.file_ticket(ticket).read()
    except PulseApiError as exc:
        raise _error(exc) from None


@app.get("/")
def root(request: Request) -> Response:
    # /plugin-apps/excalidraw → страница редактора: относительные адреса ресурсов считаются от /editor
    prefix = request.headers.get("x-forwarded-prefix") or ""
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"{prefix}/editor{query}", status_code=307)


@app.get("/editor")
def editor_page() -> Response:
    index = STATIC / "index.html"
    if not index.is_file():
        return Response("Редактор не собран: в плагине нет static/index.html (см. README, «Сборка»)",
                        status_code=500, media_type="text/plain; charset=utf-8")
    # страница меняется с версией плагина; ресурсы в assets/ — с хэшем в имени, их можно кэшировать
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")
if (STATIC / "fonts").is_dir():
    app.mount("/fonts", StaticFiles(directory=STATIC / "fonts"), name="fonts")


@app.get("/config")
def page_config() -> dict:
    return {
        "plugin": plugin.id,
        "version": plugin.version,
        "folder": (plugin.setting("folder") or "Доски").strip("/") or "Доски",
        "autosave_ms": int(float(plugin.setting("autosave_seconds", 2) or 2) * 1000),
        "chat": bool(plugin.setting("chat_panel", True)),
        "poll_ms": 2000,
    }


@app.get("/file/meta")
def file_meta(ticket: str, since: float = 0) -> dict:
    meta = _meta(ticket)
    with _lock:
        changes = [c for c in _changes.get(meta.get("path", ""), ()) if c["at"] > since]
    return {**meta, "changes": changes, "now": _now()}


@app.get("/file/content")
def file_content(ticket: str) -> Response:
    return Response(_read(ticket), media_type="application/json")


@app.put("/file/content")
async def file_save(ticket: str, request: Request) -> JSONResponse:
    body = await request.body()
    if request.headers.get("if-match"):
        # редактор пишет поверх версии, которую открыл, а файла уже нет — доску удалили (кнопкой «Удалить» или в
        # «Файлах»). Ядро по билету создало бы файл заново, и удалённая доска вернулась бы — не пишем.
        try:
            plugin.api.get(f"/api/plugin-files/{ticket}")
        except PulseApiError as exc:
            if exc.status == 404 and "Билет" not in str(exc):
                raise HTTPException(410, "Доска удалена — правки не сохранены") from None
            raise _error(exc) from None
    try:
        board = scene.parse(body)        # не даём записать на место доски что-то, что её сломает
    except CommandError as exc:
        raise HTTPException(400, str(exc)) from None
    # пустая доска поверх непустой — только если владелец её очистил сам (редактор ставит заголовок, когда все
    # элементы сцены удалены им); иначе это сброс сцены при закрытии вкладки — такой файл не пишем
    if not scene.alive(board) and request.headers.get("x-board-cleared") != "1":
        try:
            current = scene.parse(_read(ticket))
        except (HTTPException, CommandError):
            current = None
        if current and scene.alive(current):
            raise HTTPException(422, "Пустая доска не сохранена: на доске есть элементы, а редактор прислал пустую "
                                     "сцену. Обновите страницу редактора")
    try:
        result = plugin.file_ticket(ticket).write(body, if_match=request.headers.get("if-match"))
    except PulseApiError as exc:
        raise _error(exc) from None
    return JSONResponse(result)


@app.post("/presence")
async def presence(request: Request) -> dict:
    data = await request.json()
    path = str(data.get("path") or "")
    if not path:
        raise HTTPException(400, "Нет пути доски")
    if data.get("closed"):
        with _lock:
            _presence.pop(path, None)
        return {"ok": True}
    selected = [str(s) for s in (data.get("selected") or [])][:500]
    with _lock:
        fresh = path not in _presence
        _presence[path] = {"ticket": data.get("ticket"), "selected": selected, "at": _now(),
                           "mode": data.get("mode") or "edit", "access": data.get("access") or "write"}
    if fresh:
        remember(path, opened_at=_now())
    return {"ok": True}


@app.get("/boards")
def boards() -> dict:
    return {"boards": known_boards(), "open": open_boards()}


@app.delete("/boards")
def board_deleted(path: str) -> dict:
    path = path.strip()
    if not path:
        raise HTTPException(400, "Нет пути доски")
    return {"path": path, "forgotten": drop_board(path)}


# --- команды агентов -----------------------------------------------------------------------------------

def _board_arg(args: dict, write: bool = False) -> tuple[str, str]:
    """(путь, билет) доски: из --file или открытая сейчас у владельца (если она одна)."""
    arg = args.get("file")
    if arg:
        ticket = plugin.file(arg)
        meta = ticket._meta or {}
        path = meta.get("path") or (arg.get("path") if isinstance(arg, dict) else "") or ""
        return path, ticket.ticket
    live = open_boards()
    if not live:
        raise CommandError("Укажите доску: --file \"Доски/План.excalidraw\" (в редакторе сейчас ничего не открыто)")
    if len(live) > 1:
        raise CommandError("В редакторе открыто несколько досок — укажите --file: "
                           + ", ".join(b["path"] for b in live))
    path = live[0]["path"]
    with _lock:
        entry = _presence.get(path) or {}
    if not entry.get("ticket"):
        raise CommandError(f"Укажите доску: --file \"{path}\"")
    if write and entry.get("access") != "write":
        raise CommandError(f"Доска «{path}» открыта только для чтения")
    return path, entry["ticket"]


def _load(ticket: str) -> tuple[dict, str | None]:
    try:
        meta = plugin.api.get(f"/api/plugin-files/{ticket}")
    except PulseApiError as exc:
        if exc.status == 404:
            return scene.empty_board(), None
        raise CommandError(f"Доска не открылась: {exc}") from None
    try:
        data = plugin.file_ticket(ticket).read()
    except PulseApiError as exc:
        raise CommandError(f"Доска не прочиталась: {exc}") from None
    return scene.parse(data), meta.get("version")


def _modify(path: str, ticket: str, change, summary) -> dict:
    """Прочитать — поправить — записать с If-Match; файл поменяли между чтением и записью — ещё раз."""
    for _ in range(4):
        board, version = _load(ticket)
        result = change(board)
        scene.prune_files(board)
        try:
            saved = plugin.file_ticket(ticket).write(scene.dump(board), if_match=version)
        except PulseApiError as exc:
            if exc.status == 409:
                time.sleep(0.3)
                continue
            if exc.status == 403:
                raise CommandError("Доска доступна только для чтения") from None
            raise CommandError(f"Доска не сохранилась: {exc}") from None
        ids = result.get("ids", []) if isinstance(result, dict) else []
        note_change(path, summary(result), ids)
        return {"path": path, "version": saved.get("version"), **(result or {})}
    raise CommandError("Доску всё время меняют (открыта в редакторе и правится) — повторите команду")


def _json_arg(args: dict, name: str) -> Any:
    raw = args.get(name)
    if raw in (None, ""):
        raise CommandError(f"Нужен --{name} — JSON")
    if isinstance(raw, (list, dict)):
        return raw if isinstance(raw, list) else [raw]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandError(f"--{name}: неверный JSON — {exc}") from None
    return value if isinstance(value, list) else [value]


def _ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(i) for i in raw]
    return [i.strip() for i in str(raw or "").split(",") if i.strip()]


@plugin.command("list", "Доски, которые плагин знает, и какие открыты сейчас")
def cmd_list(args: dict) -> dict:
    return {
        "open": open_boards(),
        "boards": known_boards(int(args.get("limit") or 30)),
        "hint": "Все доски каталога: pulse files find .excalidraw; папка новых досок — "
                f"{(plugin.setting('folder') or 'Доски')}/",
    }


@plugin.command("get", "Содержимое доски: описание, элементы, выделение в редакторе")
def cmd_get(args: dict) -> dict:
    path, ticket = _board_arg(args)
    board, version = _load(ticket)
    if version is None:
        raise CommandError(f"Доски нет: {path}")
    remember(path)
    live = _live(path)
    selected = (live or {}).get("selected") or []
    fmt = (args.get("format") or "text").lower()
    out: dict[str, Any] = {"path": path, "version": version, "open_in_editor": bool(live), "selected": selected}
    if fmt in ("text", "both"):
        out["description"] = scene.describe(board, selected)
    if fmt in ("json", "both"):
        items = scene.elements_for_agent(board)
        if args.get("ids"):
            wanted = set(_ids(args["ids"]))
            items = [i for i in items if i["id"] in wanted]
        out["elements"] = items[:MAX_ELEMENTS_IN_ANSWER]
        if len(items) > MAX_ELEMENTS_IN_ANSWER:
            out["truncated"] = len(items)
    if fmt == "raw":
        out["board"] = board
    return out


@plugin.command("create", "Новая доска (файл .excalidraw), можно сразу с элементами")
def cmd_create(args: dict) -> dict:
    if not args.get("file"):
        raise CommandError("Нужен --file: путь новой доски, например «Доски/План.excalidraw»")
    ticket = plugin.file(args["file"])
    path = (ticket._meta or {}).get("path") or ""
    if not path.endswith(".excalidraw"):
        raise CommandError("Имя доски должно оканчиваться на .excalidraw")
    try:
        plugin.api.get(f"/api/plugin-files/{ticket.ticket}")
        exists = True
    except PulseApiError:
        exists = False
    if exists and str(args.get("overwrite")).lower() not in ("1", "true", "yes"):
        raise CommandError(f"Доска уже есть: {path} — правьте её (add, update) или выберите другое имя")
    board = scene.empty_board()
    created = scene.build(board, _json_arg(args, "elements")) if args.get("elements") else []
    try:
        saved = ticket.write(scene.dump(board))
    except PulseApiError as exc:
        raise CommandError(f"Доска не создалась: {exc}") from None
    remember(path, created_at=_now())
    return {"path": path, "version": saved.get("version"), "created": [scene.compact(board, e) for e in created
                                                                          if not e.get("containerId")]}


@plugin.command("add", "Добавить элементы на доску: фигуры с подписями, текст, стрелки между фигурами, рамки")
def cmd_add(args: dict) -> dict:
    path, ticket = _board_arg(args, write=True)
    specs = _json_arg(args, "elements")

    def change(board: dict) -> dict:
        created = scene.build(board, specs)
        return {"ids": [e["id"] for e in created],
                "created": [scene.compact(board, e) for e in created if not e.get("containerId")]}

    return _modify(path, ticket, change, lambda r: f"Агент добавил элементы: {len(r['created'])}")


@plugin.command("update", "Изменить элементы по id: текст, координаты, размер, цвета, стиль")
def cmd_update(args: dict) -> dict:
    path, ticket = _board_arg(args, write=True)
    changes = _json_arg(args, "elements")

    def change(board: dict) -> dict:
        touched = scene.patch(board, changes)
        return {"ids": [e["id"] for e in touched],
                "updated": [scene.compact(board, e) for e in touched if not e.get("containerId")]}

    return _modify(path, ticket, change, lambda r: f"Агент изменил элементы: {len(r['updated'])}")


@plugin.command("delete", "Удалить элементы по id (с их подписями)")
def cmd_delete(args: dict) -> dict:
    path, ticket = _board_arg(args, write=True)
    ids = _ids(args.get("ids"))
    if not ids:
        raise CommandError("Нужен --ids: id элементов через запятую")

    def change(board: dict) -> dict:
        removed = scene.remove(board, ids)
        return {"ids": removed, "deleted": removed}

    return _modify(path, ticket, change, lambda r: f"Агент удалил элементы: {len(r['deleted'])}")


@plugin.command("export", "Картинка доски или её части: PNG или SVG")
def cmd_export(args: dict) -> dict:
    path, ticket = _board_arg(args)
    board, version = _load(ticket)
    if version is None:
        raise CommandError(f"Доски нет: {path}")
    fmt = (args.get("format") or "png").lower()
    if fmt not in ("png", "svg"):
        raise CommandError("--format: png или svg")
    ids = _ids(args.get("ids")) or None
    try:
        scale = float(args.get("scale") or 2)
    except ValueError:
        raise CommandError("--scale — число, например 2") from None
    data = render.to_svg(board, ids).encode("utf-8") if fmt == "svg" else render.to_png(board, ids, scale)
    if args.get("out"):
        out = plugin.file(args["out"])
        try:
            out.write(data)
        except PulseApiError as exc:
            raise CommandError(f"Картинка не сохранилась: {exc}") from None
        return {"path": path, "saved": (out._meta or {}).get("path"), "format": fmt, "bytes": len(data)}
    # без --out — в папку плагина в «Файлах»: её видят и агенты (файл на диске), и владелец
    folder = plugin.files_dir / "Экспорт"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{Path(path).stem or 'доска'} {datetime.now().strftime('%Y-%m-%d %H.%M.%S')}.{fmt}"
    target = folder / name
    target.write_bytes(data)
    return {"path": path, "saved": f"{plugin.folder}/Экспорт/{name}", "disk_path": str(target), "format": fmt,
            "bytes": len(data)}


@plugin.command("check", "Проверить плагин: редактор собран, отрисовка PNG работает")
def cmd_check(args: dict) -> dict:
    checks = []
    built = (STATIC / "index.html").is_file() and (STATIC / "assets").is_dir()
    checks.append({"title": "Редактор собран", "ok": built,
                   "detail": "static/index.html и assets/ на месте" if built else "Нет сборки редактора — переустановите плагин"})
    fonts = (STATIC / "fonts").is_dir()
    checks.append({"title": "Шрифты Excalidraw локально", "ok": fonts,
                   "detail": "без CDN" if fonts else "Нет static/fonts — текст на доске будет запасным шрифтом"})
    try:
        board = scene.empty_board()
        scene.build(board, [{"type": "rectangle", "text": "проверка"}])
        render.to_png(board)
        checks.append({"title": "Отрисовка PNG", "ok": True, "detail": "cairosvg работает"})
    except Exception as exc:  # noqa: BLE001 — показываем владельцу причину как есть
        checks.append({"title": "Отрисовка PNG", "ok": False, "detail": f"{exc} — export --format svg работает и так"})
    try:
        _write_registry(_read_registry())
        checks.append({"title": "Служебная папка плагина", "ok": True, "detail": "список досок пишется"})
    except OSError as exc:
        checks.append({"title": "Служебная папка плагина", "ok": False, "detail": f"не пишется: {exc}"})
    return {"ok": all(c["ok"] for c in checks[:1]), "checks": checks}


@plugin.command("forget", "Убрать доску из списка плагина (файл не трогается)")
def cmd_forget(args: dict) -> dict:
    path = str(args.get("path") or "").strip()
    if not path:
        raise CommandError("Нужен --path доски")
    if not forget(path):
        raise CommandError(f"Такой доски в списке нет: {path}")
    return {"forgotten": path}
