"use client";

import { useCallback, useState } from "react";
import { RowControls } from "@/components/RowControls";
import { ShotCard } from "@/components/ShotCard";
import { StaleBadge } from "@/components/StaleBadge";
import {
  addShot,
  deleteShot,
  moveShot,
  updateScene,
  type SceneWithShots,
  type Shot,
} from "@/lib/api";
import { useAutosave } from "@/lib/useAutosave";

const FIELD =
  "w-full rounded border border-subtle p-2 text-sm outline-none focus:border-edge-focus";

const LABEL = "mb-1 block text-xs text-muted";

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
  /** Reload the whole script. Adding, deleting or moving a shot renumbers
   *  its siblings and can mark work elsewhere stale, so the page refetches
   *  rather than this card trying to reproduce the server's arithmetic. */
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
  const [open, setOpen] = useState(true);

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

  // A shot saving its own text is news the page above needs: rewording a
  // shot marks its still stale, and the badge for that is two stages down
  // the same scroll.
  const onShotSaved = useCallback(
    (saved: Shot) =>
      onSaved({
        ...scene,
        ...fields,
        shots: scene.shots.map((shot) => (shot.id === saved.id ? saved : shot)),
      }),
    [scene, fields, onSaved],
  );

  const shotAction = (run: () => Promise<unknown>) => () => {
    void run().then(onStructureChanged, onStructureChanged);
  };

  const shotCount = scene.shots.length;

  return (
    <li className="rounded-lg border border-subtle bg-panel">
      <div className="flex items-center gap-2 px-4 py-3">
        <button
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
          aria-label={`${open ? "Collapse" : "Expand"} scene ${scene.order_index + 1}`}
          className="text-muted hover:text-foreground"
        >
          <span aria-hidden className="inline-block w-3 text-xs">
            {open ? "▾" : "▸"}
          </span>
        </button>

        <span className="font-mono text-xs text-faint">Scene {scene.order_index + 1}</span>

        {/* Collapsed, the heading is all there is to identify the scene by,
            so it moves into the bar. Open, it is an editable field below and
            repeating it here would be two of the same thing. */}
        {!open && (
          <span className="truncate font-mono text-xs font-semibold uppercase">
            {fields.heading || "Untitled scene"}
          </span>
        )}

        {/* The separator is not decoration. Without it these two run
            together into "Scene 27 shots" for a screen reader and for
            anyone reading quickly — two numbers with nothing between them. */}
        <span className="text-faint" aria-hidden>
          ·
        </span>
        <span className="mr-auto font-mono text-xs tabular-nums text-faint">
          {shotCount} {shotCount === 1 ? "shot" : "shots"}
        </span>

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
          confirmMessage={`Delete this scene and its ${shotCount} shot${
            shotCount === 1 ? "" : "s"
          }? Everything generated from them goes too, and credits already spent are not refunded.`}
        />
      </div>

      {open && (
        <>
          {/* Every field is labelled. They used to carry a placeholder and
              nothing else, which reads fine on an empty scene and tells you
              nothing at all once the model has filled all four in — the one
              state every scene here is actually in. */}
          <div className="border-t border-subtle px-4 py-4">
            <label className="mb-3 block">
              <span className={LABEL}>Scene heading</span>
              <input
                value={fields.heading}
                onChange={(e) => edit("heading", e.target.value)}
                placeholder="INT. KITCHEN — NIGHT"
                className={`${FIELD} font-mono font-semibold uppercase`}
              />
            </label>

            <div className="mb-3 grid grid-cols-2 gap-3">
              <label className="block">
                <span className={LABEL}>Location</span>
                <input
                  value={fields.location}
                  onChange={(e) => edit("location", e.target.value)}
                  placeholder="Interior — bell foundry"
                  className={FIELD}
                />
              </label>
              <label className="block">
                <span className={LABEL}>Time of day</span>
                <input
                  value={fields.time_of_day}
                  onChange={(e) => edit("time_of_day", e.target.value)}
                  placeholder="Night"
                  className={FIELD}
                />
              </label>
            </div>

            <label className="block">
              <span className={LABEL}>What happens in this scene</span>
              <textarea
                value={fields.summary}
                onChange={(e) => edit("summary", e.target.value)}
                rows={2}
                className={FIELD}
              />
            </label>
          </div>

          {/* The shots sit behind their own rule and on the page background
              rather than the scene's panel: a scene is one thing and its
              shots are another, and before this they ran together as one
              undifferentiated column of inputs. */}
          <div className="rounded-b-lg border-t border-subtle bg-background px-4 py-4">
            <h4 className="mb-3 font-mono text-xs text-faint">
              {shotCount} {shotCount === 1 ? "shot" : "shots"} in this scene
            </h4>

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
                  canMoveDown={index < shotCount - 1}
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
          </div>
        </>
      )}
    </li>
  );
}
