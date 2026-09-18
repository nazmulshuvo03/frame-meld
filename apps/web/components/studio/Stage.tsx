"use client";

import type { ReactNode } from "react";
import type { StageId } from "@/components/studio/TransportBar";

/**
 * One of the five stages, as a band across the scroll.
 *
 * Deliberately not a card. Cards would chop one continuous process into
 * five identical rounded boxes and say nothing about the order they happen
 * in; a full-bleed band separated by a rule reads as a sequence, which is
 * what this is. The only card-like things on the page are the ones that
 * hold a single artifact — a character, a shot — because those really are
 * repeated peers.
 */
export function Stage({
  id,
  title,
  lede,
  summary,
  action,
  notices,
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
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      // The sticky transport bar is ~86px; without this an anchored stage
      // lands underneath it and looks like the page jumped to the wrong
      // place.
      className="scroll-mt-28 border-b border-subtle py-10 last:border-b-0"
      aria-labelledby={`${id}-title`}
    >
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 id={`${id}-title`} className="text-xl font-medium tracking-tight">
            {title}
          </h2>
          <p className="mt-1 max-w-prose text-sm text-body">{lede}</p>
          {summary && <div className="mt-2 text-sm text-muted">{summary}</div>}
        </div>
        {action && <div className="flex flex-wrap items-center gap-3">{action}</div>}
      </div>

      {notices && <div className="mt-4 flex flex-col gap-3">{notices}</div>}

      <div className="mt-6">{children}</div>
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
