import { useState } from "react";
import { api } from "../lib/api";

type T = { input: string; expected_output: string; is_hidden: boolean };
type P = { name: string; type: string };

const BASES = ["int", "long", "float", "bool", "string"];
const TYPES = BASES.flatMap((b) => [b, `${b}[]`, `${b}[][]`]);

/** Mentor/admin form to author an org-private problem: stdin/stdout or function-style, with sample + hidden tests. */
export default function ProblemEditor({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [difficulty, setDifficulty] = useState("easy");
  const [tags, setTags] = useState("");
  const [style, setStyle] = useState<"stdio" | "function">("stdio");
  const [fnName, setFnName] = useState("solve");
  const [params, setParams] = useState<P[]>([{ name: "nums", type: "int[]" }]);
  const [returns, setReturns] = useState("int");
  const [unordered, setUnordered] = useState(false);
  const [tests, setTests] = useState<T[]>([{ input: "", expected_output: "", is_hidden: false }]);
  const [error, setError] = useState("");

  const upd = (i: number, patch: Partial<T>) => setTests(tests.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  const updP = (i: number, patch: Partial<P>) => setParams(params.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  const fn = style === "function";

  async function save() {
    setError("");
    try {
      await api("/problems", {
        body: {
          title, description, difficulty, tags: tags.split(",").map((s) => s.trim()).filter(Boolean), tests,
          signature: fn ? { function: fnName, params, returns, compare: unordered ? "unordered" : "exact" } : null,
        },
      });
      onDone();
    } catch (e: any) {
      setError(e.message);
    }
  }

  return (
    <div className="card stack">
      <h3>New problem (visible to your organization only)</h3>
      <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <div className="row">
        <label className="small"><input type="radio" checked={!fn} onChange={() => setStyle("stdio")} /> Reads stdin, prints stdout</label>
        <label className="small"><input type="radio" checked={fn} onChange={() => setStyle("function")} /> Function (judge calls it)</label>
      </div>
      <textarea rows={5} placeholder="Description (markdown)" value={description} onChange={(e) => setDescription(e.target.value)} />
      {fn && (
        <div className="stack tight card">
          <div className="row">
            <input placeholder="functionName" value={fnName} onChange={(e) => setFnName(e.target.value)} />
            <span className="small muted">returns</span>
            <select value={returns} onChange={(e) => setReturns(e.target.value)}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
            <label className="small"><input type="checkbox" checked={unordered} onChange={(e) => setUnordered(e.target.checked)} /> list may be in any order</label>
          </div>
          {params.map((p, i) => (
            <div key={i} className="row">
              <input placeholder="paramName" value={p.name} onChange={(e) => updP(i, { name: e.target.value })} />
              <select value={p.type} onChange={(e) => updP(i, { type: e.target.value })}>{TYPES.map((t) => <option key={t}>{t}</option>)}</select>
              {params.length > 1 && <button className="link" onClick={() => setParams(params.filter((_, j) => j !== i))}>remove</button>}
            </div>
          ))}
          {params.length < 6 && <div><button onClick={() => setParams([...params, { name: "", type: "int" }])}>+ Parameter</button></div>}
          <div className="small muted">Starter code for all five languages is generated from this signature.</div>
        </div>
      )}
      <div className="row">
        <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
          <option>easy</option><option>medium</option><option>hard</option>
        </select>
        <input placeholder="tags, comma separated" value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>
      <b>Test cases</b>
      <div className="small muted">
        {fn ? "Input: one JSON value per line, one line per parameter (e.g. [2,7,11,15] then 9). Expected: the JSON return value. "
            : "Input is stdin; expected output is compared line by line. "}
        For a complexity check, start a hidden test's input with <code>@gen</code> and a generator spec (see the docs).
      </div>
      {tests.map((t, i) => (
        <div key={i} className="row">
          <textarea rows={2} placeholder={fn ? params.map((p) => `${p.name} (JSON)`).join("\n") : "stdin"} value={t.input}
            onChange={(e) => upd(i, { input: e.target.value })} />
          <textarea rows={2} placeholder={fn ? `return value (JSON ${returns})` : "expected stdout"} value={t.expected_output}
            onChange={(e) => upd(i, { expected_output: e.target.value })} />
          <label className="small"><input type="checkbox" checked={t.is_hidden} onChange={(e) => upd(i, { is_hidden: e.target.checked })} /> hidden</label>
          <button className="link" onClick={() => setTests(tests.filter((_, j) => j !== i))}>remove</button>
        </div>
      ))}
      <button onClick={() => setTests([...tests, { input: "", expected_output: "", is_hidden: true }])}>+ Add test</button>
      {error && <div className="error">{error}</div>}
      <div className="row">
        <button className="primary" onClick={save}>Save problem</button>
        <button onClick={onDone}>Cancel</button>
      </div>
    </div>
  );
}
