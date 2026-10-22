"use client";

import { useCallback, useState } from "react";
import { RowControls } from "@/components/RowControls";
import { StaleBadge } from "@/components/StaleBadge";
import { addShot, updateScene, type SceneWithShots } from "@/lib/api";
import { useAutosave } from "@/lib/useAutosave";

/** The four fields of a scene the user can type into.
 *
 *  Deliberately *not* the whole scene. The shots belong to the server: they
 *  are added, deleted and reordered by requests whose answer is a refetch,
 *  and a copy of them held here would go stale the moment one of those
 *  landed — which is exactly what "+ Add a shot" did. Text being typed has
 *  to be local so a refetch cannot yank it; structure must not be, so a
 *  refetch is visible. Splitting them is the whole fix.
 */
type SceneFields = Pick<SceneWithShots, "heading" | "summary" | "location" | "time_of_day">;

/** One scene's own fields, as the Script page's inspector shows them.
 *
 *  Its shots are listed in the table beside it, each edited on its own, so
 *  this card only adds one; it does not list them. */
export function SceneCard({
  meldId,
  scene,
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
  /** Reload the whole script. Adding a shot renumbers its siblings and can
   *  mark work elsewhere stale, so the page refetches rather than this card
   *  trying to reproduce the server's arithmetic. */
  onStructureChanged: () => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
}) {
  const [fields, setFields] = useState<SceneFields>({
    heading: scene.heading,
    summary: scene.summary,
    location: scene.location,
    time_of_day: scene.time_of_day,
  });

  const onSave = useCallback(
    async (next: SceneFields) => {
      const saved = await updateScene(meldId, scene.id, next);
      // The API does not return the scene's shots on a PATCH, so the saved
      // scene is merged into the list this card was handed rather than
      // replacing it — otherwise every field save would blank the shots.
      onSaved({ ...scene, ...saved, shots: scene.shots });
    },
    [meldId, scene, onSaved],
  );

  const { status } = useAutosave({ value: fields, onSave, delayMs: 800 });

  const edit = <K extends keyof SceneFields>(field: K, value: SceneFields[K]) =>
    setFields((current) => ({ ...current, [field]: value }));

  const shotCount = scene.shots.length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <h3 className="text-2xl">Scene {scene.order_index + 1}</h3>
        <span className="text-xs tabular-nums text-faint">
          {shotCount} {shotCount === 1 ? "shot" : "shots"}
        </span>
        {scene.state === "stale" && <StaleBadge />}
        <span className="ml-auto shrink-0 text-xs text-muted" role="status" aria-live="polite">
          {status === "saving" ? "Saving…" : status === "error" ? "Couldn't save" : ""}
        </span>
        <RowControls
          label={`scene ${scene.order_index + 1}`}
          onMove={onMove}
          onDelete={onDelete}
          canMoveUp={canMoveUp}
          canMoveDown={canMoveDown}
          confirmMessage={`Delete this scene and its ${shotCount} shot${
            shotCount === 1 ? "" : "s"
          }? Everything generated from them goes too, and credits already spent are not refunded.`}
        />
      </div>

      {/* Every field is labelled: a placeholder alone tells you nothing once
          the model has filled all four in. */}
      <label className="block">
        <span className="label">Scene heading</span>
        <input
          value={fields.heading}
          onChange={(e) => edit("heading", e.target.value)}
          placeholder="INT. KITCHEN — NIGHT"
          className="input font-heading font-semibold uppercase"
        />
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="label">Location</span>
          <input
            value={fields.location}
            onChange={(e) => edit("location", e.target.value)}
            placeholder="Interior — bell foundry"
            className="input"
          />
        </label>
        <label className="block">
          <span className="label">Time of day</span>
          <input
            value={fields.time_of_day}
            onChange={(e) => edit("time_of_day", e.target.value)}
            placeholder="Night"
            className="input"
          />
        </label>
      </div>

      <label className="block">
        <span className="label">What happens in this scene</span>
        <textarea
          value={fields.summary}
          onChange={(e) => edit("summary", e.target.value)}
          rows={3}
          className="input"
        />
      </label>

      <button
        type="button"
        onClick={() => void addShot(meldId, scene.id).then(onStructureChanged, onStructureChanged)}
        className="btn self-start"
      >
        + Add a shot to this scene
      </button>
    </div>
  );
}
