import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FilmStage } from "@/components/studio/FilmStage";
import type { FilmView, Job, ShotsView } from "@/lib/api";
import type { Studio } from "@/lib/useStudio";

/** The Film stage reads the studio and calls back into it; it owns no
 *  fetching of its own. That is the point of the split, and it is why these
 *  tests mock one object instead of nine API functions the way the old
 *  page-level suite had to. */
function studio(overrides: Partial<Studio> = {}): Studio {
  return {
    id: "meld-1",
    balance: 500,
    quotes: { generate_music: 24, generate_shot_clip: 80, render_final: 60 },
    busy: false,
    job: null,
    remaining: 0,
    start: vi.fn(),
    animateShots: vi.fn(),
    ...overrides,
  } as unknown as Studio;
}

function film(overrides: Partial<FilmView> = {}): FilmView {
  return {
    shot_count: 4,
    stills_ready: 4,
    clips_ready: 0,
    clips_stale: 0,
    duration_sec: 16,
    music: null,
    preview: null,
    final: null,
    ...overrides,
  };
}

const NO_SHOTS = null as unknown as ShotsView | null;

function show(view: FilmView, over: Partial<Studio> = {}, shots: ShotsView | null = NO_SHOTS) {
  const s = studio(over);
  render(<FilmStage studio={s} film={view} shots={shots} />);
  return s;
}

describe("FilmStage", () => {
  beforeEach(() => vi.clearAllMocks());

  it("offers the preview for free, never for credits", () => {
    // M5-D7, on the button face. Spec 4.2 and 9 both make Preview free
    // because it is the gate that makes a $6 Final render humane.
    show(film());
    expect(screen.getByRole("button", { name: /Render the preview · free/ })).toBeEnabled();
  });

  it("refuses to render while a shot has no still, and says how many", () => {
    show(film({ shot_count: 11, stills_ready: 8 }));
    expect(screen.getByRole("button", { name: /Render the preview/ })).toBeDisabled();
    expect(screen.getByText(/3 of 11 shots still have no still/)).toBeInTheDocument();
  });

  it("plays a finished preview and says what it is", () => {
    show(
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

  it("keeps a stale preview watchable and flags it", () => {
    // Nothing is destroyed by an edit (spec 4.4): the film the user already
    // watched stays where it is, it just says it is out of date.
    show(
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

  it("prices the score from the served table, not from its own fallback", () => {
    show(film(), { quotes: { generate_music: 31, generate_shot_clip: 80, render_final: 60 } });
    expect(screen.getByRole("button", { name: /31 credits/ })).toBeInTheDocument();
  });

  it("says there is nothing to assemble before a script exists", () => {
    show(film({ shot_count: 0, stills_ready: 0, duration_sec: 0 }));
    expect(screen.getByText(/Generate a script first/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Render the preview/ })).not.toBeInTheDocument();
  });

  it("shows the render progress the worker recorded, not just that it is busy", () => {
    // The whole point of jobs.progress_pct: "Assembling…" is honest at 36
    // seconds and a lie at ten minutes.
    const job: Job = {
      id: "job-1",
      kind: "render_final",
      state: "running",
      attempts: 1,
      error: null,
      progress_pct: 42,
      created_at: "",
      started_at: null,
      finished_at: null,
    };
    show(film({ shot_count: 4, stills_ready: 4, clips_ready: 4 }), { busy: true, job });
    expect(screen.getByRole("button", { name: /Assembling… 42%/ })).toBeInTheDocument();
  });

  it("says only 'Assembling…' before the first figure arrives", () => {
    // A bar reading 0% for the first seconds of a long render looks stuck.
    const job: Job = {
      id: "job-1",
      kind: "render_final",
      state: "running",
      attempts: 1,
      error: null,
      progress_pct: null,
      created_at: "",
      started_at: null,
      finished_at: null,
    };
    show(film({ shot_count: 4, stills_ready: 4, clips_ready: 4 }), { busy: true, job });
    expect(screen.getByRole("button", { name: /Assembling…$/ })).toBeInTheDocument();
  });

  // --- M6: clips, the Final render and the download ----------------------

  it("refuses the final render until every shot has a clip, and says how many", () => {
    show(film({ shot_count: 11, stills_ready: 11, clips_ready: 8 }));
    expect(screen.getByRole("button", { name: /Render the film/ })).toBeDisabled();
    expect(screen.getByText(/3 of 11 shots have no clip yet/)).toBeInTheDocument();
  });

  it("enables the final render once everything is animated", () => {
    show(film({ shot_count: 4, stills_ready: 4, clips_ready: 4 }));
    expect(screen.getByRole("button", { name: /Render the film · 60 credits/ })).toBeEnabled();
  });

  it("quotes the whole animation run on the button face", () => {
    // 4 shots, none animated, 80 credits each — the price of the action
    // before the click, like every other batch button here.
    show(film({ shot_count: 4, stills_ready: 4, clips_ready: 0 }));
    expect(
      screen.getByRole("button", { name: /Animate the 4 remaining · 320 credits/ }),
    ).toBeInTheDocument();
  });

  it("counts a stale clip as needing re-animating, not as missing", () => {
    show(film({ shot_count: 4, stills_ready: 4, clips_ready: 4, clips_stale: 2 }));
    expect(screen.getByText(/2 out of date since the still changed/)).toBeInTheDocument();
    // Still renderable: whether to pay 160 credits to redo them is the
    // user's call, not a block (M6-D2's reasoning, one artifact along).
    expect(screen.getByRole("button", { name: /Render the film/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Re-animate 2/ })).toBeInTheDocument();
  });

  it("animates only the shots that need it", () => {
    const shots = {
      shots: [
        { id: "s1", still: { asset_id: "a1" }, clip: { state: "fresh" } },
        { id: "s2", still: { asset_id: "a2" }, clip: null },
        { id: "s3", still: { asset_id: "a3" }, clip: { state: "stale" } },
      ],
    } as unknown as ShotsView;

    const s = show(film({ shot_count: 3, stills_ready: 3, clips_ready: 1 }), {}, shots);
    fireEvent.click(screen.getByRole("button", { name: /Animate the 2 remaining/ }));
    expect(s.animateShots).toHaveBeenCalledWith(["s2", "s3"]);
  });

  it("offers a download that saves rather than opens", () => {
    // The HTML download attribute is ignored cross-origin, so the link has
    // to ask the server for the header (M6-D10).
    show(
      film({
        shot_count: 4,
        stills_ready: 4,
        clips_ready: 4,
        final: {
          id: "r2",
          kind: "final",
          state: "fresh",
          asset_id: "asset-9",
          duration_sec: "16.00",
          resolution: "1920x1080",
          updated_at: "2026-09-18T00:00:00Z",
        },
      }),
    );
    const link = screen.getByRole("link", { name: /Download the film/ });
    expect(link).toHaveAttribute("href", expect.stringContaining("/assets/asset-9?download=true"));
    expect(screen.getByText(/1920x1080/)).toBeInTheDocument();
  });
});
