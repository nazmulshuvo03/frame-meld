"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { StageId } from "@/components/studio/TransportBar";

/** Which of the five stages the reader is currently inside.
 *
 *  The page already works this out for the transport bar's nav; a context
 *  rather than a prop threaded through all five stage components, none of
 *  which otherwise cares which one is current.
 */
export const CurrentStageContext = createContext<StageId | null>(null);

/**
 * One of the five stages, as a bounded region of the scroll.
 *
 * Each stage is a box with its own edge, and the edge of the one you are in
 * is drawn heavily while the other four are barely there. That is the whole
 * device: on a page five screens long, "which of these am I in" has to be
 * answerable from any pixel of it, and a heading at the top of a section is
 * only an answer while the heading is on screen.
 *
 * The stage's own header is sticky within its box, so the name, the counts
 * and the one action follow you down it and stop at its bottom edge — which
 * is itself the signal that the stage has ended.
 */
export function Stage({
  id,
  title,
  lede,
  summary,
  action,
  notices,
  working,
  children,
}: {
  id: StageId;
  title: string;
  /** One line on what this stage is for, in the user's words. Shown once,
   *  under the title, rather than repeated on every control below it. */
  lede: string;
  /** The state of this stage right now — counts, readiness. */
  summary?: ReactNode;
  action?: ReactNode;
  notices?: ReactNode;
  /** What is being made right now, if this stage is the one making it.
   *
   *  While it is set, this stage's contents are held behind it: what is on
   *  screen is about to be replaced wholesale, so leaving it editable
   *  invites edits that are about to be thrown away, and leaving it looking
   *  ordinary makes a job that takes a minute look like a button that did
   *  nothing. */
  working?: string | null;
  children: ReactNode;
}) {
  const current = useContext(CurrentStageContext) === id;

  return (
    <section
      id={id}
      // No `overflow-hidden` here, however much the rounded corners want it:
      // it would make this box a scroll container and the sticky header
      // inside would stop sticking.
      //
      // Anchored jumps have to clear the transport bar, whose real height
      // the bar itself publishes — it wraps at narrow widths and grows while
      // a job runs, so the 7rem this used to guess was only ever right at
      // one size.
      className={`scroll-mt-(--transport-h) rounded-xl border transition-colors ${
        current ? "border-strong" : "border-subtle/60"
      }`}
      aria-labelledby={`${id}-title`}
    >
      <div
        className={`sticky z-10 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 rounded-t-xl border-b px-5 py-3 backdrop-blur transition-colors ${
          current ? "border-strong bg-panel/95" : "border-subtle/60 bg-background/95"
        }`}
        style={{ top: "var(--transport-h, 87px)" }}
      >
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2
            id={`${id}-title`}
            className={`text-xl tracking-tight transition-colors ${
              current ? "font-semibold text-foreground" : "font-medium text-muted"
            }`}
          >
            {title}
          </h2>
          {summary && <div className="text-sm text-muted">{summary}</div>}
        </div>
        {action && <div className="flex flex-wrap items-center gap-3">{action}</div>}
      </div>

      <div className="px-5 pt-4 pb-8">
        {/* The lede is not in the sticky header: it is read once, at the top,
            and pinning a sentence of explanation to the window forever would
            cost a line of the viewport to say nothing new. */}
        <p className="max-w-prose text-sm text-body">{lede}</p>

        {notices && <div className="mt-4 flex flex-col gap-3">{notices}</div>}

        <div className="mt-6">
          {working && (
            // Sticky, not pinned to the top of the section: a stage can be
            // several screens long, and a message that only exists at the
            // top of it is invisible from exactly the place the user pressed
            // the button. It sits under the transport bar and this stage's
            // own header, and rides down the whole stage.
            <div
              className="sticky z-10 mb-4 flex justify-center"
              style={{ top: "calc(var(--transport-h, 87px) + 4rem)" }}
            >
              <p
                role="status"
                aria-live="polite"
                className="flex items-center gap-2.5 rounded-lg border border-subtle bg-panel px-4 py-3 text-sm text-foreground shadow-lg"
              >
                <span
                  aria-hidden
                  className="size-1.5 shrink-0 rounded-full bg-signal motion-safe:animate-pulse"
                />
                {working}
              </p>
            </div>
          )}

          <div
            aria-busy={Boolean(working)}
            className={working ? "pointer-events-none select-none opacity-25" : undefined}
            // Held behind the veil rather than unmounted: seeing what is
            // about to be replaced, greyed, says more than an empty box or a
            // skeleton of a shape the result may not even have.
            inert={working ? true : undefined}
          >
            {children}
          </div>
        </div>
      </div>
    </section>
  );
}

/** A thing the user should look at: something went out of date, or a step
 *  has to happen before another one can. */
export function Notice({
  tone = "warn",
  children,
}: {
  tone?: "warn" | "quiet";
  children: ReactNode;
}) {
  const skin =
    tone === "warn"
      ? "border-warn-border bg-warn text-warn-foreground"
      : "border-subtle bg-panel text-body";
  return (
    <p role="status" className={`rounded border ${skin} px-4 py-3 text-sm`}>
      {children}
    </p>
  );
}

/** Nothing here yet, and what to do about it. An empty stage is an
 *  invitation to act rather than a report that something is missing. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}
