import { useEffect, useState, type FormEvent } from "react";
import { api, setCsrf, type Session } from "./api";

type Page = "overview" | "jobs" | "clients" | "analytics";

type Overview = {
  school_timezone: string;
  school_timezone_confirmed: boolean;
  retention_configured: boolean;
  database: string;
  connection: { ok: boolean; checked_at: string; detail: string | null } | null;
  datasets: Array<{ dataset: string; status: string | null; blocked: boolean; record_count: number | null; error_summary: string | null }>;
};

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [page, setPage] = useState<Page>("overview");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Session>("/admin/api/me")
      .then((me) => {
        setCsrf(me.csrf_token);
        setSession(me);
      })
      .catch(() => setSession(null));
  }, []);

  if (!session) return <Login onSuccess={setSession} />;

  return (
    <div className="shell">
      <nav>
        <strong>Data Hub</strong>
        <p>{session.email}</p>
        {(["overview", "jobs", "clients", "analytics"] as Page[]).map((item) => (
          <button key={item} className={page === item ? "active" : ""} onClick={() => setPage(item)}>
            {item}
          </button>
        ))}
      </nav>
      <main>
        {error && <p className="error">{error}</p>}
        {page === "overview" && <OverviewPage />}
        {page === "jobs" && <JobsPage onError={setError} />}
        {page === "clients" && <ClientsPage role={session.role} onError={setError} />}
        {page === "analytics" && <AnalyticsPage />}
      </main>
    </div>
  );
}

function Login({ onSuccess }: { onSuccess: (session: Session) => void }) {
  const [email, setEmail] = useState("admin@example.com");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const session = await api<Session>("/admin/api/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setCsrf(session.csrf_token);
      onSuccess(session);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>ManageBac Data Hub</h1>
      <label>Email<br /><input value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label>Password<br /><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      {error && <p className="error">{error}</p>}
      <button type="submit">Sign in</button>
    </form>
  );
}

function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  useEffect(() => {
    api<Overview>("/admin/api/overview").then(setData);
  }, []);
  if (!data) return <p>Loading…</p>;
  return (
    <section>
      <h1>Overview</h1>
      {!data.school_timezone_confirmed && (
        <div className="banner">School timezone is not confirmed. Current value: {data.school_timezone}. Database: {data.database}.</div>
      )}
      {!data.retention_configured && <div className="banner">Retention is not approved, so raw responses and audit rows are kept.</div>}
      <p>Connection: {data.connection ? `${data.connection.ok ? "reachable" : "failed"} at ${data.connection.checked_at}` : "no check yet"}</p>
      <table>
        <thead><tr><th>Dataset</th><th>Status</th><th>Records</th><th>Note</th></tr></thead>
        <tbody>
          {data.datasets.map((row) => (
            <tr key={row.dataset}>
              <td>{row.dataset}</td>
              <td>{row.blocked ? "blocked" : row.status}</td>
              <td>{row.record_count ?? ""}</td>
              <td>{row.error_summary || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function JobsPage({ onError }: { onError: (message: string) => void }) {
  const [jobs, setJobs] = useState<Array<{ id: string; dataset: string; status: string; trigger: string }>>([]);
  const [dataset, setDataset] = useState("students");

  async function load() {
    const body = await api<{ data: typeof jobs }>("/admin/api/jobs");
    setJobs(body.data);
  }
  useEffect(() => { load().catch((err) => onError(err.message)); }, []);

  async function sync() {
    try {
      await api("/admin/api/jobs", {
        method: "POST",
        body: JSON.stringify({ dataset, idempotency_key: crypto.randomUUID(), mode: "full" }),
      });
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Sync failed");
    }
  }

  return (
    <section>
      <h1>Sync jobs</h1>
      <div className="row">
        <select value={dataset} onChange={(event) => setDataset(event.target.value)}>
          {["students", "teachers", "classes", "attendance_class"].map((item) => <option key={item}>{item}</option>)}
        </select>
        <button onClick={sync}>Queue sync</button>
      </div>
      <table>
        <thead><tr><th>Dataset</th><th>Status</th><th>Trigger</th></tr></thead>
        <tbody>{jobs.map((job) => <tr key={job.id}><td>{job.dataset}</td><td>{job.status}</td><td>{job.trigger}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

function ClientsPage({ role, onError }: { role: string; onError: (message: string) => void }) {
  const [secret, setSecret] = useState("");
  const [clients, setClients] = useState<Array<{ id: string; name: string; enabled: boolean }>>([]);
  if (role !== "platform_admin") return <p>Only a platform admin can manage API clients.</p>;

  async function load() {
    const body = await api<{ data: typeof clients }>("/admin/api/clients");
    setClients(body.data);
  }
  useEffect(() => { load().catch((err) => onError(err.message)); }, []);

  async function createClient() {
    try {
      const body = await api<{ credential: { secret: string } }>("/admin/api/clients", {
        method: "POST",
        body: JSON.stringify({
          name: "school-portal",
          scopes: ["students:read"],
          requests_per_minute: 60,
          records_per_day: 5000,
          bytes_per_day: 5000000,
        }),
      });
      setSecret(body.credential.secret);
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not create the client");
    }
  }

  return (
    <section>
      <h1>API clients</h1>
      <button onClick={createClient}>Create a read client</button>
      {secret && <p>Copy this key now. It is not shown again: <code>{secret}</code></p>}
      <table>
        <thead><tr><th>Name</th><th>Enabled</th></tr></thead>
        <tbody>{clients.map((client) => <tr key={client.id}><td>{client.name}</td><td>{client.enabled ? "yes" : "no"}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

function AnalyticsPage() {
  const [summary, setSummary] = useState<{ student_count: number; attendance_rate_published: boolean } | null>(null);
  useEffect(() => { api<typeof summary>("/admin/api/analytics/summary").then(setSummary); }, []);
  if (!summary) return <p>Loading…</p>;
  return (
    <section>
      <h1>Analytics</h1>
      <p>Students: {summary.student_count}. This count comes from the roster view, so a student in several classes is counted once.</p>
      <p>Attendance rate published: {summary.attendance_rate_published ? "yes" : "no"}.</p>
    </section>
  );
}
