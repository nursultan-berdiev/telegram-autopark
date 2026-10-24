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
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : res.statusText;
    throw new ApiError(res.status, detail);
  }
  return body as T;
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
  patch: <T>(path: string, data?: unknown) => withBody<T>("PATCH", path, data),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
