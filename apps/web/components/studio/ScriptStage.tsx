"use client";

import { Notice, Empty, Stage } from "@/components/studio/Stage";
import { SceneCard } from "@/components/SceneCard";
import { addScene, deleteScene, moveScene, type ScriptView } from "@/lib/api";
import type { Studio } from "@/lib/useStudio";

export function ScriptStage({ studio, script }: { studio: Studio; script: ScriptView }) {
  const { id, busy, job } = studio;
  const shotCount = script.scenes.reduce((total, scene) => total + scene.shots.length, 0);
  const writing = busy && job?.kind === "generate_script";

  return (
    <Stage
      id="script"
      title="Script"
      lede="Your story broken into scenes and shots. Edit anything here — the wording of a shot is what gets drawn."
      summary={
        script.scenes.length > 0 ? (
          <span className="font-mono tabular-nums">
            {script.scenes.length} {script.scenes.length === 1 ? "scene" : "scenes"} ·{" "}
            {shotCount} {shotCount === 1 ? "shot" : "shots"}
          </span>
        ) : null
      }
      action={
        <button
          onClick={studio.writeScript}
          disabled={busy}
          className="rounded bg-accent px-4 py-2 text-sm text-accent-foreground disabled:opacity-50"
        >
          {writing
            ? job?.state === "running"
              ? "Writing…"
              : "Queued…"
            : script.scenes.length
              ? "Write it again"
              : "Write the script"}
        </button>
      }
      notices={
        script.stale_shots > 0 ? (
          <Notice>
            <strong className="font-medium">Your story changed.</strong> {script.stale_shots}{" "}
            {script.stale_shots === 1 ? "shot is" : "shots are"} out of date. Nothing has been
            deleted — write it again when you&apos;re ready, or keep editing by hand.
          </Notice>
        ) : null
      }
    >
      {script.scenes.length === 0 ? (
        <Empty>Write a story above, then come back and turn it into a shot list.</Empty>
      ) : (
        <>
          <ol className="flex flex-col gap-8">
            {script.scenes.map((scene, index) => (
              <SceneCard
                key={scene.id}
                meldId={id}
                scene={scene}
                onSaved={(saved) => {
                  studio.setScript((current) =>
                    current === null
                      ? current
                      : {
                          ...current,
                          scenes: current.scenes.map((s) => (s.id === saved.id ? saved : s)),
                        },
                  );
                  // And refresh what that edit invalidated. Rewording a shot
                  // marks its still stale and the film with it, two stages
                  // further down this same scroll — on five separate pages
                  // that was invisible until you navigated, here it is a
                  // badge the user is looking straight at. The save is
                  // already debounced, so this is one refresh per pause in
                  // typing rather than one per keystroke.
                  studio.refreshStaleness();
                }}
                onMove={(delta) => studio.structural(() => moveScene(id, scene.id, delta))()}
                onDelete={studio.structural(() => deleteScene(id, scene.id))}
                onStructureChanged={() => void studio.loadAll()}
                canMoveUp={index > 0}
                canMoveDown={index < script.scenes.length - 1}
              />
            ))}
          </ol>

          <button
            type="button"
            onClick={studio.structural(() => addScene(id))}
            className="mt-6 rounded border border-subtle px-3 py-2 text-sm text-muted hover:bg-panel"
          >
            + Add a scene
          </button>
        </>
      )}
    </Stage>
  );
}
