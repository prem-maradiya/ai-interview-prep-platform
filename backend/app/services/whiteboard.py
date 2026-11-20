"""System design whiteboard: a small, validated scene of boxes, databases, text and arrows.

The same format is used in AI system design interviews (the interviewer reads a text description
of the diagram) and in peer interviews (both people draw; changes sync over the room's WebSocket).

Board = {"elements": [element, ...]}, element =
  {"id", "type": "box" | "db" | "queue" | "text" | "arrow", "x", "y", "w", "h", "label",
   "from"/"to": element ids (arrows; an arrow may also float with x/y/x2/y2), "x2", "y2"}
Changes are operations - {"op": "put", "element"} / {"op": "delete", "id"} / {"op": "clear"} - so
two people editing different shapes never overwrite each other (last write wins per shape).
"""
import re

MAX_ELEMENTS = 300
MAX_LABEL = 200
COORD = 20_000
TYPES = {"box", "db", "queue", "text", "arrow"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
TYPE_NAMES = {"box": "component", "db": "database", "queue": "queue", "text": "note"}


class BoardError(ValueError):
    pass


def _num(v, lo=-COORD, hi=COORD) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise BoardError("coordinates must be numbers")
    if f != f:  # NaN
        raise BoardError("coordinates must be numbers")
    return round(max(lo, min(hi, f)), 1)


def clean_element(e: dict) -> dict:
    if not isinstance(e, dict):
        raise BoardError("an element must be an object")
    eid, typ = str(e.get("id", "")), e.get("type")
    if not ID_RE.match(eid) or typ not in TYPES:
        raise BoardError("each element needs a valid id and type")
    out = {"id": eid, "type": typ, "label": str(e.get("label", ""))[:MAX_LABEL]}
    if typ == "arrow":
        for end in ("from", "to"):
            if e.get(end) is not None:
                ref = str(e[end])
                if not ID_RE.match(ref):
                    raise BoardError("arrow ends must be element ids")
                out[end] = ref
        for k in ("x", "y", "x2", "y2"):
            out[k] = _num(e.get(k, 0))
    else:
        out.update(x=_num(e.get("x", 0)), y=_num(e.get("y", 0)),
                   w=_num(e.get("w", 140), 10, 2000), h=_num(e.get("h", 60), 10, 2000))
    return out


def clean_board(board: dict | None) -> dict:
    elements = (board or {}).get("elements", [])
    if not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        raise BoardError(f"a board holds at most {MAX_ELEMENTS} elements")
    seen, out = set(), []
    for e in elements:
        c = clean_element(e)
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return {"elements": out}


def apply_ops(board: dict | None, ops: list) -> dict:
    """Apply put/delete/clear operations; returns a new board. Raises BoardError on bad input."""
    if not isinstance(ops, list) or len(ops) > 100:
        raise BoardError("send at most 100 operations at a time")
    elements = {e["id"]: e for e in (board or {}).get("elements", [])}
    for op in ops:
        kind = op.get("op") if isinstance(op, dict) else None
        if kind == "put":
            e = clean_element(op.get("element"))
            elements[e["id"]] = e
        elif kind == "delete":
            eid = str(op.get("id", ""))
            elements.pop(eid, None)
            for k in [k for k, v in elements.items() if v["type"] == "arrow" and eid in (v.get("from"), v.get("to"))]:
                elements.pop(k)  # arrows attached to a deleted shape go with it
        elif kind == "clear":
            elements = {}
        else:
            raise BoardError("unknown operation")
        if len(elements) > MAX_ELEMENTS:
            raise BoardError(f"a board holds at most {MAX_ELEMENTS} elements")
    return {"elements": list(elements.values())}


def describe(board: dict | None) -> str:
    """Plain-text version of the diagram, for the AI interviewer (and the transcript)."""
    elements = (board or {}).get("elements", [])
    shapes = {e["id"]: e for e in elements if e["type"] != "arrow"}
    if not shapes and not any(e["type"] == "arrow" for e in elements):
        return ""
    name = lambda e: e["label"].strip() or f"unlabelled {TYPE_NAMES[e['type']]}"  # noqa: E731
    lines = ["Components:"]
    for e in sorted(shapes.values(), key=lambda e: (e["y"], e["x"])):
        if e["type"] != "text":
            lines.append(f"- {name(e)} ({TYPE_NAMES[e['type']]})")
    notes = [e for e in shapes.values() if e["type"] == "text" and e["label"].strip()]
    arrows = []
    for e in elements:
        if e["type"] != "arrow":
            continue
        src, dst = shapes.get(e.get("from", "")), shapes.get(e.get("to", ""))
        if src and dst:
            arrows.append(f"- {name(src)} -> {name(dst)}" + (f": {e['label']}" if e["label"].strip() else ""))
    if arrows:
        lines += ["Connections:"] + arrows
    if notes:
        lines += ["Notes:"] + [f"- {n['label']}" for n in notes]
    return "\n".join(lines)
