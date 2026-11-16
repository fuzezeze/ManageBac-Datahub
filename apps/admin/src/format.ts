export type PillTone = "ok" | "stale" | "failed" | "blocked" | "never" | "partial" | "running" | "queued" | "neutral";

const LABELS: Record<string, string> = {
  connection_check: "Connection check",
  academic_years: "Academic years",
  subjects: "Subjects",
  teachers: "Teachers",
  students: "Students",
  year_groups: "Year groups",
  classes: "Classes",
  student_year_groups: "Student year groups",
  student_classes: "Student classes",
  teacher_classes: "Teacher classes",
  attendance_class: "Class attendance",
  attendance_homeroom: "Homeroom attendance",
  behavior_notes: "Behavior notes",
  courses: "Courses",
};

export function datasetLabel(id: string): string {
  return LABELS[id] ?? id.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function isPlatformAdmin(role: string): boolean {
  return role === "platform_admin" || role === "admin";
}

export function roleLabel(role: string): string {
  if (isPlatformAdmin(role)) return "Admin";
  if (role === "user") return "User";
  if (role === "data_operator") return "Data operator";
  if (role === "auditor") return "Auditor";
  return role;
}

export function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export function parseUtc(iso: string): Date {
  if (/[zZ]$|[+-]\d{2}:\d{2}$/.test(iso)) return new Date(iso);
  return new Date(`${iso.replace(" ", "T")}Z`);
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "Never";
  const then = parseUtc(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const delta = Math.round((then - now) / 1000);
  const abs = Math.abs(delta);
  const format = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 45) return format.format(Math.round(delta), "second");
  if (abs < 45 * 60) return format.format(Math.round(delta / 60), "minute");
  if (abs < 36 * 3600) return format.format(Math.round(delta / 3600), "hour");
  if (abs < 25 * 86400) return format.format(Math.round(delta / 86400), "day");
  return format.format(Math.round(delta / (86400 * 30)), "month");
}

export function absoluteTime(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const date = parseUtc(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const zone = timeZone || "UTC";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(date);
  } catch {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "UTC",
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(date);
  }
}

export function duration(started: string | null, finished: string | null, now = Date.now()): string {
  if (!started) return "—";
  const start = parseUtc(started).getTime();
  const end = finished ? parseUtc(finished).getTime() : now;
  if (Number.isNaN(start) || Number.isNaN(end)) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en").format(value);
}

export function formatCounts(counts: Record<string, number> | null | undefined): string {
  if (!counts || !Object.keys(counts).length) return "—";
  const parts = Object.entries(counts).filter(([, count]) => count);
  if (!parts.length) return "No changes";
  return parts.map(([key, count]) => `${formatNumber(count)} ${key}`).join(" · ");
}

export function positiveInt(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const number = Number(value);
  if (!Number.isSafeInteger(number) || number < 1) return null;
  return number;
}

export function titleCaseToken(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export type DatasetHealth = {
  dataset: string;
  blocked: boolean;
  status: string | null;
  last_success_at: string | null;
};

function freshnessMs(dataset: string): number {
  if (dataset === "connection_check") return 30 * 60 * 1000;
  if (dataset === "attendance_class" || dataset === "attendance_homeroom") return 6 * 60 * 60 * 1000;
  return 36 * 60 * 60 * 1000;
}

export function datasetTone(row: DatasetHealth, now = Date.now()): { tone: PillTone; label: string } {
  if (row.blocked || row.status === "blocked") return { tone: "blocked", label: "Blocked" };
  if (!row.status || row.status === "never") return { tone: "never", label: "Never synced" };
  if (row.status === "failed") return { tone: "failed", label: "Failed" };
  if (row.status === "degraded") return { tone: "stale", label: "Stale" };
  if ((row.status === "fresh" || row.status === "partial") && row.last_success_at) {
    const at = parseUtc(row.last_success_at).getTime();
    if (!Number.isNaN(at) && now - at > freshnessMs(row.dataset)) return { tone: "stale", label: "Stale" };
  }
  if (row.status === "partial") return { tone: "partial", label: "Partial" };
  if (row.status === "fresh") return { tone: "ok", label: "Healthy" };
  return { tone: "neutral", label: titleCaseToken(row.status) };
}

export function jobTone(status: string): { tone: PillTone; label: string } {
  switch (status) {
    case "queued":
      return { tone: "queued", label: "Queued" };
    case "running":
      return { tone: "running", label: "Running" };
    case "retry_wait":
      return { tone: "stale", label: "Waiting to retry" };
    case "succeeded":
      return { tone: "ok", label: "Succeeded" };
    case "partial_failed":
      return { tone: "partial", label: "Partial" };
    case "failed":
      return { tone: "failed", label: "Failed" };
    default:
      return { tone: "neutral", label: titleCaseToken(status) };
  }
}

export function triggerLabel(trigger: string): string {
  if (trigger === "manual") return "Manual";
  if (trigger === "schedule") return "Scheduled";
  return titleCaseToken(trigger);
}

export function httpTone(status: number): PillTone {
  if (status >= 200 && status < 300) return "ok";
  if (status >= 400 && status < 500) return "stale";
  if (status >= 500) return "failed";
  return "neutral";
}

export function isoDate(offsetDays: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

export function pad(value: number | null | undefined): string {
  return String(value ?? 0).padStart(2, "0");
}
