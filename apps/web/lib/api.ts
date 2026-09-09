const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type User = { id: string; email: string };

export type MeldSummary = {
  id: string;
  title: string;
  stage: string;
  status: string;
  aspect_ratio: string;
  created_at: string;
  updated_at: string;
};

export type Story = { text: string; input_hash: string; updated_at: string };

export type MeldDetail = MeldSummary & { story: Story | null };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    // Required so the HttpOnly session cookie travels cross-origin
    // (web on :3000, api on :8000). Omitting this is the single most
    // common cause of unexplained 401s here.
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Body was not JSON; keep the status text.
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const signup = (email: string, password: string) =>
  request<User>("/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) });

export const login = (email: string, password: string) =>
  request<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });

export const logout = () => request<void>("/auth/logout", { method: "POST" });

export const me = () => request<User>("/auth/me");

export const listMelds = () => request<MeldSummary[]>("/melds");

export const createMeld = (title?: string) =>
  request<MeldSummary>("/melds", {
    method: "POST",
    body: JSON.stringify(title ? { title } : {}),
  });

export const getMeld = (id: string) => request<MeldDetail>(`/melds/${id}`);

export const renameMeld = (id: string, title: string) =>
  request<MeldSummary>(`/melds/${id}`, { method: "PATCH", body: JSON.stringify({ title }) });

export const deleteMeld = (id: string) => request<void>(`/melds/${id}`, { method: "DELETE" });

export const saveStory = (id: string, text: string) =>
  request<Story>(`/melds/${id}/story`, { method: "PUT", body: JSON.stringify({ text }) });
