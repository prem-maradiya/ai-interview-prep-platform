import { useEffect, useRef, useState } from "react";

export type El = {
  id: string; type: "box" | "db" | "queue" | "text" | "arrow"; label: string;
  x: number; y: number; w?: number; h?: number; from?: string; to?: string; x2?: number; y2?: number;
};
export type Board = { elements: El[] };
export type Op = { op: "put"; element: El } | { op: "delete"; id: string } | { op: "clear" };

type Tool = "select" | "box" | "db" | "queue" | "text" | "arrow";
const TOOLS: { id: Tool; label: string; title: string }[] = [
  { id: "select", label: "↖ Select", title: "Select and drag shapes" },
  { id: "box", label: "▭ Service", title: "Add a service/component" },
  { id: "db", label: "⛁ Database", title: "Add a database" },
  { id: "queue", label: "☰ Queue", title: "Add a queue or stream" },
  { id: "text", label: "T Note", title: "Add a note" },
  { id: "arrow", label: "→ Arrow", title: "Click one shape, then another, to connect them" },
];
const DEFAULT_LABEL: Record<string, string> = { box: "Service", db: "Database", queue: "Queue", text: "Note", arrow: "" };
// Canvas units. Kept modest so labels stay readable when the board is only ~450px wide.
const W = 900;
const H = 560;
const BOX = { w: 170, h: 64 };

export function applyOps(board: Board, ops: Op[]): Board {
  let els = [...board.elements];
  for (const op of ops) {
    if (op.op === "clear") els = [];
    else if (op.op === "delete") els = els.filter((e) => e.id !== op.id && e.from !== op.id && e.to !== op.id);
    else {
      const i = els.findIndex((e) => e.id === op.element.id);
      if (i >= 0) els[i] = op.element;
      else els.push(op.element);
    }
  }
  return { elements: els };
}

const newId = () => Math.random().toString(36).slice(2, 10);

/** Where the line from a shape's centre towards (tx, ty) leaves the shape's rectangle. */
function edgePoint(e: El, tx: number, ty: number) {
  const w = e.w ?? BOX.w, h = e.h ?? BOX.h, cx = e.x + w / 2, cy = e.y + h / 2;
  const dx = tx - cx, dy = ty - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const s = Math.min(Math.abs((w / 2) / (dx || 1e-9)), Math.abs((h / 2) / (dy || 1e-9)));
  return { x: cx + dx * s, y: cy + dy * s };
}

/**
 * Shared system-design whiteboard. Changes are reported as small operations (`onOps`) so they can
 * be sent to the other person and merged shape by shape; `board` updates from outside are shown as
 * they arrive.
 */
export default function Whiteboard({ board, onOps, readOnly = false }: { board: Board; onOps: (ops: Op[]) => void; readOnly?: boolean }) {
  const [tool, setTool] = useState<Tool>("select");
  const [selected, setSelected] = useState<string | null>(null);
  const [arrowFrom, setArrowFrom] = useState<string | null>(null);
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number; el: El } | null>(null);
  const [label, setLabel] = useState("");
  const svg = useRef<SVGSVGElement | null>(null);
  const labelInput = useRef<HTMLInputElement | null>(null);
  const focusLabel = useRef(false); // select the label of a shape we just placed, so typing replaces it

  const els = drag ? board.elements.map((e) => (e.id === drag.id ? drag.el : e)) : board.elements;
  const byId = Object.fromEntries(els.map((e) => [e.id, e]));
  const sel = selected ? byId[selected] : null;

  useEffect(() => setLabel(sel?.label ?? ""), [selected, sel?.label]);
  useEffect(() => {
    if (focusLabel.current && sel && labelInput.current) {
      focusLabel.current = false;
      labelInput.current.focus();
      labelInput.current.select();
    }
  });

  const point = (ev: React.PointerEvent) => {
    const m = svg.current!.getScreenCTM()!.inverse();
    const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(m);
    return { x: Math.round(p.x), y: Math.round(p.y) };
  };
  const put = (e: El) => onOps([{ op: "put", element: e }]);

  function onCanvasDown(ev: React.PointerEvent) {
    if (readOnly) return;
    if (tool === "select" || tool === "arrow") {
      setSelected(null);
      setArrowFrom(null);
      return;
    }
    const { x, y } = point(ev);
    const size = tool === "text" ? { w: 200, h: 34 } : { w: BOX.w, h: tool === "db" ? 86 : BOX.h };
    const e: El = { id: newId(), type: tool, label: DEFAULT_LABEL[tool], x: x - size.w / 2, y: y - size.h / 2, ...size };
    put(e);
    setSelected(e.id);
    setTool("select");
    focusLabel.current = true;
  }

  function onShapeDown(ev: React.PointerEvent, e: El) {
    ev.stopPropagation();
    if (readOnly) return;
    if (tool === "arrow") {
      if (!arrowFrom) setArrowFrom(e.id);
      else if (arrowFrom !== e.id) {
        const a: El = { id: newId(), type: "arrow", label: "", x: 0, y: 0, from: arrowFrom, to: e.id };
        put(a);
        setArrowFrom(null);
        setSelected(a.id);
        setTool("select");
        focusLabel.current = true;
      }
      return;
    }
    setSelected(e.id);
    const p = point(ev);
    (ev.target as Element).setPointerCapture?.(ev.pointerId);
    setDrag({ id: e.id, dx: p.x - e.x, dy: p.y - e.y, el: e });
  }

  function onMove(ev: React.PointerEvent) {
    if (!drag) return;
    const p = point(ev);
    setDrag({ ...drag, el: { ...drag.el, x: Math.max(0, Math.min(W - 20, p.x - drag.dx)), y: Math.max(0, Math.min(H - 20, p.y - drag.dy)) } });
  }

  function onUp() {
    if (!drag) return;
    const orig = board.elements.find((e) => e.id === drag.id);
    if (orig && (orig.x !== drag.el.x || orig.y !== drag.el.y)) put(drag.el); // one op per drag, not per pixel
    setDrag(null);
  }

  function saveLabel() {
    if (sel && label !== sel.label) put({ ...sel, label: label.slice(0, 200) });
  }

  function remove() {
    if (sel) onOps([{ op: "delete", id: sel.id }]);
    setSelected(null);
  }

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if ((ev.key === "Delete" || ev.key === "Backspace") && selected && document.activeElement?.tagName !== "INPUT") remove();
      if (ev.key === "Escape") { setArrowFrom(null); setTool("select"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const shape = (e: El) => {
    const w = e.w ?? BOX.w, h = e.h ?? BOX.h;
    const cls = `wb-shape ${selected === e.id ? "sel" : ""} ${arrowFrom === e.id ? "src" : ""}`;
    const text = (
      <text x={e.x + w / 2} y={e.y + h / 2} textAnchor="middle" dominantBaseline="middle" className="wb-label">
        {e.label}
      </text>
    );
    if (e.type === "text") {
      return (
        <g key={e.id} className={cls} onPointerDown={(ev) => onShapeDown(ev, e)}>
          <rect x={e.x} y={e.y} width={w} height={h} className="wb-hit" />
          <text x={e.x + 4} y={e.y + h / 2} dominantBaseline="middle" className="wb-note">{e.label}</text>
        </g>
      );
    }
    const body = e.type === "db" ? (
      <path d={`M${e.x},${e.y + 10} a${w / 2},10 0 0,0 ${w},0 a${w / 2},10 0 0,0 ${-w},0 v${h - 20} a${w / 2},10 0 0,0 ${w},0 v${-(h - 20)}`} />
    ) : e.type === "queue" ? (
      <g>
        <rect x={e.x} y={e.y} width={w} height={h} rx={6} />
        {[0.25, 0.5, 0.75].map((f) => <line key={f} x1={e.x + w * f} y1={e.y + 6} x2={e.x + w * f} y2={e.y + h - 6} className="wb-inner" />)}
      </g>
    ) : <rect x={e.x} y={e.y} width={w} height={h} rx={10} />;
    return <g key={e.id} className={cls} onPointerDown={(ev) => onShapeDown(ev, e)}>{body}{text}</g>;
  };

  const arrow = (e: El) => {
    const a = e.from ? byId[e.from] : null, b = e.to ? byId[e.to] : null;
    if (!a || !b) return null;
    const bc = { x: b.x + (b.w ?? BOX.w) / 2, y: b.y + (b.h ?? BOX.h) / 2 }, ac = { x: a.x + (a.w ?? BOX.w) / 2, y: a.y + (a.h ?? BOX.h) / 2 };
    const p1 = edgePoint(a, bc.x, bc.y), p2 = edgePoint(b, ac.x, ac.y);
    return (
      <g key={e.id} className={`wb-arrow ${selected === e.id ? "sel" : ""}`} onPointerDown={(ev) => { ev.stopPropagation(); if (!readOnly) setSelected(e.id); }}>
        <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} className="wb-hit-line" />
        <line x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y} markerEnd="url(#wb-head)" />
        {e.label && <text x={(p1.x + p2.x) / 2} y={(p1.y + p2.y) / 2 - 6} textAnchor="middle" className="wb-arrow-label">{e.label}</text>}
      </g>
    );
  };

  return (
    <div className="wb">
      {!readOnly && (
        <>
          <div className="wb-toolbar row wrap">
            {TOOLS.map((t) => (
              <button key={t.id} type="button" title={t.title} className={tool === t.id ? "primary" : ""} onClick={() => { setTool(t.id); setArrowFrom(null); }}>
                {t.label}
              </button>
            ))}
          </div>
          {/* Always present (disabled when nothing is selected), so the canvas never jumps around. */}
          <div className="wb-toolbar row">
            <input ref={labelInput} className="wb-label-input" value={label} maxLength={200} disabled={!sel}
              placeholder={sel ? "Label" : "Select a shape or arrow to label it"}
              onChange={(e) => setLabel(e.target.value)} onBlur={saveLabel}
              onKeyDown={(e) => { if (e.key === "Enter") { saveLabel(); (e.target as HTMLInputElement).blur(); } }} />
            <button type="button" onClick={remove} disabled={!sel} title="Delete (Del)">Delete</button>
            <span className="small muted">
              {tool === "arrow" ? (arrowFrom ? "Now click the target shape" : "Click the source shape")
                : tool === "select" ? "Drag to move. The interviewer reads your labels." : "Click the canvas to place it"}
            </span>
          </div>
        </>
      )}
      <svg ref={svg} viewBox={`0 0 ${W} ${H}`} className={`wb-canvas tool-${tool}`} onPointerDown={onCanvasDown} onPointerMove={onMove} onPointerUp={onUp}>
        <defs>
          <marker id="wb-head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" className="wb-head" />
          </marker>
          <pattern id="wb-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0 L0 0 0 40" className="wb-grid" />
          </pattern>
        </defs>
        <rect width={W} height={H} fill="url(#wb-grid)" />
        {els.filter((e) => e.type === "arrow").map(arrow)}
        {els.filter((e) => e.type !== "arrow").map(shape)}
        {els.length === 0 && (
          <text x={W / 2} y={H / 2} textAnchor="middle" className="wb-empty">
            {readOnly ? "Nothing drawn yet" : "Pick a shape above and click here to place it"}
          </text>
        )}
      </svg>
    </div>
  );
}
