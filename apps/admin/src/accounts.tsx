import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import { errorMessage, roleLabel } from "./format";
import { Dialog, Empty, ErrorBanner, PageHead, Pill } from "./ui";

type CatalogReport = { key: string; title: string; description: string; category: string };
type Account = { id: string; email: string; role: string; disabled: boolean; reports: string[] };

export function AccountsPage({ currentEmail }: { currentEmail: string }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [catalog, setCatalog] = useState<CatalogReport[]>([]);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState<Account | null>(null);
  const [granting, setGranting] = useState<Account | null>(null);

  async function load() {
    const body = await api<{ data: Account[]; reports: CatalogReport[] }>("/admin/api/users");
    setAccounts(body.data);
    setCatalog(body.reports);
  }

  useEffect(() => {
    load().catch((err) => setError(errorMessage(err, "Could not load accounts")));
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/admin/api/users", { method: "POST", body: JSON.stringify({ email, password, role }) });
      setEmail("");
      setPassword("");
      setRole("user");
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not create the account"));
    } finally {
      setBusy(false);
    }
  }

  async function update(account: Account, patch: Record<string, string | boolean | string[]>) {
    setError("");
    try {
      await api(`/admin/api/users/${account.id}`, { method: "PATCH", body: JSON.stringify(patch) });
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not update the account"));
      throw err;
    }
  }

  return (
    <section className="stack">
      <PageHead title="Accounts" lede="Admins see the full console. Users see only the reports assigned here." />
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <form className="panel form-grid" onSubmit={create}>
        <h2>New account</h2>
        <div className="quota-grid">
          <label className="field">
            Email
            <input type="email" autoComplete="off" value={email} onChange={(event) => setEmail(event.target.value)} required />
          </label>
          <label className="field">
            Password
            <input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} required />
          </label>
          <label className="field">
            Role
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              <option value="user">User — analytics only</option>
              <option value="admin">Admin — full console</option>
            </select>
          </label>
        </div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </button>
      </form>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Email</th>
              <th>Role</th>
              <th>Reports</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {accounts === null ? (
              <tr>
                <td colSpan={5} className="empty">
                  Loading accounts…
                </td>
              </tr>
            ) : accounts.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <Empty>No accounts yet.</Empty>
                </td>
              </tr>
            ) : (
              accounts.map((account) => {
                const self = account.email === currentEmail;
                return (
                  <tr key={account.id}>
                    <td>{account.email}</td>
                    <td>
                      <select
                        value={account.role === "platform_admin" ? "admin" : account.role}
                        disabled={self}
                        onChange={(event) => update(account, { role: event.target.value })}
                      >
                        <option value="admin">Admin</option>
                        <option value="user">User</option>
                      </select>
                    </td>
                    <td>
                      {account.role === "admin" ? (
                        "All reports"
                      ) : account.reports.length === 0 ? (
                        "None"
                      ) : (
                        <span className="chips">
                          {account.reports.map((key) => (
                            <span key={key} className="chip">
                              {catalog.find((item) => item.key === key)?.title ?? key}
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                    <td>{account.disabled ? <Pill tone="failed">Disabled</Pill> : <Pill tone="ok">Active</Pill>}</td>
                    <td className="actions">
                      {account.role === "user" ? (
                        <button type="button" className="ghost" onClick={() => setGranting(account)}>
                          Choose reports
                        </button>
                      ) : null}
                      <button type="button" className="ghost" disabled={self} onClick={() => update(account, { disabled: !account.disabled })}>
                        {account.disabled ? "Enable" : "Disable"}
                      </button>
                      <button type="button" className="ghost" onClick={() => setResetting(account)}>
                        Set password
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {granting ? (
        <ReportDialog
          email={granting.email}
          catalog={catalog}
          selected={granting.reports}
          onClose={() => setGranting(null)}
          onSave={async (reports) => {
            await update(granting, { reports });
            setGranting(null);
          }}
        />
      ) : null}
      {resetting ? (
        <PasswordDialog
          email={resetting.email}
          onClose={() => setResetting(null)}
          onSave={async (next) => {
            await update(resetting, { password: next });
            setResetting(null);
          }}
        />
      ) : null}
      <p className="hint">Role labels: {roleLabel("platform_admin")} sees every page and every report. {roleLabel("user")} sees Insights only, and only the reports chosen here.</p>
    </section>
  );
}

function ReportDialog({
  email,
  catalog,
  selected,
  onClose,
  onSave,
}: {
  email: string;
  catalog: CatalogReport[];
  selected: string[];
  onClose: () => void;
  onSave: (reports: string[]) => Promise<void>;
}) {
  const [chosen, setChosen] = useState<string[]>(selected);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const categories = [...new Set(catalog.map((item) => item.category))];

  function toggle(key: string) {
    setChosen((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave(chosen);
    } catch (err) {
      setError(errorMessage(err, "Could not save the reports"));
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Reports for ${email}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="hint">This user sees a card only for the reports checked here.</p>
        {categories.map((category) => (
          <fieldset key={category} className="api-picker">
            <legend>{category}</legend>
            {catalog
              .filter((item) => item.category === category)
              .map((item) => (
                <label key={item.key} className="check">
                  <input type="checkbox" checked={chosen.includes(item.key)} onChange={() => toggle(item.key)} />
                  <span>
                    {item.title}
                    <small>{item.description}</small>
                  </span>
                </label>
              ))}
          </fieldset>
        ))}
        <ErrorBanner message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save reports"}
        </button>
      </form>
    </Dialog>
  );
}

function PasswordDialog({ email, onClose, onSave }: { email: string; onClose: () => void; onSave: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave(password);
    } catch (err) {
      setError(errorMessage(err, "Could not set the password"));
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Password for ${email}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          New password
          <input type="password" autoComplete="new-password" minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} required />
        </label>
        <ErrorBanner message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save password"}
        </button>
      </form>
    </Dialog>
  );
}
