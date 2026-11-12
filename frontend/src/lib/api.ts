export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    const detail =
      typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `HTTP ${status}`;
    super(detail);
    this.status = status;
    this.body = body;
  }
}

export const UNAUTHORIZED_EVENT = "lifeos:unauthorized";

type ApiInit = Omit<RequestInit, "body"> & { json?: unknown };

export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const { json, ...rest } = init;
  const headers = new Headers(rest.headers);
  let body: BodyInit | undefined;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }
  const res = await fetch(path, { ...rest, headers, body, credentials: "same-origin" });
  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data: unknown = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    throw new ApiError(res.status, data);
  }
  return data as T;
}

export const qs = (params: Record<string, string>) => new URLSearchParams(params).toString();
