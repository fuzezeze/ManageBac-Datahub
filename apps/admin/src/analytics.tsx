import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api } from "./api";
import { datasetLabel, errorMessage, relativeTime, titleCaseToken } from "./format";
import { Empty, ErrorBanner, PageHead } from "./ui";

type Filter = { key: string; label: string; kind: string };
type Column = { key: string; label: string };
type Freshness = { as_of: string | null; datasets: Array<{ dataset: string; last_success_at: string | null }> };
type ReportMeta = {
  key: string;
  title: string;
  description: string;
  category: string;
  filters: Filter[];
  columns: Column[];
  freshness?: Freshness;
};
type ReportResult = { report: ReportMeta; data: Array<Record<string, string | number | null>>; meta: { returned_count: number } };

function displayCell(column: string, value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const text = String(value);
  if (column === "attendance_type" || column === "status" || column === "role") return titleCaseToken(text);
  return text;
}

function freshnessLine(report: ReportMeta): string {
  const freshness = report.freshness;
  if (!freshness) return "Synced from the school database";
  const sources = freshness.datasets.map((item) => datasetLabel(item.dataset)).join(", ");
  if (!freshness.as_of) return `Not synced yet · ${sources}`;
  return `Updated ${relativeTime(freshness.as_of)} · ${sources}`;
}

export function AnalyticsPage() {
  const [reports, setReports] = useState<ReportMeta[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("All");
  const [openKey, setOpenKey] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [submitted, setSubmitted] = useState(0);
  const [result, setResult] = useState<ReportResult | null>(null);
  const [reportError, setReportError] = useState("");
  const [running, setRunning] = useState(false);

  function load() {
    return api<{ reports: ReportMeta[] }>("/admin/api/analytics/reports").then((body) => setReports(body.reports));
  }

  useEffect(() => {
    load().catch((err) => setError(errorMessage(err, "Could not load reports")));
  }, []);

  const categories = useMemo(() => {
    const names = new Set((reports ?? []).map((item) => item.category));
    return ["All", ...names];
  }, [reports]);

  const visible = (reports ?? []).filter((item) => {
    if (category !== "All" && item.category !== category) return false;
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return `${item.title} ${item.description} ${item.category}`.toLowerCase().includes(needle);
  });

  const open = reports?.find((item) => item.key === openKey) ?? null;

  useEffect(() => {
    if (!openKey || submitted === 0) return;
    let cancelled = false;
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value.trim()) params.set(key, value.trim());
    }
    const qs = params.toString();
    setRunning(true);
    setReportError("");
    api<ReportResult>(`/admin/api/analytics/reports/${openKey}${qs ? `?${qs}` : ""}`)
      .then((body) => {
        if (!cancelled) setResult(body);
      })
      .catch((err) => {
        if (!cancelled) {
          setResult(null);
          setReportError(errorMessage(err, "Could not open the report"));
        }
      })
      .finally(() => {
        if (!cancelled) setRunning(false);
      });
    return () => {
      cancelled = true;
    };
  }, [openKey, submitted]);

  function openReport(item: ReportMeta) {
    setFilters({});
    setResult(null);
    setReportError("");
    setOpenKey(item.key);
    setSubmitted((value) => value + 1);
  }

  return (
    <section className="stack">
      <PageHead title="Data Insights Hub" lede="Reports from the school database. An admin chooses which cards each user can open." />
      <ErrorBanner message={error} onDismiss={() => setError("")} />
      {open ? (
        <ReportView
          report={open}
          filters={filters}
          setFilters={setFilters}
          running={running}
          error={reportError}
          result={result}
          onBack={() => {
            setOpenKey("");
            setResult(null);
          }}
          onSubmit={(event) => {
            event.preventDefault();
            setSubmitted((value) => value + 1);
          }}
        />
      ) : (
        <>
          <label className="field hub-search">
            Search reports
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search reports, for example attendance" />
          </label>
          {reports && reports.length > 0 ? (
            <div className="hub-cats" role="tablist" aria-label="Report categories">
              {categories.map((item) => (
                <button key={item} type="button" className={item === category ? "active" : ""} onClick={() => setCategory(item)}>
                  {item}
                </button>
              ))}
            </div>
          ) : null}
          {reports === null ? (
            <p className="hint" role="status">
              Loading reports…
            </p>
          ) : reports.length === 0 ? (
            <Empty>No reports are assigned to this account yet. An admin can assign them on Accounts.</Empty>
          ) : visible.length === 0 ? (
            <Empty>No reports match that search.</Empty>
          ) : (
            <div className="hub-grid">
              {visible.map((item) => (
                <button key={item.key} type="button" className={`hub-card cat-${item.category.toLowerCase()}`} onClick={() => openReport(item)}>
                  <span className="hub-cat">{item.category}</span>
                  <strong>{item.title}</strong>
                  <p>{item.description}</p>
                  <span className="hub-open">Open report</span>
                  <footer>{freshnessLine(item)}</footer>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function ReportView({
  report,
  filters,
  setFilters,
  running,
  error,
  result,
  onBack,
  onSubmit,
}: {
  report: ReportMeta;
  filters: Record<string, string>;
  setFilters: (value: Record<string, string> | ((current: Record<string, string>) => Record<string, string>)) => void;
  running: boolean;
  error: string;
  result: ReportResult | null;
  onBack: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <div className="panel stack">
      <div className="page-head">
        <div>
          <button type="button" className="ghost" onClick={onBack}>
            All reports
          </button>
          <h2>{report.title}</h2>
          <p className="hint">{report.description}</p>
          <p className="hint">{freshnessLine(report)}</p>
        </div>
      </div>
      <ErrorBanner message={error} />
      <form className="filters" onSubmit={onSubmit}>
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
      {result && result.report.key === report.key ? (
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
    </div>
  );
}
