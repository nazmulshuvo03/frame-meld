"use client";

import { useEffect, useRef, useState } from "react";

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
  enabled?: boolean;
}): { status: SaveStatus } {
  const [status, setStatus] = useState<SaveStatus>("idle");

  // The value as the server last confirmed it. Comparing against this
  // rather than the previous render's value means an in-flight save that
  // resolves late cannot mark stale text as "saved".
  const savedRef = useRef<T>(value);
  const firstRunRef = useRef(true);

  // The newest value the hook has seen, used to tell whether a completed
  // save is still current.
  const latestRef = useRef<T>(value);

  useEffect(() => {
    // Nothing to compare against until the caller says the value is real.
    // Without this the first run captures a placeholder — the empty string
    // a page holds before its fetch resolves — and the real text arriving
    // one render later looks like a user edit, saving it straight back and
    // bumping the Meld's updated_at.
    if (!enabled) return;

    latestRef.current = value;

    // Skip the initial mount: the value just came from the server, so
    // saving it back immediately would be a pointless write.
    if (firstRunRef.current) {
      firstRunRef.current = false;
      savedRef.current = value;
      return;
    }

    if (value === savedRef.current) {
      // The text matches what the server has — the user undid their edit.
      // Report it as saved rather than leaving a stale "unsaved", which
      // would nag on close for work that is not actually pending.
      setStatus((current) => (current === "idle" ? "idle" : "saved"));
      return;
    }

    setStatus("unsaved");

    const timer = setTimeout(async () => {
      const attempted = value;
      setStatus("saving");
      try {
        await onSave(attempted);
        savedRef.current = attempted;
        // Only claim "saved" if this is still the newest text. If the user
        // typed while the request was in flight, a pending timer will save
        // the newer value; saying "saved" here would tell them their latest
        // work is safe when it is not.
        if (attempted === latestRef.current) {
          setStatus("saved");
        }
      } catch {
        setStatus("error");
      }
    }, delayMs);

    // Every keystroke clears the pending timer, so only the last one in a
    // burst actually fires.
    return () => clearTimeout(timer);
  }, [value, onSave, delayMs, enabled]);

  // Warn on close while anything is unsaved, in flight, or failed.
  useEffect(() => {
    if (status !== "unsaved" && status !== "saving" && status !== "error") return;

    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [status]);

  return { status };
}
