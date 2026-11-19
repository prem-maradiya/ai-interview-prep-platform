import { useEffect, useState } from "react";
import { api, type Comment } from "../lib/api";
import { isAdmin, isStaff, useAuth } from "../lib/auth";

/** Mentor feedback thread on an interview or submission. The owner can reply. */
export default function CommentThread({ targetType, targetId }: { targetType: "interview" | "submission"; targetId: string }) {
  const { user } = useAuth();
  const [items, setItems] = useState<Comment[] | null>(null); // null = still loading
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const load = () =>
    api<Comment[]>(`/comments?target_type=${targetType}&target_id=${targetId}`).then(setItems).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, [targetType, targetId]);

  async function post() {
    setError("");
    try {
      await api("/comments", { body: { target_type: targetType, target_id: targetId, body: draft } });
      setDraft("");
      load();
    } catch (e: any) {
      setError(e.message);
    }
  }

  async function remove(id: string) {
    await api(`/comments/${id}`, { method: "DELETE" });
    load();
  }

  return (
    <div className="card stack">
      <h3>Mentor feedback {!!items?.length && <span className="pill">{items.length}</span>}</h3>
      {items === null && !error && <p className="muted small">Loading…</p>}
      {items?.length === 0 && (
        <p className="muted small">{isStaff(user) ? "Leave feedback for the candidate." : "No feedback from a mentor yet."}</p>
      )}
      {items?.map((c) => (
        <div key={c.id} className={`comment ${c.author_role === "candidate" ? "" : "staff"}`}>
          <div className="row between small">
            <span><b>{c.author_name}</b> <span className="pill">{c.author_role}</span></span>
            <span className="muted">
              {new Date(c.created_at).toLocaleString()}
              {(c.author_id === user?.id || isAdmin(user)) && <button className="link" onClick={() => remove(c.id)}>delete</button>}
            </span>
          </div>
          <div className="comment-body">{c.body}</div>
        </div>
      ))}
      <textarea rows={3} value={draft} onChange={(e) => setDraft(e.target.value)}
        placeholder={isStaff(user) ? "Write feedback…" : "Reply to your mentor…"} />
      {error && <div className="error">{error}</div>}
      <div><button className="primary" disabled={!draft.trim()} onClick={post}>Post</button></div>
    </div>
  );
}
