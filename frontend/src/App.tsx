import type { ComponentType } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { isStaff, useAuth } from "./lib/auth";
import AuthPage from "./pages/AuthPage";
import Dashboard from "./pages/Dashboard";
import Problems from "./pages/Problems";
import ProblemSolve from "./pages/ProblemSolve";
import Interviews from "./pages/Interviews";
import InterviewRoom from "./pages/InterviewRoom";
import OrgAdmin from "./pages/OrgAdmin";
import Documents from "./pages/Documents";
import Assignments, { AssignmentDetail } from "./pages/Assignments";
import SubmissionView from "./pages/SubmissionView";
import MemberActivity from "./pages/MemberActivity";
import AchievementsPage from "./pages/Achievements";
import ProblemListPage from "./pages/ProblemLists";
import NotificationBell from "./components/NotificationBell";
import OAuthComplete from "./pages/OAuthComplete";
import Account from "./pages/Account";
import { ForgotPassword, ResetPassword, Unsubscribe } from "./pages/PublicPages";

// Opened from emails; they work whether or not someone is signed in.
const PUBLIC_PAGES: Record<string, ComponentType> = {
  "/forgot-password": ForgotPassword,
  "/reset-password": ResetPassword,
  "/unsubscribe": Unsubscribe,
};

export default function App() {
  const { user, loading, signOut } = useAuth();
  const location = useLocation();
  // Social sign-in lands here signed out; it must render before the auth gate.
  if (location.pathname === "/oauth/complete" && !user) return <OAuthComplete />;
  const PublicPage = PUBLIC_PAGES[location.pathname];
  if (PublicPage) return <PublicPage />;
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
          <NavLink to="/assignments">Assignments</NavLink>
          <NavLink to="/achievements">Achievements</NavLink>
          {isStaff(user) && <NavLink to="/org">Organization</NavLink>}
        </nav>
        <NotificationBell />
        <span className="who">
          <Link to="/account" title="Account settings">{user.full_name}</Link> · <b>{user.tenant.name}</b> <span className="pill">{user.role}</span>
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
          <Route path="/assignments" element={<Assignments />} />
          <Route path="/achievements" element={<AchievementsPage />} />
          <Route path="/account" element={<Account />} />
          <Route path="/oauth/complete" element={<OAuthComplete />} />
          <Route path="/lists/:id" element={<ProblemListPage />} />
          <Route path="/assignments/:id" element={<AssignmentDetail />} />
          <Route path="/submissions/:id" element={<SubmissionView />} />
          <Route path="/org/members/:id" element={isStaff(user) ? <MemberActivity /> : <Navigate to="/" />} />
          <Route path="/org" element={isStaff(user) ? <OrgAdmin /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
