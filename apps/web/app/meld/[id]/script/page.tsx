"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { StaleBadge } from "@/components/StaleBadge";
import { useJobPolling } from "@/lib/useJobPolling";
import { ApiError, generateScript, getScript, type Job, type ScriptView } from "@/lib/api";

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

  const { job } = useJobPolling({ meldId: id, jobId, onSettled });

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
        <p role="alert" className="text-sm text-red-600">
          {loadError}
        </p>
      </main>
    );
  }

  if (script === null) {
    return <main className="p-8 text-sm text-neutral-500">Loading…</main>;
  }

  const busy = jobId !== null;
  const shotCount = script.scenes.reduce((total, scene) => total + scene.shots.length, 0);

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6">
        <Link href={`/meld/${id}`} className="text-sm text-neutral-500 hover:underline">
          ← Story
        </Link>
        <div className="mt-1 flex items-baseline justify-between">
          <h1 className="text-2xl font-semibold">Script</h1>
          <button
            onClick={onGenerate}
            disabled={busy}
            className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
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
        {script.scenes.length > 0 && (
          <p className="mt-1 text-sm text-neutral-500">
            {script.scenes.length} {script.scenes.length === 1 ? "scene" : "scenes"} ·{" "}
            {shotCount} {shotCount === 1 ? "shot" : "shots"}
          </p>
        )}
      </header>

      {script.stale_shots > 0 && (
        <div
          role="status"
          className="mb-6 rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <strong className="font-medium">Your story changed.</strong> {script.stale_shots}{" "}
          {script.stale_shots === 1 ? "shot is" : "shots are"} out of date. Nothing has been
          deleted — regenerate when you&apos;re ready, or keep editing by hand.
        </div>
      )}

      {actionError && (
        <p role="alert" className="mb-6 text-sm text-red-600">
          {actionError}
        </p>
      )}

      {script.scenes.length === 0 ? (
        <p className="text-sm text-neutral-500">
          No script yet. Generate one from your story to get a shot list you can edit.
        </p>
      ) : (
        <ol className="flex flex-col gap-8">
          {script.scenes.map((scene) => (
            <li key={scene.id}>
              <div className="mb-2 flex items-baseline gap-3">
                <h2 className="font-mono text-sm font-semibold uppercase tracking-wide">
                  {scene.heading || "Untitled scene"}
                </h2>
                {scene.state === "stale" && <StaleBadge />}
              </div>
              <p className="mb-3 text-sm text-neutral-600">{scene.summary}</p>
              <ol className="flex flex-col gap-3">
                {scene.shots.map((shot) => (
                  <li key={shot.id} className="rounded border border-neutral-200 p-4 text-sm">
                    <div className="mb-2 flex items-center gap-2 text-xs text-neutral-500">
                      <span>Shot {shot.order_index + 1}</span>
                      <span>·</span>
                      <span>{shot.camera}</span>
                      <span>·</span>
                      <span>{shot.duration_sec}s</span>
                      {shot.state === "stale" && <StaleBadge className="ml-auto" />}
                    </div>
                    <p>{shot.image_prompt}</p>
                    <p className="mt-1 text-neutral-500">Motion: {shot.motion_prompt}</p>
                    {shot.speech_text && (
                      <p className="mt-1 italic text-neutral-600">&ldquo;{shot.speech_text}&rdquo;</p>
                    )}
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}
