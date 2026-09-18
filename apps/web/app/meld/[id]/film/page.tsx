"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CreditBalance } from "@/components/CreditBalance";
import { QuoteButton } from "@/components/QuoteButton";
import { StaleBadge } from "@/components/StaleBadge";
import { useJobPolling } from "@/lib/useJobPolling";
import {
  ApiError,
  assetUrl,
  type FilmView,
  generateMusic,
  getBalance,
  getFilm,
  getPricing,
  type Job,
  renderPreview,
} from "@/lib/api";

//: Used only until /credits/pricing lands — see the Cast page for why this
//: exists and why it is never the source of truth.
const FALLBACK_MUSIC_CREDITS = 24;

export default function FilmPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [film, setFilm] = useState<FilmView | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [quotes, setQuotes] = useState<Record<string, number> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadBalance = useCallback(
    () => getBalance().then((result) => setBalance(result.balance), () => {}),
    [],
  );

  const load = useCallback(
    () =>
      getFilm(id).then(
        (result) => {
          setFilm(result);
          setLoadError(null);
        },
        (err) => {
          if (err instanceof ApiError && err.status === 401) router.replace("/login");
          else if (err instanceof ApiError && err.status === 404) router.replace("/melds");
          else setLoadError("Couldn't load this film. Check your connection and reload.");
        },
      ),
    [id, router],
  );

  useEffect(() => {
    void load();
    void loadBalance();
    getPricing().then((pricing) => setQuotes(pricing.quotes), () => {});
  }, [load, loadBalance]);

  const onSettled = useCallback(
    (job: Job) => {
      setJobId(null);
      if (job.state === "failed") {
        setActionError(job.error ?? "That failed. Nothing was charged for a render.");
      } else {
        void load();
      }
      void loadBalance();
    },
    [load, loadBalance],
  );

  const { job } = useJobPolling({ meldId: id, jobId, onSettled });

  const start = async (request: () => Promise<Job>) => {
    setActionError(null);
    try {
      const started = await request();
      // A cache hit comes back already succeeded: there is nothing to poll
      // and nothing was charged.
      if (started.state === "succeeded") await load();
      else setJobId(started.id);
      await loadBalance();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else if (err instanceof ApiError) setActionError(err.message);
      else setActionError("Couldn't start. Try again.");
      await loadBalance();
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

  if (film === null) {
    return <main className="p-8 text-sm text-neutral-500">Loading…</main>;
  }

  const busy = jobId !== null;
  const musicCredits = quotes?.generate_music ?? FALLBACK_MUSIC_CREDITS;
  const undrawn = film.shot_count - film.stills_ready;
  const ready = film.shot_count > 0 && undrawn === 0;
  const preview = film.preview;

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6">
        <Link href={`/meld/${id}/shots`} className="text-sm text-neutral-500 hover:underline">
          ← Shots
        </Link>
        <div className="mt-1 flex flex-wrap items-baseline justify-between gap-4">
          <h1 className="text-2xl font-semibold">Film</h1>
          <CreditBalance balance={balance} />
        </div>
        {film.shot_count > 0 && (
          <p className="mt-1 text-sm text-neutral-500">
            {film.shot_count} {film.shot_count === 1 ? "shot" : "shots"} ·{" "}
            {film.stills_ready} drawn · {film.duration_sec.toFixed(1)}s
          </p>
        )}
      </header>

      {actionError && (
        <p role="alert" className="mb-6 text-sm text-red-600">
          {actionError}
        </p>
      )}

      {film.shot_count === 0 ? (
        <p className="text-sm text-neutral-500">
          No shots yet. Generate a script first, then draw the stills.
        </p>
      ) : (
        <div className="flex flex-col gap-8">
          <section className="rounded border border-neutral-200 p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-medium">
                Preview
                {preview?.state === "stale" && <StaleBadge />}
              </h2>
              <button
                onClick={() => void start(() => renderPreview(id))}
                disabled={busy || !ready}
                className="rounded bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {busy && job?.kind === "render_preview"
                  ? job.state === "running"
                    ? "Assembling…"
                    : "Queued…"
                  : preview?.asset_id
                    ? "Render again · free"
                    : "Render the preview · free"}
              </button>
            </div>

            <p className="mt-2 text-sm text-neutral-600">
              A full draft of the film — your stills animated, with the narration and the
              score over them. It costs nothing, so watch it as often as you like before
              spending anything on the real thing.
            </p>

            {!ready && (
              <p role="status" className="mt-3 text-sm text-amber-900">
                {undrawn} of {film.shot_count} shots still have no still. Draw them on the{" "}
                <Link href={`/meld/${id}/shots`} className="underline">
                  Shots page
                </Link>{" "}
                first — a preview with gaps in it would not tell you anything about the film.
              </p>
            )}

            {preview?.asset_id && (
              <div className="mt-4">
                {/* keyed on the asset so swapping in a new render replaces the
                    element rather than leaving the browser showing a cached
                    first frame of the old one. */}
                <video
                  key={preview.asset_id}
                  src={assetUrl(preview.asset_id)}
                  controls
                  className="w-full rounded bg-black"
                />
                <p className="mt-2 text-xs text-neutral-500">
                  {preview.resolution} · {preview.duration_sec ?? "?"}s
                  {preview.state === "stale" &&
                    " · the film has changed since this was made"}
                </p>
              </div>
            )}
          </section>

          <section className="rounded border border-neutral-200 p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-medium">
                Music
                {film.music?.state === "stale" && <StaleBadge />}
              </h2>
              <QuoteButton
                credits={musicCredits}
                balance={balance}
                busy={busy}
                busyLabel={job?.state === "running" ? "Composing…" : "Queued…"}
                label={film.music?.asset_id ? "Score it again" : "Score the film"}
                onClick={() => void start(() => generateMusic(id))}
              />
            </div>

            <p className="mt-2 text-sm text-neutral-600">
              One instrumental track across the whole film, written from your title and scene
              headings and mixed under the narration. Optional — a preview renders without it.
            </p>

            {film.music?.asset_id && (
              <div className="mt-4">
                <audio
                  key={film.music.asset_id}
                  src={assetUrl(film.music.asset_id)}
                  controls
                  className="w-full"
                />
                <p className="mt-2 text-xs text-neutral-500">
                  {film.music.duration_sec ?? "?"}s · {film.music.credits_spent} credits
                </p>
                <p className="mt-2 text-xs text-neutral-500">{film.music.text}</p>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
