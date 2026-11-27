// Клиент API: access-токен живёт только в памяти, refresh — в httpOnly cookie.

export type Problem = {
  type: string;
  title: string;
  status: number;
  detail?: string;
  errors?: { loc: (string | number)[]; msg: string }[];
  [key: string]: unknown;
};

export class ApiError extends Error {
  constructor(public problem: Problem) {
    super(problem.detail ?? problem.title);
  }
  get code(): string {
    return this.problem.type.replace("/problems/", "");
  }
}

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken !== null;
}

async function parseProblem(resp: Response): Promise<Problem> {
  try {
    return (await resp.json()) as Problem;
  } catch {
    return { type: "/problems/network", title: "Сервис временно недоступен", status: resp.status };
  }
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const resp = await fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" });
        if (!resp.ok) {
          accessToken = null;
          return false;
        }
        const data = (await resp.json()) as { access_token: string };
        accessToken = data.access_token;
        return true;
      } catch {
        return false;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
  }
  return refreshing;
}

type Options = { method?: string; body?: unknown; retry?: boolean };

export async function api<T>(path: string, { method = "GET", body, retry = true }: Options = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (method !== "GET") headers["Idempotency-Key"] = crypto.randomUUID();

  let resp: Response;
  try {
    resp = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError({
      type: "/problems/network",
      title: "Нет связи с сервером",
      detail: "Проверьте интернет и попробуйте ещё раз.",
      status: 0,
    });
  }

  if (resp.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refreshSession()) return api<T>(path, { method, body, retry: false });
  }
  if (!resp.ok) throw new ApiError(await parseProblem(resp));
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const p = err.problem;
    if (p.errors?.length) return p.errors.map((e) => e.msg.replace(/^Value error, /, "")).join(". ");
    return p.detail ? `${p.title}. ${p.detail}` : p.title;
  }
  return "Что-то пошло не так. Попробуйте ещё раз.";
}
