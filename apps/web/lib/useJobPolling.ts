"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, cancelJob, getJob, type Job } from "@/lib/api";

const INTERVAL_MS = 1500;

/** Poll one job until it stops moving.
 *
 *  Polling rather than websockets: a script takes seconds, the page is open
 *  while it runs, and a socket would be a second transport to operate for
 *  one screen. Revisit at M6, where renders take minutes.
 */
export function useJobPolling({
  meldId,
  jobId,
  onSettled,
}: {
  meldId: string;
  jobId: string | null;
  onSettled: (job: Job) => void;
}): { job: Job | null; cancel: () => Promise<void>; cancelError: string | null } {
  const [job, setJob] = useState<Job | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);

  // Kept in a ref so a caller that rebuilds the callback each render does
  // not restart the interval — which would poll far faster than intended.
  const onSettledRef = useRef(onSettled);
  useEffect(() => {
    onSettledRef.current = onSettled;
  }, [onSettled]);

  useEffect(() => {
    // No setJob(null) here: once jobId clears, the caller has already
    // received the settled job via onSettled and stops treating this hook
    // as busy (see ScriptPage's `busy = jobId !== null`), so a stale `job`
    // value is simply never read again. Resetting it would be a
    // synchronous setState with nothing downstream depending on it.
    if (!jobId) return;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const next = await getJob(meldId, jobId);
        if (cancelled) return;
        setJob(next);
        if (next.state === "queued" || next.state === "running") {
          timer = setTimeout(tick, INTERVAL_MS);
        } else {
          onSettledRef.current(next);
        }
      } catch (err) {
        if (cancelled) return;
        // A 404 means the job is gone; anything else may be transient. Keep
        // polling through the transient case rather than stranding the page
        // on a single blip, but stop on 404 or there is nothing to find.
        if (err instanceof ApiError && err.status === 404) return;
        timer = setTimeout(tick, INTERVAL_MS);
      }
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [meldId, jobId]);

  const cancel = useCallback(async () => {
    if (!jobId) return;
    setCancelError(null);
    try {
      const stopped = await cancelJob(meldId, jobId);
      setJob(stopped);
      // Settle it here rather than waiting for the next poll: the caller
      // clears its busy flag from onSettled, and a user who just pressed
      // Cancel should not watch the button say "Queued…" for another
      // second and a half.
      onSettledRef.current(stopped);
    } catch (err) {
      // A 409 means a worker claimed it between the render and the click.
      // Not an error the user did anything about — the job simply finishes.
      setCancelError(
        err instanceof ApiError ? err.message : "Couldn't stop that. It may already be running.",
      );
    }
  }, [meldId, jobId]);

  return { job, cancel, cancelError };
}
