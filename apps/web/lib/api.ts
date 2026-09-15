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

/** Exposed only so the timeout -- the one behaviour this module adds that
 *  no public call can exercise on its own -- is testable. A visible,
 *  named seam is better than leaving it unguarded. */
export const __testing = { request };

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

export const CAMERA_ANGLES = ["wide", "medium", "close", "insert", "establishing"] as const;
export const DIALOGUE_KINDS = ["none", "narration", "off_screen", "on_screen"] as const;

export type CameraAngle = (typeof CAMERA_ANGLES)[number];
export type DialogueKind = (typeof DIALOGUE_KINDS)[number];

export type Shot = {
  id: string;
  order_index: number;
  image_prompt: string;
  motion_prompt: string;
  /** A decimal string, not a number: the API serialises it from Decimal and
   *  the value goes straight back on the next edit. Parsing to a float and
   *  back would change 4.05 and make the shot look edited when it was not. */
  duration_sec: string;
  camera: CameraAngle;
  dialogue_kind: DialogueKind;
  speech_text: string;
  state: string;
  content_hash: string;
  updated_at: string;
};

/** A scene's own fields — what a PATCH returns.
 *
 *  Without `shots`, deliberately: the API's scene-edit response omits them
 *  (it does not load the relationship for a field edit), so a type that
 *  promised them would be a lie the compiler cannot catch.
 */
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
};

/** A scene with its shots — what the script listing returns. */
export type SceneWithShots = Scene & { shots: Shot[] };

export type ScriptView = {
  scenes: SceneWithShots[];
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

export const getScript = (meldId: string) => request<ScriptView>(`/melds/${meldId}/script`);

export const generateScript = (meldId: string) =>
  request<Job>(`/melds/${meldId}/script/generate`, { method: "POST" });

export const getJob = (meldId: string, jobId: string) =>
  request<Job>(`/melds/${meldId}/jobs/${jobId}`);

// Narrowed to exactly what the API's ScenePatch/ShotPatch declare. A wider
// Partial<Scene>/Partial<Shot> would let a caller pass e.g. `state` or
// `content_hash` — fields neither patch schema declares. The backend now
// rejects an unknown field outright (extra="forbid"), but catching the
// mistake here, at compile time, is friendlier than a 422 at request time.
export type ScenePatch = Partial<
  Pick<Scene, "heading" | "summary" | "location" | "time_of_day">
>;

export type ShotPatch = Partial<
  Pick<
    Shot,
    "image_prompt" | "motion_prompt" | "duration_sec" | "camera" | "dialogue_kind" | "speech_text"
  >
>;

export const updateScene = (meldId: string, sceneId: string, patch: ScenePatch) =>
  request<Scene>(`/melds/${meldId}/scenes/${sceneId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const updateShot = (meldId: string, shotId: string, patch: ShotPatch) =>
  request<Shot>(`/melds/${meldId}/shots/${shotId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

// --- Cast, credits and assets (M3) ---------------------------------------

export const CHARACTER_ROLES = ["lead", "supporting", "background"] as const;
export type CharacterRole = (typeof CHARACTER_ROLES)[number];

export type CharacterImage = {
  id: string;
  kind: string;
  is_canonical: boolean;
  state: string;
  /** Null until a generation succeeds. */
  asset_id: string | null;
  credits_spent: number;
  updated_at: string;
};

export type Character = {
  id: string;
  order_index: number;
  name: string;
  role: CharacterRole;
  description: string;
  appearance: string;
  /** Which voice reads this character's lines. Inside the character's
   *  content hash and outside their visual hash, so changing it invalidates
   *  their dialogue and leaves their sheet alone. */
  voice_id: string | null;
  state: string;
  content_hash: string;
  updated_at: string;
  sheet: CharacterImage | null;
};

export type CastView = {
  characters: Character[];
  stale_characters: number;
  stale_sheets: number;
};

export type Pricing = { version: number; quotes: Record<string, number> };

/** The asset route is derivable from an id, so there is no URL field on
 *  the API's response -- a stored URL would be a second thing to keep
 *  true. Rendered straight into an <img src>, which relies on the session
 *  cookie being sent with a subresource request: fine while web and api
 *  are same-site, and the reason M5's signed URLs exist. */
export const assetUrl = (assetId: string) => `${BASE}/assets/${assetId}`;

export const getCast = (meldId: string) => request<CastView>(`/melds/${meldId}/cast`);

export const generateCast = (meldId: string) =>
  request<Job>(`/melds/${meldId}/cast/generate`, { method: "POST" });

export const generateSheet = (meldId: string, characterId: string) =>
  request<Job>(`/melds/${meldId}/characters/${characterId}/sheet`, { method: "POST" });

// Narrowed to exactly what the API's CharacterPatch declares, so passing
// `state` or `content_hash` is a compile error rather than a 422.
export type CharacterPatch = Partial<
  Pick<Character, "name" | "role" | "description" | "appearance" | "voice_id">
>;

export const updateCharacter = (meldId: string, characterId: string, patch: CharacterPatch) =>
  request<Character>(`/melds/${meldId}/characters/${characterId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const getBalance = () => request<{ balance: number }>("/credits");
export const getPricing = () => request<Pricing>("/credits/pricing");

// --- Shots: stills and speech (M4) ---------------------------------------

export type ShotImage = {
  id: string;
  state: string;
  /** Null until a generation succeeds. */
  asset_id: string | null;
  /** Which sheets this picture was drawn from, in the order they were
   *  attached. Exposed because "why does this face look wrong" is otherwise
   *  unanswerable from the outside. */
  ref_character_image_ids: string[];
  credits_spent: number;
  updated_at: string;
};

export type AudioClip = {
  id: string;
  kind: "narration" | "dialogue" | "music" | "sfx";
  state: string;
  asset_id: string | null;
  text: string;
  voice_id: string;
  /** A decimal string, like a shot's duration, for the same reason. */
  duration_sec: string | null;
  credits_spent: number;
  updated_at: string;
};

/** A shot plus everything generated from it. Extends Shot rather than
 *  restating it: the Script and Shots pages show the same rows. */
export type ShotMedia = Shot & {
  character_ids: string[];
  character_names: string[];
  still: ShotImage | null;
  audio: AudioClip | null;
};

export type ShotsView = {
  shots: ShotMedia[];
  stale_stills: number;
  stale_audio: number;
  /** Whether anyone has been placed in a shot yet. "Nobody is in any shot"
   *  and "nobody has been placed yet" look identical from the rows alone. */
  cast_into_shots: boolean;
};

export const getShots = (meldId: string) => request<ShotsView>(`/melds/${meldId}/shots`);

export const castShots = (meldId: string) =>
  request<Job>(`/melds/${meldId}/shots/cast`, { method: "POST" });

export const generateStill = (meldId: string, shotId: string) =>
  request<Job>(`/melds/${meldId}/shots/${shotId}/still`, { method: "POST" });

export const generateSpeech = (meldId: string, shotId: string) =>
  request<Job>(`/melds/${meldId}/shots/${shotId}/speech`, { method: "POST" });

/** Served rather than hard-coded here, so the list cannot drift from the one
 *  the speech adapter will actually accept — a voice the provider rejects is
 *  a failed job and a refunded credit, for a value this UI offered. */
export const getVoices = (meldId: string) => request<string[]>(`/melds/${meldId}/voices`);
