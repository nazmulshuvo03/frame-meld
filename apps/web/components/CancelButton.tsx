"use client";

import type { Job } from "@/lib/api";

/** "Stop" next to whatever is currently running.
 *
 *  Shown only while a job is queued. Once a worker has claimed it the
 *  provider call is in flight and the money is committed, so there is
 *  nothing honest to offer — the API refuses it with a 409 and this button
 *  gets out of the way rather than lying about what it can do.
 */
export function CancelButton({
  job,
  onCancel,
  error,
}: {
  job: Job | null;
  onCancel: () => void;
  error: string | null;
}) {
  // No job yet is the first poll not having landed, which is still queued
  // as far as anyone watching is concerned. `!job` rather than `=== null`
  // because a poll that answered with nothing lands here as undefined, and
  // a Stop button is not worth a crash.
  const stoppable = !job || job.state === "queued";
  if (!stoppable) return null;

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={onCancel}
        className="rounded border border-subtle px-3 py-2 text-sm text-muted hover:bg-panel"
      >
        Stop
      </button>
      {error && (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      )}
    </div>
  );
}
