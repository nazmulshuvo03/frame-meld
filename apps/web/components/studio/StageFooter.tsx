"use client";

import Link from "next/link";
import { STAGES, stageHref, stageIndex, type StageId } from "@/components/studio/stages";

/** Back and Next, pinned to the bottom of the window, with the five stages
 *  as a row of bars between them -- filled up to the one you are on.
 *
 *  Pinned rather than placed at the end of the page: a stage can be several
 *  screens long, and the way to the next one should not depend on how far
 *  down you have scrolled. */
export function StageFooter({ meldId, current }: { meldId: string; current: StageId }) {
  const index = stageIndex(current);
  const previous = STAGES[index - 1];
  const next = STAGES[index + 1];

  return (
    <footer className="sticky bottom-0 z-20 grid grid-cols-[1fr_auto_1fr] items-center gap-4 border-t border-subtle bg-background px-5 py-3 lg:px-10">
      <div>
        {previous && (
          <Link href={stageHref(meldId, previous.id)} className="btn">
            <span aria-hidden>←</span>
            <span>
              <span className="sr-only">Back to </span>
              {previous.label}
            </span>
          </Link>
        )}
      </div>

      <div
        className="flex gap-1.5"
        role="img"
        aria-label={`Stage ${index + 1} of ${STAGES.length}`}
      >
        {STAGES.map((stage, i) => (
          <span key={stage.id} className={`h-[3px] w-[22px] ${i <= index ? "bg-accent" : "bg-subtle"}`} />
        ))}
      </div>

      <div className="flex justify-end">
        {next && (
          <Link href={stageHref(meldId, next.id)} className="btn btn-primary blueprint">
            Next: {next.label} <span aria-hidden>→</span>
          </Link>
        )}
      </div>
    </footer>
  );
}
