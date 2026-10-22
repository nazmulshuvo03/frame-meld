"use client";

import { useState } from "react";
import Link from "next/link";
import { CancelButton } from "@/components/CancelButton";
import { timecode } from "@/components/studio/stages";
import type { Job } from "@/lib/api";

/** What the studio is doing, in the user's words rather than the job kind's. */
const WORKING: Record<string, string> = {
  generate_script: "Writing the shot list",
  generate_characters: "Reading your story",
  generate_character_image: "Drawing a character",
  map_shot_characters: "Working out who is in each shot",
  generate_shot_image: "Drawing a still",
  generate_speech: "Recording a line",
  generate_music: "Composing the score",
  render_preview: "Assembling the preview",
  generate_shot_clip: "Animating a shot",
  render_final: "Assembling the film",
};

function activity(job: Job | null, remaining: number): string {
  if (job === null) return "Starting…";
  const what = WORKING[job.kind] ?? "Working";
  const queued = remaining > 0 ? ` · ${remaining} to go` : "";
  if (job.state === "queued") return `Queued${queued}`;
  if (job.progress_pct === null) return `${what}…${queued}`;
  return `${what}… ${job.progress_pct}%${queued}`;
}

/** The film-frame mark beside the name. Thin-stroke, like every icon here. */
export function BrandMark() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      aria-hidden
      className="shrink-0 text-accent"
    >
      <rect x="2" y="5" width="20" height="14" />
      <path d="M7 5v14M17 5v14M2 9.5h5M2 14.5h5M17 9.5h5M17 14.5h5" />
    </svg>
  );
}

/** The Meld's title, edited where it is shown. Saved on Enter or on leaving
 *  the field; Escape puts it back. An empty title is not a title, so it
 *  reverts rather than saving. */
function TitleField({ title, onRename }: { title: string; onRename: (title: string) => void }) {
  const [draft, setDraft] = useState(title);

  const commit = () => {
    const trimmed = draft.trim();
    if (trimmed && trimmed !== title) onRename(trimmed);
    else setDraft(title);
  };

  return (
    <input
      aria-label="Meld title"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setDraft(title);
          // Blur after the reset lands, so commit sees the saved title.
          requestAnimationFrame(() => (e.target as HTMLInputElement).blur());
        }
      }}
      className="w-full max-w-[420px] truncate border-b border-dashed border-transparent bg-transparent font-heading font-semibold outline-none hover:border-subtle focus-visible:border-edge-focus focus-visible:outline-none"
    />
  );
}

/** The credit balance, as the header's one standing figure. */
export function Credits({ balance }: { balance: number | null }) {
  if (balance === null) return null;
  return (
    <span
      className="inline-flex items-center gap-2 border border-subtle px-3 py-[7px] font-heading text-sm font-semibold tabular-nums"
      role="status"
      aria-live="polite"
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        aria-hidden
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v10M7 12h10" />
      </svg>
      <span>
        <span>{balance}</span> credits
      </span>
    </span>
  );
}

/**
 * The studio's header: one line, always on screen.
 *
 * Three cells on the prototype's grid: the brand over the sidebar's column,
 * this Meld's title, then the figures -- the film's length, what the studio
 * is doing while a job runs, and the credit balance. While a render runs, a
 * thin scrub line under the bar is the one piece of motion on the page.
 */
export function StudioHeader({
  title,
  durationSec,
  shotCount,
  balance,
  job,
  busy,
  remaining,
  cancelError,
  onStop,
  onRename,
  onLogout,
}: {
  title: string;
  onRename: (title: string) => void;
  onLogout: () => void;
  durationSec: number;
  shotCount: number;
  balance: number | null;
  job: Job | null;
  busy: boolean;
  remaining: number;
  cancelError: string | null;
  onStop: () => void;
}) {
  const progress = job?.progress_pct ?? null;

  return (
    <header className="sticky top-0 z-20 border-b border-subtle bg-background">
      <div className="grid h-14 grid-cols-[auto_minmax(0,1fr)_auto] items-center lg:grid-cols-[248px_minmax(0,1fr)_auto]">
        <Link
          href="/melds"
          aria-label="FrameMeld — back to my Melds"
          className="flex h-full items-center gap-2.5 border-r border-subtle px-5 hover:text-accent-ink"
        >
          <BrandMark />
          <span className="hidden font-heading text-[19px] font-semibold tracking-[0.01em] sm:inline">
            FrameMeld
          </span>
        </Link>

        <div className="flex min-w-0 items-baseline gap-3.5 px-6">
          <span className="kicker hidden text-faint sm:inline">Meld</span>
          <h1 className="min-w-0 flex-1 text-xl">
            {/* Keyed by the saved title, so a rename landing from elsewhere
                resets the draft rather than fighting it. */}
            <TitleField key={title} title={title} onRename={onRename} />
          </h1>
        </div>

        <div className="flex items-center gap-4 px-5">
          {busy ? (
            <span
              className="flex items-center gap-2 text-sm text-signal"
              role="status"
              aria-live="polite"
            >
              <span className="size-1.5 bg-signal motion-safe:animate-pulse" aria-hidden />
              <span className="hidden max-w-[14rem] truncate md:inline">
                {activity(job, remaining)}
              </span>
            </span>
          ) : (
            shotCount > 0 && (
              <p className="hidden font-heading text-[15px] tabular-nums text-muted lg:block">
                {timecode(durationSec)}
                <span> · {shotCount} shots</span>
              </p>
            )
          )}

          {busy && <CancelButton job={job} onCancel={onStop} error={cancelError} />}

          <Credits balance={balance} />

          <button type="button" onClick={onLogout} className="btn btn-ghost text-muted">
            Log out
          </button>
        </div>
      </div>

      {/* The scrub line: only drawn while something is rendering, because a
          progress bar that is always visible is furniture, not information. */}
      {progress !== null && busy && (
        <div className="absolute inset-x-0 -bottom-px h-0.5" aria-hidden>
          <div
            className="h-full bg-signal transition-[width] duration-500 ease-linear"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </header>
  );
}
