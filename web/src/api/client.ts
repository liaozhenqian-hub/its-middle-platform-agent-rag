export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly payload?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClient {
  get<T>(path: string, init?: RequestInit): Promise<T>;
  post<T>(path: string, body?: unknown, init?: RequestInit): Promise<T>;
  put<T>(path: string, body?: unknown, init?: RequestInit): Promise<T>;
  patch<T>(path: string, body?: unknown, init?: RequestInit): Promise<T>;
  delete<T>(path: string, body?: unknown, init?: RequestInit): Promise<T>;
  request<T>(path: string, init?: RequestInit): Promise<T>;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function detailFrom(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === "object" && "msg" in item ? String(item.msg) : String(item),
        )
        .join("; ");
    }
  }
  return fallback;
}

export function createApiClient(getCsrfToken: () => string | null): ApiClient {
  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const method = (init.method ?? "GET").toUpperCase();
    const headers = new Headers(init.headers);
    if (!SAFE_METHODS.has(method)) {
      const csrfToken = getCsrfToken();
      if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
    }
    const response = await fetch(`/api${path}`, {
      ...init,
      method,
      headers,
      credentials: "include",
    });
    const contentType = response.headers.get("content-type") ?? "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (!response.ok) {
      throw new ApiError(response.status, detailFrom(payload, response.statusText || "请求失败"), payload);
    }
    return (response.status === 204 ? undefined : payload) as T;
  }

  function withBody<T>(
    method: string,
    path: string,
    body?: unknown,
    init: RequestInit = {},
  ): Promise<T> {
    const headers = new Headers(init.headers);
    let resolvedBody = body as BodyInit | null | undefined;
    if (body !== undefined && !(body instanceof FormData) && typeof body !== "string") {
      headers.set("Content-Type", "application/json");
      resolvedBody = JSON.stringify(body);
    }
    return request<T>(path, { ...init, method, headers, body: resolvedBody });
  }

  return {
    request,
    get: (path, init) => request(path, { ...init, method: "GET" }),
    post: <T>(path: string, body?: unknown, init?: RequestInit) =>
      withBody<T>("POST", path, body, init),
    put: <T>(path: string, body?: unknown, init?: RequestInit) =>
      withBody<T>("PUT", path, body, init),
    patch: <T>(path: string, body?: unknown, init?: RequestInit) =>
      withBody<T>("PATCH", path, body, init),
    delete: <T>(path: string, body?: unknown, init?: RequestInit) =>
      withBody<T>("DELETE", path, body, init),
  };
}
