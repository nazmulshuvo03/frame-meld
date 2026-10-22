"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Notice, Stage } from "@/components/studio/Stage";
import { timecode } from "@/components/studio/stages";
import { ApiError, saveStory, type MeldDetail, type Story } from "@/lib/api";
import { useAutosave, type SaveStatus } from "@/lib/useAutosave";

const STATUS_TEXT: Record<SaveStatus, string> = {
  idle: "",
  unsaved: "Unsaved changes",
  saving: "Saving…",
  saved: "Saved",
  error: "Couldn't save — check your connection",
};

export function StoryStage({
  meldId,
  meld,
  onSaved,
}: {
  meldId: string;
  meld: MeldDetail;
  /** Told what the server confirmed, so the caller's copy of the Meld
   *  follows the editor. */
  onSaved?: (story: Story) => void;
}) {
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
  // A ref so a caller passing a fresh arrow every render does not change
  // `onSave`, which would restart the autosave debounce on each of its renders.
  const onSavedRef = useRef(onSaved);
  useEffect(() => {
    onSavedRef.current = onSaved;
  }, [onSaved]);

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
        onSavedRef.current?.(saved);
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
      lede="Write it however you like — a paragraph or a few pages. Everything after this step is made from this text."
      summary="Editing it later marks the script out of date but keeps your shots."
      action={
        <>
          <span
            className={`self-center text-sm ${status === "error" ? "text-danger" : "text-muted"}`}
            role="status"
            aria-live="polite"
          >
            {STATUS_TEXT[status]}
          </span>
          <div className="grid grid-cols-2 border border-subtle">
            <div className="border-r border-subtle px-4 py-2">
              <p className="font-heading text-[26px] leading-tight font-semibold tabular-nums">
                {words}
              </p>
              <p className="text-xs text-muted">{words === 1 ? "word" : "words"}</p>
            </div>
            <div className="px-4 py-2">
              {/* Read aloud at about 2.6 words a second -- the pace the
                  narration is voiced at, near enough to plan a film by. */}
              <p className="font-heading text-[26px] leading-tight font-semibold tabular-nums">
                ~{timecode(words / 2.6)}
              </p>
              <p className="text-xs text-muted">est. runtime</p>
            </div>
          </div>
        </>
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
      <div className="blueprint bg-sheet focus-within:border-edge-focus">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={conflict}
          aria-label="Story"
          placeholder="Elena climbed the lighthouse stairs at dusk…"
          className="block min-h-[calc(100vh-340px)] w-full resize-y px-10 py-8 text-[17px] leading-[1.75] outline-none focus-visible:outline-none disabled:text-muted"
        />
      </div>
    </Stage>
  );
}
