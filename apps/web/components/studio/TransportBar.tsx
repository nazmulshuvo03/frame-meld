"use client";

import Link from "next/link";
import { CancelButton } from "@/components/CancelButton";
import type { Job } from "@/lib/api";

export type StageId = "story" | "script" | "cast" | "shots" | "film";

export type StageMark = {
  id: StageId;
  label: string;
  /** How many of this stage's things exist. Drives the filled marker, and
   *  reads as a count next to the name on wide screens. */
  done: number;
  /** How many are out of date. Drives the amber marker. */
  stale: number;
};

/** Seconds as `0:04 / 0:17`-style timecode.
 *
 *  Film durations are read as minutes and seconds, not as `17.5s`, and this
 *  bar is the one place the whole film's length is stated. */
function timecode(seconds: number): string {
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

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

/**
 * The bar that stays put while the five stages scroll under it.
 *
 * It is the only element on the page allowed to move, and it is shaped like
 * the transport on an edit suite rather than like a page header: the film's
 * length on the left, what the studio is doing in the middle, the money on
 * the right, and — while a render runs — a scrub line across the full width
 * underneath. A render is the one thing here that takes minutes, so it gets
 * the one piece of motion.
 */
export function TransportBar({
  title,
  durationSec,
  shotCount,
  balance,
  stages,
  current,
  job,
  busy,
  remaining,
  cancelError,
  onStop,
}: {
  title: string;
  durationSec: number;
  shotCount: number;
  balance: number | null;
  stages: StageMark[];
  current: StageId;
  job: Job | null;
  busy: boolean;
  remaining: number;
  cancelError: string | null;
  onStop: () => void;
}) {
  const progress = job?.progress_pct ?? null;

  return (
    <header className="sticky top-0 z-20 border-b border-subtle bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3">
        <Link
          href="/melds"
          className="text-sm text-muted hover:text-foreground"
          aria-label="Back to my Melds"
        >
          ←
        </Link>

        <h1 className="mr-auto truncate text-base font-medium" title={title}>
          {title}
        </h1>

        {shotCount > 0 && (
          <p className="font-mono text-sm tabular-nums text-muted">
            {timecode(durationSec)}
            <span className="text-faint"> · {shotCount} shots</span>
          </p>
        )}

        {busy ? (
          <span
            className="flex items-center gap-2 text-sm text-signal"
            role="status"
            aria-live="polite"
          >
            <span
              className="size-1.5 rounded-full bg-signal motion-safe:animate-pulse"
              aria-hidden
            />
            {activity(job, remaining)}
          </span>
        ) : null}

        {busy && <CancelButton job={job} onCancel={onStop} error={cancelError} />}

        {balance !== null && (
          <span
            className="font-mono text-sm tabular-nums text-body"
            role="status"
            aria-live="polite"
          >
            {balance}
            <span className="text-faint"> cr</span>
          </span>
        )}
      </div>

      <nav
        aria-label="Stages"
        className="mx-auto flex max-w-5xl gap-1 overflow-x-auto px-6 pb-2 text-sm"
      >
        {stages.map((stage) => {
          const active = stage.id === current;
          return (
            <a
              key={stage.id}
              href={`#${stage.id}`}
              aria-current={active ? "step" : undefined}
              className={`flex items-center gap-2 rounded px-2.5 py-1 whitespace-nowrap transition-colors ${
                active ? "bg-panel text-foreground" : "text-muted hover:text-foreground"
              }`}
            >
              {/* Filled once the stage has something in it, amber when some
                  of it is out of date. A dot rather than a tick or a number:
                  these are five states of one artifact, not a checklist and
                  not a ranking. */}
              <span
                aria-hidden
                className={`size-1.5 rounded-full ${
                  stage.stale > 0
                    ? "bg-warn-border"
                    : stage.done > 0
                      ? "bg-signal"
                      : "bg-subtle ring-1 ring-strong"
                }`}
              />
              {stage.label}
              {stage.done > 0 && (
                <span className="font-mono text-xs tabular-nums text-faint">{stage.done}</span>
              )}
            </a>
          );
        })}
      </nav>

      {/* The scrub line. Full width, one pixel tall until something is
          rendering, because a progress bar that is always visible is
          furniture rather than information. */}
      <div className="h-0.5 w-full bg-subtle" aria-hidden>
        {progress !== null && busy && (
          <div
            className="h-full bg-signal transition-[width] duration-500 ease-linear"
            style={{ width: `${progress}%` }}
          />
        )}
      </div>
    </header>
  );
}
