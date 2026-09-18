"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Notice, Stage } from "@/components/studio/Stage";
import { ApiError, saveStory, type MeldDetail } from "@/lib/api";
import { useAutosave, type SaveStatus } from "@/lib/useAutosave";

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  unsaved: "Unsaved changes",
  saving: "Saving…",
  saved: "Saved",
  error: "Couldn't save — check your connection",
};

export function StoryStage({ meldId, meld }: { meldId: string; meld: MeldDetail }) {
  const [text, setText] = useState(meld.story?.text ?? "");
  const [conflict, setConflict] = useState(false);

  // The hash the server last confirmed. A ref, not state: changing it must
  // not re-render, and onSave must see the newest value rather than one
  // captured in a stale closure.
  const contentHashRef = useRef<string | undefined>(meld.story?.content_hash);

  // The studio refetches every stage after any generation, so `meld` can
  // arrive again while the user is mid-sentence. Adopt the server's text
  // only when this editor has nothing of its own to lose — otherwise a
  // refetch triggered by an unrelated stage would overwrite live typing.
  const loadedRef = useRef(false);
  useEffect(() => {
    if (loadedRef.current) return;
    loadedRef.current = true;
    contentHashRef.current = meld.story?.content_hash;
  }, [meld]);

  const onSave = useCallback(
    async (value: string) => {
      try {
        const saved = await saveStory(meldId, value, contentHashRef.current);
        contentHashRef.current = saved.content_hash;
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // Almost always this user's own second tab. Stop editing rather
          // than racing it: the textarea goes read-only and the notice
          // explains why. Rethrown so useAutosave still reports "error" and
          // keeps the close warning armed, because this text really is
          // unsaved.
          setConflict(true);
        }
        throw err;
      }
    },
    [meldId],
  );

  const { status } = useAutosave({ value: text, onSave, delayMs: 800, enabled: true });
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;

  return (
    <Stage
      id="story"
      title="Story"
      lede="Write it however you like — a paragraph or a few pages. Everything below is made from this."
      summary={
        <span className="font-mono tabular-nums">
          {words} {words === 1 ? "word" : "words"}
        </span>
      }
      action={
        <span
          className={`text-sm ${status === "error" ? "text-danger" : "text-muted"}`}
          role="status"
          aria-live="polite"
        >
          {STATUS_TEXT[status]}
        </span>
      }
      notices={
        conflict ? (
          <Notice>
            <strong className="font-medium">This Meld was edited somewhere else.</strong> Your
            changes here have not been saved. Copy anything you want to keep, then{" "}
            <button onClick={() => window.location.reload()} className="underline">
              reload
            </button>{" "}
            to get the latest version.
          </Notice>
        ) : null
      }
    >
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={conflict}
        placeholder="Elena climbed the lighthouse stairs at dusk…"
        className="min-h-80 w-full resize-y rounded border border-subtle p-6 font-serif text-lg leading-relaxed outline-none focus:border-edge-focus disabled:bg-panel disabled:text-muted"
      />
    </Stage>
  );
}
