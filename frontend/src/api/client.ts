// Тонкий клієнт API: сесійна cookie (HttpOnly) + CSRF-токен у пам'яті.
// Жодних токенів у localStorage — XSS не зможе їх викрасти.

import { currentLang, tr } from "../i18n";

// Увага: i18n імпортує hooks/useAuth, а той — цей модуль (циклічний імпорт).
// Тому currentLang()/tr() викликаються лише всередині функцій, ніколи на верхньому рівні.

let csrfToken: string | null = null;

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function ensureCsrf(force = false): Promise<string> {
  if (csrfToken && !force) return csrfToken;
  const r = await fetch("/api/auth/csrf/", { credentials: "same-origin", headers: { "Accept-Language": currentLang() } });
  const data = await r.json();
  csrfToken = data.csrfToken as string;
  return csrfToken;
}

export function resetCsrf() {
  csrfToken = null;
}

function extractMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const d = data as Record<string, unknown>;
  if (typeof d.detail === "string") return d.detail;
  if (Array.isArray(d.detail) && typeof d.detail[0] === "string") return d.detail[0];
  for (const [key, value] of Object.entries(d)) {
    if (Array.isArray(value) && typeof value[0] === "string") {
      return key === "non_field_errors" ? value[0] : `${value[0]}`;
    }
    if (typeof value === "string") return value;
  }
  return fallback;
}

type Body = object | FormData | Blob | ArrayBuffer | undefined;

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: Body; headers?: Record<string, string>; signal?: AbortSignal } = {},
): Promise<T> {
  const method = (options.method || "GET").toUpperCase();
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Accept-Language": currentLang(),
    ...(options.headers || {}),
  };
  let body: BodyInit | undefined;

  if (options.body instanceof FormData || options.body instanceof Blob || options.body instanceof ArrayBuffer) {
    body = options.body as BodyInit;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const doFetch = async () => {
    if (method !== "GET" && method !== "HEAD") headers["X-CSRFToken"] = await ensureCsrf();
    return fetch(path, { method, headers, body, credentials: "same-origin", signal: options.signal });
  };

  let response = await doFetch();
  // CSRF-токен міг змінитися після входу (Django ротує його) — повторюємо один раз
  if (response.status === 403 && method !== "GET") {
    const text = await response.clone().text();
    if (text.includes("CSRF")) {
      await ensureCsrf(true);
      response = await doFetch();
    }
  }

  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("Content-Type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401) {
      window.dispatchEvent(new CustomEvent("bc:unauthorized"));
    }
    throw new ApiError(response.status, extractMessage(data, tr("misc.errorStatus", { status: response.status })), data);
  }
  return data as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, body?: Body) => api<T>(path, { method: "POST", body: body ?? {} });
export const patch = <T>(path: string, body: Body) => api<T>(path, { method: "PATCH", body });
export const del = <T>(path: string, body?: Body) => api<T>(path, { method: "DELETE", body });

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return tr("misc.unknownError");
}
