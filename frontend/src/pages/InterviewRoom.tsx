import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import Markdown from "react-markdown";
import { api, apiStream, type Interview, type Message, type RunResult, type Scorecard } from "../lib/api";
import { listen, speak, stopSpeaking, voiceSupport, type Listener } from "../lib/voice";

const STARTER: Record<string, string> = {
  python: "# Sketch your solution here. The interviewer sees it when you send your next answer.\n\n",
  javascript: "// Sketch your solution here. The interviewer sees it when you send your next answer.\n\n",
};

export default function InterviewRoom() {
  const { id } = useParams();
  const [iv, setIv] = useState<Interview | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  // voice
  const [voiceOn, setVoiceOn] = useState(false);
  const [listening, setListening] = useState(false);
  const [spoke, setSpoke] = useState(false); // current draft came (at least partly) from dictation
  const listener = useRef<Listener | null>(null);
  const draftBeforeListening = useRef("");
  // code editor (technical interviews)
  const [lang, setLang] = useState("python");
  const [code, setCode] = useState(STARTER.python);
  const [stdin, setStdin] = useState("");
  const [runResult, setRunResult] = useState<RunResult | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Interview>(`/interviews/${id}`).then((data) => {
      setIv(data);
      if (data.code) {
        setCode(data.code);
        setLang(data.code_language ?? "python");
      }
    });
    return () => {
      listener.current?.stop();
      stopSpeaking();
    };
  }, [id]);

  // Block body on purpose: newer browsers make scrollIntoView() return a Promise, and React would
  // try to call any non-undefined return value as the effect's cleanup function and crash.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [iv?.messages.length, busy, streaming]);

  function toggleVoice() {
    const next = !voiceOn;
    setVoiceOn(next);
    if (next && iv) {
      const lastQ = [...iv.messages].reverse().find((m) => m.sender === "interviewer");
      if (lastQ) speak(lastQ.content);
    } else {
      stopSpeaking();
      listener.current?.stop();
    }
  }

  function toggleMic() {
    if (listening) {
      listener.current?.stop();
      return;
    }
    stopSpeaking(); // don't transcribe the interviewer's own voice
    setError("");
    draftBeforeListening.current = draft ? draft.trimEnd() + " " : "";
    listener.current = listen(
      (text) => {
        setDraft(draftBeforeListening.current + text);
        setSpoke(true);
      },
      () => setListening(false),
      (msg) => setError(msg),
    );
    setListening(true);
  }

  async function send() {
    if (!iv || !draft.trim()) return;
    listener.current?.stop();
    setBusy("send");
    setError("");
    setStreaming("");
    const content = draft;
    const technical = iv.kind === "technical";
    const body = {
      content,
      input_mode: spoke ? "voice" : "text",
      ...(technical ? { code, code_language: lang } : {}),
    };
    setDraft("");
    setSpoke(false);
    let sent = false;
    try {
      await apiStream(`/interviews/${id}/messages/stream`, body, (event, data) => {
        if (event === "candidate") {
          sent = true;
          setIv((cur) => (cur ? { ...cur, messages: [...cur.messages, data as Message] } : cur));
        } else if (event === "delta") {
          setStreaming((s) => (s ?? "") + data.text);
        } else if (event === "done") {
          setStreaming(null);
          setIv((cur) => (cur ? { ...cur, messages: [...cur.messages, data as Message] } : cur));
          if (voiceOn) speak(data.content);
        } else if (event === "error") {
          setError(data.detail);
        }
      });
    } catch (e: any) {
      if (!sent) setDraft(content);
      setError(e.message);
    } finally {
      setStreaming(null);
      setBusy("");
    }
  }

  async function runCode() {
    setBusy("run");
    setError("");
    try {
      setRunResult(await api<RunResult>(`/interviews/${id}/run`, { body: { language: lang, code, stdin } }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function end() {
    listener.current?.stop();
    stopSpeaking();
    setBusy("end");
    setError("");
    try {
      setIv(await api<Interview>(`/interviews/${id}/end`, { method: "POST" }));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (!iv) return <div className="muted">Loading…</div>;
  const active = iv.status === "active";
  const technical = iv.kind === "technical";

  const chat = (
    <div className="stack">
      <div className="card chat">
        {iv.messages.map((m) => (
          <div key={m.seq} className={`bubble ${m.sender}`}>
            <div className="small muted">
              {m.sender === "interviewer" ? "Interviewer" : "You"}
              {m.input_mode === "voice" && " · 🎙"}
              {m.code && " · shared code"}
            </div>
            <Markdown>{m.content}</Markdown>
          </div>
        ))}
        {busy === "send" && (
          streaming ? (
            <div className="bubble interviewer">
              <div className="small muted">Interviewer</div>
              <Markdown>{streaming}</Markdown>
            </div>
          ) : (
            <div className="bubble interviewer muted">Interviewer is typing…</div>
          )
        )}
        <div ref={bottom} />
      </div>
      {error && <div className="error">{error}</div>}
      {active && (
        <div className="row answer-box">
          <textarea className="grow" rows={4} value={draft}
            placeholder={listening ? "Listening… speak your answer" : "Type your answer… (Ctrl+Enter to send)"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); }} />
          <div className="stack tight">
            {voiceOn && voiceSupport.recognition && (
              <button className={listening ? "mic on" : "mic"} onClick={toggleMic} disabled={busy === "send"}
                title={listening ? "Stop dictation" : "Speak your answer"}>
                {listening ? "■ Stop" : "🎙 Speak"}
              </button>
            )}
            <button className="primary" onClick={send} disabled={!!busy || !draft.trim()}>Send</button>
          </div>
        </div>
      )}
      {technical && active && <div className="small muted">Your editor code is shared with the interviewer each time you send an answer (if it changed).</div>}
    </div>
  );

  return (
    <div className="stack">
      <div className="row between wrap">
        <h2>
          {iv.kind.replace("_", " ")} interview · {iv.level} {iv.role_title}
          {(iv.has_resume || iv.has_job_description) && <span className="pill">tailored</span>}
        </h2>
        <div className="row">
          {active && (voiceSupport.synthesis || voiceSupport.recognition) && (
            <button onClick={toggleVoice} className={voiceOn ? "active-toggle" : ""}>
              {voiceOn ? "🔊 Voice on" : "🔈 Voice off"}
            </button>
          )}
          {active && <button onClick={end} disabled={!!busy}>{busy === "end" ? "Scoring…" : "End & get feedback"}</button>}
        </div>
      </div>
      {voiceOn && !voiceSupport.recognition && (
        <div className="small muted">This browser can read questions aloud but doesn't support speech input; try Chrome or Edge to answer by voice.</div>
      )}
      {iv.scorecard && <ScorecardView s={iv.scorecard} />}
      {technical ? (
        <div className="interview-split">
          {chat}
          <div className="stack">
            <div className="row between">
              <select value={lang} onChange={(e) => { setLang(e.target.value); if (!code.trim() || code === STARTER[lang]) setCode(STARTER[e.target.value]); }}
                disabled={!active}>
                <option value="python">python</option>
                <option value="javascript">javascript</option>
              </select>
              <button onClick={runCode} disabled={!active || !!busy}>{busy === "run" ? "Running…" : "Run"}</button>
            </div>
            <div className="editor">
              <Editor height="100%" language={lang} theme="vs-dark" value={code} onChange={(v) => setCode(v ?? "")}
                options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false, readOnly: !active }} />
            </div>
            <textarea rows={2} value={stdin} onChange={(e) => setStdin(e.target.value)} placeholder="stdin for Run (optional)" />
            {runResult && (
              <div className="card small">
                <b className={`verdict ${runResult.results[0].status === "ran" ? "accepted" : runResult.verdict}`}>
                  {runResult.results[0].status.replace(/_/g, " ")}
                </b> · {runResult.runtime_ms} ms
                <pre>{runResult.results[0].stdout || "(no output)"}</pre>
                {runResult.results[0].stderr && <pre className="stderr">{runResult.results[0].stderr}</pre>}
              </div>
            )}
          </div>
        </div>
      ) : chat}
    </div>
  );
}

function ScorecardView({ s }: { s: Scorecard }) {
  return (
    <div className="card stack scorecard">
      <div className="row between">
        <h3>Scorecard</h3>
        <div className="row">
          <span className={`pill signal ${s.hire_signal}`}>{s.hire_signal.replace("_", " ")}</span>
          <span className="stat-value">{s.overall_score}/100</span>
        </div>
      </div>
      <p>{s.summary}</p>
      {s.dimensions.map((d) => (
        <div key={d.name}>
          <div className="row between"><b>{d.name}</b><span>{"●".repeat(d.score)}{"○".repeat(5 - d.score)}</span></div>
          <div className="small muted">{d.comment}</div>
        </div>
      ))}
      <div className="grid2">
        <div><h4>Strengths</h4><ul>{s.strengths.map((x) => <li key={x}>{x}</li>)}</ul></div>
        <div><h4>To improve</h4><ul>{s.improvements.map((x) => <li key={x}>{x}</li>)}</ul></div>
      </div>
      {s.delivery && (
        <div className="card delivery">
          <h4>🎙 Speaking delivery ({s.delivery.voice_answers} spoken answers)</h4>
          <div className="row wrap small">
            <span>Avg <b>{s.delivery.avg_words_per_answer}</b> words/answer</span>
            <span>· <b>{s.delivery.filler_words}</b> filler words ({s.delivery.fillers_per_100_words}/100 words)</span>
            {s.delivery.top_fillers.length > 0 && <span>· most used: {s.delivery.top_fillers.map((f) => <span key={f} className="tag">{f}</span>)}</span>}
          </div>
          <ul>{s.delivery.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      )}
      {s.suggested_topics.length > 0 && <div>Practice next: {s.suggested_topics.map((t) => <span key={t} className="tag">{t}</span>)}</div>}
    </div>
  );
}
