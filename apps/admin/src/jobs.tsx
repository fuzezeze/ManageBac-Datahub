import { useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import {
  datasetLabel,
  duration,
  errorMessage,
  formatCounts,
  isoDate,
  jobTone,
  pad,
  titleCaseToken,
  triggerLabel,
} from "./format";
import { Dialog, Empty, ErrorBanner, PageHead, Pill, Time } from "./ui";

type Job = {
  id: string;
  dataset: string;
  status: string;
  trigger: string;
  scope: Record<string, string>;
  requested_by: string | null;
  error_summary: string | null;
  counts: Record<string, number>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type Schedule = {
  dataset: string;
  blocked: boolean;
  supports_dates: boolean;
  cadence: string | null;
  hour: number | null;
  minute: number;
  interval_minutes: number | null;
  enabled: boolean;
};

type JobDetail = {
  job: Job;
  attempts: Array<{
    attempt_number: number;
    status: string;
    started_at: string;
    finished_at: string | null;
    error_code: string | null;
    error_summary: string | null;
    counts: Record<string, number>;
  }>;
  quality_issues: Array<{ code: string; message: string; mb_id: string | null }>;
};

function live(status: string): boolean {
  return status === "queued" || status === "running" || status === "retry_wait";
}

function cadenceLabel(row: Schedule): string {
  if (row.cadence === "daily") return `Daily at ${pad(row.hour)}:${pad(row.minute)}`;
  if (row.cadence === "hourly") return `Hourly at :${pad(row.minute)}`;
  if (row.cadence === "interval") return `Every ${row.interval_minutes ?? 0} min`;
  return titleCaseToken(row.cadence);
}

function scopeLabel(scope: Record<string, string> | null | undefined): string {
  if (!scope) return "—";
  const parts = [scope.mode ? titleCaseToken(scope.mode) : "", scope.date_from && scope.date_to ? `${scope.date_from} to ${scope.date_to}` : ""].filter(Boolean);
  return parts.join(" · ") || "—";
}

export function JobsPage({ zone }: { zone: string }) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<JobDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [queueOpen, setQueueOpen] = useState(false);
  const [editing, setEditing] = useState<Schedule | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    api<{ data: Job[] }>("/admin/api/jobs")
      .then((body) => {
        if (cancelled) return;
        setJobs(body.data);
        if (body.data.some((job) => live(job.status))) timer = window.setTimeout(() => setTick((value) => value + 1), 3000);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err, "Could not load sync jobs"));
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [tick]);

  useEffect(() => {
    let cancelled = false;
    api<{ data: Schedule[] }>("/admin/api/schedules")
      .then((body) => {
        if (!cancelled) setSchedules(body.data);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err, "Could not load schedules"));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setDetail(null);
    setDetailError("");
  }, [selected]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const id = selected;
    api<JobDetail>(`/admin/api/jobs/${id}`)
      .then((body) => {
        if (!cancelled) setDetail(body);
      })
      .catch((err) => {
        if (!cancelled) setDetailError(errorMessage(err, "Could not load this run"));
      });
    return () => {
      cancelled = true;
    };
  }, [selected, tick]);

  async function saveSchedule(next: Schedule) {
    const saved = await api<Schedule>(`/admin/api/schedules/${next.dataset}`, {
      method: "PATCH",
      body: JSON.stringify({
        cadence: next.cadence,
        hour: next.hour,
        minute: next.minute,
        interval_minutes: next.interval_minutes,
        enabled: next.enabled,
      }),
    });
    setSchedules((current) => current?.map((row) => (row.dataset === next.dataset ? { ...row, ...saved } : row)) ?? current);
  }

  const runnable = (schedules ?? []).filter((row) => !row.blocked);

  return (
    <section className="stack">
      <PageHead title="Jobs">
        <button type="button" className="primary" onClick={() => setQueueOpen(true)} disabled={runnable.length === 0}>
          Queue sync
        </button>
      </PageHead>
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      {notice ? <div className="banner ok">{notice}</div> : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Status</th>
              <th>Trigger</th>
              <th>Requested by</th>
              <th>Started</th>
              <th>Duration</th>
              <th>Counts</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs === null ? (
              <tr>
                <td colSpan={8} className="empty">
                  Loading sync jobs…
                </td>
              </tr>
            ) : jobs.length === 0 ? (
              <tr>
                <td colSpan={8}>
                  <Empty>No syncs yet. Queue one above.</Empty>
                </td>
              </tr>
            ) : (
              jobs.map((job) => {
                const tone = jobTone(job.status);
                return (
                  <tr
                    key={job.id}
                    className={selected === job.id ? "clickable selected" : "clickable"}
                    tabIndex={0}
                    onClick={() => setSelected(job.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelected(job.id);
                      }
                    }}
                  >
                    <td>{datasetLabel(job.dataset)}</td>
                    <td>
                      <Pill tone={tone.tone}>{tone.label}</Pill>
                    </td>
                    <td>{triggerLabel(job.trigger)}</td>
                    <td>{job.requested_by || "Scheduler"}</td>
                    <td>
                      <Time iso={job.started_at || job.created_at} zone={zone} />
                    </td>
                    <td>{job.started_at ? duration(job.started_at, job.finished_at) : "—"}</td>
                    <td>{formatCounts(job.counts)}</td>
                    <td className="clip" title={job.error_summary || ""}>
                      {job.error_summary || "—"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="panel">
        <h2>Schedule</h2>
        <div className="table-wrap flat">
          <table>
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Cadence</th>
                <th>Enabled</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {schedules === null ? (
                <tr>
                  <td colSpan={4} className="empty">
                    Loading schedules…
                  </td>
                </tr>
              ) : (
                schedules.map((row) => (
                  <tr key={row.dataset}>
                    <td>{datasetLabel(row.dataset)}</td>
                    <td>{cadenceLabel(row)}</td>
                    <td>
                      <button
                        type="button"
                        className={row.enabled ? "toggle on" : "toggle"}
                        aria-pressed={row.enabled}
                        onClick={() =>
                          saveSchedule({ ...row, enabled: !row.enabled }).catch((err) => setError(errorMessage(err, "Could not update the schedule")))
                        }
                      >
                        {row.enabled ? "On" : "Off"}
                      </button>
                    </td>
                    <td>
                      <button type="button" className="ghost" onClick={() => setEditing(row)}>
                        Edit
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {queueOpen && schedules ? (
        <QueueDialog
          datasets={runnable}
          onClose={() => setQueueOpen(false)}
          onQueued={(label) => {
            setNotice(`Queued ${label}.`);
            setQueueOpen(false);
            setTick((value) => value + 1);
          }}
        />
      ) : null}
      {editing ? (
        <ScheduleDialog
          schedule={editing}
          onClose={() => setEditing(null)}
          onSave={async (next) => {
            await saveSchedule(next);
            setEditing(null);
          }}
        />
      ) : null}
      {selected ? (
        <Dialog title={detail ? datasetLabel(detail.job.dataset) : "Sync run"} wide onClose={() => setSelected(null)}>
          {detailError ? <ErrorBanner message={detailError} /> : null}
          {!detail && !detailError ? <p className="hint">Loading this run…</p> : null}
          {detail ? (
            <div className="stack">
              <p className="hint">
                {triggerLabel(detail.job.trigger)} · {scopeLabel(detail.job.scope)} · {detail.job.requested_by || "Scheduler"}
              </p>
              <h3>Attempts</h3>
              {detail.attempts.length === 0 ? (
                <Empty>No attempts yet.</Empty>
              ) : (
                <div className="table-wrap flat">
                  <table>
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Status</th>
                        <th>Started</th>
                        <th>Duration</th>
                        <th>Counts</th>
                        <th>Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.attempts.map((attempt) => {
                        const tone = jobTone(attempt.status);
                        return (
                          <tr key={attempt.attempt_number}>
                            <td>{attempt.attempt_number}</td>
                            <td>
                              <Pill tone={tone.tone}>{tone.label}</Pill>
                            </td>
                            <td>
                              <Time iso={attempt.started_at} zone={zone} />
                            </td>
                            <td>{duration(attempt.started_at, attempt.finished_at)}</td>
                            <td>{formatCounts(attempt.counts)}</td>
                            <td>{attempt.error_summary || attempt.error_code || "—"}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <h3>Quality issues</h3>
              {detail.quality_issues.length === 0 ? (
                <Empty>No quality issues.</Empty>
              ) : (
                <ul className="issue-list">
                  {detail.quality_issues.map((issue, index) => (
                    <li key={`${issue.code}-${issue.mb_id}-${index}`}>
                      <strong>{issue.code}</strong>
                      <span>{issue.message}</span>
                      {issue.mb_id ? <code>{issue.mb_id}</code> : null}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </Dialog>
      ) : null}
    </section>
  );
}

function QueueDialog({
  datasets,
  onClose,
  onQueued,
}: {
  datasets: Schedule[];
  onClose: () => void;
  onQueued: (label: string) => void;
}) {
  const [dataset, setDataset] = useState(datasets.find((row) => row.dataset === "students")?.dataset || datasets[0]?.dataset || "");
  const [dateFrom, setDateFrom] = useState(isoDate(-7));
  const [dateTo, setDateTo] = useState(isoDate(0));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const current = datasets.find((row) => row.dataset === dataset);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!current) return;
    if (current.supports_dates && dateTo < dateFrom) {
      setError("The end date is before the start date.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body: Record<string, string> = { dataset, idempotency_key: crypto.randomUUID(), mode: "full" };
      if (current.supports_dates) {
        body.date_from = dateFrom;
        body.date_to = dateTo;
      }
      await api("/admin/api/jobs", { method: "POST", body: JSON.stringify(body) });
      onQueued(datasetLabel(dataset));
    } catch (err) {
      setError(errorMessage(err, "Could not queue the sync"));
      setBusy(false);
    }
  }

  return (
    <Dialog title="Queue sync" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          Dataset
          <select value={dataset} onChange={(event) => setDataset(event.target.value)}>
            {datasets.map((row) => (
              <option key={row.dataset} value={row.dataset}>
                {datasetLabel(row.dataset)}
              </option>
            ))}
          </select>
        </label>
        {current?.supports_dates ? (
          <div className="quota-grid">
            <label className="field">
              From
              <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} required />
            </label>
            <label className="field">
              To
              <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} required />
            </label>
          </div>
        ) : null}
        <ErrorBanner message={error} />
        <button type="submit" className="primary" disabled={busy || !dataset}>
          {busy ? "Queuing…" : "Queue sync"}
        </button>
      </form>
    </Dialog>
  );
}

function ScheduleDialog({
  schedule,
  onClose,
  onSave,
}: {
  schedule: Schedule;
  onClose: () => void;
  onSave: (next: Schedule) => Promise<void>;
}) {
  const [cadence, setCadence] = useState(schedule.cadence || "daily");
  const [hour, setHour] = useState(schedule.hour ?? 2);
  const [minute, setMinute] = useState(schedule.minute ?? 0);
  const [interval, setInterval] = useState(schedule.interval_minutes ?? 15);
  const [enabled, setEnabled] = useState(schedule.enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSave({
        ...schedule,
        cadence,
        hour: cadence === "daily" ? hour : null,
        minute,
        interval_minutes: cadence === "interval" ? interval : null,
        enabled,
      });
    } catch (err) {
      setError(errorMessage(err, "Could not update the schedule"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title={`Schedule ${datasetLabel(schedule.dataset)}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          Cadence
          <select value={cadence} onChange={(event) => setCadence(event.target.value)}>
            <option value="daily">Daily</option>
            <option value="hourly">Hourly</option>
            <option value="interval">Interval</option>
          </select>
        </label>
        {cadence === "daily" ? (
          <label className="field">
            Hour
            <input type="number" min={0} max={23} value={hour} onChange={(event) => setHour(Number(event.target.value))} />
          </label>
        ) : null}
        {cadence !== "interval" ? (
          <label className="field">
            Minute
            <input type="number" min={0} max={59} value={minute} onChange={(event) => setMinute(Number(event.target.value))} />
          </label>
        ) : (
          <label className="field">
            Every minutes
            <input type="number" min={5} max={1440} value={interval} onChange={(event) => setInterval(Number(event.target.value))} />
          </label>
        )}
        <label className="check">
          <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
          Enabled
        </label>
        <ErrorBanner message={error} />
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : "Save schedule"}
        </button>
      </form>
    </Dialog>
  );
}
