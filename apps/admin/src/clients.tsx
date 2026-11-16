import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import { errorMessage, formatNumber, positiveInt } from "./format";
import { Dialog, Empty, ErrorBanner, PageHead, Pill, When } from "./ui";

type ReadApi = { name: string; title: string; route: string; scope: string; fields: string[] };

type Credential = { id: string; public_id: string; expires_at: string; revoked_at: string | null };

type Client = {
  id: string;
  name: string;
  enabled: boolean;
  requests_per_minute: number;
  records_per_day: number;
  bytes_per_day: number;
  scopes: string[];
  fields: Record<string, string[]>;
  credentials: Credential[];
};

type Catalog = { data: Client[]; available_scopes: string[]; apis: ReadApi[] };

type Draft = {
  name: string;
  selected: Record<string, boolean>;
  limited: Record<string, boolean>;
  chosen: Record<string, string[]>;
  rpm: string;
  records: string;
  bytes: string;
  days: string;
};

function blankDraft(): Draft {
  return { name: "", selected: {}, limited: {}, chosen: {}, rpm: "60", records: "5000", bytes: "5000000", days: "180" };
}

function draftFrom(client: Client, apis: ReadApi[]): Draft {
  const selected: Record<string, boolean> = {};
  const limited: Record<string, boolean> = {};
  const chosen: Record<string, string[]> = {};
  for (const item of apis) {
    if (client.scopes.includes(item.scope)) selected[item.name] = true;
    const granted = client.fields[item.name] ?? [];
    if (granted.length) {
      selected[item.name] = true;
      limited[item.name] = true;
      chosen[item.name] = granted;
    }
  }
  return {
    name: client.name,
    selected,
    limited,
    chosen,
    rpm: String(client.requests_per_minute),
    records: String(client.records_per_day),
    bytes: String(client.bytes_per_day),
    days: "180",
  };
}

function effectiveFields(api: ReadApi, draft: Draft): string[] {
  if (!draft.selected[api.name]) return [];
  if (!draft.limited[api.name]) return api.fields;
  return draft.chosen[api.name] ?? [];
}

function payloadFrom(draft: Draft, apis: ReadApi[]) {
  const scopes = new Set<string>();
  const fields: Record<string, string[]> = {};
  for (const item of apis) {
    if (!draft.selected[item.name]) continue;
    const chosen = effectiveFields(item, draft);
    if (draft.limited[item.name] && chosen.length === 0) {
      throw new Error(`Choose at least one field for ${item.title}, or turn the limit off.`);
    }
    scopes.add(item.scope);
    if (draft.limited[item.name]) fields[item.name] = chosen;
    if ((item.name === "students" || item.name === "teachers") && chosen.includes("email")) {
      scopes.add(`${item.name}:contact`);
    }
  }
  if (!draft.name.trim()) throw new Error("Client name is required.");
  if (scopes.size === 0) throw new Error("Choose at least one API.");
  const rpm = positiveInt(draft.rpm);
  const records = positiveInt(draft.records);
  const bytes = positiveInt(draft.bytes);
  const days = positiveInt(draft.days);
  if (!rpm || !records || !bytes || !days) throw new Error("Quotas and key lifetime must be positive whole numbers.");
  return {
    name: draft.name.trim(),
    scopes: [...scopes],
    fields,
    requests_per_minute: rpm,
    records_per_day: records,
    bytes_per_day: bytes,
    credential_days: days,
  };
}

export function ClientsPage({ zone }: { zone: string }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Client | "new" | null>(null);
  const [secret, setSecret] = useState<{ name: string; secret: string } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Client | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<Credential | null>(null);

  async function load() {
    const body = await api<Catalog>("/admin/api/clients");
    setCatalog({
      ...body,
      apis: body.apis ?? [],
      data: body.data.map((client) => ({
        ...client,
        scopes: client.scopes ?? [],
        fields: client.fields ?? {},
        credentials: client.credentials ?? [],
      })),
    });
  }

  useEffect(() => {
    load().catch((err) => setError(errorMessage(err, "Could not load API clients")));
  }, []);

  async function toggle(client: Client) {
    setError("");
    const enabled = !client.enabled;
    setCatalog((current) =>
      current ? { ...current, data: current.data.map((item) => (item.id === client.id ? { ...item, enabled } : item)) } : current,
    );
    try {
      await api(`/admin/api/clients/${client.id}/enabled`, { method: "POST", body: JSON.stringify({ enabled }) });
    } catch (err) {
      setError(errorMessage(err, "Could not update the client"));
      await load().catch(() => undefined);
    }
  }

  async function rotate(client: Client) {
    setError("");
    try {
      const body = await api<{ credential: { secret: string } }>(`/admin/api/clients/${client.id}/credentials`, {
        method: "POST",
        body: JSON.stringify({ credential_days: 180 }),
      });
      setSecret({ name: client.name, secret: body.credential.secret });
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not rotate the key"));
    }
  }

  async function revoke(credential: Credential) {
    setError("");
    try {
      await api(`/admin/api/credentials/${credential.id}/revoke`, { method: "POST" });
      setPendingRevoke(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not revoke the key"));
    }
  }

  async function remove(client: Client) {
    setError("");
    try {
      await api(`/admin/api/clients/${client.id}`, { method: "DELETE" });
      setPendingDelete(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not delete the client"));
    }
  }

  const apis = catalog?.apis ?? [];

  return (
    <section className="stack">
      <PageHead title="API clients" lede="Issue read keys, choose which fields a school system can see, and set quotas.">
        <button type="button" className="primary" onClick={() => setEditing("new")} disabled={!catalog}>
          Create a read client
        </button>
      </PageHead>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      {!catalog ? (
        <p className="hint">Loading clients…</p>
      ) : catalog.data.length === 0 ? (
        <Empty>No API clients yet. Create one to issue a read key.</Empty>
      ) : (
        <div className="client-list">
          {catalog.data.map((client) => (
            <article key={client.id} className="client-card">
              <header>
                <div>
                  <h2>{client.name}</h2>
                  <p className="hint">
                    {formatNumber(client.requests_per_minute)} requests/min · {formatNumber(client.records_per_day)} records/day ·{" "}
                    {formatNumber(client.bytes_per_day)} bytes/day
                  </p>
                </div>
                <button type="button" className={client.enabled ? "toggle on" : "toggle"} aria-pressed={client.enabled} onClick={() => toggle(client)}>
                  {client.enabled ? "Enabled" : "Disabled"}
                </button>
              </header>
              <div className="chips">
                {client.scopes.map((scope) => (
                  <span key={scope} className="chip">
                    {scope}
                  </span>
                ))}
              </div>
              {Object.keys(client.fields).length > 0 ? (
                <ul className="field-grants">
                  {Object.entries(client.fields).map(([resource, names]) => (
                    <li key={resource}>
                      <strong>{apis.find((item) => item.name === resource)?.title || resource}</strong>
                      <span>{names.join(", ")}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="hint">All fields are returned for the granted APIs.</p>
              )}
              <div className="cred-list">
                {client.credentials.map((credential) => (
                  <div key={credential.id} className="cred">
                    <code>{credential.public_id}</code>
                    {credential.revoked_at ? <Pill tone="failed">Revoked</Pill> : <Pill tone="ok">Active</Pill>}
                    <span className="hint">
                      Expires <When iso={credential.expires_at} zone={zone} />
                    </span>
                    {!credential.revoked_at ? (
                      <button type="button" className="ghost" onClick={() => setPendingRevoke(credential)}>
                        Revoke
                      </button>
                    ) : null}
                  </div>
                ))}
              </div>
              <div className="page-actions">
                <button type="button" className="secondary" onClick={() => setEditing(client)}>
                  Edit
                </button>
                <button type="button" className="secondary" onClick={() => rotate(client)}>
                  Rotate key
                </button>
                <button type="button" className="danger" onClick={() => setPendingDelete(client)}>
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {editing && catalog ? (
        <ClientDialog
          apis={catalog.apis}
          initial={editing === "new" ? blankDraft() : draftFrom(editing, catalog.apis)}
          mode={editing === "new" ? "create" : "edit"}
          onClose={() => setEditing(null)}
          onSaved={async (issued) => {
            setEditing(null);
            if (issued) setSecret(issued);
            await load();
          }}
          clientId={editing === "new" ? null : editing.id}
        />
      ) : null}
      {secret ? <SecretDialog name={secret.name} secret={secret.secret} onClose={() => setSecret(null)} /> : null}
      {pendingDelete ? (
        <Dialog title={`Delete ${pendingDelete.name}`} onClose={() => setPendingDelete(null)}>
          <p className="hint">Its keys stop working immediately.</p>
          <div className="page-actions">
            <button type="button" className="danger" onClick={() => remove(pendingDelete)}>
              Delete client
            </button>
            <button type="button" className="secondary" onClick={() => setPendingDelete(null)}>
              Cancel
            </button>
          </div>
        </Dialog>
      ) : null}
      {pendingRevoke ? (
        <Dialog title="Revoke this key" onClose={() => setPendingRevoke(null)}>
          <p className="hint">
            <code>{pendingRevoke.public_id}</code> will be rejected on the next request.
          </p>
          <div className="page-actions">
            <button type="button" className="danger" onClick={() => revoke(pendingRevoke)}>
              Revoke key
            </button>
            <button type="button" className="secondary" onClick={() => setPendingRevoke(null)}>
              Cancel
            </button>
          </div>
        </Dialog>
      ) : null}
    </section>
  );
}

function ClientDialog({
  apis,
  initial,
  mode,
  clientId,
  onClose,
  onSaved,
}: {
  apis: ReadApi[];
  initial: Draft;
  mode: "create" | "edit";
  clientId: string | null;
  onClose: () => void;
  onSaved: (secret: { name: string; secret: string } | null) => Promise<void>;
}) {
  const [draft, setDraft] = useState(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function toggleApi(name: string) {
    setDraft((current) => ({ ...current, selected: { ...current.selected, [name]: !current.selected[name] } }));
  }

  function toggleField(api: ReadApi, field: string) {
    setDraft((current) => {
      const existing = current.chosen[api.name] ?? [];
      const next = existing.includes(field) ? existing.filter((item) => item !== field) : [...existing, field];
      return { ...current, chosen: { ...current.chosen, [api.name]: next } };
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = payloadFrom(draft, apis);
      if (mode === "create") {
        const body = await api<{ credential: { secret: string } }>("/admin/api/clients", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        await onSaved({ name: payload.name, secret: body.credential.secret });
      } else if (clientId) {
        await api(`/admin/api/clients/${clientId}`, {
          method: "PATCH",
          body: JSON.stringify({
            name: payload.name,
            scopes: payload.scopes,
            fields: payload.fields,
            requests_per_minute: payload.requests_per_minute,
            records_per_day: payload.records_per_day,
            bytes_per_day: payload.bytes_per_day,
          }),
        });
        await onSaved(null);
      }
    } catch (err) {
      setError(errorMessage(err, "Could not save the client"));
      setBusy(false);
    }
  }

  return (
    <Dialog title={mode === "create" ? "Create a read client" : "Edit client"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          Name
          <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} required />
        </label>
        <fieldset className="api-picker">
          <legend>APIs</legend>
          <p className="hint">Leave a field limit off to return every field. Email is included with the full student or teacher payload.</p>
          {apis.map((item) => (
            <div key={item.name} className="api-block">
              <label className="check">
                <input type="checkbox" checked={Boolean(draft.selected[item.name])} onChange={() => toggleApi(item.name)} />
                <span>
                  {item.title}
                  <small>{item.route}</small>
                </span>
              </label>
              {draft.selected[item.name] ? (
                <div className="limit">
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={Boolean(draft.limited[item.name])}
                      onChange={() => setDraft((current) => ({ ...current, limited: { ...current.limited, [item.name]: !current.limited[item.name] } }))}
                    />
                    Limit fields
                  </label>
                  {draft.limited[item.name] ? (
                    <div className="checks">
                      {item.fields.map((field) => (
                        <label key={field} className="check">
                          <input
                            type="checkbox"
                            checked={(draft.chosen[item.name] ?? []).includes(field)}
                            onChange={() => toggleField(item, field)}
                          />
                          {field}
                        </label>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ))}
        </fieldset>
        <div className="quota-grid">
          <label className="field">
            Requests per minute
            <input value={draft.rpm} onChange={(event) => setDraft({ ...draft, rpm: event.target.value })} inputMode="numeric" />
          </label>
          <label className="field">
            Records per day
            <input value={draft.records} onChange={(event) => setDraft({ ...draft, records: event.target.value })} inputMode="numeric" />
          </label>
          <label className="field">
            Bytes per day
            <input value={draft.bytes} onChange={(event) => setDraft({ ...draft, bytes: event.target.value })} inputMode="numeric" />
          </label>
          {mode === "create" ? (
            <label className="field">
              Key lifetime (days)
              <input value={draft.days} onChange={(event) => setDraft({ ...draft, days: event.target.value })} inputMode="numeric" />
            </label>
          ) : null}
        </div>
        <ErrorBanner message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : mode === "create" ? "Create client" : "Save client"}
        </button>
      </form>
    </Dialog>
  );
}

function SecretDialog({ name, secret, onClose }: { name: string; secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [hint, setHint] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
      setHint("");
    } catch {
      setCopied(false);
      setHint("Clipboard is blocked. Select the key and copy it manually.");
    }
  }

  return (
    <Dialog title={`Key for ${name}`}>
      <p className="hint">Copy this key now. It is not shown again.</p>
      <code className="secret">{secret}</code>
      {hint ? <p className="hint">{hint}</p> : null}
      <div className="page-actions">
        <button type="button" className="secondary" onClick={copy}>
          {copied ? "Copied" : "Copy key"}
        </button>
        <button type="button" className="primary" onClick={onClose}>
          I have saved this key
        </button>
      </div>
    </Dialog>
  );
}
