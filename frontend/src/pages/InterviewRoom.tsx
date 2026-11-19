import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api, apiStream, type Interview, type Message, type Scorecard } from "../lib/api";

export default function InterviewRoom() {
  const { id } = useParams();
  const [iv, setIv] = useState<Interview | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [streaming, setStreaming] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Interview>(`/interviews/${id}`).then(setIv);
  }, [id]);
  // Block body on purpose: newer browsers make scrollIntoView() return a Promise, and React would
  // try to call any non-undefined return value as the effect's cleanup function and crash.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [iv?.messages.length, busy, streaming]);

  async function send() {
    if (!iv || !draft.trim()) return;
    setBusy("send");
    setError("");
    setStreaming("");
    const content = draft;
    setDraft("");
    let sent = false;
    try {
      await apiStream(`/interviews/${id}/messages/stream`, { content }, (event, data) => {
        if (event === "candidate") {
          sent = true;
          setIv((cur) => (cur ? { ...cur, messages: [...cur.messages, data as Message] } : cur));
        } else if (event === "delta") {
          setStreaming((s) => (s ?? "") + data.text);
        } else if (event === "done") {
          setStreaming(null);
          setIv((cur) => (cur ? { ...cur, messages: [...cur.messages, data as Message] } : cur));
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

  async function end() {
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

  return (
    <div className="stack">
      <div className="row between">
        <h2>{iv.kind.replace("_", " ")} interview · {iv.level} {iv.role_title}</h2>
        {active && <button onClick={end} disabled={!!busy}>{busy === "end" ? "Scoring…" : "End & get feedback"}</button>}
      </div>
      {iv.scorecard && <ScorecardView s={iv.scorecard} />}
      <div className="card chat">
        {iv.messages.map((m) => (
          <div key={m.seq} className={`bubble ${m.sender}`}>
            <div className="small muted">{m.sender === "interviewer" ? "Interviewer" : "You"}</div>
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
        <div className="row">
          <textarea className="grow" rows={4} value={draft} placeholder="Type your answer… (Ctrl+Enter to send)"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); }} />
          <button className="primary" onClick={send} disabled={!!busy || !draft.trim()}>Send</button>
        </div>
      )}
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
      {s.suggested_topics.length > 0 && <div>Practice next: {s.suggested_topics.map((t) => <span key={t} className="tag">{t}</span>)}</div>}
    </div>
  );
}
