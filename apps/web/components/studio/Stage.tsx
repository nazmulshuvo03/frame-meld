"use client";

import type { ReactNode } from "react";
import { stageNumber, type StageId } from "@/components/studio/stages";

/**
 * One of the five stages, as a page of its own.
 *
 * The header (step, title, what this stage is for, its counts and its one
 * action) sits at the top of the page; the stage's work fills the rest. The
 * studio's own header and Back/Next footer frame it, so this component only
 * has to say what *this* page is.
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
  /** The state of this stage right now -- counts, readiness. */
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
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex flex-col">
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div className="max-w-2xl">
          <p className="kicker text-accent-ink">Step {stageNumber(id)} of 05</p>
          <h2 id={`${id}-title`} className="mt-0.5 mb-1.5 text-[42px]">
            {title}
          </h2>
          <p className="text-pretty text-body">{lede}</p>
          {summary && <div className="mt-1.5 text-sm tabular-nums text-muted">{summary}</div>}
        </div>
        {action && <div className="flex flex-wrap items-start gap-2.5">{action}</div>}
      </header>

      {notices && <div className="mt-5 flex flex-col gap-3">{notices}</div>}

      <div className="mt-6">
        {working && (
          // Sticky under the studio's header: a stage can be several screens
          // long, and a message that only exists at the top of it is
          // invisible from exactly the place the user pressed the button.
          <div className="sticky top-18 z-10 mb-4 flex justify-center">
            <p
              role="status"
              aria-live="polite"
              className="blueprint flex items-center gap-2.5 bg-background px-4 py-3 text-sm text-foreground shadow-lg"
            >
              <span aria-hidden className="size-1.5 shrink-0 bg-signal motion-safe:animate-pulse" />
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
      : "border-subtle text-body";
  return (
    <p role="status" className={`border-l-2 ${skin} px-4 py-3 text-sm`}>
      {children}
    </p>
  );
}

/** Nothing here yet, and what to do about it. An empty stage is an
 *  invitation to act rather than a report that something is missing. */
export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}
