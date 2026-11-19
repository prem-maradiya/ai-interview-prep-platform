import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";

type Item = { id: string; kind: string; title: string; body: string; link: string; read: boolean; created_at: string };
const ICON: Record<string, string> = { assignment: "📋", due_soon: "⏰", comment: "💬", badge: "🏅" };
const POLL_MS = 60_000;

function ago(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationBell() {
  const nav = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  const load = () =>
    api<{ unread_count: number; items: Item[] }>("/notifications?limit=20")
      .then((r) => { setItems(r.items); setUnread(r.unread_count); })
      .catch(() => {}); // a failed poll just keeps the last state

  useEffect(() => {
    load();
    const t = setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    load();
    setOpen(false);
  }, [location.pathname]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  async function openItem(n: Item) {
    if (!n.read) await api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => {});
    setOpen(false);
    if (n.link) nav(n.link);
    load();
  }

  async function readAll() {
    await api("/notifications/read-all", { method: "POST" });
    load();
  }

  return (
    <div className="bell" ref={box}>
      <button className="bell-button" onClick={() => setOpen(!open)} aria-label={`Notifications (${unread} unread)`}>
        🔔{unread > 0 && <span className="bell-count">{unread > 9 ? "9+" : unread}</span>}
      </button>
      {open && (
        <div className="bell-panel card">
          <div className="row between">
            <b>Notifications</b>
            {unread > 0 && <button className="link small" onClick={readAll}>Mark all read</button>}
          </div>
          {items.length === 0 && <p className="muted small">You're all caught up.</p>}
          {items.map((n) => (
            <button key={n.id} className={`bell-item ${n.read ? "" : "unread"}`} onClick={() => openItem(n)}>
              <span className="bell-icon">{ICON[n.kind] ?? "•"}</span>
              <span className="bell-text">
                <span className="bell-title">{n.title}</span>
                {n.body && <span className="small muted">{n.body}</span>}
                <span className="small muted">{ago(n.created_at)}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
