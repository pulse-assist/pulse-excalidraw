"""Доска Excalidraw как данные: разбор файла *.excalidraw, описание для агентов, правки элементов.

Формат файла — JSON Excalidraw (`type: "excalidraw"`, `version: 2`, `elements`, `appState`, `files`). Правки агентов
делаются здесь, на сервере: элемент получает новые `version`/`versionNonce`/`updated` — редактор, открытый у владельца,
видит, что элемент изменился, и подтягивает его (слияние по элементам, web/src/merge.js).
"""

from __future__ import annotations

import json
import math
import random
import string
import time
from typing import Any

from pulse_plugin import CommandError

SHAPES = ("rectangle", "ellipse", "diamond")
LINEAR = ("arrow", "line")
KNOWN = (*SHAPES, *LINEAR, "text", "frame", "freedraw", "image", "embeddable", "iframe", "magicframe")

# цвета палитры Excalidraw — агент может звать их словами
COLORS = {
    "black": "#1e1e1e", "чёрный": "#1e1e1e", "черный": "#1e1e1e",
    "gray": "#868e96", "grey": "#868e96", "серый": "#868e96",
    "red": "#e03131", "красный": "#e03131",
    "pink": "#c2255c", "розовый": "#c2255c",
    "violet": "#9c36b5", "фиолетовый": "#9c36b5",
    "blue": "#1971c2", "синий": "#1971c2",
    "cyan": "#0c8599", "голубой": "#0c8599",
    "teal": "#099268", "бирюзовый": "#099268",
    "green": "#2f9e44", "зелёный": "#2f9e44", "зеленый": "#2f9e44",
    "yellow": "#f08c00", "жёлтый": "#f08c00", "желтый": "#f08c00",
    "orange": "#e8590c", "оранжевый": "#e8590c",
    "transparent": "transparent", "нет": "transparent",
}
# светлые заливки той же палитры (backgroundColor)
FILLS = {
    "gray": "#e9ecef", "grey": "#e9ecef", "серый": "#e9ecef",
    "red": "#ffc9c9", "красный": "#ffc9c9",
    "pink": "#fcc2d7", "розовый": "#fcc2d7",
    "violet": "#eebefa", "фиолетовый": "#eebefa",
    "blue": "#a5d8ff", "синий": "#a5d8ff",
    "cyan": "#99e9f2", "голубой": "#99e9f2",
    "teal": "#96f2d7", "бирюзовый": "#96f2d7",
    "green": "#b2f2bb", "зелёный": "#b2f2bb", "зеленый": "#b2f2bb",
    "yellow": "#ffec99", "жёлтый": "#ffec99", "желтый": "#ffec99",
    "orange": "#ffd8a8", "оранжевый": "#ffd8a8",
    "transparent": "transparent", "нет": "transparent",
}
FONTS = {"hand": 5, "excalifont": 5, "virgil": 1, "normal": 6, "nunito": 6, "sans": 2, "helvetica": 2,
         "code": 3, "mono": 3, "cascadia": 3, "comic": 8, "lilita": 7}
FONT_NAMES = {1: "Virgil", 2: "Helvetica", 3: "Cascadia", 5: "Excalifont", 6: "Nunito", 7: "Lilita One",
              8: "Comic Shanns"}
LINE_HEIGHT = 1.25
GAP = 120                                    # отступ при автоматической раскладке (место для стрелок)


def empty_board() -> dict:
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "pulse-excalidraw",
        "elements": [],
        "appState": {"gridSize": 20, "viewBackgroundColor": "#ffffff"},
        "files": {},
    }


def parse(data: bytes) -> dict:
    """Файл доски → dict. Пустой файл — пустая доска; не Excalidraw — понятная ошибка."""
    if not data.strip():
        return empty_board()
    try:
        board = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandError(f"Файл не похож на доску Excalidraw: {exc}") from None
    if not isinstance(board, dict) or not isinstance(board.get("elements", []), list):
        raise CommandError("Файл не похож на доску Excalidraw: нет списка elements")
    board.setdefault("type", "excalidraw")
    board.setdefault("version", 2)
    board.setdefault("elements", [])
    board.setdefault("appState", {})
    board.setdefault("files", {})
    return board


def dump(board: dict) -> bytes:
    return json.dumps(board, ensure_ascii=False, indent=2).encode("utf-8")


def alive(board: dict) -> list[dict]:
    return [e for e in board.get("elements") or [] if isinstance(e, dict) and not e.get("isDeleted")]


def by_id(board: dict) -> dict[str, dict]:
    return {e["id"]: e for e in alive(board) if e.get("id")}


# --- описание для агентов -----------------------------------------------------------------------------

def _num(value: Any) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def bound_text(board: dict, element: dict) -> str:
    """Подпись фигуры или стрелки — её связанный текстовый элемент."""
    for ref in element.get("boundElements") or []:
        if ref.get("type") == "text":
            text = by_id(board).get(ref.get("id"))
            if text:
                return text.get("originalText") or text.get("text") or ""
    return ""


def label_of(board: dict, element: dict) -> str:
    if element.get("type") == "text":
        return element.get("originalText") or element.get("text") or ""
    if element.get("type") in ("frame", "magicframe"):
        return element.get("name") or ""
    return bound_text(board, element)


def compact(board: dict, element: dict) -> dict:
    """Элемент без служебных полей — то, что нужно агенту, чтобы понять и поправить доску."""
    kind = element.get("type")
    if (element.get("customData") or {}).get("pulse") == "sticky":
        kind = "sticky"
    item: dict[str, Any] = {"id": element.get("id"), "type": kind,
                            "x": _num(element.get("x")), "y": _num(element.get("y")),
                            "width": _num(element.get("width")), "height": _num(element.get("height"))}
    label = label_of(board, element)
    if label:
        item["text"] = label
    if kind == "text" and element.get("containerId"):
        item["container"] = element["containerId"]
    for key in ("strokeColor", "backgroundColor"):
        value = element.get(key)
        if value and value not in ("#1e1e1e", "#000000", "transparent"):
            item[key] = value
    if element.get("angle"):
        item["angle"] = round(math.degrees(element["angle"]), 1)
    if kind in LINEAR:
        item["points"] = [[_num(p[0]), _num(p[1])] for p in element.get("points") or []]
        for end in ("start", "end"):
            binding = element.get(f"{end}Binding") or {}
            if binding.get("elementId"):
                item["from" if end == "start" else "to"] = binding["elementId"]
    if element.get("frameId"):
        item["frame"] = element["frameId"]
    if element.get("groupIds"):
        item["groups"] = element["groupIds"]
    if element.get("link"):
        item["link"] = element["link"]
    if element.get("locked"):
        item["locked"] = True
    return item


def elements_for_agent(board: dict) -> list[dict]:
    """Все видимые элементы, кроме подписей фигур: подпись уже стоит в поле text своей фигуры."""
    items = []
    for element in alive(board):
        if element.get("type") == "text" and element.get("containerId") and element["containerId"] in by_id(board):
            continue
        items.append(compact(board, element))
    return items


def bounds(elements: list[dict]) -> tuple[float, float, float, float] | None:
    boxes = [element_box(e) for e in elements]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def element_box(e: dict) -> tuple[float, float, float, float]:
    x, y = float(e.get("x") or 0), float(e.get("y") or 0)
    if e.get("type") in (*LINEAR, "freedraw") and e.get("points"):
        xs = [x + float(p[0]) for p in e["points"]]
        ys = [y + float(p[1]) for p in e["points"]]
        return min(xs), min(ys), max(xs), max(ys)
    w, h = float(e.get("width") or 0), float(e.get("height") or 0)
    return min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h)


def describe(board: dict, selected: list[str] | None = None) -> str:
    """Текстовое описание доски: рамки, фигуры с подписями, свободный текст, связи стрелками."""
    index = by_id(board)
    items = elements_for_agent(board)
    if not items:
        return "Доска пустая."
    names = {}
    for item in items:
        text = (item.get("text") or "").replace("\n", " ").strip()
        names[item["id"]] = f"{_kind_ru(item['type'])} «{text[:60]}»" if text else f"{_kind_ru(item['type'])} {item['id']}"
    lines = []
    frames = [i for i in items if i["type"] in ("frame", "magicframe")]
    box = bounds(alive(board))
    lines.append(f"Элементов: {len(items)}; область {box[2] - box[0]:.0f}×{box[3] - box[1]:.0f}"
                 f" (x {box[0]:.0f}…{box[2]:.0f}, y {box[1]:.0f}…{box[3]:.0f})." if box else f"Элементов: {len(items)}.")

    def line_for(item: dict) -> str:
        if item["type"] in LINEAR:
            a, b = item.get("from"), item.get("to")
            label = f" «{item['text']}»" if item.get("text") else ""
            if a or b:
                return (f"- {_kind_ru(item['type'])}{label} [{item['id']}]: {names.get(a, a or 'свободный конец')}"
                        f" → {names.get(b, b or 'свободный конец')}")
            return f"- {_kind_ru(item['type'])}{label} [{item['id']}] из ({item['x']:.0f}, {item['y']:.0f})"
        extra = []
        if item.get("backgroundColor"):
            extra.append(f"заливка {item['backgroundColor']}")
        if item.get("link"):
            extra.append(f"ссылка {item['link']}")
        tail = f"; {', '.join(extra)}" if extra else ""
        return (f"- {names[item['id']]} [{item['id']}] в ({item['x']:.0f}, {item['y']:.0f}), "
                f"{item['width']:.0f}×{item['height']:.0f}{tail}")

    in_frame = {f["id"]: [] for f in frames}
    loose = []
    for item in items:
        if item["type"] in ("frame", "magicframe"):
            continue
        (in_frame[item["frame"]] if item.get("frame") in in_frame else loose).append(item)
    for frame in frames:
        lines.append(f"\nРамка «{frame.get('text') or frame['id']}» [{frame['id']}]:")
        lines.extend(line_for(i) for i in in_frame[frame["id"]]) if in_frame[frame["id"]] else lines.append("- пусто")
    if loose:
        if frames:
            lines.append("\nВне рамок:")
        lines.extend(line_for(i) for i in loose)
    if selected:
        chosen = [names.get(s) or (f"{_kind_ru(index[s]['type'])} {s}" if s in index else s) for s in selected]
        lines.append(f"\nВыделено в редакторе: {', '.join(chosen)}")
    return "\n".join(lines)


def _kind_ru(kind: str) -> str:
    return {"sticky": "заметка", "rectangle": "прямоугольник", "ellipse": "эллипс", "diamond": "ромб", "arrow": "стрелка",
            "line": "линия", "text": "текст", "frame": "рамка", "magicframe": "рамка", "freedraw": "рисунок",
            "image": "картинка", "embeddable": "встройка", "iframe": "встройка"}.get(kind, kind)


# --- создание и правка элементов ----------------------------------------------------------------------

def _rid(size: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(random.choice(alphabet) for _ in range(size))


def _seed() -> int:
    return random.randint(1, 2**31 - 1)


def _now_ms() -> int:
    return int(time.time() * 1000)


def color(value: Any, fill: bool = False) -> str:
    text = str(value).strip()
    if not text:
        return "transparent" if fill else "#1e1e1e"
    table = FILLS if fill else COLORS
    return table.get(text.lower(), text)


def _base(kind: str, x: float, y: float, width: float, height: float) -> dict:
    return {
        "id": _rid(), "type": kind, "x": x, "y": y, "width": width, "height": height, "angle": 0,
        "strokeColor": "#1e1e1e", "backgroundColor": "transparent", "fillStyle": "solid", "strokeWidth": 2,
        "strokeStyle": "solid", "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None, "index": None,
        "roundness": None, "seed": _seed(), "version": 1, "versionNonce": _seed(), "isDeleted": False,
        "boundElements": None, "updated": _now_ms(), "link": None, "locked": False,
    }


def text_size(text: str, font_size: float, family: int = 5) -> tuple[float, float]:
    """Примерный размер текста: точный редактор пересчитает при открытии, для раскладки этого хватает."""
    lines = text.split("\n") or [""]
    factor = {3: 0.6, 2: 0.55, 6: 0.55}.get(family, 0.58)
    width = max(len(line) for line in lines) * font_size * factor
    return max(width, font_size * 0.6), len(lines) * font_size * LINE_HEIGHT


def touch(element: dict) -> None:
    """Элемент изменён агентом: редактор и слияние должны увидеть новую версию."""
    element["version"] = int(element.get("version") or 1) + 1
    element["versionNonce"] = _seed()
    element["updated"] = _now_ms()


def _text_element(text: str, x: float, y: float, spec: dict, container: dict | None = None) -> dict:
    size = float(spec.get("fontSize") or (20 if container is None else 20))
    family = _font(spec.get("font") or spec.get("fontFamily"))
    width, height = text_size(text, size, family)
    element = _base("text", x, y, width, height)
    element.update({
        "text": text, "originalText": text, "fontSize": size, "fontFamily": family,
        "textAlign": spec.get("textAlign") or ("center" if container else "left"),
        "verticalAlign": spec.get("verticalAlign") or ("middle" if container else "top"),
        "containerId": container["id"] if container else None, "autoResize": True, "lineHeight": LINE_HEIGHT,
        "strokeColor": color(spec.get("textColor") or spec.get("strokeColor") or "#1e1e1e"),
    })
    if container is not None:
        _place_label(container, element)
    return element


def _font(value: Any) -> int:
    if value in (None, ""):
        return 5
    if isinstance(value, int) or str(value).isdigit():
        return int(value)
    return FONTS.get(str(value).lower(), 5)


def _place_label(container: dict, text: dict) -> None:
    """Подпись по центру фигуры (у стрелки — по середине пути)."""
    if container.get("type") in LINEAR:
        points = container.get("points") or [[0, 0]]
        mid = points[len(points) // 2] if len(points) > 2 else [(points[0][0] + points[-1][0]) / 2,
                                                              (points[0][1] + points[-1][1]) / 2]
        cx, cy = container["x"] + mid[0], container["y"] + mid[1]
    else:
        cx = container["x"] + container["width"] / 2
        cy = container["y"] + container["height"] / 2
        inner = max(container["width"] - 20, 20)
        if text["width"] > inner:             # не влезает в строку — переносим по словам
            per_line = max(int(inner / (text["fontSize"] * 0.58)), 4)
            wrapped = _wrap(text["originalText"], per_line)
            text["text"] = wrapped
            text["width"], text["height"] = text_size(wrapped, text["fontSize"], text["fontFamily"])
            if text["height"] + 20 > container["height"]:
                container["height"] = text["height"] + 40
                cy = container["y"] + container["height"] / 2
    text["x"] = cx - text["width"] / 2
    text["y"] = cy - text["height"] / 2


def _wrap(text: str, per_line: int) -> str:
    out = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            if line and len(line) + 1 + len(word) > per_line:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}" if line else word
        out.append(line)
    return "\n".join(out)


def _bind(element: dict, ref_id: str, ref_type: str) -> None:
    refs = list(element.get("boundElements") or [])
    if not any(r.get("id") == ref_id for r in refs):
        refs.append({"id": ref_id, "type": ref_type})
    element["boundElements"] = refs


def _next_spot(board: dict, width: float, height: float, placed: list[dict]) -> tuple[float, float]:
    """Свободное место: правее того, что агент уже поставил в этой команде, иначе под всей доской."""
    if placed:
        box = bounds(placed[-1:])
        return box[2] + GAP, box[1]
    box = bounds(alive(board))
    if box is None:
        return 100.0, 100.0
    return box[0], box[3] + GAP


def _edge_point(shape: dict, toward: tuple[float, float]) -> tuple[float, float]:
    """Точка на границе фигуры в сторону другой точки — туда упирается стрелка."""
    cx, cy = shape["x"] + shape["width"] / 2, shape["y"] + shape["height"] / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = shape["width"] / 2 + 8, shape["height"] / 2 + 8
    if shape["type"] == "ellipse":
        t = 1 / math.sqrt((dx / hw) ** 2 + (dy / hh) ** 2)
    elif shape["type"] == "diamond":
        t = 1 / (abs(dx) / hw + abs(dy) / hh)
    else:
        t = min(hw / abs(dx) if dx else math.inf, hh / abs(dy) if dy else math.inf)
    return cx + dx * t, cy + dy * t


def _center(shape: dict) -> tuple[float, float]:
    return shape["x"] + shape["width"] / 2, shape["y"] + shape["height"] / 2


def build(board: dict, specs: list[dict]) -> list[dict]:
    """Новые элементы по простому описанию агента. Возвращает созданные (с подписями)."""
    if not isinstance(specs, list):
        raise CommandError("elements — список элементов JSON: [{\"type\": \"rectangle\", \"text\": \"…\"}]")
    index = by_id(board)
    created: list[dict] = []
    placed: list[dict] = []
    aliases: dict[str, dict] = {}            # ссылки внутри одной команды: {"id": "a"} … {"from": "a"}
    pending_arrows = []
    for raw in specs:
        if not isinstance(raw, dict):
            raise CommandError(f"Элемент должен быть объектом JSON: {raw!r}")
        spec = dict(raw)
        kind = str(spec.get("type") or ("text" if spec.get("text") and not spec.get("label") else "rectangle"))
        kind = {"rect": "rectangle", "box": "rectangle", "circle": "ellipse", "sticky": "rectangle",
                "note": "rectangle", "connector": "arrow"}.get(kind, kind)
        sticky = raw.get("type") in ("sticky", "note")
        if kind not in (*SHAPES, *LINEAR, "text", "frame"):
            raise CommandError(f"Тип «{kind}» агент создать не может: rectangle, ellipse, diamond, text, arrow, "
                               "line, frame (и sticky — заметка)")
        if kind in LINEAR:
            pending_arrows.append(spec | {"type": kind})
            continue
        label = spec.get("label", spec.get("text") if kind != "text" else None)
        if kind == "text":
            text = str(spec.get("text") or "")
            if not text:
                raise CommandError("У текста нет text")
            family = _font(spec.get("font") or spec.get("fontFamily"))
            w, h = text_size(text, float(spec.get("fontSize") or 20), family)
        else:
            w = float(spec.get("width") or (200 if kind != "frame" else 800))
            h = float(spec.get("height") or (100 if kind != "frame" else 500))
            if sticky:
                w, h = float(spec.get("width") or 200), float(spec.get("height") or 200)
        if spec.get("x") is None or spec.get("y") is None:
            x, y = _next_spot(board, w, h, placed)
        else:
            x, y = float(spec["x"]), float(spec["y"])
        if kind == "text":
            element = _text_element(str(spec["text"]), x, y, spec)
        else:
            element = _base(kind, x, y, w, h)
            if kind == "rectangle":
                element["roundness"] = {"type": 3}
            elif kind == "diamond":
                element["roundness"] = {"type": 2}
            if kind == "frame":
                element.update({"name": str(spec.get("name") or label or "Рамка"), "strokeWidth": 2,
                                "roughness": 0, "strokeColor": "#bbb"})
                label = None
            if sticky:
                element["backgroundColor"] = "#ffec99"
                element["strokeColor"] = "#f08c00"
                element["roughness"] = 0
                element["customData"] = {"pulse": "sticky"}      # заметка: в описании — «заметка», не прямоугольник
        _style(element, spec)
        if spec.get("frame") or spec.get("frameId"):
            frame_ref = str(spec.get("frame") or spec.get("frameId"))
            frame = aliases.get(frame_ref) or index.get(frame_ref)
            if not frame or frame.get("type") not in ("frame", "magicframe"):
                raise CommandError(f"Рамки «{frame_ref}» нет на доске")
            element["frameId"] = frame["id"]
        created.append(element)
        placed.append(element)
        if spec.get("id"):
            aliases[str(spec["id"])] = element
        if label not in (None, "") and kind != "text":
            text = _text_element(str(label), 0, 0, spec, container=element)
            text["frameId"] = element.get("frameId")
            _bind(element, text["id"], "text")
            created.append(text)
    lookup = {**index, **aliases, **{e["id"]: e for e in created}}
    for spec in pending_arrows:
        created.extend(_arrow(board, spec, lookup, placed))
    board["elements"] = list(board.get("elements") or []) + created
    return created


def _arrow(board: dict, spec: dict, lookup: dict, placed: list[dict]) -> list[dict]:
    start = lookup.get(str(spec.get("from"))) if spec.get("from") else None
    end = lookup.get(str(spec.get("to"))) if spec.get("to") else None
    if spec.get("from") and not start:
        raise CommandError(f"Стрелка from: элемента «{spec['from']}» нет на доске")
    if spec.get("to") and not end:
        raise CommandError(f"Стрелка to: элемента «{spec['to']}» нет на доске")
    if spec.get("points"):
        x, y = float(spec.get("x") or 0), float(spec.get("y") or 0)
        points = [[float(p[0]), float(p[1])] for p in spec["points"]]
    elif start and end:
        a = _edge_point(start, _center(end))
        b = _edge_point(end, _center(start))
        x, y = a
        points = [[0.0, 0.0], [b[0] - a[0], b[1] - a[1]]]
    else:
        x0, y0 = (float(spec["x"]), float(spec["y"])) if spec.get("x") is not None else _next_spot(board, 200, 0, placed)
        x, y = x0, y0
        points = [[0.0, 0.0], [float(spec.get("width") or 200), float(spec.get("height") or 0)]]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    element = _base(spec["type"], x, y, max(xs) - min(xs), max(ys) - min(ys))
    element.update({"points": points, "lastCommittedPoint": None, "startBinding": None, "endBinding": None,
                    "startArrowhead": None, "endArrowhead": "arrow" if spec["type"] == "arrow" else None,
                    "elbowed": False, "roundness": {"type": 2}})
    if spec.get("startArrowhead") is not None:
        element["startArrowhead"] = spec["startArrowhead"] or None
    if spec.get("endArrowhead") is not None:
        element["endArrowhead"] = spec["endArrowhead"] or None
    _style(element, spec)
    for end_name, shape in (("startBinding", start), ("endBinding", end)):
        if shape:
            element[end_name] = {"elementId": shape["id"], "focus": 0, "gap": 8}
            _bind(shape, element["id"], "arrow")
            if shape in alive(board):
                touch(shape)
    out = [element]
    label = spec.get("label") or spec.get("text")
    if label:
        text = _text_element(str(label), 0, 0, {**spec, "fontSize": spec.get("fontSize") or 16}, container=element)
        _bind(element, text["id"], "text")
        out.append(text)
    return out


STYLE_KEYS = {"strokeWidth", "strokeStyle", "roughness", "opacity", "fillStyle", "link", "locked", "angle"}


def _style(element: dict, spec: dict) -> None:
    if spec.get("strokeColor") or spec.get("color"):
        element["strokeColor"] = color(spec.get("strokeColor") or spec.get("color"))
    if spec.get("backgroundColor") or spec.get("fill"):
        element["backgroundColor"] = color(spec.get("backgroundColor") or spec.get("fill"), fill=True)
    for key in STYLE_KEYS:
        if key in spec:
            element[key] = math.radians(float(spec[key])) if key == "angle" else spec[key]


EDITABLE = {"x", "y", "width", "height", "text", "label", "strokeColor", "backgroundColor", "color", "fill",
            "fontSize", "name", "points", *STYLE_KEYS}


def patch(board: dict, changes: list[dict]) -> list[dict]:
    """Правка элементов по id: координаты, размер, текст/подпись, цвета, стиль. Возвращает изменённые."""
    if not isinstance(changes, list):
        raise CommandError("elements — список правок JSON: [{\"id\": \"…\", \"text\": \"…\"}]")
    index = by_id(board)
    touched: dict[str, dict] = {}
    for change in changes:
        if not isinstance(change, dict) or not change.get("id"):
            raise CommandError(f"У правки нет id элемента: {change!r}")
        element = index.get(str(change["id"]))
        if element is None:
            raise CommandError(f"Элемента «{change['id']}» нет на доске (pulse excalidraw get — список)")
        unknown = set(change) - EDITABLE - {"id"}
        if unknown:
            raise CommandError(f"Эти поля так не меняются: {', '.join(sorted(unknown))}; можно: {', '.join(sorted(EDITABLE))}")
        dx = float(change["x"]) - element["x"] if "x" in change else 0.0
        dy = float(change["y"]) - element["y"] if "y" in change else 0.0
        for key in ("x", "y", "width", "height"):
            if key in change:
                element[key] = float(change[key])
        if "points" in change and element.get("type") in LINEAR:
            element["points"] = [[float(p[0]), float(p[1])] for p in change["points"]]
        if "name" in change and element.get("type") in ("frame", "magicframe"):
            element["name"] = str(change["name"])
        _style(element, change)
        new_text = change.get("text", change.get("label"))
        if element.get("type") == "text":
            if new_text is not None:
                element["text"] = element["originalText"] = str(new_text)
                element["width"], element["height"] = text_size(str(new_text), element.get("fontSize") or 20,
                                                                 element.get("fontFamily") or 5)
            if "fontSize" in change:
                element["fontSize"] = float(change["fontSize"])
                element["width"], element["height"] = text_size(element["originalText"], element["fontSize"],
                                                                 element.get("fontFamily") or 5)
        elif new_text is not None:
            _relabel(board, element, str(new_text), change, touched)
        # фигуру подвинули — подпись едет с ней, стрелки к ней перестраиваются в редакторе по привязке
        if dx or dy or "width" in change or "height" in change:
            for ref in element.get("boundElements") or []:
                bound = index.get(ref.get("id"))
                if bound and ref.get("type") == "text":
                    _place_label(element, bound)
                    touch(bound)
                    touched[bound["id"]] = bound
                elif bound and ref.get("type") == "arrow":
                    _reroute(bound, index)
                    touch(bound)
                    touched[bound["id"]] = bound
                    for label_ref in bound.get("boundElements") or []:      # подпись стрелки — на середину пути
                        label = index.get(label_ref.get("id"))
                        if label and label_ref.get("type") == "text":
                            _place_label(bound, label)
                            touch(label)
                            touched[label["id"]] = label
            if element.get("type") in ("frame", "magicframe") and (dx or dy):
                for child in alive(board):
                    if child.get("frameId") == element["id"]:
                        child["x"] += dx
                        child["y"] += dy
                        touch(child)
                        touched[child["id"]] = child
        touch(element)
        touched[element["id"]] = element
    return list(touched.values())


def _relabel(board: dict, element: dict, text: str, spec: dict, touched: dict) -> None:
    index = by_id(board)
    for ref in element.get("boundElements") or []:
        bound = index.get(ref.get("id"))
        if bound and ref.get("type") == "text":
            if not text:
                bound["isDeleted"] = True
                element["boundElements"] = [r for r in element["boundElements"] if r.get("id") != bound["id"]]
            else:
                bound["text"] = bound["originalText"] = text
                bound["width"], bound["height"] = text_size(text, bound.get("fontSize") or 20, bound.get("fontFamily") or 5)
                _place_label(element, bound)
            touch(bound)
            touched[bound["id"]] = bound
            return
    if text:
        label = _text_element(text, 0, 0, spec, container=element)
        label["frameId"] = element.get("frameId")
        _bind(element, label["id"], "text")
        board["elements"].append(label)
        touched[label["id"]] = label


def _reroute(arrow: dict, index: dict) -> None:
    start = index.get((arrow.get("startBinding") or {}).get("elementId"))
    end = index.get((arrow.get("endBinding") or {}).get("elementId"))
    points = arrow.get("points") or [[0, 0], [0, 0]]
    absolute = [[arrow["x"] + p[0], arrow["y"] + p[1]] for p in points]
    if start:
        absolute[0] = list(_edge_point(start, _center(end) if end else tuple(absolute[-1])))
    if end:
        absolute[-1] = list(_edge_point(end, _center(start) if start else tuple(absolute[0])))
    arrow["x"], arrow["y"] = absolute[0]
    arrow["points"] = [[p[0] - absolute[0][0], p[1] - absolute[0][1]] for p in absolute]
    xs, ys = [p[0] for p in arrow["points"]], [p[1] for p in arrow["points"]]
    arrow["width"], arrow["height"] = max(xs) - min(xs), max(ys) - min(ys)


def remove(board: dict, ids: list[str]) -> list[str]:
    """Удалить элементы (с подписями и стрелками к ним). Возвращает id удалённых."""
    index = by_id(board)
    missing = [i for i in ids if i not in index]
    if missing:
        raise CommandError(f"Нет на доске: {', '.join(missing)}")
    doomed = set(ids)
    for element_id in ids:
        element = index[element_id]
        for ref in element.get("boundElements") or []:
            if ref.get("type") == "text":
                doomed.add(ref.get("id"))
        if element.get("type") in ("frame", "magicframe"):
            for child in alive(board):
                if child.get("frameId") == element_id:
                    child["frameId"] = None
                    touch(child)
    for element in alive(board):
        if element["id"] in doomed:
            continue
        for end in ("startBinding", "endBinding"):
            if (element.get(end) or {}).get("elementId") in doomed:
                element[end] = None
                touch(element)
        refs = element.get("boundElements") or []
        if any(r.get("id") in doomed for r in refs):
            element["boundElements"] = [r for r in refs if r.get("id") not in doomed] or None
            touch(element)
    # удалённые убираем из файла: открытый редактор поймёт это при слиянии (элемента нет в новой версии файла)
    board["elements"] = [e for e in board.get("elements") or [] if e.get("id") not in doomed]
    return sorted(doomed)


def prune_files(board: dict) -> None:
    """Картинки, на которые больше не ссылается ни один элемент, из файла доски убираются."""
    used = {e.get("fileId") for e in alive(board) if e.get("type") == "image"}
    board["files"] = {k: v for k, v in (board.get("files") or {}).items() if k in used}
