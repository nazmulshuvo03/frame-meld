"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CancelButton } from "@/components/CancelButton";
import { SceneCard } from "@/components/SceneCard";
import { useJobPolling } from "@/lib/useJobPolling";
import {
  addScene,
  ApiError,
  deleteScene,
  generateScript,
  getScript,
  type Job,
  moveScene,
  type ScriptView,
} from "@/lib/api";

export default function ScriptPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [script, setScript] = useState<ScriptView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(
    () =>
      getScript(id).then(
        (result) => {
          setScript(result);
          setLoadError(null);
        },
        (err) => {
          // Mirrors the story editor's classification: only a 401 means the
          // session is gone, only a 404 means this Meld is not theirs.
          if (err instanceof ApiError && err.status === 401) router.replace("/login");
          else if (err instanceof ApiError && err.status === 404) router.replace("/melds");
          else setLoadError("Couldn't load this script. Check your connection and reload.");
        },
      ),
    [id, router],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const onSettled = useCallback(
    (job: Job) => {
      setJobId(null);
      if (job.state === "failed") {
        setActionError(job.error ?? "Generating the script failed. Try again.");
      } else {
        void load();
      }
    },
    [load],
  );

  const { job, cancel, cancelError } = useJobPolling({ meldId: id, jobId, onSettled });

  const onGenerate = async () => {
    setActionError(null);
    try {
      const started = await generateScript(id);
      if (started.state === "succeeded") {
        // A cache hit: these exact inputs were generated before, so there
        // is nothing to wait for.
        await load();
      } else {
        setJobId(started.id);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else if (err instanceof ApiError) setActionError(err.message);
      else setActionError("Couldn't start generating. Try again.");
    }
  };

  if (loadError) {
    return (
      <main className="p-8">
        <p role="alert" className="text-sm text-danger">
          {loadError}
        </p>
      </main>
    );
  }

  if (script === null) {
    return <main className="p-8 text-sm text-muted">Loading…</main>;
  }

  const busy = jobId !== null;
  const shotCount = script.scenes.reduce((total, scene) => total + scene.shots.length, 0);

  // Every structural edit refetches rather than patching local state.
  // Adding, deleting or moving renumbers siblings and can mark work stale
  // two stages downstream; reproducing the server's arithmetic here would be
  // a second implementation of the staleness rules, kept in step by hand.
  const structural = (run: () => Promise<unknown>) => () => {
    setActionError(null);
    void run().then(
      () => void load(),
      (err) => {
        if (err instanceof ApiError) setActionError(err.message);
        else setActionError("Couldn't save that change. Try again.");
        void load();
      },
    );
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6">
        <div className="flex items-baseline justify-between">
          <Link href={`/meld/${id}`} className="text-sm text-muted hover:underline">
            ← Story
          </Link>
          <Link
            href={`/meld/${id}/cast`}
            className="text-sm text-muted hover:underline"
          >
            Next up: Cast →
          </Link>
        </div>
        <div className="mt-1 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">Script</h1>
          <div className="flex items-center gap-3">
            {busy && (
              <CancelButton job={job} onCancel={() => void cancel()} error={cancelError} />
            )}
            <button
            onClick={onGenerate}
            disabled={busy}
            className="rounded bg-accent px-4 py-2 text-sm text-accent-foreground disabled:opacity-50"
          >
            {busy
              ? job?.state === "running"
                ? "Writing the shot list…"
                : "Queued…"
              : script.scenes.length
                  ? "Regenerate"
                  : "Generate script"}
            </button>
          </div>
        </div>
        {script.scenes.length > 0 && (
          <p className="mt-1 text-sm text-muted">
            {script.scenes.length} {script.scenes.length === 1 ? "scene" : "scenes"} ·{" "}
            {shotCount} {shotCount === 1 ? "shot" : "shots"}
          </p>
        )}
      </header>

      {script.stale_shots > 0 && (
        <div
          role="status"
          className="mb-6 rounded border border-warn-border bg-warn p-4 text-sm text-warn-foreground"
        >
          <strong className="font-medium">Your story changed.</strong> {script.stale_shots}{" "}
          {script.stale_shots === 1 ? "shot is" : "shots are"} out of date. Nothing has been
          deleted — regenerate when you&apos;re ready, or keep editing by hand.
        </div>
      )}

      {actionError && (
        <p role="alert" className="mb-6 text-sm text-danger">
          {actionError}
        </p>
      )}

      {script.scenes.length === 0 ? (
        <p className="text-sm text-muted">
          No script yet. Generate one from your story to get a shot list you can edit.
        </p>
      ) : (
        <ol className="flex flex-col gap-8">
          {script.scenes.map((scene, index) => (
            <SceneCard
              key={scene.id}
              meldId={id}
              scene={scene}
              onSaved={(saved) =>
                setScript((current) =>
                  current === null
                    ? current
                    : {
                        ...current,
                        scenes: current.scenes.map((s) => (s.id === saved.id ? saved : s)),
                      },
                )
              }
              onMove={(delta) => structural(() => moveScene(id, scene.id, delta))()}
              onDelete={structural(() => deleteScene(id, scene.id))}
              onStructureChanged={() => void load()}
              canMoveUp={index > 0}
              canMoveDown={index < script.scenes.length - 1}
            />
          ))}
        </ol>
      )}

      {script.scenes.length > 0 && (
        <button
          type="button"
          onClick={structural(() => addScene(id))}
          className="mt-6 self-start rounded border border-subtle px-3 py-2 text-sm text-muted hover:bg-panel"
        >
          + Add a scene
        </button>
      )}
    </main>
  );
}
