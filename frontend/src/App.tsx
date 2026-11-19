import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { isStaff, useAuth } from "./lib/auth";
import AuthPage from "./pages/AuthPage";
import Dashboard from "./pages/Dashboard";
import Problems from "./pages/Problems";
import ProblemSolve from "./pages/ProblemSolve";
import Interviews from "./pages/Interviews";
import InterviewRoom from "./pages/InterviewRoom";
import OrgAdmin from "./pages/OrgAdmin";
import Documents from "./pages/Documents";

export default function App() {
  const { user, loading, signOut } = useAuth();
  if (loading) return <div className="center muted">Loading…</div>;
  if (!user) return <AuthPage />;

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">PrepForge</span>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/problems">Problems</NavLink>
          <NavLink to="/interviews">Mock interviews</NavLink>
          <NavLink to="/documents">Resume &amp; jobs</NavLink>
          {isStaff(user) && <NavLink to="/org">Organization</NavLink>}
        </nav>
        <span className="who">
          {user.full_name} · <b>{user.tenant.name}</b> <span className="pill">{user.role}</span>
          <button className="link" onClick={signOut}>Sign out</button>
        </span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/problems" element={<Problems />} />
          <Route path="/problems/:id" element={<ProblemSolve />} />
          <Route path="/interviews" element={<Interviews />} />
          <Route path="/interviews/:id" element={<InterviewRoom />} />
          <Route path="/documents" element={<Documents />} />
          <Route path="/org" element={isStaff(user) ? <OrgAdmin /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
