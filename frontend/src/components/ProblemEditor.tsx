import { useState } from "react";
import { api } from "../lib/api";

type T = { input: string; expected_output: string; is_hidden: boolean };

/** Mentor/admin form to author an org-private problem with sample + hidden tests. */
export default function ProblemEditor({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [difficulty, setDifficulty] = useState("easy");
  const [tags, setTags] = useState("");
  const [tests, setTests] = useState<T[]>([{ input: "", expected_output: "", is_hidden: false }]);
  const [error, setError] = useState("");

  const upd = (i: number, patch: Partial<T>) => setTests(tests.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  async function save() {
    setError("");
    try {
      await api("/problems", {
        body: { title, description, difficulty, tags: tags.split(",").map((s) => s.trim()).filter(Boolean), tests },
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
      <textarea rows={5} placeholder="Description (markdown). Problems read stdin and write stdout." value={description} onChange={(e) => setDescription(e.target.value)} />
      <div className="row">
        <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
          <option>easy</option><option>medium</option><option>hard</option>
        </select>
        <input placeholder="tags, comma separated" value={tags} onChange={(e) => setTags(e.target.value)} />
      </div>
      <b>Test cases</b>
      {tests.map((t, i) => (
        <div key={i} className="row">
          <textarea rows={2} placeholder="stdin" value={t.input} onChange={(e) => upd(i, { input: e.target.value })} />
          <textarea rows={2} placeholder="expected stdout" value={t.expected_output} onChange={(e) => upd(i, { expected_output: e.target.value })} />
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
