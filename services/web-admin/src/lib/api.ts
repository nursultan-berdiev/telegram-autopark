// Тонкий клиент к core-api. Кука веб-сессии шлётся автоматически
// (credentials: include); X-Requested-With нужен для куки-мутаций (CSRF) и
// безвреден на чтениях.

const BASE = "/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  // ...opts ПЕРВЫМ, иначе opts.headers затёр бы слитый объект и X-Requested-With
  // не ушёл бы ни в один POST (включая /web-logout, который требует CSRF-заголовок).
  const res = await fetch(BASE + path, {
    ...opts,
    credentials: "include",
    headers: { "X-Requested-With": "fleet-admin", ...(opts.headers ?? {}) },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, errorText(body, res.statusText));
  }
  return body as T;
}

// FastAPI на 422 кладёт в detail массив {loc,msg,...}; String(массив) дал бы
// «[object Object]». Разворачиваем в читаемый текст, строку отдаём как есть.
function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join("; ");
    }
    if (detail != null) return String(detail);
  }
  return fallback;
}

function withBody<T>(method: string, path: string, data?: unknown) {
  return request<T>(path, {
    method,
    headers: data != null ? { "Content-Type": "application/json" } : {},
    body: data != null ? JSON.stringify(data) : undefined,
  });
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) => withBody<T>("POST", path, data),
  put: <T>(path: string, data?: unknown) => withBody<T>("PUT", path, data),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
