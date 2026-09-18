"use client";

import { useCallback, useState } from "react";
import { StaleBadge } from "@/components/StaleBadge";
import { CAMERA_ANGLES, DIALOGUE_KINDS, updateShot, type Shot } from "@/lib/api";
import { useAutosave } from "@/lib/useAutosave";

const FIELD =
  "w-full rounded border border-subtle p-2 text-sm outline-none focus:border-edge-focus";

export function ShotCard({
  meldId,
  shot: initial,
  onSaved,
}: {
  meldId: string;
  shot: Shot;
  onSaved: (shot: Shot) => void;
}) {
  // The card owns its shot after mount. The parent's copy is the value it
  // loaded with; re-syncing from the parent on every refetch would yank
  // half-typed text out from under the user.
  const [shot, setShot] = useState(initial);

  // The whole shot is the autosave value. useAutosave compares by identity,
  // so this only fires when `edit` below actually produces a new object —
  // a re-render with no change does not look like an edit.
  const onSave = useCallback(
    async (next: Shot) => {
      const saved = await updateShot(meldId, next.id, {
        image_prompt: next.image_prompt,
        motion_prompt: next.motion_prompt,
        duration_sec: next.duration_sec,
        camera: next.camera,
        dialogue_kind: next.dialogue_kind,
        speech_text: next.speech_text,
      });
      onSaved(saved);
    },
    [meldId, onSaved],
  );

  const { status } = useAutosave({ value: shot, onSave, delayMs: 800 });

  const edit = <K extends keyof Shot>(field: K, value: Shot[K]) =>
    setShot((current) => ({ ...current, [field]: value }));

  return (
    <li className="rounded border border-subtle p-4">
      <div className="mb-3 flex items-center gap-2 text-xs text-muted">
        <span>Shot {shot.order_index + 1}</span>
        {shot.state === "stale" && <StaleBadge />}
        <span className="ml-auto" role="status" aria-live="polite">
          {status === "saving" && "Saving…"}
          {status === "saved" && "Saved"}
          {status === "error" && <span className="text-danger">Couldn&apos;t save</span>}
        </span>
      </div>

      <label className="mb-2 block">
        <span className="mb-1 block text-xs text-muted">
          Image prompt — what the frame looks like
        </span>
        <textarea
          value={shot.image_prompt}
          onChange={(e) => edit("image_prompt", e.target.value)}
          rows={2}
          className={FIELD}
        />
      </label>

      <label className="mb-2 block">
        <span className="mb-1 block text-xs text-muted">
          Motion prompt — what moves within it
        </span>
        <textarea
          value={shot.motion_prompt}
          onChange={(e) => edit("motion_prompt", e.target.value)}
          rows={2}
          className={FIELD}
        />
      </label>

      <div className="mb-2 grid grid-cols-3 gap-2">
        <label>
          <span className="mb-1 block text-xs text-muted">Seconds (max 5)</span>
          <input
            type="number"
            min="0.5"
            max="5"
            step="0.25"
            value={shot.duration_sec}
            // Kept as the string the user typed. The API takes a decimal
            // string, and a round trip through Number would turn 4.05 into
            // 4.050000000000001 and make the shot look edited.
            onChange={(e) => edit("duration_sec", e.target.value)}
            className={FIELD}
          />
        </label>
        <label>
          <span className="mb-1 block text-xs text-muted">Camera</span>
          <select
            value={shot.camera}
            onChange={(e) => edit("camera", e.target.value as Shot["camera"])}
            className={FIELD}
          >
            {CAMERA_ANGLES.map((angle) => (
              <option key={angle} value={angle}>
                {angle}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="mb-1 block text-xs text-muted">Dialogue</span>
          <select
            value={shot.dialogue_kind}
            onChange={(e) => edit("dialogue_kind", e.target.value as Shot["dialogue_kind"])}
            className={FIELD}
          >
            {DIALOGUE_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {kind.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
      </div>

      {shot.dialogue_kind !== "none" && (
        <label className="block">
          <span className="mb-1 block text-xs text-muted">Spoken over this shot</span>
          <textarea
            value={shot.speech_text}
            onChange={(e) => edit("speech_text", e.target.value)}
            rows={2}
            className={FIELD}
          />
        </label>
      )}
    </li>
  );
}
