import { Suspense } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const api = vi.hoisted(() => ({
  getMeld: vi.fn(),
  getScript: vi.fn(),
  getCast: vi.fn(),
  getShots: vi.fn(),
  getFilm: vi.fn(),
  getBalance: vi.fn(),
  getPricing: vi.fn(),
  getVoices: vi.fn(),
  renderPreview: vi.fn(),
  getJob: vi.fn(),
  // The story editor autosaves. Unmocked it makes a real request that
  // never settles under jsdom, and every test in this file hangs in mount().
  saveStory: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, ...api };
});

const { default: StudioPage } = await import("@/app/meld/[id]/page");

/** Render the page and wait for its first load.
 *
 *  The `act` wrapper is load-bearing, not decoration. The page reads its
 *  route params with React 19's `use()`, which suspends; inside Testing
 *  Library's synchronous act scope that promise never gets a chance to
 *  resolve and the tree stays on the fallback forever. Awaiting an async
 *  act is what lets the microtask queue drain.
 */
async function mount() {
  await act(async () => {
    render(
      <Suspense fallback={null}>
        <StudioPage params={Promise.resolve({ id: "meld-1" })} />
      </Suspense>,
    );
  });
}

describe("StudioPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getMeld.mockResolvedValue({
      id: "meld-1",
      title: "The Lighthouse",
      stage: "story",
      status: "draft",
      story: { text: "Elena climbed the stairs.", content_hash: "h1" },
    });
    api.getScript.mockResolvedValue({ scenes: [], stale_shots: 0 });
    api.getCast.mockResolvedValue({ characters: [], stale_characters: 0, stale_sheets: 0 });
    api.getShots.mockResolvedValue({
      shots: [],
      cast_into_shots: false,
      stale_stills: 0,
      stale_audio: 0,
    });
    api.getFilm.mockResolvedValue({
      shot_count: 4,
      stills_ready: 4,
      clips_ready: 0,
      clips_stale: 0,
      duration_sec: 16,
      music: null,
      preview: null,
      final: null,
    });
    api.getBalance.mockResolvedValue({ balance: 500 });
    api.getPricing.mockResolvedValue({ version: 1, quotes: {} });
    api.getVoices.mockResolvedValue([]);
    api.saveStory.mockResolvedValue({ text: "", content_hash: "h1" });
  });

  it("puts all five stages on one page", async () => {
    // The whole point of the rewrite: no navigation between stages.
    await mount();
    for (const stage of ["Story", "Script", "Cast", "Shots", "Film"]) {
      expect(await screen.findByRole("heading", { name: stage, level: 2 })).toBeInTheDocument();
    }
  });

  it("shows the film's length and the balance in the transport bar", async () => {
    await mount();
    // 16 seconds as timecode, because a film's length is read in minutes
    // and seconds rather than as a decimal.
    expect(await screen.findByText("0:16")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();
  });

  it("sends a signed-out user to login rather than showing an error", async () => {
    const { ApiError } = await import("@/lib/api");
    api.getMeld.mockRejectedValue(new ApiError(401, "Not authenticated"));
    await mount();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("sends a user to the dashboard when the Meld is not theirs", async () => {
    const { ApiError } = await import("@/lib/api");
    api.getMeld.mockRejectedValue(new ApiError(404, "Not found"));
    await mount();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/melds"));
  });

  it("does not poll when a render comes back already done", async () => {
    // A cache hit: spec 4.1's free second press. Polling a job that has
    // already succeeded would be a request for nothing.
    api.renderPreview.mockResolvedValue({
      id: "job-1",
      kind: "render_preview",
      state: "succeeded",
    });
    await mount();

    fireEvent.click(await screen.findByRole("button", { name: /Render the preview/ }));
    await waitFor(() => expect(api.getFilm).toHaveBeenCalledTimes(2));
    expect(api.getJob).not.toHaveBeenCalled();
  });

  it("shows the reason an action was refused", async () => {
    const { ApiError } = await import("@/lib/api");
    api.renderPreview.mockRejectedValue(
      new ApiError(422, "3 of 11 shots have no finished still"),
    );
    await mount();

    fireEvent.click(await screen.findByRole("button", { name: /Render the preview/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("3 of 11 shots");
  });
});
