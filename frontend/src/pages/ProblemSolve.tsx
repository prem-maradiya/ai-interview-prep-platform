import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import Markdown from "react-markdown";
import { api, type Problem, type RunResult, type Submission } from "../lib/api";

const LANGS = ["python", "javascript"] as const;
type Lang = (typeof LANGS)[number];

export default function ProblemSolve() {
  const { id } = useParams();
  const [problem, setProblem] = useState<Problem | null>(null);
  const [lang, setLang] = useState<Lang>("python");
  const [code, setCode] = useState("");
  const [customInput, setCustomInput] = useState("");
  const [useCustom, setUseCustom] = useState(false);
  const [result, setResult] = useState<RunResult | null>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [history, setHistory] = useState<Submission[]>([]);
  const [hint, setHint] = useState<{ level: number; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const draftKey = (l: Lang) => `pf_draft_${id}_${l}`;

  // Editor activity sent with submissions so mentors get integrity signals (paste-heavy, tab switching).
  const telemetry = useRef({ paste_chars: 0, paste_events: 0, tab_switches: 0, opened_at: Date.now() });
  useEffect(() => {
    telemetry.current = { paste_chars: 0, paste_events: 0, tab_switches: 0, opened_at: Date.now() };
    const onVisibility = () => {
      if (document.hidden) telemetry.current.tab_switches += 1;
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [id]);

  function onEditorMount(editor: any) {
    editor.onDidPaste((e: any) => {
      const pasted = editor.getModel()?.getValueInRange(e.range) ?? "";
      telemetry.current.paste_chars += pasted.length;
      telemetry.current.paste_events += 1;
    });
  }

  function telemetryPayload() {
    const { opened_at, ...rest } = telemetry.current;
    return { ...rest, time_spent_s: Math.round((Date.now() - opened_at) / 1000) };
  }

  useEffect(() => {
    api<Problem>(`/problems/${id}`).then((p) => {
      setProblem(p);
      setCode(localStorage.getItem(draftKey("python")) ?? p.starter_code.python ?? "");
    });
    api<Submission[]>(`/submissions?problem_id=${id}`).then(setHistory);
  }, [id]);

  function switchLang(l: Lang) {
    setLang(l);
    setCode(localStorage.getItem(draftKey(l)) ?? problem?.starter_code[l] ?? "");
  }

  function onCode(v?: string) {
    setCode(v ?? "");
    try { localStorage.setItem(draftKey(lang), v ?? ""); } catch {}
  }

  async function act(kind: "run" | "submit") {
    setBusy(kind);
    setError("");
    try {
      const body = {
        language: lang, code, custom_input: kind === "run" && useCustom ? customInput : undefined,
        telemetry: kind === "submit" ? telemetryPayload() : undefined,
      };
      if (kind === "run") {
        setResult(await api<RunResult>(`/problems/${id}/run`, { body }));
        setSubmission(null);
      } else {
        const s = await api<Submission>(`/problems/${id}/submit`, { body });
        setSubmission(s);
        setResult(s);
        setHistory([s, ...history]);
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function review() {
    if (!submission) return;
    setBusy("review");
    try {
      const s = await api<Submission>(`/submissions/${submission.id}/review`, { method: "POST" });
      setSubmission(s);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function getHint() {
    const level = Math.min(3, (hint?.level ?? 0) + 1);
    setBusy("hint");
    try {
      const r = await api<{ text: string }>(`/problems/${id}/hint`, { body: { code, level, language: lang } });
      setHint({ level, text: r.text });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (!problem) return <div className="muted">Loading…</div>;

  return (
    <div className="solve">
      <section className="card prose">
        <h2>{problem.title} <span className={`diff ${problem.difficulty}`}>{problem.difficulty}</span></h2>
        <Markdown>{problem.description}</Markdown>
        <h4>Sample tests</h4>
        {problem.sample_tests.map((t, i) => (
          <div key={t.id} className="sample">
            <div className="small muted">Example {i + 1}</div>
            <pre>Input:{"\n"}{t.input || "(empty)"}</pre>
            <pre>Output:{"\n"}{t.expected_output}</pre>
          </div>
        ))}
        <p className="muted small">+ {problem.hidden_test_count} hidden tests on submit</p>
        <div className="row">
          <button onClick={getHint} disabled={!!busy || hint?.level === 3}>
            {busy === "hint" ? "Thinking…" : hint ? `Next hint (${hint.level + 1}/3)` : "Get a hint"}
          </button>
        </div>
        {hint && <div className="hint"><b>Hint {hint.level}:</b> <Markdown>{hint.text}</Markdown></div>}
        {history.length > 0 && (
          <>
            <h4>Your submissions</h4>
            {history.slice(0, 8).map((s) => (
              <div key={s.id} className="row between small">
                <button className="link" onClick={() => { setSubmission(s); setResult(s); }}>
                  <span className={`verdict ${s.verdict}`}>{s.verdict.replace(/_/g, " ")}</span>
                </button>
                <span className="muted">
                  {s.language} · {s.passed}/{s.total} · <Link to={`/submissions/${s.id}`}>{new Date(s.created_at).toLocaleString()}</Link>
                </span>
              </div>
            ))}
          </>
        )}
      </section>

      <section className="stack">
        <div className="row between">
          <select value={lang} onChange={(e) => switchLang(e.target.value as Lang)}>
            {LANGS.map((l) => <option key={l}>{l}</option>)}
          </select>
          <div className="row">
            <button onClick={() => act("run")} disabled={!!busy}>{busy === "run" ? "Running…" : "Run"}</button>
            <button className="primary" onClick={() => act("submit")} disabled={!!busy}>{busy === "submit" ? "Judging…" : "Submit"}</button>
          </div>
        </div>
        <div className="editor">
          <Editor height="100%" language={lang} theme="vs-dark" value={code} onChange={onCode} onMount={onEditorMount}
            options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false }} />
        </div>
        <label className="small"><input type="checkbox" checked={useCustom} onChange={(e) => setUseCustom(e.target.checked)} /> Run with custom input</label>
        {useCustom && <textarea rows={3} value={customInput} onChange={(e) => setCustomInput(e.target.value)} placeholder="stdin" />}
        {error && <div className="error">{error}</div>}
        {result && <Results r={result} />}
        {submission && (
          <div className="card">
            {submission.ai_review ? (
              <div className="prose"><h4>AI code review</h4><Markdown>{submission.ai_review}</Markdown></div>
            ) : (
              <button onClick={review} disabled={!!busy}>{busy === "review" ? "Reviewing…" : "Get AI code review"}</button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function Results({ r }: { r: RunResult }) {
  return (
    <div className="card">
      <div className="row between">
        <b className={`verdict ${r.verdict}`}>{r.verdict.replace(/_/g, " ")}</b>
        <span className="muted small">{r.passed}/{r.total} passed · max {r.runtime_ms} ms</span>
      </div>
      {r.results.map((t) => (
        <details key={t.index} className="test" open={!t.passed && !t.hidden}>
          <summary>
            {t.passed ? "✅" : "❌"} Test {t.index + 1} {t.hidden && <span className="muted">(hidden)</span>} — {t.status.replace(/_/g, " ")} · {t.time_ms} ms
          </summary>
          {!t.hidden && (
            <div className="grid2 small">
              <pre>Input:{"\n"}{t.input}</pre>
              {t.expected != null && <pre>Expected:{"\n"}{t.expected}</pre>}
              <pre>Output:{"\n"}{t.stdout}</pre>
              {t.stderr && <pre className="stderr">{t.stderr}</pre>}
            </div>
          )}
        </details>
      ))}
    </div>
  );
}
