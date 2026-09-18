import { Suspense } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FilmView } from "@/lib/api";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

const api = vi.hoisted(() => ({
  getFilm: vi.fn(),
  getBalance: vi.fn(),
  getPricing: vi.fn(),
  generateMusic: vi.fn(),
  renderPreview: vi.fn(),
  getJob: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, ...api };
});

const { default: FilmPage } = await import("@/app/meld/[id]/film/page");

function film(overrides: Partial<FilmView> = {}): FilmView {
  return {
    shot_count: 4,
    stills_ready: 4,
    duration_sec: 16,
    music: null,
    preview: null,
    ...overrides,
  };
}

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
        <FilmPage params={Promise.resolve({ id: "meld-1" })} />
      </Suspense>,
    );
  });
}

async function show(view: FilmView) {
  api.getFilm.mockResolvedValue(view);
  await mount();
  await screen.findByRole("heading", { name: "Film" });
}

describe("FilmPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getBalance.mockResolvedValue({ balance: 500 });
    api.getPricing.mockResolvedValue({ version: 1, quotes: { generate_music: 24 } });
  });

  it("offers the preview for free, never for credits", async () => {
    // M5-D7, on the button face. Spec 4.2 and 9 both make Preview free
    // because it is the gate that makes a $6 Final render humane.
    await show(film());
    expect(screen.getByRole("button", { name: /Render the preview · free/ })).toBeEnabled();
  });

  it("refuses to render while a shot has no still, and says how many", async () => {
    await show(film({ shot_count: 11, stills_ready: 8 }));
    expect(screen.getByRole("button", { name: /Render the preview/ })).toBeDisabled();
    expect(screen.getByText(/3 of 11 shots still have no still/)).toBeInTheDocument();
  });

  it("plays a finished preview and says what it is", async () => {
    await show(
      film({
        preview: {
          id: "r1",
          kind: "preview",
          state: "fresh",
          asset_id: "asset-1",
          duration_sec: "16.00",
          resolution: "1280x720",
          updated_at: "2026-09-18T00:00:00Z",
        },
      }),
    );
    const video = document.querySelector("video");
    expect(video).not.toBeNull();
    expect(video?.getAttribute("src")).toContain("/assets/asset-1");
    expect(screen.getByText(/1280x720/)).toBeInTheDocument();
  });

  it("keeps a stale preview watchable and flags it", async () => {
    // Nothing is destroyed by an edit (spec 4.4): the film the user
    // already watched stays where it is, it just says it is out of date.
    await show(
      film({
        preview: {
          id: "r1",
          kind: "preview",
          state: "stale",
          asset_id: "asset-1",
          duration_sec: "16.00",
          resolution: "1280x720",
          updated_at: "2026-09-18T00:00:00Z",
        },
      }),
    );
    expect(document.querySelector("video")).not.toBeNull();
    expect(screen.getByText("Out of date")).toBeInTheDocument();
  });

  it("prices the score from the served table, not from its own fallback", async () => {
    api.getPricing.mockResolvedValue({ version: 1, quotes: { generate_music: 31 } });
    await show(film());
    expect(await screen.findByRole("button", { name: /31 credits/ })).toBeInTheDocument();
  });

  it("does not poll when a render comes back already done", async () => {
    // A cache hit: spec 4.1's free second press. Polling a job that has
    // already succeeded would be a request for nothing.
    api.renderPreview.mockResolvedValue({ id: "job-1", kind: "render_preview", state: "succeeded" });
    await show(film());

    fireEvent.click(screen.getByRole("button", { name: /Render the preview/ }));
    await waitFor(() => expect(api.getFilm).toHaveBeenCalledTimes(2));
    expect(api.getJob).not.toHaveBeenCalled();
  });

  it("shows the reason a render was refused", async () => {
    const { ApiError } = await import("@/lib/api");
    api.renderPreview.mockRejectedValue(new ApiError(422, "3 of 11 shots have no finished still"));
    await show(film());

    fireEvent.click(screen.getByRole("button", { name: /Render the preview/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("3 of 11 shots");
  });

  it("sends a signed-out user to login rather than showing an error", async () => {
    const { ApiError } = await import("@/lib/api");
    api.getFilm.mockRejectedValue(new ApiError(401, "Not authenticated"));
    await mount();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("says there is nothing to assemble before a script exists", async () => {
    await show(film({ shot_count: 0, stills_ready: 0, duration_sec: 0 }));
    expect(screen.getByText(/Generate a script first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Render the preview/ })).not.toBeInTheDocument();
  });
});
