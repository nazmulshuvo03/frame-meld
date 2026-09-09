"use client";

import { useEffect, useRef, useState } from "react";

export type SaveStatus = "idle" | "unsaved" | "saving" | "saved" | "error";

export function useAutosave<T>({
  value,
  onSave,
  delayMs = 800,
}: {
  value: T;
  onSave: (value: T) => Promise<void>;
  delayMs?: number;
}): { status: SaveStatus } {
  const [status, setStatus] = useState<SaveStatus>("idle");

  // The value as the server last confirmed it. Comparing against this
  // rather than the previous render's value means an in-flight save that
  // resolves late cannot mark stale text as "saved".
  const savedRef = useRef<T>(value);
  const firstRunRef = useRef(true);

  useEffect(() => {
    // Skip the initial mount: the value just came from the server, so
    // saving it back immediately would be a pointless write.
    if (firstRunRef.current) {
      firstRunRef.current = false;
      savedRef.current = value;
      return;
    }

    if (value === savedRef.current) return;

    setStatus("unsaved");

    const timer = setTimeout(async () => {
      const attempted = value;
      setStatus("saving");
      try {
        await onSave(attempted);
        savedRef.current = attempted;
        setStatus("saved");
      } catch {
        setStatus("error");
      }
    }, delayMs);

    // Every keystroke clears the pending timer, so only the last one in a
    // burst actually fires.
    return () => clearTimeout(timer);
  }, [value, onSave, delayMs]);

  // Warn on close while a save is pending or failed, so work is not lost.
  useEffect(() => {
    if (status !== "unsaved" && status !== "error") return;

    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [status]);

  return { status };
}
