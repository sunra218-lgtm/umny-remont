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
// Приложение бригады подписывает продление сеанса ключом телефона.
let deviceSigner: (() => Promise<Record<string, string>>) | null = null;

export function setDeviceSigner(signer: (() => Promise<Record<string, string>>) | null) {
  deviceSigner = signer;
}

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
        const headers = deviceSigner ? await deviceSigner() : {};
        const resp = await fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin", headers });
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

type Options = { method?: string; body?: unknown; form?: FormData; retry?: boolean; idempotencyKey?: string };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { body, form, retry = true, idempotencyKey } = options;
  let { method = "GET" } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (form) method = method === "GET" ? "POST" : method;
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (method !== "GET") headers["Idempotency-Key"] = idempotencyKey ?? crypto.randomUUID();

  let resp: Response;
  try {
    resp = await fetch(`/api/v1${path}`, {
      method,
      headers,
      body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
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
    if (await refreshSession()) return api<T>(path, { ...options, method, retry: false });
  }
  if (!resp.ok) throw new ApiError(await parseProblem(resp));
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const p = err.problem;
    const rows = p.rows as { row: number; message: string }[] | undefined;
    if (rows?.length) {
      return `${p.title}. ` + rows.map((r) => (r.row ? `Строка ${r.row}: ${r.message}` : r.message)).join(". ");
    }
    if (p.errors?.length) return p.errors.map((e) => e.msg.replace(/^Value error, /, "")).join(". ");
    return p.detail ? `${p.title}. ${p.detail}` : p.title;
  }
  return "Что-то пошло не так. Попробуйте ещё раз.";
}
