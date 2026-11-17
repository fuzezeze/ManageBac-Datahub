import { useEffect, useState } from "react";
import { api } from "./api";
import { absoluteTime, datasetLabel, datasetTone, errorMessage, formatNumber, relativeTime } from "./format";
import { Empty, ErrorBanner, PageHead, Pill, Time } from "./ui";

type Overview = {
  school_timezone: string;
  school_timezone_confirmed: boolean;
  retention_configured: boolean;
  connection: { ok: boolean; checked_at: string; detail: string | null } | null;
  datasets: Array<{
    dataset: string;
    status: string | null;
    blocked: boolean;
    record_count: number | null;
    error_summary: string | null;
    last_success_at: string | null;
    last_attempt_at: string | null;
    last_attempt_status: string | null;
  }>;
};

type Alert = {
  id: string;
  kind: string;
  message: string;
  status: string;
  created_at: string;
};

export function OverviewPage({ zone, onZone }: { zone: string; onZone: (zone: string) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [error, setError] = useState("");
  const [alertError, setAlertError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  async function load() {
    setRefreshing(true);
    setError("");
    setAlertError("");
    const overviewPromise = api<Overview>("/admin/api/overview");
    const alertsPromise = api<{ data: Alert[] }>("/admin/api/alerts");
    try {
      const overview = await overviewPromise;
      setData(overview);
      onZone(overview.school_timezone || "Asia/Hong_Kong");
    } catch (err) {
      setError(errorMessage(err, "Could not load the overview"));
    }
    try {
      setAlerts((await alertsPromise).data);
    } catch (err) {
      setAlertError(errorMessage(err, "Could not load alerts"));
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  async function ack(id: string) {
    setAlertError("");
    try {
      await api(`/admin/api/alerts/${id}/ack`, { method: "POST" });
      setAlerts((current) => current?.map((alert) => (alert.id === id ? { ...alert, status: "acked" } : alert)) ?? null);
    } catch (err) {
      setAlertError(errorMessage(err, "Could not acknowledge the alert"));
    }
  }

  const timeZone = data?.school_timezone || zone || "Asia/Hong_Kong";
  const openAlerts = (alerts ?? []).filter((alert) => alert.status === "open");
  const tones = (data?.datasets ?? []).map((row) => datasetTone(row));
  const healthy = tones.filter((tone) => tone.tone === "ok").length;
  const blocked = tones.filter((tone) => tone.tone === "blocked").length;
  const attention = (data?.datasets.length ?? 0) - healthy - blocked;
  const latest = (data?.datasets ?? []).reduce<{ at: string; dataset: string } | null>((best, row) => {
    if (!row.last_success_at) return best;
    if (!best || row.last_success_at > best.at) return { at: row.last_success_at, dataset: row.dataset };
    return best;
  }, null);

  return (
    <section className="stack">
      <PageHead title="Overview">
        <button type="button" className="secondary" onClick={() => load()} disabled={refreshing}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </PageHead>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      {!data ? (
        <p className="hint" role="status">
          Loading overview…
        </p>
      ) : (
        <>
          <div className="stat-grid">
            <article className={`stat ${data.connection ? (data.connection.ok ? "good" : "bad") : ""}`}>
              <span className="label">ManageBac</span>
              <strong>{data.connection ? (data.connection.ok ? "Reachable" : "Unreachable") : "No check yet"}</strong>
              <p>
                {data.connection
                  ? `${relativeTime(data.connection.checked_at)} · ${absoluteTime(data.connection.checked_at, timeZone)}`
                  : "The connection check has not run."}
              </p>
              {data.connection?.detail ? <p className="clamp">{data.connection.detail}</p> : null}
            </article>
            <article className="stat">
              <span className="label">Datasets</span>
              <strong>
                {healthy} healthy
              </strong>
              <p>
                {data.datasets.length} datasets · {blocked} blocked
                {attention ? ` · ${attention} need attention` : ""}
              </p>
            </article>
            <article className={`stat ${openAlerts.length ? "bad" : "good"}`}>
              <span className="label">Alerts</span>
              <strong>{alerts ? (openAlerts.length ? `${openAlerts.length} open` : "None open") : "—"}</strong>
              {alertError ? <p>{alertError}</p> : null}
            </article>
            <article className="stat">
              <span className="label">Last success</span>
              <strong>{latest ? relativeTime(latest.at) : "None yet"}</strong>
              <p>{latest ? `${datasetLabel(latest.dataset)} · ${absoluteTime(latest.at, timeZone)}` : "No dataset has synced successfully."}</p>
            </article>
          </div>
          {openAlerts.length > 0 && (
            <div className="panel">
              <h2>Open alerts</h2>
              <ul className="alert-list">
                {openAlerts.map((alert) => (
                  <li key={alert.id}>
                    <div>
                      <strong>{alert.message}</strong>
                      <p>
                        {alert.kind} · <Time iso={alert.created_at} zone={timeZone} />
                      </p>
                    </div>
                    <button type="button" className="secondary" onClick={() => ack(alert.id)}>
                      Acknowledge
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Status</th>
                  <th>Records</th>
                  <th>Last success</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {data.datasets.map((row) => {
                  const tone = datasetTone(row);
                  return (
                    <tr key={row.dataset}>
                      <td>{datasetLabel(row.dataset)}</td>
                      <td>
                        <Pill tone={tone.tone}>{tone.label}</Pill>
                      </td>
                      <td>{formatNumber(row.record_count)}</td>
                      <td>
                        <Time iso={row.last_success_at} zone={timeZone} />
                      </td>
                      <td className="clip" title={row.error_summary || ""}>
                        {row.error_summary || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {data && data.datasets.length === 0 ? <Empty>No datasets are configured.</Empty> : null}
    </section>
  );
}
