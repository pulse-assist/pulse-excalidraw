"""Доска → SVG (и PNG через cairosvg) без браузера — чтобы агент мог «посмотреть» доску и выгрузить картинку.

Рисует чисто, без «рукописной» дрожи Excalidraw: фигуры, подписи, стрелки, рамки, рисунки от руки и картинки.
Точную картинку в стиле Excalidraw даёт сам редактор (меню «Экспорт» у владельца).
"""

from __future__ import annotations

import math
from html import escape

from . import scene

PAD = 30
FONT_CSS = {1: "Virgil, 'Comic Sans MS', cursive", 2: "Helvetica, Arial, 'DejaVu Sans', sans-serif",
            3: "'Cascadia Code', 'DejaVu Sans Mono', monospace", 5: "Excalifont, 'Comic Sans MS', 'DejaVu Sans', cursive",
            6: "Nunito, 'DejaVu Sans', sans-serif", 7: "'Lilita One', 'DejaVu Sans', sans-serif",
            8: "'Comic Shanns', 'DejaVu Sans Mono', monospace"}


def _dash(e: dict) -> str:
    width = float(e.get("strokeWidth") or 2)
    if e.get("strokeStyle") == "dashed":
        return f' stroke-dasharray="{width * 4:.0f} {width * 3:.0f}"'
    if e.get("strokeStyle") == "dotted":
        return f' stroke-dasharray="{width:.0f} {width * 2.5:.0f}"'
    return ""


def _paint(e: dict, fill: bool = True) -> str:
    stroke = e.get("strokeColor") or "#1e1e1e"
    background = e.get("backgroundColor") or "transparent"
    fill_value = background if fill and background != "transparent" else "none"
    opacity = float(e.get("opacity", 100)) / 100
    fill_opacity = ' fill-opacity="0.55"' if fill and e.get("fillStyle") in ("hachure", "cross-hatch") else ""
    return (f' stroke="{escape(stroke)}" stroke-width="{float(e.get("strokeWidth") or 2):g}" fill="{escape(fill_value)}"'
            f'{fill_opacity} opacity="{opacity:g}" stroke-linejoin="round" stroke-linecap="round"{_dash(e)}')


def _rotate(e: dict) -> str:
    angle = float(e.get("angle") or 0)
    if not angle:
        return ""
    cx = e["x"] + e.get("width", 0) / 2
    cy = e["y"] + e.get("height", 0) / 2
    return f' transform="rotate({math.degrees(angle):.2f} {cx:.1f} {cy:.1f})"'


def _shape(e: dict) -> str:
    x, y, w, h = e["x"], e["y"], e.get("width", 0), e.get("height", 0)
    kind = e["type"]
    if kind == "rectangle":
        r = min(w, h) * 0.12 if e.get("roundness") else 0
        r = min(r, 32)
        return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r:.1f}"{_paint(e)}{_rotate(e)}/>'
    if kind == "ellipse":
        return (f'<ellipse cx="{x + w / 2:.1f}" cy="{y + h / 2:.1f}" rx="{w / 2:.1f}" ry="{h / 2:.1f}"'
                f'{_paint(e)}{_rotate(e)}/>')
    if kind == "diamond":
        pts = f"{x + w / 2:.1f},{y:.1f} {x + w:.1f},{y + h / 2:.1f} {x + w / 2:.1f},{y + h:.1f} {x:.1f},{y + h / 2:.1f}"
        return f'<polygon points="{pts}"{_paint(e)}{_rotate(e)}/>'
    return ""


def _arrowhead(tip: tuple[float, float], prev: tuple[float, float], e: dict, kind: str | None) -> str:
    if not kind:
        return ""
    angle = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
    size = 10 + float(e.get("strokeWidth") or 2) * 3
    stroke = escape(e.get("strokeColor") or "#1e1e1e")
    width = float(e.get("strokeWidth") or 2)
    if kind in ("dot", "circle", "circle_outline"):
        fill = stroke if kind != "circle_outline" else "none"
        return f'<circle cx="{tip[0]:.1f}" cy="{tip[1]:.1f}" r="{size / 3:.1f}" fill="{fill}" stroke="{stroke}"/>'
    if kind == "bar":
        a = angle + math.pi / 2
        dx, dy = math.cos(a) * size / 2, math.sin(a) * size / 2
        return (f'<line x1="{tip[0] - dx:.1f}" y1="{tip[1] - dy:.1f}" x2="{tip[0] + dx:.1f}" y2="{tip[1] + dy:.1f}" '
                f'stroke="{stroke}" stroke-width="{width:g}"/>')
    left = (tip[0] - size * math.cos(angle - 0.45), tip[1] - size * math.sin(angle - 0.45))
    right = (tip[0] - size * math.cos(angle + 0.45), tip[1] - size * math.sin(angle + 0.45))
    if kind in ("triangle", "triangle_outline", "diamond", "diamond_outline"):
        fill = stroke if not kind.endswith("outline") else "none"
        return (f'<polygon points="{tip[0]:.1f},{tip[1]:.1f} {left[0]:.1f},{left[1]:.1f} {right[0]:.1f},{right[1]:.1f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{width:g}" stroke-linejoin="round"/>')
    return (f'<polyline points="{left[0]:.1f},{left[1]:.1f} {tip[0]:.1f},{tip[1]:.1f} {right[0]:.1f},{right[1]:.1f}" '
            f'fill="none" stroke="{stroke}" stroke-width="{width:g}" stroke-linecap="round" stroke-linejoin="round"/>')


def _linear(e: dict) -> str:
    pts = [(e["x"] + float(p[0]), e["y"] + float(p[1])) for p in e.get("points") or []]
    if len(pts) < 2:
        return ""
    closed = e["type"] == "line" and len(pts) > 2 and pts[0] == pts[-1]
    path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    tag = "polygon" if closed else "polyline"
    out = f'<{tag} points="{path}"{_paint(e, fill=closed)}/>'
    if e["type"] == "arrow":
        out += _arrowhead(pts[-1], pts[-2], e, e.get("endArrowhead"))
        out += _arrowhead(pts[0], pts[1], e, e.get("startArrowhead"))
    return out


def _freedraw(e: dict) -> str:
    pts = [(e["x"] + float(p[0]), e["y"] + float(p[1])) for p in e.get("points") or []]
    if not pts:
        return ""
    path = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
    stroke = escape(e.get("strokeColor") or "#1e1e1e")
    return (f'<path d="{path}" fill="none" stroke="{stroke}" stroke-width="{float(e.get("strokeWidth") or 2) * 1.5:g}" '
            f'stroke-linecap="round" stroke-linejoin="round" opacity="{float(e.get("opacity", 100)) / 100:g}"/>')


def _text(e: dict) -> str:
    size = float(e.get("fontSize") or 20)
    family = FONT_CSS.get(int(e.get("fontFamily") or 5), FONT_CSS[5])
    lines = str(e.get("text") or "").split("\n")
    align = e.get("textAlign") or "left"
    anchor = {"left": "start", "center": "middle", "right": "end"}[align] if align in ("left", "center", "right") else "start"
    x = e["x"] + {"start": 0, "middle": e.get("width", 0) / 2, "end": e.get("width", 0)}[anchor]
    line_height = size * float(e.get("lineHeight") or scene.LINE_HEIGHT)
    fill = escape(e.get("strokeColor") or "#1e1e1e")
    spans = []
    for i, line in enumerate(lines):
        y = e["y"] + line_height * i + line_height / 2
        spans.append(f'<tspan x="{x:.1f}" y="{y:.1f}">{escape(line) or " "}</tspan>')
    return (f'<text font-family="{escape(family)}" font-size="{size:g}" fill="{fill}" text-anchor="{anchor}" '
            f'dominant-baseline="central" opacity="{float(e.get("opacity", 100)) / 100:g}"{_rotate(e)}>'
            f'{"".join(spans)}</text>')


def _frame(e: dict) -> str:
    x, y, w, h = e["x"], e["y"], e.get("width", 0), e.get("height", 0)
    name = escape(e.get("name") or "Рамка")
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" fill="none" stroke="#bbb" '
            f'stroke-width="1.5"/><text x="{x:.1f}" y="{y - 8:.1f}" font-family="{FONT_CSS[2]}" font-size="14" '
            f'fill="#868e96">{name}</text>')


def _image(e: dict, files: dict) -> str:
    data = (files.get(e.get("fileId")) or {}).get("dataURL")
    x, y, w, h = e["x"], e["y"], e.get("width", 0), e.get("height", 0)
    if not data:
        return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="#f1f3f5" stroke="#ced4da"/>')
    return (f'<image x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" href="{escape(data)}" '
            f'preserveAspectRatio="none"{_rotate(e)}/>')


def to_svg(board: dict, ids: list[str] | None = None, background: bool = True) -> str:
    elements = scene.alive(board)
    if ids:
        wanted = set(ids)
        for e in elements:                       # с фигурой — её подпись, с рамкой — её содержимое
            if e["id"] in wanted:
                wanted.update(r.get("id") for r in e.get("boundElements") or [] if r.get("type") == "text")
        wanted.update(e["id"] for e in elements if e.get("frameId") in wanted)
        elements = [e for e in elements if e["id"] in wanted]
    box = scene.bounds(elements) or (0, 0, 200, 100)
    x0, y0 = box[0] - PAD, box[1] - PAD - 20
    width, height = box[2] - box[0] + PAD * 2, box[3] - box[1] + PAD * 2 + 20
    bg = (board.get("appState") or {}).get("viewBackgroundColor") or "#ffffff"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
             f'viewBox="{x0:.1f} {y0:.1f} {width:.1f} {height:.1f}" width="{width:.0f}" height="{height:.0f}">']
    if background:
        parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{width:.1f}" height="{height:.1f}" fill="{escape(bg)}"/>')
    files = board.get("files") or {}
    index = {e["id"]: e for e in elements}
    # рамки — под всем остальным, как в редакторе
    for e in sorted(elements, key=lambda item: 0 if item["type"] in ("frame", "magicframe") else 1):
        kind = e["type"]
        if kind in scene.SHAPES:
            parts.append(_shape(e))
        elif kind in scene.LINEAR:
            parts.append(_linear(e))
        elif kind == "text":
            container = index.get(e.get("containerId"))
            if container and container["type"] in scene.LINEAR:     # подпись стрелки — на подложке, как в редакторе
                parts.append(f'<rect x="{e["x"] - 4:.1f}" y="{e["y"]:.1f}" width="{e.get("width", 0) + 8:.1f}" '
                             f'height="{e.get("height", 0):.1f}" fill="{escape(bg)}"/>')
            parts.append(_text(e))
        elif kind == "freedraw":
            parts.append(_freedraw(e))
        elif kind == "image":
            parts.append(_image(e, files))
        elif kind in ("frame", "magicframe"):
            parts.append(_frame(e))
        elif kind in ("embeddable", "iframe"):
            parts.append(_shape({**e, "type": "rectangle", "backgroundColor": "#f1f3f5"}))
    parts.append("</svg>")
    return "".join(parts)


def to_png(board: dict, ids: list[str] | None = None, scale: float = 2.0, background: bool = True) -> bytes:
    import cairosvg

    svg = to_svg(board, ids, background)
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), scale=max(0.5, min(scale, 4.0)))
