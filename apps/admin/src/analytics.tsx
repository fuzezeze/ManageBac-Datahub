import { useEffect, useState } from "react";
import { api } from "./api";
import { errorMessage, formatNumber, titleCaseToken } from "./format";
import { Empty, ErrorBanner, PageHead } from "./ui";

type Summary = {
  student_count: number;
  attendance: Array<{ business_date: string; attendance_type: string; status: string; record_count: number }>;
  attendance_rate_published: boolean;
};

type Filter = { key: string; label: string; kind: string };
type Column = { key: string; label: string };
type ReportMeta = { key: string; title: string; description: string; filters: Filter[]; columns: Column[] };
type ReportResult = { report: ReportMeta; data: Array<Record<string, string | number | null>>; meta: { returned_count: number } };

type RosterRow = {
  mb_id: string;
  student_id: string | null;
  first_name: string | null;
  last_name: string | null;
  status: string | null;
  year_group_name: string | null;
};

function displayCell(column: string, value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const text = String(value);
  if (column === "attendance_type" || column === "status" || column === "role") return titleCaseToken(text);
  return text;
}

export function AnalyticsPage({ admin }: { admin: boolean }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [reports, setReports] = useState<ReportMeta[]>([]);
  const [reportKey, setReportKey] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(0);
  const [result, setResult] = useState<ReportResult | null>(null);
  const [reportError, setReportError] = useState("");
  const [running, setRunning] = useState(false);
  const [roster, setRoster] = useState<RosterRow[] | null>(null);
  const [rosterError, setRosterError] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    api<Summary>("/admin/api/analytics/summary")
      .then(setSummary)
      .catch((err) => setError(errorMessage(err, "Could not load analytics")));
    api<{ reports: ReportMeta[] }>("/admin/api/analytics/reports")
      .then((body) => {
        setReports(body.reports);
        if (body.reports[0]) {
          setReportKey(body.reports[0].key);
          setSubmitted(1);
        }
      })
      .catch((err) => setReportError(errorMessage(err, "Could not load reports")));
    if (admin) {
      api<{ data: RosterRow[] }>("/admin/api/analytics/roster")
        .then((body) => setRoster(body.data))
        .catch((err) => setRosterError(errorMessage(err, "Could not load the roster")));
    }
  }, [admin]);

  useEffect(() => {
    if (!reportKey || submitted === 0) return;
    const report = reports.find((item) => item.key === reportKey);
    if (!report) return;
    let cancelled = false;
    const params = new URLSearchParams();
    for (const filter of report.filters) {
      const value = filters[filter.key]?.trim();
      if (value) params.set(filter.key, value);
    }
    const qs = params.toString();
    setRunning(true);
    setReportError("");
    api<ReportResult>(`/admin/api/analytics/reports/${reportKey}${qs ? `?${qs}` : ""}`)
      .then((body) => {
        if (!cancelled) setResult(body);
      })
      .catch((err) => {
        if (!cancelled) {
          setResult(null);
          setReportError(errorMessage(err, "Could not run the report"));
        }
      })
      .finally(() => {
        if (!cancelled) setRunning(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reportKey, submitted, reports]);

  const report = reports.find((item) => item.key === reportKey) ?? null;
  const rosterRows = (roster ?? []).filter((row) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return `${row.first_name ?? ""} ${row.last_name ?? ""} ${row.student_id ?? ""} ${row.year_group_name ?? ""}`.toLowerCase().includes(needle);
  });

  return (
    <section className="stack">
      <PageHead
        title="Analytics"
        lede={admin ? "Roster size, published attendance, and live reports." : "Published attendance and live reports for this school."}
      />
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      {!summary ? (
        <p className="hint" role="status">
          Loading analytics…
        </p>
      ) : (
        <>
          <div className="stat-grid">
            <article className="stat">
              <span className="label">Students</span>
              <strong>{formatNumber(summary.student_count)}</strong>
              <p>A student in several classes is counted once.</p>
            </article>
          </div>
          {summary.attendance_rate_published ? (
            <div className="banner ok">Attendance rate is published.</div>
          ) : (
            <div className="banner info">Attendance rate is not published.</div>
          )}
          <div className="panel">
            <h2>Attendance by day</h2>
            {summary.attendance.length === 0 ? (
              <Empty>No attendance has been published.</Empty>
            ) : (
              <div className="table-wrap flat">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Type</th>
                      <th>Status</th>
                      <th>Records</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.attendance.map((row) => (
                      <tr key={`${row.business_date}-${row.attendance_type}-${row.status}`}>
                        <td>{row.business_date}</td>
                        <td>{titleCaseToken(row.attendance_type)}</td>
                        <td>{titleCaseToken(row.status)}</td>
                        <td>{formatNumber(row.record_count)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      <div className="panel stack">
        <h2>Reports</h2>
        <ErrorBanner message={reportError} onDismiss={() => setReportError("")} />
        {reports.length === 0 ? (
          <p className="hint">Loading reports…</p>
        ) : (
          <>
            <div className="report-tabs" role="tablist">
              {reports.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={item.key === reportKey ? "active" : ""}
                  onClick={() => {
                    setFilters({});
                    setReportKey(item.key);
                    setSubmitted((value) => value + 1);
                  }}
                >
                  {item.title}
                </button>
              ))}
            </div>
            {report ? <p className="hint">{report.description}</p> : null}
            {report ? (
              <form
                className="filters"
                onSubmit={(event) => {
                  event.preventDefault();
                  setSubmitted((value) => value + 1);
                }}
              >
                {report.filters.map((filter) => (
                  <label key={filter.key} className="field">
                    {filter.label}
                    <input
                      type={filter.kind === "date" ? "date" : "text"}
                      value={filters[filter.key] ?? ""}
                      onChange={(event) => setFilters((current) => ({ ...current, [filter.key]: event.target.value }))}
                    />
                  </label>
                ))}
                <button type="submit" className="primary" disabled={running}>
                  {running ? "Running…" : "Run report"}
                </button>
              </form>
            ) : null}
            {result && result.report.key === reportKey ? (
              <>
                {result.meta.returned_count >= 500 ? <p className="hint">Showing the first 500 rows. Narrow the filters to see a shorter list.</p> : null}
                {result.data.length === 0 ? (
                  <Empty>No rows match these filters.</Empty>
                ) : (
                  <div className="table-wrap flat">
                    <table>
                      <thead>
                        <tr>
                          {result.report.columns.map((column) => (
                            <th key={column.key}>{column.label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {result.data.map((row, index) => (
                          <tr key={index}>
                            {result.report.columns.map((column) => (
                              <td key={column.key}>{displayCell(column.key, row[column.key])}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : running ? (
              <p className="hint">Running the report…</p>
            ) : null}
          </>
        )}
      </div>

      {admin ? (
        <div className="panel stack">
          <h2>Roster</h2>
          <p className="hint">Names stay on this page. The list shows up to 200 students.</p>
          <ErrorBanner message={rosterError} />
          <label className="field">
            Search
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name, student ID, or year group" />
          </label>
          {roster === null && !rosterError ? (
            <p className="hint">Loading the roster…</p>
          ) : roster && rosterRows.length === 0 ? (
            <Empty>{roster.length === 0 ? "No students are in the roster yet." : "No students match that search."}</Empty>
          ) : roster ? (
            <div className="table-wrap flat">
              <table>
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Student ID</th>
                    <th>Year group</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rosterRows.map((row) => (
                    <tr key={row.mb_id}>
                      <td>{`${row.first_name ?? ""} ${row.last_name ?? ""}`.trim() || "—"}</td>
                      <td>{row.student_id || "—"}</td>
                      <td>{row.year_group_name || "—"}</td>
                      <td>{titleCaseToken(row.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
