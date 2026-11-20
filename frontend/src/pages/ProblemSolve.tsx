import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Discussion, Editorial } from "../components/ProblemCommunity";
import Editor from "@monaco-editor/react";
import Markdown from "react-markdown";
import { LANGUAGES, TEMPLATES, monacoLang, type LangId } from "../lib/languages";
import { api, type Problem, type RunResult, type Submission } from "../lib/api";

type Lang = LangId;

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
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"problem" | "discussion" | "editorial">(
    (params.get("tab") as "discussion" | "editorial") ?? "problem");
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
      setCode(localStorage.getItem(draftKey("python")) ?? p.starter_code.python ?? TEMPLATES.python);
    });
    api<Submission[]>(`/submissions?problem_id=${id}`).then(setHistory);
  }, [id]);

  function switchLang(l: Lang) {
    setLang(l);
    setCode(localStorage.getItem(draftKey(l)) ?? problem?.starter_code[l] ?? TEMPLATES[l]);
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
        {problem.contest && (
          <div className="contest-banner small">
            🏁 Part of the running contest <Link to={`/contests/${problem.contest.id}`}><b>{problem.contest.title}</b></Link>
            {problem.contest.locked ? ": your submissions count, and hints, AI reviews and discussions are off until it ends." : " (you're staff, so nothing is locked)."}
          </div>
        )}
        <div className="tabs">
          {(["problem", "discussion", "editorial"] as const).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
          ))}
        </div>
        {tab === "discussion" && <Discussion problemId={problem.id} />}
        {tab === "editorial" && <Editorial problemId={problem.id} />}
        {tab === "problem" && (<>
        <Markdown>{problem.description}</Markdown>
        {problem.signature && (
          <div className="signature">
            <div className="small muted">Implement this function (the judge calls it and checks the return value):</div>
            <code>{signatureText(problem.signature)}</code>
            {problem.signature.compare === "unordered" && <div className="small muted">The returned list may be in any order.</div>}
          </div>
        )}
        <h4>Sample tests</h4>
        {problem.sample_tests.map((t, i) => (
          <div key={t.id} className="sample">
            <div className="small muted">Example {i + 1}</div>
            <pre>Input:{"\n"}{problem.signature ? namedArgs(problem.signature, t.input) : t.input || "(empty)"}</pre>
            <pre>{problem.signature ? "Returns" : "Output"}:{"\n"}{t.expected_output}</pre>
          </div>
        ))}
        <p className="muted small">+ {problem.hidden_test_count} hidden tests on submit</p>
        <div className="row">
          <button onClick={getHint} disabled={!!busy || hint?.level === 3 || !!problem.contest?.locked}>
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
        </>)}
      </section>

      <section className="stack">
        <div className="row between">
          <select value={lang} onChange={(e) => switchLang(e.target.value as Lang)}>
            {LANGUAGES.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
          <div className="row">
            <button onClick={() => act("run")} disabled={!!busy}>{busy === "run" ? "Running…" : "Run"}</button>
            <button className="primary" onClick={() => act("submit")} disabled={!!busy}>{busy === "submit" ? "Judging…" : "Submit"}</button>
          </div>
        </div>
        <div className="editor">
          <Editor height="100%" language={monacoLang(lang)} theme="vs-dark" value={code} onChange={onCode} onMount={onEditorMount}
            options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false }} />
        </div>
        <label className="small"><input type="checkbox" checked={useCustom} onChange={(e) => setUseCustom(e.target.checked)} /> Run with custom input</label>
        {useCustom && (
          <textarea rows={Math.max(3, problem.signature?.params.length ?? 0)} value={customInput}
            onChange={(e) => setCustomInput(e.target.value)}
            placeholder={problem.signature
              ? problem.signature.params.map((p) => `${p.name} (${p.type}) as JSON, e.g. ${exampleFor(p.type)}`).join("\n")
              : "stdin"} />
        )}
        {error && <div className="error">{error}</div>}
        {result && <Results r={result} fn={!!problem.signature} />}
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

export function signatureText(s: { function: string; params: { name: string; type: string }[]; returns: string }) {
  return `${s.function}(${s.params.map((p) => `${p.name}: ${p.type}`).join(", ")}) -> ${s.returns}`;
}

function namedArgs(s: { params: { name: string }[] }, input: string) {
  const lines = input.split("\n").filter((l) => l.trim());
  return lines.length === s.params.length ? s.params.map((p, i) => `${p.name} = ${lines[i]}`).join("\n") : input;
}

function exampleFor(type: string) {
  const base = type.replace(/\[\]/g, "");
  const one = { int: "3", long: "3", float: "1.5", bool: "true", string: '"abc"' }[base] ?? "0";
  const depth = (type.match(/\[\]/g) || []).length;
  return depth === 0 ? one : depth === 1 ? `[${one},${one}]` : `[[${one}],[${one}]]`;
}

export function Results({ r, fn = false }: { r: RunResult; fn?: boolean }) {
  return (
    <div className="card">
      <div className="row between">
        <b className={`verdict ${r.verdict}`}>{r.verdict.replace(/_/g, " ")}</b>
        <span className="muted small">{r.passed}/{r.total} passed · max {r.runtime_ms} ms CPU</span>
      </div>
      {r.verdict === "too_slow" && (
        <div className="small note-slow">
          Your answers are correct, but the solution ran out of time on a large input. Look for a more efficient
          algorithm (for example O(n) or O(n log n) instead of O(n²)).
        </div>
      )}
      {r.results.map((t) => (
        <details key={t.index} className="test" open={!t.passed && !t.hidden}>
          <summary>
            {t.passed ? "✅" : "❌"} Test {t.index + 1} {t.hidden && <span className="muted">(hidden)</span>}
            {t.large && <span className="tag">large input</span>} — {t.status.replace(/_/g, " ")} · {t.time_ms} ms CPU
          </summary>
          {t.hidden && t.large && t.input && <div className="small muted">{t.input}</div>}
          {!t.hidden && (
            <div className="grid2 small">
              <pre>Input:{"\n"}{t.input}</pre>
              {t.expected != null && <pre>Expected:{"\n"}{t.expected}</pre>}
              <pre>{fn ? "Returned" : "Output"}:{"\n"}{t.stdout}</pre>
              {t.printed && <pre>Printed:{"\n"}{t.printed}</pre>}
              {t.stderr && <pre className="stderr">{t.stderr}</pre>}
            </div>
          )}
        </details>
      ))}
    </div>
  );
}
