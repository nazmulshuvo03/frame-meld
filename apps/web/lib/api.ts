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

export type Story = { text: string; content_hash: string; updated_at: string };

export type MeldDetail = MeldSummary & { story: Story | null };

/** No response is ever worth waiting on forever: without this a hung
 *  backend leaves the editor saying "Saving…" with the close warning armed
 *  and no way for the user to tell that nothing is happening. */
const DEFAULT_TIMEOUT_MS = 15_000;

type RequestInitWithTimeout = RequestInit & { timeoutMs?: number };

async function request<T>(path: string, init: RequestInitWithTimeout = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal, ...rest } = init;
  const timeout = AbortSignal.timeout(timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...rest,
      // Required so the HttpOnly session cookie travels cross-origin
      // (web on :3000, api on :8000). Omitting this is the single most
      // common cause of unexplained 401s here.
      credentials: "include",
      // AbortSignal.any so a caller's own cancellation still works. Passing
      // only the timeout would silently ignore it.
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch (err) {
    // A deliberate cancellation is the caller's business — rethrow it
    // untouched so it is not mistaken for a failure and reported to a user.
    if (signal?.aborted) throw err;
    // Status 0 is this module's convention for "no answer at all". Callers
    // branch on 401 and 404; everything else, including this, falls through
    // to their "check your connection" path, which is the right message.
    throw new ApiError(
      0,
      timeout.aborted
        ? "The server took too long to respond."
        : "Couldn't reach the server.",
    );
  }

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

export const saveStory = (id: string, text: string, ifContentHash?: string) =>
  request<Story>(`/melds/${id}/story`, {
    method: "PUT",
    // Omitted rather than sent as null on the first save after a load,
    // when there is nothing to compare against yet.
    body: JSON.stringify(
      ifContentHash ? { text, if_content_hash: ifContentHash } : { text },
    ),
  });

export type Shot = {
  id: string;
  order_index: number;
  image_prompt: string;
  motion_prompt: string;
  /** A decimal string, not a number: the API serialises it from Decimal and
   *  the value goes straight back on the next edit. Parsing to a float and
   *  back would change 4.05 and make the shot look edited when it was not. */
  duration_sec: string;
  camera: string;
  dialogue_kind: string;
  speech_text: string;
  state: string;
  content_hash: string;
  updated_at: string;
};

export type Scene = {
  id: string;
  order_index: number;
  heading: string;
  summary: string;
  location: string;
  time_of_day: string;
  state: string;
  content_hash: string;
  updated_at: string;
  shots: Shot[];
};

export type ScriptView = {
  scenes: Scene[];
  stale_scenes: number;
  stale_shots: number;
};

export type Job = {
  id: string;
  kind: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  attempts: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export const CAMERA_ANGLES = ["wide", "medium", "close", "insert", "establishing"] as const;
export const DIALOGUE_KINDS = ["none", "narration", "off_screen", "on_screen"] as const;

export const getScript = (meldId: string) => request<ScriptView>(`/melds/${meldId}/script`);

export const generateScript = (meldId: string) =>
  request<Job>(`/melds/${meldId}/script/generate`, { method: "POST" });

export const getJob = (meldId: string, jobId: string) =>
  request<Job>(`/melds/${meldId}/jobs/${jobId}`);

export const updateScene = (meldId: string, sceneId: string, patch: Partial<Scene>) =>
  request<Scene>(`/melds/${meldId}/scenes/${sceneId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const updateShot = (meldId: string, shotId: string, patch: Partial<Shot>) =>
  request<Shot>(`/melds/${meldId}/shots/${shotId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
