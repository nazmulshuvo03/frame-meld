"use client";

import { useState } from "react";
import { Notice, Empty, Stage } from "@/components/studio/Stage";
import { timecode } from "@/components/studio/stages";
import { SceneCard } from "@/components/SceneCard";
import { ShotCard } from "@/components/ShotCard";
import { StaleBadge } from "@/components/StaleBadge";
import {
  addScene,
  deleteScene,
  deleteShot,
  moveScene,
  moveShot,
  type ScriptView,
  type Shot,
} from "@/lib/api";
import type { Studio } from "@/lib/useStudio";

const pad = (n: number) => String(n).padStart(2, "0");

export function ScriptStage({ studio, script }: { studio: Studio; script: ScriptView }) {
  const { id, busy, job } = studio;
  const shotCount = script.scenes.reduce((total, scene) => total + scene.shots.length, 0);
  const writing = busy && job?.kind === "generate_script";
  // Every shot in film order, numbered across the whole film the way the
  // timeline and the table show them.
  const rows = script.scenes
    .flatMap((scene) => scene.shots.map((shot, indexInScene) => ({ scene, shot, indexInScene })))
    .map((row, index) => ({ ...row, number: index + 1 }));
  const runtime = rows.reduce((total, { shot }) => total + (Number(shot.duration_sec) || 0), 0);

  // What the inspector shows. Held by id, so a refetch that replaces the
  // objects keeps the same thing selected; anything deleted falls back to
  // the first shot.
  const [selection, setSelection] = useState<{ kind: "shot" | "scene"; id: string } | null>(null);
  const sceneIndex =
    selection?.kind === "scene" ? script.scenes.findIndex((scene) => scene.id === selection.id) : -1;
  const pickedShot =
    rows.find((row) => selection?.kind === "shot" && row.shot.id === selection.id) ?? rows[0];
  const selected =
    sceneIndex >= 0
      ? ({ kind: "scene", scene: script.scenes[sceneIndex] } as const)
      : pickedShot
        ? ({ kind: "shot", ...pickedShot } as const)
        : null;

  const updateShotInScript = (saved: Shot) =>
    studio.setScript((current) =>
      current === null
        ? current
        : {
            ...current,
            scenes: current.scenes.map((scene) => ({
              ...scene,
              shots: scene.shots.map((shot) => (shot.id === saved.id ? saved : shot)),
            })),
          },
    );
  const drawn = new Set(
    (studio.shots?.shots ?? []).filter((shot) => shot.still?.asset_id).map((shot) => shot.id),
  );

  return (
    <Stage
      id="script"
      title="Script"
      lede="The story broken into scenes and shots. Each shot is one still, one motion and one spoken line, up to five seconds long. The wording of a shot is what gets drawn."
      summary={
        script.scenes.length > 0 ? (
          <span>
            {script.scenes.length} {script.scenes.length === 1 ? "scene" : "scenes"} · {shotCount}{" "}
            {shotCount === 1 ? "shot" : "shots"}
          </span>
        ) : null
      }
      action={
        <button
          onClick={studio.writeScript}
          disabled={busy}
          className={script.scenes.length ? "btn" : "btn btn-primary blueprint"}
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
      working={
        writing
          ? job?.state === "running"
            ? "Writing the shot list… this replaces every scene and shot below."
            : "Queued — the shot list is about to be written."
          : null
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
          {/* The film as a strip: one segment per shot, as wide as the shot
              is long, tinted once its still is drawn. Each one opens its shot
              in the inspector. */}
          <div className="mb-7">
            <div className="mb-1.5 flex justify-between text-xs tabular-nums text-muted">
              <span>0:00</span>
              <span>Timeline · click a shot</span>
              <span>{timecode(runtime)}</span>
            </div>
            <div className="flex h-9 gap-0.5" role="group" aria-label="Timeline">
              {rows.map(({ shot, number }) => {
                const active = selected?.kind === "shot" && selected.shot.id === shot.id;
                return (
                  <button
                    key={shot.id}
                    type="button"
                    onClick={() => setSelection({ kind: "shot", id: shot.id })}
                    title={shot.image_prompt}
                    aria-label={`Shot ${number}`}
                    aria-pressed={active}
                    style={{ flex: `${Number(shot.duration_sec) || 1} 1 0` }}
                    className={`flex min-w-0 items-end px-1 py-[3px] font-heading text-xs ${
                      active
                        ? "bg-accent text-accent-foreground"
                        : `hover:bg-accent-wash ${drawn.has(shot.id) ? "bg-accent-wash" : "bg-panel"}`
                    }`}
                  >
                    {pad(number)}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid items-start gap-7 xl:grid-cols-[minmax(0,1fr)_minmax(420px,0.8fr)]">
            <div className="flex flex-col gap-4">
              <div className="max-h-[calc(100vh-330px)] min-h-80 overflow-auto border border-subtle">
                <table className="w-full border-collapse text-[13px]">
                  <thead className="sticky top-0 z-[1] bg-background">
                    <tr className="kicker text-left text-muted">
                      <th className="w-10 border-b border-subtle p-2 font-normal">#</th>
                      <th className="border-b border-subtle p-2 font-normal">Shot</th>
                      <th className="w-24 border-b border-subtle p-2 font-normal">Camera</th>
                      <th className="w-14 border-b border-subtle p-2 text-right font-normal">Sec</th>
                    </tr>
                  </thead>
                  {script.scenes.map((scene) => {
                    const sceneActive = selected?.kind === "scene" && selected.scene.id === scene.id;
                    return (
                      <tbody key={scene.id}>
                        <tr
                          onClick={() => setSelection({ kind: "scene", id: scene.id })}
                          className={`cursor-pointer ${sceneActive ? "bg-accent-tint" : "bg-panel hover:bg-accent-tint"}`}
                        >
                          <td colSpan={4} className="border-b border-subtle p-2">
                            <button
                              type="button"
                              onClick={() => setSelection({ kind: "scene", id: scene.id })}
                              aria-current={sceneActive ? "true" : undefined}
                              className="flex w-full items-baseline gap-2 text-left"
                            >
                              <span className="kicker shrink-0 text-accent-ink">
                                Scene {scene.order_index + 1}
                              </span>
                              <span className="truncate font-heading text-sm font-semibold uppercase">
                                {scene.heading || "Untitled scene"}
                              </span>
                              {scene.state === "stale" && <StaleBadge />}
                            </button>
                          </td>
                        </tr>
                        {rows
                          .filter((row) => row.scene.id === scene.id)
                          .map(({ shot, number }) => {
                            const active = selected?.kind === "shot" && selected.shot.id === shot.id;
                            return (
                              <tr
                                key={shot.id}
                                onClick={() => setSelection({ kind: "shot", id: shot.id })}
                                className={`cursor-pointer align-top ${
                                  active ? "bg-accent-tint" : "hover:bg-foreground/[0.04]"
                                }`}
                              >
                                <td className="border-b border-foreground/[0.08] p-2 font-heading text-faint">
                                  {pad(number)}
                                </td>
                                <td className="border-b border-foreground/[0.08] p-2">
                                  <button
                                    type="button"
                                    onClick={() => setSelection({ kind: "shot", id: shot.id })}
                                    aria-current={active ? "true" : undefined}
                                    className="line-clamp-1 text-left"
                                  >
                                    {shot.image_prompt || "Describe the frame."}
                                  </button>
                                  <div className="line-clamp-1 text-xs text-faint">
                                    {shot.dialogue_kind === "none"
                                      ? "silent"
                                      : `${shot.dialogue_kind.replace("_", " ")} · “${shot.speech_text}”`}
                                    {shot.state === "stale" && (
                                      <span className="text-warn-foreground"> · out of date</span>
                                    )}
                                  </div>
                                </td>
                                <td className="border-b border-foreground/[0.08] p-2">{shot.camera}</td>
                                <td className="border-b border-foreground/[0.08] p-2 text-right tabular-nums">
                                  {Number(shot.duration_sec).toFixed(1)}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    );
                  })}
                </table>
              </div>

              <button
                type="button"
                onClick={studio.structural(() => addScene(id))}
                className="grid min-h-14 w-full place-items-center border border-dashed border-subtle font-heading text-[17px] font-semibold text-muted hover:border-accent hover:text-accent-ink"
              >
                + Add a scene
              </button>
            </div>

            {selected && (
              <aside aria-label="Inspector" className="blueprint p-5 xl:sticky xl:top-[84px]">
                {selected.kind === "shot" ? (
                  <ShotCard
                    // Keyed so choosing another shot starts a fresh editor;
                    // the one being left flushes its pending save on unmount.
                    key={selected.shot.id}
                    meldId={id}
                    shot={selected.shot}
                    number={selected.number}
                    onSaved={(saved) => {
                      updateShotInScript(saved);
                      // Rewording a shot marks its still stale and the film
                      // with it. The save is already debounced, so this is
                      // one refresh per pause in typing.
                      studio.refreshStaleness();
                    }}
                    onMove={(delta) =>
                      studio.structural(() => moveShot(id, selected.shot.id, delta))()
                    }
                    onDelete={studio.structural(() => deleteShot(id, selected.shot.id))}
                    canMoveUp={selected.indexInScene > 0}
                    canMoveDown={selected.indexInScene < selected.scene.shots.length - 1}
                  />
                ) : (
                  <SceneCard
                    key={selected.scene.id}
                    meldId={id}
                    scene={selected.scene}
                    onSaved={(saved) => {
                      studio.setScript((current) =>
                        current === null
                          ? current
                          : {
                              ...current,
                              scenes: current.scenes.map((s) => (s.id === saved.id ? saved : s)),
                            },
                      );
                      studio.refreshStaleness();
                    }}
                    onMove={(delta) =>
                      studio.structural(() => moveScene(id, selected.scene.id, delta))()
                    }
                    onDelete={studio.structural(() => deleteScene(id, selected.scene.id))}
                    onStructureChanged={() => void studio.loadAll()}
                    canMoveUp={sceneIndex > 0}
                    canMoveDown={sceneIndex < script.scenes.length - 1}
                  />
                )}
              </aside>
            )}
          </div>
        </>
      )}
    </Stage>
  );
}
