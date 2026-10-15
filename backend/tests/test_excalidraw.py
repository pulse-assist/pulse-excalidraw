"""Тесты плагина без Пульса: доска — в памяти, билеты и API Пульса подменены."""

import json

import pytest
from fastapi.testclient import TestClient
from pulse_plugin import PulseApiError

from excalidraw_plugin import app as module
from excalidraw_plugin import render, scene


class Store:
    """Файлы по билетам, как /api/plugin-files/{ticket}: версия меняется при каждой записи, If-Match проверяется."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.paths: dict[str, str] = {}
        self.versions: dict[str, int] = {}

    def add(self, ticket: str, path: str, board: dict | None = None):
        self.paths[ticket] = path
        if board is not None:
            self.files[ticket] = scene.dump(board)
            self.versions[ticket] = 1

    def get(self, path: str, **params):
        ticket = path.rsplit("/", 1)[-1]
        if ticket not in self.files:
            raise PulseApiError("404: Файл не найден", 404)
        return {"path": self.paths[ticket], "version": f"v{self.versions[ticket]}", "access": "write",
                "name": self.paths[ticket].split("/")[-1]}

    def board(self, ticket: str) -> dict:
        return json.loads(self.files[ticket])


class Ticket:
    def __init__(self, store: Store, ticket: str, meta=None):
        self.store, self.ticket, self._meta = store, ticket, meta or {"path": store.paths.get(ticket)}

    def read(self) -> bytes:
        return self.store.files[self.ticket]

    def write(self, data: bytes, *, if_match=None, on_conflict="error"):
        current = self.store.versions.get(self.ticket)
        if if_match and current is not None and if_match != f"v{current}":
            raise PulseApiError("409: Файл был изменён", 409)
        self.store.files[self.ticket] = data
        self.store.versions[self.ticket] = (current or 0) + 1
        return {"saved": True, "version": f"v{self.store.versions[self.ticket]}"}


@pytest.fixture
def store(monkeypatch, tmp_path):
    s = Store()
    monkeypatch.setitem(module.plugin.__dict__, "api", s)
    monkeypatch.setattr(module.plugin, "file_ticket", lambda t: Ticket(s, t))
    monkeypatch.setattr(module.plugin, "file", lambda arg: Ticket(s, arg["ticket"], {"path": arg["path"]}))
    monkeypatch.setattr(module.plugin, "files_dir", tmp_path)
    monkeypatch.setattr(module.plugin, "data_dir", tmp_path / "data")
    module._presence.clear()
    module._changes.clear()
    return s


def arg(ticket: str, path: str) -> dict:
    return {"ticket": ticket, "path": path, "name": path.split("/")[-1]}


http = TestClient(module.app)


def command(name: str, **args):
    return http.post(f"/commands/{name}", json=args)


def test_create_add_get_flow(store):
    store.add("t1", "Доски/План.excalidraw")
    r = command("create", file=arg("t1", "Доски/План.excalidraw"))
    assert r.status_code == 200, r.text
    r = command("add", file=arg("t1", "Доски/План.excalidraw"), elements=json.dumps([
        {"id": "a", "type": "rectangle", "text": "Заявка", "fill": "blue"},
        {"id": "b", "type": "diamond", "text": "Одобрена?"},
        {"type": "arrow", "from": "a", "to": "b", "text": "да"},
    ]))
    assert r.status_code == 200, r.text
    board = store.board("t1")
    kinds = sorted(e["type"] for e in board["elements"])
    assert kinds == ["arrow", "diamond", "rectangle", "text", "text", "text"]
    rect = next(e for e in board["elements"] if e["type"] == "rectangle")
    assert rect["backgroundColor"] == "#a5d8ff"
    arrow = next(e for e in board["elements"] if e["type"] == "arrow")
    assert arrow["startBinding"]["elementId"] == rect["id"]
    assert any(ref["id"] == arrow["id"] for ref in rect["boundElements"])
    text = command("get", file=arg("t1", "Доски/План.excalidraw")).json()["description"]
    assert "«Заявка»" in text and "→" in text and "«да»" in text
    # правка агента записана в журнал — редактор покажет её владельцу
    meta = http.get("/file/meta", params={"ticket": "t1"}).json()
    assert meta["changes"][0]["summary"].startswith("Агент добавил")


def test_create_refuses_existing(store):
    store.add("t1", "Доски/План.excalidraw", scene.empty_board())
    r = command("create", file=arg("t1", "Доски/План.excalidraw"))
    assert r.status_code == 400 and "уже есть" in r.json()["detail"]


def test_update_moves_label_and_reroutes_arrow(store):
    board = scene.empty_board()
    created = scene.build(board, [{"id": "a", "type": "rectangle", "text": "A", "x": 0, "y": 0},
                                  {"id": "b", "type": "rectangle", "text": "B", "x": 400, "y": 0},
                                  {"type": "arrow", "from": "a", "to": "b"}])
    store.add("t1", "Доски/x.excalidraw", board)
    a = created[0]
    r = command("update", file=arg("t1", "Доски/x.excalidraw"),
                elements=json.dumps([{"id": a["id"], "y": 300, "text": "Новое"}]))
    assert r.status_code == 200, r.text
    after = scene.by_id(store.board("t1"))
    label = next(e for e in after.values() if e["type"] == "text" and e.get("containerId") == a["id"])
    assert label["originalText"] == "Новое" and label["y"] > 300
    arrow = next(e for e in after.values() if e["type"] == "arrow")
    assert arrow["y"] > 250 and arrow["version"] > 1


def test_arrow_label_follows_reroute(store):
    board = scene.empty_board()
    made = scene.build(board, [{"id": "a", "type": "rectangle", "x": 0, "y": 0},
                               {"id": "b", "type": "rectangle", "x": 400, "y": 0},
                               {"type": "arrow", "from": "a", "to": "b", "text": "да"}])
    store.add("t1", "Доски/x.excalidraw", board)
    command("update", file=arg("t1", "Доски/x.excalidraw"), elements=json.dumps([{"id": made[1]["id"], "x": 1000}]))
    after = scene.by_id(store.board("t1"))
    arrow = next(e for e in after.values() if e["type"] == "arrow")
    label = next(e for e in after.values() if e.get("containerId") == arrow["id"])
    middle = arrow["x"] + arrow["points"][-1][0] / 2
    assert abs(label["x"] + label["width"] / 2 - middle) < 1


def test_update_unknown_id(store):
    store.add("t1", "Доски/x.excalidraw", scene.empty_board())
    r = command("update", file=arg("t1", "Доски/x.excalidraw"), elements='[{"id": "nope", "x": 1}]')
    assert r.status_code == 400 and "nope" in r.json()["detail"]


def test_delete_removes_label_and_unbinds(store):
    board = scene.empty_board()
    created = scene.build(board, [{"id": "a", "type": "rectangle", "text": "A"}, {"id": "b", "type": "ellipse"},
                                  {"type": "arrow", "from": "a", "to": "b"}])
    store.add("t1", "Доски/x.excalidraw", board)
    r = command("delete", file=arg("t1", "Доски/x.excalidraw"), ids=created[0]["id"])
    assert r.status_code == 200, r.text
    after = store.board("t1")["elements"]
    assert {e["type"] for e in after} == {"ellipse", "arrow"}
    assert next(e for e in after if e["type"] == "arrow")["startBinding"] is None


def test_modify_retries_on_conflict(store, monkeypatch):
    store.add("t1", "Доски/x.excalidraw", scene.empty_board())
    real = Ticket.write
    calls = {"n": 0}

    def flaky(self, data, *, if_match=None, on_conflict="error"):
        calls["n"] += 1
        if calls["n"] == 1:                     # владелец сохранил между чтением и записью агента
            store.versions["t1"] += 1
        return real(self, data, if_match=if_match, on_conflict=on_conflict)

    monkeypatch.setattr(Ticket, "write", flaky)
    r = command("add", file=arg("t1", "Доски/x.excalidraw"), elements='[{"type": "text", "text": "привет"}]')
    assert r.status_code == 200, r.text
    assert calls["n"] == 2


def test_open_board_without_file(store):
    store.add("t9", "Доски/Открытая.excalidraw", scene.empty_board())
    assert command("get").status_code == 400
    http.post("/presence", json={"path": "Доски/Открытая.excalidraw", "ticket": "t9", "selected": ["x"]})
    r = command("get")
    assert r.status_code == 200 and r.json()["path"] == "Доски/Открытая.excalidraw"
    assert r.json()["selected"] == ["x"]
    listing = command("list").json()
    assert listing["open"][0]["path"] == "Доски/Открытая.excalidraw"
    assert "ticket" not in listing["open"][0]
    http.post("/presence", json={"path": "Доски/Открытая.excalidraw", "closed": True})
    assert command("list").json()["open"] == []
    assert command("list").json()["boards"][0]["path"] == "Доски/Открытая.excalidraw"     # помнит после закрытия
    assert command("forget", path="Доски/Открытая.excalidraw").status_code == 200
    assert command("list").json()["boards"] == []


def test_save_rejects_garbage(store):
    store.add("t1", "Доски/x.excalidraw", scene.empty_board())
    r = http.put("/file/content", params={"ticket": "t1"}, content=b"not json")
    assert r.status_code == 400
    r = http.put("/file/content", params={"ticket": "t1"}, content=scene.dump(scene.empty_board()),
                 headers={"If-Match": "v0"})
    assert r.status_code == 409


def test_empty_save_over_board_needs_clear_flag(store):
    board = scene.empty_board()
    scene.build(board, [{"type": "rectangle", "text": "не потерять"}])
    store.add("t1", "Доски/x.excalidraw", board)
    empty = scene.dump(scene.empty_board())
    r = http.put("/file/content", params={"ticket": "t1"}, content=empty, headers={"If-Match": "v1"})
    assert r.status_code == 422 and len(scene.alive(store.board("t1"))) == 2
    r = http.put("/file/content", params={"ticket": "t1"}, content=empty, headers={"If-Match": "v1", "X-Board-Cleared": "1"})
    assert r.status_code == 200 and scene.alive(store.board("t1")) == []


def test_export_svg_and_png(store, tmp_path):
    board = scene.empty_board()
    scene.build(board, [{"type": "rectangle", "text": "Привет", "fill": "green"}, {"type": "text", "text": "Текст"}])
    store.add("t1", "Доски/x.excalidraw", board)
    r = command("export", file=arg("t1", "Доски/x.excalidraw"), format="svg")
    assert r.status_code == 200, r.text
    svg = (tmp_path / "Экспорт").glob("*.svg").__next__().read_text()
    assert "Привет" in svg and "<rect" in svg
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        pytest.skip("cairosvg не установлен")
    png = render.to_png(board)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_describe_frames_and_selection():
    board = scene.empty_board()
    made = scene.build(board, [{"id": "f", "type": "frame", "text": "Идеи"},
                               {"type": "sticky", "text": "Первая", "frame": "f", "x": 10, "y": 10}])
    text = scene.describe(board, [made[1]["id"]])
    assert "Рамка «Идеи»" in text and "заметка «Первая»" in text and "Выделено" in text


def test_build_rejects_unknown_type():
    with pytest.raises(Exception, match="не может"):
        scene.build(scene.empty_board(), [{"type": "image"}])
