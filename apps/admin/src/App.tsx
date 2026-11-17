import { useEffect, useState, type FormEvent } from "react";
import { api, setCsrf, type Session } from "./api";
import { AccountsPage } from "./accounts";
import { AnalyticsPage } from "./analytics";
import { AuditPage } from "./audit";
import { ClientsPage } from "./clients";
import { errorMessage, isPlatformAdmin, roleLabel } from "./format";
import { JobsPage } from "./jobs";
import { OverviewPage } from "./overview";

type Page = "overview" | "jobs" | "clients" | "audit" | "accounts" | "analytics";

const NAV: Array<{ label: string; items: Array<[Page, string]> }> = [
  { label: "Health", items: [["overview", "Overview"]] },
  { label: "Sync", items: [["jobs", "Jobs"]] },
  {
    label: "Access",
    items: [
      ["clients", "Clients"],
      ["audit", "Audit"],
      ["accounts", "Accounts"],
    ],
  },
  { label: "Insight", items: [["analytics", "Insights"]] },
];

export function App() {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [page, setPage] = useState<Page>("overview");
  const [zone, setZone] = useState("Asia/Hong_Kong");
  const [logoutError, setLogoutError] = useState("");

  function enter(next: Session) {
    setCsrf(next.csrf_token);
    setSession(next);
    setPage(isPlatformAdmin(next.role) ? "overview" : "analytics");
  }

  useEffect(() => {
    api<Session>("/admin/api/me")
      .then(enter)
      .catch(() => setSession(null))
      .finally(() => setReady(true));
  }, []);

  async function signOut() {
    setLogoutError("");
    try {
      await api("/admin/api/logout", { method: "POST" });
      setCsrf("");
      setSession(null);
    } catch (err) {
      setLogoutError(errorMessage(err, "Could not sign out"));
    }
  }

  if (!ready) return <div className="boot">Loading…</div>;
  if (!session) return <Login onSuccess={enter} />;

  const admin = isPlatformAdmin(session.role);
  const current = admin ? page : "analytics";

  return (
    <div className="shell">
      <nav>
        <div className="nav-brand">
          <img src="/ycis-mark.png" alt="YCIS" width={40} height={40} />
          <div>
            <strong>ManageBac Data Hub</strong>
            <span>YCIS HONG KONG</span>
          </div>
        </div>
        <p className="nav-user">
          <span>{session.email}</span>
          <span className="role">{roleLabel(session.role)}</span>
        </p>
        <div className="nav-scroll">
          {(admin ? NAV : [{ label: "Insight", items: [["analytics", "Insights"] as [Page, string]] }]).map((group) => (
            <div key={group.label} className="nav-cluster">
              {admin ? <p className="nav-group">{group.label}</p> : null}
              {group.items.map(([item, label]) => (
                <button key={item} type="button" className={current === item ? "active" : ""} onClick={() => setPage(item)}>
                  {label}
                </button>
              ))}
            </div>
          ))}
          <button type="button" className="sign-out" onClick={signOut}>
            Sign out
          </button>
          {logoutError ? <p className="nav-error">{logoutError}</p> : null}
        </div>
      </nav>
      <main>
        {current === "overview" && <OverviewPage zone={zone} onZone={setZone} />}
        {current === "jobs" && <JobsPage zone={zone} />}
        {current === "clients" && <ClientsPage zone={zone} />}
        {current === "audit" && <AuditPage zone={zone} />}
        {current === "accounts" && <AccountsPage currentEmail={session.email} />}
        {current === "analytics" && <AnalyticsPage />}
      </main>
    </div>
  );
}

function Login({ onSuccess }: { onSuccess: (session: Session) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await api<Session>("/admin/api/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      onSuccess(session);
    } catch (err) {
      setError(errorMessage(err, "Sign-in failed"));
      setBusy(false);
    }
  }

  return (
    <div className="signin">
      <section className="brand-panel" aria-label="YCIS Hong Kong">
        <div className="brand-copy">
          <div className="lockup">
            <img src="/ycis-mark.png" alt="" width={76} height={76} />
            <div>
              <div className="ycis">YCIS</div>
              <div className="place">HONG KONG</div>
            </div>
          </div>
          <h1>ManageBac Data Hub</h1>
          <p className="lede">Read-only school data</p>
        </div>
      </section>
      <div className="signin-stage">
        <form className="card" onSubmit={submit}>
          <h1>Sign in</h1>
          <label>
            Email
            <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {error ? <p className="error">{error}</p> : null}
          <button type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
