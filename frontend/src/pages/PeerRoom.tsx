import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Editor from "@monaco-editor/react";
import Markdown from "react-markdown";
import { api, tokens, type Problem, type RunResult } from "../lib/api";
import { LANGUAGES, TEMPLATES, monacoLang, type LangId } from "../lib/languages";
import { Results, signatureText } from "./ProblemSolve";
import { HIRE_LABEL } from "./Peer";
import Whiteboard, { applyOps, type Board, type Op } from "../components/Whiteboard";

type Person = { id: string; name: string } | null;
type Message = { id: string; sender_id: string; round: number; text: string; created_at: string };
type Feedback = { round: number; from_id: string; to_id: string; problem_solving: number; communication: number; code_quality: number; hire: string; comments: string };
type State = {
  id: string; status: "open" | "active" | "completed" | "cancelled"; round: number; my_role: "candidate" | "interviewer" | "viewer";
  me: string; host: Person; guest: Person; invited: Person; candidate_id: string | null; interviewer_id: string | null;
  problem: Problem | null; problems: { round: number; id: string; title: string; candidate_id: string | null }[];
  code: { language: string; code: string | null }; notes: string | null; messages: Message[]; feedback: Feedback[];
  feedback_given: number[]; online: string[]; board: Board;
};

const wsUrl = (id: string) => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/peer/${id}/ws`;

/** A live peer interview room: problem, shared editor (only the candidate types), chat, runs, then feedback. */
export default function PeerRoom() {
  const { id = "" } = useParams();
  const [s, setS] = useState<State | null>(null);
  const [code, setCode] = useState("");
  const [lang, setLang] = useState<LangId>("python");
  const [messages, setMessages] = useState<Message[]>([]);
  const [online, setOnline] = useState<string[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [notes, setNotes] = useState("");
  const [chat, setChat] = useState("");
  const [board, setBoard] = useState<Board>({ elements: [] });
  const [panel, setPanel] = useState<"code" | "board">("code");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const ws = useRef<WebSocket | null>(null);
  const timers = useRef<{ code?: number; notes?: number }>({});
  const chatEnd = useRef<HTMLDivElement | null>(null);
  const current = useRef<State | null>(null); // latest state, for the socket handler

  async function refresh() {
    try {
      const st = await api<State>(`/peer/${id}`);
      applyState(st);
    } catch (e: any) {
      setError(e.message);
    }
  }

  function applyState(st: State) {
    current.current = st;
    setS(st);
    setMessages(st.messages);
    setOnline(st.online);
    setNotes(st.notes ?? "");
    setBoard(st.board ?? { elements: [] });
    const l = (st.code.language as LangId) || "python";
    setLang(l);
    setCode(st.code.code ?? st.problem?.starter_code?.[l] ?? TEMPLATES[l]);
  }

  // Live connection, with reconnect. The token goes in the first message, never in the URL.
  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let ping: number | undefined;
    const open = async () => {
      await api("/auth/me").catch(() => {}); // refreshes an expired access token first
      if (stopped) return;
      const sock = new WebSocket(wsUrl(id));
      ws.current = sock;
      sock.onopen = () => {
        sock.send(JSON.stringify({ type: "auth", token: tokens.get() }));
        retry = 0;
        setConnected(true);
        ping = window.setInterval(() => sock.readyState === 1 && sock.send(JSON.stringify({ type: "ping" })), 25_000);
      };
      sock.onmessage = (ev) => {
        const m = JSON.parse(ev.data);
        if (m.type === "state") applyState(m.state);
        else if (m.type === "changed") { setRun(null); refresh(); }
        else if (m.type === "presence") setOnline(m.online);
        else if (m.type === "chat") setMessages((xs) => (xs.some((x) => x.id === m.message.id) ? xs : [...xs, m.message]));
        else if (m.type === "run") setRun(m.result);
        else if (m.type === "board" && current.current && m.from !== current.current.me) setBoard((b) => applyOps(b, m.ops));
        else if (m.type === "error") setError(m.detail);
        else if (m.type === "code") {
          const cur = current.current;
          if (cur && m.from !== cur.me && m.round === cur.round) { // ignore the echo of our own typing
            setCode(m.code);
            setLang(m.language);
          }
        }
      };
      sock.onclose = (ev) => {
        setConnected(false);
        window.clearInterval(ping);
        if (stopped || ev.code === 4401 || ev.code === 4403) return;
        retry += 1;
        window.setTimeout(open, Math.min(10_000, 500 * 2 ** retry));
      };
    };
    open();
    return () => {
      stopped = true;
      window.clearInterval(ping);
      ws.current?.close();
    };
  }, [id]);

  useEffect(() => chatEnd.current?.scrollIntoView({ block: "nearest" }), [messages.length]);

  const send = (m: object) => ws.current?.readyState === 1 && ws.current.send(JSON.stringify(m));

  function onCode(value: string | undefined) {
    const v = value ?? "";
    setCode(v);
    window.clearTimeout(timers.current.code);
    timers.current.code = window.setTimeout(() => send({ type: "code", code: v, language: lang }), 250);
  }

  function switchLang(l: LangId) {
    const starter = s?.problem?.starter_code?.[l] ?? TEMPLATES[l];
    if (!confirm("Switch language? The editor will reset to the starter code.")) return;
    setLang(l);
    setCode(starter);
    send({ type: "code", code: starter, language: l });
  }

  function onBoardOps(ops: Op[]) {
    setBoard((b) => applyOps(b, ops)); // show it right away, then share it
    send({ type: "board", ops });
  }

  function onNotes(v: string) {
    setNotes(v);
    window.clearTimeout(timers.current.notes);
    timers.current.notes = window.setTimeout(() => send({ type: "notes", text: v }), 500);
  }

  function sendChat(e: React.FormEvent) {
    e.preventDefault();
    if (!chat.trim()) return;
    send({ type: "chat", text: chat });
    setChat("");
  }

  async function action(name: string, path: string, confirmText?: string) {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(name);
    setError("");
    try {
      const r = await api<any>(`/peer/${id}/${path}`, { method: "POST", ...(name === "run" ? { body: { language: lang, code } } : {}) });
      if (name === "run") setRun(r);
      else if (r && r.id) applyState(r);
      else refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  if (!s) return <div className="muted">{error || "Loading…"}</div>;
  const partner = s.my_role === "viewer" ? null : s.me === s.host?.id ? s.guest ?? s.invited : s.host;
  const name = (uid: string | null) => (uid === s.host?.id ? s.host?.name : uid === s.guest?.id ? s.guest?.name : "?");
  const participant = s.my_role !== "viewer";

  if (s.status === "open" || s.status === "cancelled") {
    return (
      <div className="card stack narrow">
        <h2>Peer interview</h2>
        {s.status === "cancelled" ? <p className="muted">This room was cancelled.</p> : s.me === s.host?.id ? (
          <>
            <p>Waiting for {s.invited ? <b>{s.invited.name}</b> : "a teammate"} to join… {connected ? "" : "(connecting)"}</p>
            <p className="small muted">{s.invited ? "They've been notified." : "Your room is listed under Open rooms for everyone in your organization."} This page updates as soon as someone joins.</p>
            <div><button onClick={() => action("cancel", "cancel", "Cancel this room?")}>Cancel room</button></div>
          </>
        ) : (
          <>
            <p><b>{s.host?.name}</b> is waiting for a partner.</p>
            <div><button className="primary" onClick={() => action("join", "join")}>Join</button></div>
          </>
        )}
        {error && <div className="error">{error}</div>}
        <Link to="/peer" className="small">Back to peer interviews</Link>
      </div>
    );
  }

  const done = s.status === "completed";
  const toRate = s.problems.filter((p) => p.round <= (done ? 2 : s.round - 1) && s.my_role !== "viewer" && p.candidate_id !== s.me
    && !s.feedback_given.includes(p.round));

  return (
    <div className="stack">
      <div className="row between wrap">
        <div>
          <h2 style={{ margin: 0 }}>Peer interview · round {s.round} of 2 {done && <span className="pill">completed</span>}</h2>
          <div className="small muted">
            Candidate: <b>{name(s.candidate_id)}</b> · Interviewer: <b>{name(s.interviewer_id)}</b>
            {participant && <> · you are the <b>{s.my_role}</b></>}
            {" · "}{partner && (online.includes(partner.id) ? <span className="ok">● {partner.name} online</span> : <span className="muted">○ {partner?.name} offline</span>)}
            {!connected && !done && <span className="bad"> · reconnecting…</span>}
          </div>
        </div>
        {participant && !done && (
          <div className="row">
            {s.round === 1 && <button onClick={() => action("swap", "swap", "Swap roles and start round 2 with a new problem?")} disabled={!!busy}>Swap roles → round 2</button>}
            <button onClick={() => action("end", "end", "End the interview for both of you?")} disabled={!!busy}>End interview</button>
          </div>
        )}
      </div>
      {error && <div className="error">{error}</div>}

      {toRate.map((p) => <FeedbackForm key={p.round} id={id} round={p.round} who={name(p.candidate_id) ?? ""} onDone={refresh} />)}
      {s.feedback.length > 0 && (
        <div className="card stack">
          <h3>Feedback</h3>
          {s.feedback.map((f) => (
            <div key={f.round} className="task-row">
              <b>Round {f.round}</b>: {name(f.from_id)} rated {name(f.to_id)} — <b>{HIRE_LABEL[f.hire]}</b> · problem solving {f.problem_solving}/5 ·
              communication {f.communication}/5 · code quality {f.code_quality}/5
              {f.comments && <div className="small">“{f.comments}”</div>}
            </div>
          ))}
        </div>
      )}

      <div className="solve">
        <section className="card prose">
          {s.problem && (
            <>
              <h3>{s.problem.title} <span className={`diff ${s.problem.difficulty}`}>{s.problem.difficulty}</span></h3>
              <Markdown>{s.problem.description}</Markdown>
              {s.problem.signature && <code>{signatureText(s.problem.signature)}</code>}
              <h4>Examples</h4>
              {s.problem.sample_tests.map((t, i) => (
                <div key={t.id} className="sample">
                  <pre>Input {i + 1}:{"\n"}{t.input || "(empty)"}</pre>
                  <pre>Expected:{"\n"}{t.expected_output}</pre>
                </div>
              ))}
            </>
          )}
          {s.my_role === "interviewer" && !done && (
            <div className="stack tight">
              <h4>Your private notes</h4>
              <div className="small muted">Only you can see these. Tip: ask about complexity, edge cases and testing before they run the code.</div>
              <textarea rows={6} value={notes} onChange={(e) => onNotes(e.target.value)} placeholder="What went well, what to improve…" />
            </div>
          )}
          <h4>Chat</h4>
          <div className="chat peer-chat">
            {messages.filter((m) => m.round === s.round || done).map((m) => (
              <div key={m.id} className={`bubble ${m.sender_id === s.me ? "candidate" : ""}`}>
                <div className="small muted">{name(m.sender_id)}</div>{m.text}
              </div>
            ))}
            <div ref={chatEnd} />
          </div>
          {participant && !done && (
            <form className="row" onSubmit={sendChat}>
              <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="Message…" maxLength={2000} />
              <button type="submit">Send</button>
            </form>
          )}
        </section>

        <section className="stack">
          <div className="tabs">
            <button className={panel === "code" ? "active" : ""} onClick={() => setPanel("code")}>Code</button>
            <button className={panel === "board" ? "active" : ""} onClick={() => setPanel("board")}>
              Whiteboard {board.elements.length > 0 && <span className="small muted">({board.elements.length})</span>}
            </button>
          </div>
          {panel === "board" ? (
            <div className="card">
              <div className="small muted">Both of you can draw. Good for "how would this scale?" follow-ups.</div>
              <Whiteboard board={board} onOps={onBoardOps} readOnly={!participant || done} />
            </div>
          ) : (<>
          <div className="row between">
            <select value={lang} onChange={(e) => switchLang(e.target.value as LangId)} disabled={s.my_role !== "candidate" || done}>
              {LANGUAGES.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
            </select>
            {s.my_role === "candidate" && !done
              ? <button className="primary" onClick={() => action("run", "run")} disabled={!!busy}>{busy === "run" ? "Running…" : "Run examples"}</button>
              : <span className="small muted">{done ? "Final code" : `${name(s.candidate_id)} is typing — you can watch live`}</span>}
          </div>
          <div className="editor">
            <Editor height="100%" language={monacoLang(lang)} theme="vs-dark" value={code} onChange={onCode}
              options={{ minimap: { enabled: false }, fontSize: 14, scrollBeyondLastLine: false, readOnly: s.my_role !== "candidate" || done }} />
          </div>
          {run && <Results r={run} fn={!!s.problem?.signature} />}
          </>)}
        </section>
      </div>
    </div>
  );
}

function FeedbackForm({ id, round, who, onDone }: { id: string; round: number; who: string; onDone: () => void }) {
  const [f, setF] = useState({ problem_solving: 3, communication: 3, code_quality: 3, hire: "yes", comments: "" });
  const [error, setError] = useState("");
  const score = (k: "problem_solving" | "communication" | "code_quality", label: string) => (
    <label className="row small">
      <span style={{ minWidth: 130 }}>{label}</span>
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} type="button" className={f[k] === n ? "primary" : ""} onClick={() => setF({ ...f, [k]: n })}>{n}</button>
      ))}
    </label>
  );
  async function submit() {
    setError("");
    try {
      await api(`/peer/${id}/feedback`, { body: { round, ...f } });
      onDone();
    } catch (e: any) {
      setError(e.message);
    }
  }
  return (
    <div className="card stack">
      <h3>Rate {who}'s round {round}</h3>
      {score("problem_solving", "Problem solving")}
      {score("communication", "Communication")}
      {score("code_quality", "Code quality")}
      <label className="row small">
        <span style={{ minWidth: 130 }}>Would you hire?</span>
        <select value={f.hire} onChange={(e) => setF({ ...f, hire: e.target.value })}>
          {Object.entries(HIRE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <textarea rows={3} placeholder="What went well, and one thing to work on" value={f.comments} onChange={(e) => setF({ ...f, comments: e.target.value })} />
      {error && <div className="error">{error}</div>}
      <div><button className="primary" onClick={submit}>Send feedback</button></div>
    </div>
  );
}
