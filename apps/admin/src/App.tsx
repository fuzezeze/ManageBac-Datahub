import { useEffect, useState, type FormEvent } from "react";
import { api, setCsrf, type Session } from "./api";

type Page = "overview" | "sync" | "clients" | "users" | "usage" | "analytics";

type Overview = {
  school_timezone: string;
  school_timezone_confirmed: boolean;
  retention_configured: boolean;
  database: string;
  connection: { ok: boolean; checked_at: string; detail: string | null } | null;
  datasets: Array<{ dataset: string; status: string | null; blocked: boolean; record_count: number | null; error_summary: string | null }>;
};

type ReadApi = { name: string; title: string; route: string; scope: string; fields: string[] };
type FieldMap = Record<string, string[]>;

const NAV: Array<[Page, string]> = [
  ["overview", "Overview"],
  ["sync", "Sync"],
  ["clients", "APIs"],
  ["users", "Users"],
  ["usage", "Usage"],
  ["analytics", "Analytics"],
];

export function App() {
  const [session, setSession] = useState<Session | null>(null);
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
  return <Workspace session={session} error={error} onError={setError} onSignOut={() => setSession(null)} />;
}

function Workspace({ session, error, onError, onSignOut }: { session: Session; error: string; onError: (message: string) => void; onSignOut: () => void }) {
  const isUser = session.role === "user";
  const [page, setPage] = useState<Page>(isUser ? "analytics" : "overview");
  const pages = isUser ? ([["analytics", "Analytics"]] as Array<[Page, string]>) : NAV;

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
        <p className="nav-user">{session.email}<br />{isUser ? "User" : "Admin"}</p>
        {pages.map(([item, label]) => (
          <button key={item} className={page === item ? "active" : ""} onClick={() => setPage(item)}>
            {label}
          </button>
        ))}
        <button
          onClick={async () => {
            try {
              await api("/admin/api/logout", { method: "POST" });
            } catch {
              // The session cookie is cleared locally even if the server session already expired.
            }
            setCsrf("");
            onSignOut();
          }}
        >
          Sign out
        </button>
      </nav>
      <main>
        {error && <p className="error">{error}</p>}
        {page === "overview" && <OverviewPage />}
        {page === "sync" && <SyncPage onError={onError} />}
        {page === "clients" && <ClientsPage role={session.role} onError={onError} />}
        {page === "users" && <UsersPage onError={onError} />}
        {page === "usage" && <UsagePage />}
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
          <p className="card-note">Admins manage sync and APIs. Users open Analytics only.</p>
          <label>
            Email
            <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label>
            Password
            <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">Sign in</button>
        </form>
      </div>
    </div>
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

type Schedule = {
  dataset: string;
  blocked: boolean;
  supports_dates: boolean;
  cadence: string;
  hour: number | null;
  minute: number;
  interval_minutes: number | null;
  enabled: boolean;
};

function SyncPage({ onError }: { onError: (message: string) => void }) {
  const [rows, setRows] = useState<Schedule[]>([]);
  const [jobs, setJobs] = useState<Array<{ id: string; dataset: string; status: string; trigger: string }>>([]);
  const [dataset, setDataset] = useState("");
  const [dateFrom, setDateFrom] = useState(isoDaysAgo(7));
  const [dateTo, setDateTo] = useState(isoDaysAgo(0));

  async function load() {
    const [schedules, jobBody] = await Promise.all([
      api<{ data: Schedule[] }>("/admin/api/schedules"),
      api<{ data: typeof jobs }>("/admin/api/jobs"),
    ]);
    setRows(schedules.data);
    setJobs(jobBody.data);
    setDataset((current) => current || schedules.data.find((row) => !row.blocked)?.dataset || "");
  }
  useEffect(() => { load().catch((err) => onError(err.message)); }, []);

  const selected = rows.find((row) => row.dataset === dataset);

  async function save(row: Schedule) {
    try {
      await api(`/admin/api/schedules/${row.dataset}`, {
        method: "PATCH",
        body: JSON.stringify({
          cadence: row.cadence,
          hour: row.hour,
          minute: row.minute,
          interval_minutes: row.interval_minutes,
          enabled: row.enabled,
        }),
      });
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not save the schedule");
    }
  }

  async function syncNow() {
    if (!selected) return;
    try {
      await api("/admin/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          dataset: selected.dataset,
          idempotency_key: crypto.randomUUID(),
          mode: "full",
          ...(selected.supports_dates ? { date_from: dateFrom, date_to: dateTo } : {}),
        }),
      });
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Sync failed");
    }
  }

  function edit(datasetName: string, patch: Partial<Schedule>) {
    setRows((current) => current.map((row) => (row.dataset === datasetName ? { ...row, ...patch } : row)));
  }

  return (
    <section>
      <h1>Sync</h1>
      <p>Each dataset keeps its own schedule. A manual sync runs once and does not change that schedule.</p>
      <div className="toolbar">
        <select value={dataset} onChange={(event) => setDataset(event.target.value)}>
          {rows.filter((row) => !row.blocked).map((row) => <option key={row.dataset}>{row.dataset}</option>)}
        </select>
        {selected?.supports_dates && (
          <>
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </>
        )}
        <button onClick={syncNow}>Sync now</button>
      </div>
      <table>
        <thead><tr><th>Dataset</th><th>Cadence</th><th>When</th><th>Enabled</th><th></th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.dataset}>
              <td>{row.dataset}{row.blocked ? " (blocked)" : ""}</td>
              <td>
                <select value={row.cadence} disabled={row.blocked} onChange={(event) => edit(row.dataset, { cadence: event.target.value })}>
                  <option value="daily">daily</option>
                  <option value="hourly">hourly</option>
                  <option value="interval">interval</option>
                </select>
              </td>
              <td>
                {row.cadence === "daily" && (
                  <input type="number" min={0} max={23} value={row.hour ?? 2} onChange={(event) => edit(row.dataset, { hour: Number(event.target.value) })} />
                )}
                {row.cadence !== "interval" && (
                  <input type="number" min={0} max={59} value={row.minute} onChange={(event) => edit(row.dataset, { minute: Number(event.target.value) })} />
                )}
                {row.cadence === "interval" && (
                  <input type="number" min={5} max={1440} value={row.interval_minutes ?? 15} onChange={(event) => edit(row.dataset, { interval_minutes: Number(event.target.value) })} />
                )}
              </td>
              <td><input type="checkbox" checked={row.enabled} disabled={row.blocked} onChange={(event) => edit(row.dataset, { enabled: event.target.checked })} /></td>
              <td><button className="secondary" disabled={row.blocked} onClick={() => save(row)}>Save</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Recent jobs</h2>
      <table>
        <thead><tr><th>Dataset</th><th>Status</th><th>Trigger</th></tr></thead>
        <tbody>{jobs.map((job) => <tr key={job.id}><td>{job.dataset}</td><td>{job.status}</td><td>{job.trigger}</td></tr>)}</tbody>
      </table>
    </section>
  );
}

function ClientsPage({ role, onError }: { role: string; onError: (message: string) => void }) {
  const [apis, setApis] = useState<ReadApi[]>([]);
  const [clients, setClients] = useState<Array<{ id: string; name: string; enabled: boolean; scopes: string[]; fields: FieldMap }>>([]);
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldMap>({});
  const [secret, setSecret] = useState("");

  async function load() {
    const body = await api<{ data: typeof clients; apis: ReadApi[] }>("/admin/api/clients");
    setClients(body.data);
    setApis(body.apis);
  }
  useEffect(() => { load().catch((err) => onError(err.message)); }, []);
  if (role !== "platform_admin") return <p>Only an admin can manage APIs.</p>;

  function toggle(resource: string, field: string, on: boolean) {
    setFields((current) => {
      const chosen = new Set(current[resource] || []);
      if (on) chosen.add(field);
      else chosen.delete(field);
      const next = { ...current, [resource]: [...chosen] };
      if (next[resource].length === 0) delete next[resource];
      return next;
    });
  }

  function scopesFor(selected: FieldMap) {
    const scopes = new Set<string>();
    for (const apiDef of apis) {
      const chosen = selected[apiDef.name] || [];
      if (!chosen.length) continue;
      scopes.add(apiDef.scope);
      if (chosen.includes("email") && apiDef.name === "students") scopes.add("students:contact");
      if (chosen.includes("email") && apiDef.name === "teachers") scopes.add("teachers:contact");
    }
    return [...scopes];
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const scopes = scopesFor(fields);
    const payload = {
      name,
      scopes,
      fields,
      requests_per_minute: 60,
      records_per_day: 5000,
      bytes_per_day: 5000000,
    };
    try {
      if (editing) {
        await api(`/admin/api/clients/${editing}`, { method: "PATCH", body: JSON.stringify(payload) });
        setSecret("");
      } else {
        const body = await api<{ credential: { secret: string } }>("/admin/api/clients", { method: "POST", body: JSON.stringify(payload) });
        setSecret(body.credential.secret);
      }
      setName("");
      setFields({});
      setEditing(null);
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not save the API");
    }
  }

  async function remove(id: string) {
    try {
      await api(`/admin/api/clients/${id}`, { method: "DELETE" });
      if (editing === id) {
        setEditing(null);
        setName("");
        setFields({});
      }
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not delete the API");
    }
  }

  return (
    <section>
      <h1>Read APIs</h1>
      <p>Each API client calls the read endpoints you select. Checked fields are the only ones returned for that endpoint.</p>
      <form onSubmit={save}>
        <div className="toolbar">
          <input placeholder="API name" value={name} onChange={(event) => setName(event.target.value)} required />
          <button type="submit">{editing ? "Save API" : "Add API"}</button>
          {editing && <button type="button" className="secondary" onClick={() => { setEditing(null); setName(""); setFields({}); }}>Cancel</button>}
        </div>
        <div className="api-fields">
          {apis.map((apiDef) => (
            <fieldset key={apiDef.name}>
              <legend>{apiDef.title}</legend>
              <div className="checks">
                {apiDef.fields.map((field) => (
                  <label key={field} className="check">
                    <input
                      type="checkbox"
                      checked={(fields[apiDef.name] || []).includes(field)}
                      onChange={(event) => toggle(apiDef.name, field, event.target.checked)}
                    />
                    {field}
                  </label>
                ))}
              </div>
            </fieldset>
          ))}
        </div>
      </form>
      {secret && <p>Copy this key now. It is not shown again: <code>{secret}</code></p>}
      <table>
        <thead><tr><th>Name</th><th>Enabled</th><th>Fields</th><th></th></tr></thead>
        <tbody>
          {clients.map((client) => (
            <tr key={client.id}>
              <td>{client.name}</td>
              <td>{client.enabled ? "yes" : "no"}</td>
              <td>{Object.entries(client.fields).map(([resource, names]) => `${resource}: ${names.join(", ")}`).join(" · ") || "all default fields"}</td>
              <td>
                <button className="secondary" onClick={() => { setEditing(client.id); setName(client.name); setFields(client.fields); }}>Edit</button>
                <button className="secondary" onClick={() => remove(client.id)}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function UsersPage({ onError }: { onError: (message: string) => void }) {
  const [users, setUsers] = useState<Array<{ id: string; email: string; role: string; disabled: boolean }>>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");

  async function load() {
    const body = await api<{ data: typeof users }>("/admin/api/users");
    setUsers(body.data);
  }
  useEffect(() => { load().catch((err) => onError(err.message)); }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/admin/api/users", { method: "POST", body: JSON.stringify({ email, password, role }) });
      setEmail("");
      setPassword("");
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not add the user");
    }
  }

  async function setDisabled(id: string, disabled: boolean) {
    try {
      await api(`/admin/api/users/${id}`, { method: "PATCH", body: JSON.stringify({ disabled }) });
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not update the user");
    }
  }

  return (
    <section>
      <h1>Users</h1>
      <p>Admin can open every page. User can open Analytics only.</p>
      <form className="toolbar" onSubmit={create}>
        <input type="email" placeholder="Email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        <input type="password" placeholder="Password" value={password} onChange={(event) => setPassword(event.target.value)} required />
        <select value={role} onChange={(event) => setRole(event.target.value)}>
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
        <button type="submit">Add user</button>
      </form>
      <table>
        <thead><tr><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {users.map((user) => (
            <tr key={user.id}>
              <td>{user.email}</td>
              <td>{user.role}</td>
              <td>{user.disabled ? "disabled" : "active"}</td>
              <td>
                <button className="secondary" onClick={() => setDisabled(user.id, !user.disabled)}>
                  {user.disabled ? "Enable" : "Disable"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function UsagePage() {
  const [body, setBody] = useState<{
    by_route: Array<{ route: string; request_count: number; success_count: number; error_count: number; record_count: number; byte_count: number }>;
    by_client: Array<{ client_name: string; route: string; request_count: number; success_count: number; error_count: number; record_count: number }>;
  } | null>(null);
  useEffect(() => { api<typeof body>("/admin/api/usage").then(setBody); }, []);
  if (!body) return <p>Loading…</p>;
  return (
    <section>
      <h1>API usage</h1>
      <p>Counts come from the daily usage buckets written by each Internal API call.</p>
      <table>
        <thead><tr><th>API</th><th>Requests</th><th>Success</th><th>Errors</th><th>Records</th><th>Bytes</th></tr></thead>
        <tbody>
          {body.by_route.map((row) => (
            <tr key={row.route}>
              <td>{row.route}</td>
              <td>{row.request_count}</td>
              <td>{row.success_count}</td>
              <td>{row.error_count}</td>
              <td>{row.record_count}</td>
              <td>{row.byte_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>By client</h2>
      <table>
        <thead><tr><th>Client</th><th>API</th><th>Requests</th><th>Success</th><th>Errors</th><th>Records</th></tr></thead>
        <tbody>
          {body.by_client.map((row) => (
            <tr key={`${row.client_name}-${row.route}`}>
              <td>{row.client_name || "unknown"}</td>
              <td>{row.route}</td>
              <td>{row.request_count}</td>
              <td>{row.success_count}</td>
              <td>{row.error_count}</td>
              <td>{row.record_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

type ReportSpec = {
  key: string;
  title: string;
  description: string;
  filters: Array<{ key: string; label: string; kind: string }>;
  columns: Array<{ key: string; label: string }>;
};

function AnalyticsPage() {
  const [reports, setReports] = useState<ReportSpec[]>([]);
  const [key, setKey] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<Array<Record<string, string | null>>>([]);
  const [loadedAt, setLoadedAt] = useState("");

  useEffect(() => {
    api<{ reports: ReportSpec[] }>("/admin/api/analytics/reports").then((body) => {
      setReports(body.reports);
      setKey((current) => current || body.reports[0]?.key || "");
    });
  }, []);

  const report = reports.find((item) => item.key === key);

  useEffect(() => {
    if (!key) return;
    let stop = false;
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries(filters)) {
      if (value) query.set(name, value);
    }
    const path = `/admin/api/analytics/reports/${key}${query.size ? `?${query}` : ""}`;
    async function load() {
      const body = await api<{ data: Array<Record<string, string | null>> }>(path);
      if (!stop) {
        setRows(body.data);
        setLoadedAt(new Date().toLocaleTimeString());
      }
    }
    load().catch(() => undefined);
    const timer = window.setInterval(() => { load().catch(() => undefined); }, 15000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [key, filters]);

  return (
    <section>
      <h1>Analytics</h1>
      <div className="toolbar">
        {reports.map((item) => (
          <button key={item.key} className={item.key === key ? "primary" : "secondary"} onClick={() => { setKey(item.key); setFilters({}); }}>
            {item.title}
          </button>
        ))}
      </div>
      {report && <p>{report.description} Last loaded {loadedAt || "…"}.</p>}
      {report && (
        <div className="toolbar">
          {report.filters.map((filter) => (
            <label key={filter.key} className="filter">
              {filter.label}
              <input
                type={filter.kind === "date" ? "date" : "search"}
                value={filters[filter.key] || ""}
                onChange={(event) => setFilters((current) => ({ ...current, [filter.key]: event.target.value }))}
              />
            </label>
          ))}
        </div>
      )}
      {report && (
        <table>
          <thead>
            <tr>{report.columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {report.columns.map((column) => <td key={column.key}>{row[column.key] ?? ""}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function isoDaysAgo(days: number) {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}
