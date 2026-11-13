export type Session = { email: string; role: string; csrf_token: string };

let csrf = "";

export function setCsrf(token: string) {
  csrf = token;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("content-type", "application/json");
  if (csrf && options.method && options.method !== "GET") headers.set("x-csrf-token", csrf);
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || response.statusText);
  return body as T;
}
