import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { api } from "../lib/api";
import { isStaff, useAuth } from "../lib/auth";

type Access = { unlocked: boolean; reason: string; contest?: { id: string; title: string } };
type Post = {
  id: string; parent_id: string | null; body: string; deleted: boolean; author: { id: string; name: string; role: string } | null;
  score: number; my_vote: number; created_at: string; edited: boolean; can_edit: boolean; can_delete: boolean; replies?: Post[];
};

function Locked({ a, problemId, onUnlock, what }: { a: Access; problemId: string; onUnlock: () => void; what: string }) {
  const [confirming, setConfirming] = useState(false);
  if (a.reason === "contest") {
    return <div className="card muted">The {what} for this problem are hidden while the contest <b>{a.contest?.title}</b> is running.</div>;
  }
  return (
    <div className="card stack tight">
      <div>Solve the problem to unlock the {what}.</div>
      {!confirming ? (
        <div><button onClick={() => setConfirming(true)}>Show anyway</button></div>
      ) : (
        <div className="stack tight">
          <div className="small muted">Peeking is fine when you're stuck, but it's recorded and your mentors will see it if you solve the problem afterwards.</div>
          <div className="row">
            <button className="primary" onClick={() => api(`/problems/${problemId}/reveal`, { method: "POST" }).then(onUnlock)}>Yes, show me</button>
            <button onClick={() => setConfirming(false)}>Keep trying</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** Discussion thread for a problem (organization-only), unlocked after solving or revealing. */
export function Discussion({ problemId }: { problemId: string }) {
  const [data, setData] = useState<(Access & { count: number; items: Post[] }) | null>(null);
  const [sort, setSort] = useState<"top" | "new">("top");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const load = () => api<Access & { count: number; items: Post[] }>(`/problems/${problemId}/discussions?sort=${sort}`).then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [problemId, sort]);

  async function post(text: string, parent_id?: string) {
    setError("");
    try {
      await api(`/problems/${problemId}/discussions`, { body: { body: text, parent_id } });
      load();
      return true;
    } catch (e: any) {
      setError(e.message);
      return false;
    }
  }

  if (!data) return <div className="muted small">{error || "Loading…"}</div>;
  if (!data.unlocked) return <Locked a={data} problemId={problemId} onUnlock={load} what={`discussions (${data.count} post${data.count === 1 ? "" : "s"})`} />;
  return (
    <div className="stack">
      <div className="row between">
        <b>{data.count} post{data.count === 1 ? "" : "s"}</b>
        <select value={sort} onChange={(e) => setSort(e.target.value as "top" | "new")}>
          <option value="top">Top</option><option value="new">Newest</option>
        </select>
      </div>
      <div className="stack tight">
        <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} placeholder="Share an approach, a gotcha or a question (markdown and ```code``` work)" />
        <div><button className="primary" disabled={!body.trim()} onClick={async () => { if (await post(body)) setBody(""); }}>Post</button></div>
      </div>
      {error && <div className="error">{error}</div>}
      {data.items.length === 0 && <div className="muted small">No posts yet. Be the first to share how you solved it.</div>}
      {data.items.map((p) => <PostView key={p.id} p={p} onChanged={load} onReply={(t) => post(t, p.id)} />)}
    </div>
  );
}

function PostView({ p, onChanged, onReply, nested = false }: { p: Post; onChanged: () => void; onReply?: (t: string) => Promise<boolean>; nested?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(p.body);
  const [replying, setReplying] = useState(false);
  const [reply, setReply] = useState("");
  const [vote, setVote] = useState({ score: p.score, mine: p.my_vote });

  async function cast(v: number) {
    const value = vote.mine === v ? 0 : v;
    try {
      const r = await api<{ score: number; my_vote: number }>(`/discussions/${p.id}/vote`, { body: { value } });
      setVote({ score: r.score, mine: r.my_vote });
    } catch { /* own post or locked: ignore */ }
  }

  return (
    <div className={`post ${nested ? "nested" : "card"}`}>
      <div className="row between small">
        <span>
          {p.author ? <b>{p.author.name}</b> : <span className="muted">deleted</span>}
          {p.author && p.author.role !== "candidate" && <span className="pill">{p.author.role}</span>}
          <span className="muted"> · {new Date(p.created_at).toLocaleString()}{p.edited && " · edited"}</span>
        </span>
        {!p.deleted && (
          <span className="votes">
            <button className={`link ${vote.mine === 1 ? "voted" : ""}`} onClick={() => cast(1)} title="Helpful">▲</button>
            <b>{vote.score}</b>
            <button className={`link ${vote.mine === -1 ? "voted" : ""}`} onClick={() => cast(-1)} title="Not helpful">▼</button>
          </span>
        )}
      </div>
      {editing ? (
        <div className="stack tight">
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="row">
            <button className="primary" onClick={() => api(`/discussions/${p.id}`, { method: "PATCH", body: { body: text } }).then(() => { setEditing(false); onChanged(); })}>Save</button>
            <button onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      ) : <div className="prose"><Markdown>{p.body}</Markdown></div>}
      <div className="row small">
        {onReply && !p.deleted && <button className="link" onClick={() => setReplying(!replying)}>Reply</button>}
        {p.can_edit && !editing && <button className="link" onClick={() => setEditing(true)}>Edit</button>}
        {p.can_delete && <button className="link" onClick={() => api(`/discussions/${p.id}`, { method: "DELETE" }).then(onChanged)}>Delete</button>}
      </div>
      {replying && onReply && (
        <div className="stack tight">
          <textarea rows={2} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Reply…" />
          <div><button onClick={async () => { if (await onReply(reply)) { setReply(""); setReplying(false); } }} disabled={!reply.trim()}>Reply</button></div>
        </div>
      )}
      {(p.replies ?? []).map((r) => <PostView key={r.id} p={r} onChanged={onChanged} nested />)}
    </div>
  );
}

/** The organization's editorial: readable once unlocked; mentors write it (optionally from an AI draft). */
export function Editorial({ problemId }: { problemId: string }) {
  const { user } = useAuth();
  const staff = isStaff(user);
  const [data, setData] = useState<(Access & { exists: boolean; editorial: { body: string; author: string | null; updated_at: string } | null }) | null>(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = () => api<any>(`/problems/${problemId}/editorial`).then(setData).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [problemId]);

  async function run(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setError("");
    try { await fn(); } catch (e: any) { setError(e.message); } finally { setBusy(""); }
  }

  if (!data) return <div className="muted small">{error || "Loading…"}</div>;
  if (!data.unlocked) {
    return data.exists ? <Locked a={data} problemId={problemId} onUnlock={load} what="editorial" />
      : <div className="card muted">There's no editorial for this problem yet.</div>;
  }
  if (editing) {
    return (
      <div className="stack">
        <textarea rows={16} value={text} onChange={(e) => setText(e.target.value)} placeholder="## Intuition…" />
        {error && <div className="error">{error}</div>}
        <div className="row">
          <button className="primary" disabled={!text.trim() || !!busy} onClick={() => run("save", async () => {
            await api(`/problems/${problemId}/editorial`, { method: "PUT", body: { body: text } });
            setEditing(false);
            await load();
          })}>Publish</button>
          <button disabled={!!busy} onClick={() => run("draft", async () => {
            setText((await api<{ body: string }>(`/problems/${problemId}/editorial/draft`, { method: "POST" })).body);
          })}>{busy === "draft" ? "Drafting…" : "Draft with AI"}</button>
          <button onClick={() => setEditing(false)}>Cancel</button>
        </div>
        <div className="small muted">AI drafts are a starting point: check the reasoning and the reference solution before publishing.</div>
      </div>
    );
  }
  return (
    <div className="stack">
      {data.editorial ? (
        <>
          <div className="small muted">By {data.editorial.author ?? "a mentor"} · updated {new Date(data.editorial.updated_at).toLocaleDateString()}</div>
          <div className="prose"><Markdown>{data.editorial.body}</Markdown></div>
        </>
      ) : <div className="muted">No editorial yet.</div>}
      {staff && (
        <div className="row">
          <button onClick={() => { setText(data.editorial?.body ?? ""); setEditing(true); }}>{data.editorial ? "Edit editorial" : "Write editorial"}</button>
          {data.editorial && <button onClick={() => run("delete", async () => { await api(`/problems/${problemId}/editorial`, { method: "DELETE" }); await load(); })}>Delete</button>}
        </div>
      )}
    </div>
  );
}
