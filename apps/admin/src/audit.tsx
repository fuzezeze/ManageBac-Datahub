import { useEffect, useState } from "react";
import { api } from "./api";
import { errorMessage, formatNumber, httpTone } from "./format";
import { Empty, ErrorBanner, PageHead, Pill, Time } from "./ui";

type Client = { id: string; name: string };

type Entry = {
  request_id: string;
  client_id: string | null;
  requested_at: string;
  http_method: string;
  route_template: string;
  http_status: number;
  error_code: string | null;
  returned_record_count: number;
  response_bytes: number;
  authenticated: boolean;
};

export function AuditPage({ zone }: { zone: string }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState("");
  const [rows, setRows] = useState<Entry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<{ data: Client[] }>("/admin/api/clients")
      .then((body) => setClients(body.data))
      .catch(() => setClients([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const query = clientId ? `?client_id=${encodeURIComponent(clientId)}` : "";
    api<{ data: Entry[] }>(`/admin/api/audit${query}`)
      .then((body) => {
        if (!cancelled) setRows(body.data);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err, "Could not load the request log"));
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  const names = new Map(clients.map((client) => [client.id, client.name]));

  return (
    <section className="stack">
      <PageHead title="Audit">
        <label className="field inline">
          Client
          <select value={clientId} onChange={(event) => setClientId(event.target.value)}>
            <option value="">All clients</option>
            {clients.map((client) => (
              <option key={client.id} value={client.id}>
                {client.name}
              </option>
            ))}
          </select>
        </label>
      </PageHead>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Client</th>
              <th>Request</th>
              <th>Status</th>
              <th>Records</th>
              <th>Bytes</th>
              <th>Auth</th>
            </tr>
          </thead>
          <tbody>
            {rows === null ? (
              <tr>
                <td colSpan={7} className="empty">
                  Loading the request log…
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={7}>
                  <Empty>No API requests yet.</Empty>
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.request_id}>
                  <td>
                    <Time iso={row.requested_at} zone={zone} />
                  </td>
                  <td>{row.client_id ? names.get(row.client_id) || row.client_id : "—"}</td>
                  <td>
                    <code>
                      {row.http_method} {row.route_template}
                    </code>
                  </td>
                  <td>
                    <Pill tone={httpTone(row.http_status)}>{row.error_code || row.http_status}</Pill>
                  </td>
                  <td>{formatNumber(row.returned_record_count)}</td>
                  <td>{formatNumber(row.response_bytes)}</td>
                  <td>{row.authenticated ? "Signed in" : "Rejected"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
