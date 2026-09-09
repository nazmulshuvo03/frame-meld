"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SaveStatus = "idle" | "unsaved" | "saving" | "saved" | "error";

export function useAutosave<T>({
  value,
  onSave,
  delayMs = 800,
  enabled = true,
}: {
  value: T;
  onSave: (value: T) => Promise<void>;
  delayMs?: number;
  // Expected to be monotonic (e.g. flips false -> true once, when the real
  // value becomes available, and never back). A caller that toggles it
  // back and forth — tab visibility, an online/offline flag — will hit a
  // path that never settles: the false transition runs this effect's
  // cleanup and then bails before any status update. Not reachable from
  // this app's own caller, but the hook is generic, so the next caller
  // should know.
  enabled?: boolean;
}): { status: SaveStatus } {
  const [status, setStatus] = useState<SaveStatus>("idle");

  // The value as the server last confirmed it. Comparing against this
  // rather than the previous render's value means an in-flight save that
  // resolves late cannot mark stale text as "saved".
  const savedRef = useRef<T>(value);
  const firstRunRef = useRef(true);

  // What the server will hold once the queue drains. Distinct from savedRef,
  // which is what it has *confirmed* — those differ for one round trip after
  // every dispatch, and comparing against the wrong one lets the hook report
  // "saved" while a request for different text is still in flight.
  const pendingRef = useRef<T>(value);

  // The newest value the hook has seen, used to tell whether a completed
  // save is still current.
  const latestRef = useRef<T>(value);

  // A completed save that turns out to be stale (see the timer callback's
  // else branch below) bumps this to make the main effect re-evaluate,
  // since neither `value` nor `onSave` necessarily changed.
  const [saveTick, setSaveTick] = useState(0);

  // Kept in a ref so the unmount flush below — which must not re-run on
  // every onSave identity change — always calls the current one.
  const onSaveRef = useRef(onSave);
  useEffect(() => {
    onSaveRef.current = onSave;
  }, [onSave]);

  // Every save — debounced or flushed on unmount — goes through this chain,
  // so at most one request is in flight and they apply in issue order.
  // Without it an unmount flush can race a still-pending debounced save and
  // the server lands on whichever request happens to arrive last, which may
  // be the older text.
  const queueRef = useRef<Promise<unknown>>(Promise.resolve());

  const enqueueSave = useCallback((next: T) => {
    // Both the value and the target are bound here, at issue time. Reading
    // onSaveRef at dispatch time instead would pair a queued value with a
    // re-bound target if the caller's onSave identity changed in between.
    const fn = onSaveRef.current;
    const run = () => fn(next);
    pendingRef.current = next;
    // Chained on both settle paths so one failed save cannot stall the queue.
    queueRef.current = queueRef.current.then(run, run);
    return queueRef.current;
  }, []);

  // Flush on unmount. A client-side navigation does not fire beforeunload,
  // and the debounce cleanup would otherwise discard text typed within the
  // last delayMs without a trace.
  useEffect(() => {
    return () => {
      if (latestRef.current !== pendingRef.current) {
        // Queued rather than fired directly, so it lands after any save
        // already in flight instead of racing it. The catch only suppresses
        // an unhandled rejection — there is no UI left to report to.
        void enqueueSave(latestRef.current).catch(() => {});
      }
    };
  }, [enqueueSave]);

  useEffect(() => {
    // Nothing to compare against until the caller says the value is real.
    // Without this the first run captures whatever placeholder the caller
    // held before its real value became available, and that real value
    // arriving one render later looks like a user edit — saving it
    // straight back for no reason.
    if (!enabled) return;

    latestRef.current = value;

    // Skip the initial run: this value just became available, so saving
    // it back immediately would be a pointless write.
    if (firstRunRef.current) {
      firstRunRef.current = false;
      savedRef.current = value;
      pendingRef.current = value;
      return;
    }

    if (value === pendingRef.current) {
      // The value matches what was last confirmed — the user undid their
      // edit. Report it as saved rather than leaving a stale "unsaved",
      // which would nag on close for work that is not actually pending.
      setStatus((current) => (current === "idle" ? "idle" : "saved"));
      return;
    }

    setStatus("unsaved");

    const timer = setTimeout(async () => {
      const attempted = value;
      setStatus("saving");
      try {
        const link = enqueueSave(attempted);
        await link;
        savedRef.current = attempted;

        if (attempted !== latestRef.current) {
          // The text changed while this was in flight and the effect will
          // not re-run on its own — nudge it.
          setSaveTick((tick) => tick + 1);
        } else if (queueRef.current === link) {
          // Newest text, and nothing queued behind us: genuinely saved.
          setStatus("saved");
        }
        // Otherwise later links are still pending; leave "saving" and let
        // the last of them settle the status. Claiming "saved" here would
        // un-arm the close warning while an older value is still queued.
      } catch {
        // The server never received `attempted`, so what it will hold is
        // still whatever it last confirmed. Without resetting this, the
        // unmount flush would see pendingRef already claiming the failed
        // text and decline to retry it.
        pendingRef.current = savedRef.current;
        setStatus("error");
      }
    }, delayMs);

    // Every keystroke clears the pending timer, so only the last one in a
    // burst actually fires.
    return () => clearTimeout(timer);
  }, [value, onSave, delayMs, enabled, saveTick, enqueueSave]);

  // Warn on close while anything is unsaved, in flight, or failed.
  useEffect(() => {
    if (status !== "unsaved" && status !== "saving" && status !== "error") return;

    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [status]);

  return { status };
}
