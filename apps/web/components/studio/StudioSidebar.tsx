"use client";

import Link from "next/link";
import { stageHref, stageNumber, type StageId } from "@/components/studio/stages";

export type StageMark = {
  id: StageId;
  label: string;
  /** The stage in a few words: "250 words", "18 shots · 1:07". */
  status: string;
  /** Everything this stage makes has been made. Drives the tick. */
  done: boolean;
  /** How many of its things are out of date. */
  stale: number;
};

export type BudgetRow = { label: string; credits: number };

/**
 * The column beside every stage: the pipeline, then what is left to pay for.
 *
 * The pipeline is the stage nav -- each stage with its number, its state in
 * a few words and a tick once it is finished. The budget below it answers
 * the question the credit balance alone cannot: is that enough to finish?
 */
export function StudioSidebar({
  meldId,
  stages,
  current,
  budget,
  balance,
}: {
  meldId: string;
  stages: StageMark[];
  current: StageId;
  /** Null when there is nothing to cost yet -- no shots, no budget. */
  budget: BudgetRow[] | null;
  balance: number | null;
}) {
  const total = budget?.reduce((sum, row) => sum + row.credits, 0) ?? 0;
  const short = balance !== null && balance < total;

  return (
    <aside className="sticky top-14 hidden h-[calc(100vh-3.5rem)] flex-col gap-6 overflow-auto border-r border-subtle px-4 py-5 lg:flex">
      <nav aria-label="Stages" className="flex flex-col gap-1.5">
        <span className="kicker px-2.5 pb-1.5 text-faint">Pipeline</span>
        {stages.map((stage) => {
          const active = stage.id === current;
          return (
            <Link
              key={stage.id}
              href={stageHref(meldId, stage.id)}
              aria-current={active ? "step" : undefined}
              className={`grid grid-cols-[28px_1fr_auto] items-center gap-2 border p-2.5 ${
                active ? "border-accent bg-accent-tint" : "border-transparent hover:bg-accent-tint"
              }`}
            >
              <span
                className={`font-heading text-[15px] tabular-nums ${active ? "text-accent-ink" : "text-faint"}`}
              >
                {stageNumber(stage.id)}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="font-heading text-[17px] leading-tight font-semibold">
                  {stage.label}
                </span>
                <span className="truncate text-xs text-muted">
                  {stage.status}
                  {stage.stale > 0 && (
                    <span className="text-warn-foreground"> · {stage.stale} out of date</span>
                  )}
                </span>
              </span>
              {stage.done && !active && (
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  className="text-accent"
                  aria-label="done"
                >
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              )}
            </Link>
          );
        })}
      </nav>

      {budget && (
        <section aria-label="To finish this film" className="blueprint flex flex-col gap-2.5 p-3.5">
          <span className="kicker text-faint">To finish this film</span>
          {budget.map((row) => (
            <div key={row.label} className="flex justify-between gap-2 text-[13px]">
              <span className="text-muted">{row.label}</span>
              <span className="tabular-nums">{row.credits}</span>
            </div>
          ))}
          <div className="flex justify-between border-t border-subtle pt-2 font-heading text-base font-semibold">
            <span>Total</span>
            <span className="tabular-nums">{total} cr</span>
          </div>
          {balance !== null && (
            <>
              <div className="h-1 bg-panel" aria-hidden>
                <div
                  className="h-full bg-accent"
                  style={{ width: `${Math.min(100, Math.round((balance / Math.max(total, 1)) * 100))}%` }}
                />
              </div>
              <span className={`text-xs ${short ? "text-warn-foreground" : "text-muted"}`}>
                {total === 0
                  ? "Nothing left to pay for."
                  : short
                    ? `You have ${balance}. ${total - balance} more needed.`
                    : `You have ${balance}. Covered.`}
              </span>
            </>
          )}
        </section>
      )}
    </aside>
  );
}
