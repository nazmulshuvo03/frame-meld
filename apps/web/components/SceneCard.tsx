"use client";

import { useCallback, useState } from "react";
import { RowControls } from "@/components/RowControls";
import { ShotCard } from "@/components/ShotCard";
import { StaleBadge } from "@/components/StaleBadge";
import { addShot, deleteShot, moveShot, updateScene, type SceneWithShots, type Shot } from "@/lib/api";
import { useAutosave } from "@/lib/useAutosave";

const FIELD =
  "w-full rounded border border-subtle p-2 text-sm outline-none focus:border-edge-focus";

export function SceneCard({
  meldId,
  scene: initial,
  onSaved,
  onMove,
  onDelete,
  onStructureChanged,
  canMoveUp,
  canMoveDown,
}: {
  meldId: string;
  scene: SceneWithShots;
  onSaved: (scene: SceneWithShots) => void;
  onMove: (delta: -1 | 1) => void;
  onDelete: () => void;
  /** Reload the whole script. Adding, deleting or moving a shot renumbers
   *  its siblings and can mark work elsewhere stale, so the page refetches
   *  rather than this card trying to reproduce the server's arithmetic. */
  onStructureChanged: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  const [scene, setScene] = useState(initial);

  const onSave = useCallback(
    async (next: SceneWithShots) => {
      const saved = await updateScene(meldId, next.id, {
        heading: next.heading,
        summary: next.summary,
        location: next.location,
        time_of_day: next.time_of_day,
      });
      // The API does not return the scene's shots on a PATCH, so the saved
      // scene is merged into what this card already holds rather than
      // replacing it — otherwise every field save would blank the shot list.
      onSaved({ ...next, ...saved, shots: next.shots });
    },
    [meldId, onSaved],
  );

  const { status } = useAutosave({ value: scene, onSave, delayMs: 800 });

  const edit = <K extends keyof SceneWithShots>(field: K, value: SceneWithShots[K]) =>
    setScene((current) => ({ ...current, [field]: value }));

  const onShotSaved = useCallback(
    (saved: Shot) =>
      setScene((current) => ({
        ...current,
        shots: current.shots.map((shot) => (shot.id === saved.id ? saved : shot)),
      })),
    [],
  );

  const shotAction = (run: () => Promise<unknown>) => () => {
    void run().then(onStructureChanged, onStructureChanged);
  };

  return (
    <li>
      <div className="mb-2 flex items-center gap-2">
        <input
          value={scene.heading}
          onChange={(e) => edit("heading", e.target.value)}
          placeholder="INT. KITCHEN — NIGHT"
          className={`${FIELD} font-mono font-semibold uppercase`}
        />
        {scene.state === "stale" && <StaleBadge />}
        <span className="shrink-0 text-xs text-muted" role="status" aria-live="polite">
          {status === "saving" ? "Saving…" : status === "error" ? "Couldn't save" : ""}
        </span>
        <RowControls
          label={`scene ${scene.order_index + 1}`}
          onMove={onMove}
          onDelete={onDelete}
          canMoveUp={canMoveUp}
          canMoveDown={canMoveDown}
          confirmMessage={`Delete this scene and its ${scene.shots.length} shot${
            scene.shots.length === 1 ? "" : "s"
          }? Everything generated from them goes too, and credits already spent are not refunded.`}
        />
      </div>

      <div className="mb-2 grid grid-cols-2 gap-2">
        <input
          value={scene.location}
          onChange={(e) => edit("location", e.target.value)}
          placeholder="Location"
          className={FIELD}
        />
        <input
          value={scene.time_of_day}
          onChange={(e) => edit("time_of_day", e.target.value)}
          placeholder="Time of day"
          className={FIELD}
        />
      </div>

      <textarea
        value={scene.summary}
        onChange={(e) => edit("summary", e.target.value)}
        placeholder="What happens in this scene"
        rows={2}
        className={`${FIELD} mb-3`}
      />

      <ol className="flex flex-col gap-3">
        {scene.shots.map((shot, index) => (
          <ShotCard
            key={shot.id}
            meldId={meldId}
            shot={shot}
            onSaved={onShotSaved}
            onMove={(delta) => shotAction(() => moveShot(meldId, shot.id, delta))()}
            onDelete={shotAction(() => deleteShot(meldId, shot.id))}
            canMoveUp={index > 0}
            canMoveDown={index < scene.shots.length - 1}
          />
        ))}
      </ol>

      <button
        type="button"
        onClick={shotAction(() => addShot(meldId, scene.id))}
        className="mt-3 rounded border border-subtle px-3 py-1.5 text-xs text-muted hover:bg-panel"
      >
        + Add a shot
      </button>
    </li>
  );
}
