"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CancelButton } from "@/components/CancelButton";
import { CreditBalance } from "@/components/CreditBalance";
import { QuoteButton } from "@/components/QuoteButton";
import { StaleBadge } from "@/components/StaleBadge";
import { useJobPolling } from "@/lib/useJobPolling";
import {
  ApiError,
  assetDownloadUrl,
  assetUrl,
  type FilmView,
  generateClip,
  generateMusic,
  getBalance,
  getFilm,
  getPricing,
  getShots,
  type Job,
  renderFinal,
  renderPreview,
} from "@/lib/api";

/** "Assembling… 42%" once the render service has reported a figure.
 *
 *  Falls back to the bare word when `progress_pct` is null, which is the
 *  honest answer for the gap between a render starting and its first
 *  reported frame — a bar that reads 0% for the first few seconds of a
 *  ten-minute render is worse than no number, because it looks stuck.
 */
function assemblingLabel(job: Job): string {
  return job.progress_pct === null ? "Assembling…" : `Assembling… ${job.progress_pct}%`;
}

//: Used only until /credits/pricing lands — see the Cast page for why this
//: exists and why it is never the source of truth.
const FALLBACK_MUSIC_CREDITS = 24;
const FALLBACK_CLIP_CREDITS = 80;
const FALLBACK_FINAL_CREDITS = 60;

export default function FilmPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [film, setFilm] = useState<FilmView | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [quotes, setQuotes] = useState<Record<string, number> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // M6-D11 / M4-D5: animating is N ordinary per-shot jobs run one at a
  // time, not a batch job — so one clip failing refunds its own 80 credits
  // and the rest still get made. The same queue-in-the-page shape the Shots
  // page uses, which reuses the single-job polling hook unchanged.
  const pending = useRef<string[]>([]);
  const [remaining, setRemaining] = useState(0);

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

  const drainQueue = useCallback(() => {
    // A local declaration rather than the useCallback calling itself: the
    // cache-hit branch has to continue straight to the next shot without
    // waiting for a poll that will never happen.
    function step() {
      const next = pending.current.shift();
      setRemaining(pending.current.length);
      if (next === undefined) return;
      generateClip(id, next).then(
        (started) => {
          if (started.state === "succeeded") {
            void load();
            step();
          } else {
            setJobId(started.id);
          }
        },
        (err) => {
          // One clip failing does not abandon the rest. Typically a 402.
          if (err instanceof ApiError) setActionError(err.message);
          step();
        },
      );
    }
    step();
  }, [id, load]);

  const onSettled = useCallback(
    (job: Job) => {
      setJobId(null);
      if (job.state === "failed") {
        setActionError(job.error ?? "That failed. Anything charged was refunded.");
      } else {
        void load();
      }
      void loadBalance();
      drainQueue();
    },
    [drainQueue, load, loadBalance],
  );

  const { job, cancel, cancelError } = useJobPolling({ meldId: id, jobId, onSettled });

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
        <p role="alert" className="text-sm text-danger">
          {loadError}
        </p>
      </main>
    );
  }

  if (film === null) {
    return <main className="p-8 text-sm text-muted">Loading…</main>;
  }

  const busy = jobId !== null || remaining > 0;
  const musicCredits = quotes?.generate_music ?? FALLBACK_MUSIC_CREDITS;
  const clipCredits = quotes?.generate_shot_clip ?? FALLBACK_CLIP_CREDITS;
  const finalCredits = quotes?.render_final ?? FALLBACK_FINAL_CREDITS;
  const undrawn = film.shot_count - film.stills_ready;
  const ready = film.shot_count > 0 && undrawn === 0;
  const unanimated = film.shot_count - film.clips_ready;
  const animated = film.shot_count > 0 && unanimated === 0;
  const preview = film.preview;
  const final = film.final;

  const onAnimateAll = () => {
    setActionError(null);
    // Fetched here rather than held in state: the Film page does not
    // otherwise need the shot list, and asking for it on the one click that
    // needs it is cheaper than keeping a second copy in sync.
    void getShots(id).then((view) => {
      pending.current = view.shots
        .filter((shot) => shot.still?.asset_id && shot.clip?.state !== "fresh")
        .map((shot) => shot.id);
      setRemaining(pending.current.length);
      drainQueue();
    });
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6">
        <Link href={`/meld/${id}/shots`} className="text-sm text-muted hover:underline">
          ← Shots
        </Link>
        <div className="mt-1 flex flex-wrap items-baseline justify-between gap-4">
          <h1 className="text-2xl font-semibold">Film</h1>
          <CreditBalance balance={balance} />
          {busy && (
            <CancelButton
              job={job}
              onCancel={() => {
                pending.current = [];
                setRemaining(0);
                void cancel();
              }}
              error={cancelError}
            />
          )}
        </div>
        {film.shot_count > 0 && (
          <p className="mt-1 text-sm text-muted">
            {film.shot_count} {film.shot_count === 1 ? "shot" : "shots"} ·{" "}
            {film.stills_ready} drawn · {film.duration_sec.toFixed(1)}s
          </p>
        )}
      </header>

      {actionError && (
        <p role="alert" className="mb-6 text-sm text-danger">
          {actionError}
        </p>
      )}

      {film.shot_count === 0 ? (
        <p className="text-sm text-muted">
          No shots yet. Generate a script first, then draw the stills.
        </p>
      ) : (
        <div className="flex flex-col gap-8">
          <section className="rounded border border-subtle p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-medium">
                Preview
                {preview?.state === "stale" && <StaleBadge />}
              </h2>
              <button
                onClick={() => void start(() => renderPreview(id))}
                disabled={busy || !ready}
                className="rounded bg-accent px-4 py-2 text-sm text-accent-foreground disabled:opacity-50"
              >
                {busy && job?.kind === "render_preview"
                  ? job.state === "running"
                    ? assemblingLabel(job)
                    : "Queued…"
                  : preview?.asset_id
                    ? "Render again · free"
                    : "Render the preview · free"}
              </button>
            </div>

            <p className="mt-2 text-sm text-body">
              A full draft of the film — your stills animated, with the narration and the
              score over them. It costs nothing, so watch it as often as you like before
              spending anything on the real thing.
            </p>

            {!ready && (
              <p role="status" className="mt-3 text-sm text-warn-foreground">
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
                <p className="mt-2 text-xs text-muted">
                  {preview.resolution} · {preview.duration_sec ?? "?"}s
                  {preview.state === "stale" &&
                    " · the film has changed since this was made"}
                </p>
              </div>
            )}
          </section>

          <section className="rounded border border-subtle p-4">
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

            <p className="mt-2 text-sm text-body">
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
                <p className="mt-2 text-xs text-muted">
                  {film.music.duration_sec ?? "?"}s · {film.music.credits_spent} credits
                </p>
                <p className="mt-2 text-xs text-muted">{film.music.text}</p>
              </div>
            )}
          </section>

          <section className="rounded border border-subtle p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-medium">Clips</h2>
              <div className="flex items-center gap-3">
                {remaining > 0 && (
                  <span role="status" className="text-sm text-muted">
                    {remaining} left…
                  </span>
                )}
                {unanimated > 0 || film.clips_stale > 0 ? (
                  <QuoteButton
                    credits={clipCredits * Math.max(unanimated + film.clips_stale, 1)}
                    balance={balance}
                    busy={busy}
                    busyLabel="Animating…"
                    label={
                      unanimated > 0
                        ? `Animate the ${unanimated + film.clips_stale} remaining`
                        : `Re-animate ${film.clips_stale}`
                    }
                    onClick={onAnimateAll}
                  />
                ) : null}
              </div>
            </div>

            <p className="mt-2 text-sm text-body">
              Each still animated into a real video clip. This is the expensive part —{" "}
              {clipCredits} credits a shot — so watch the preview first and be happy with
              the pacing before you spend it.
            </p>

            <p className="mt-3 text-sm text-muted">
              {film.clips_ready} of {film.shot_count} shots animated
              {film.clips_stale > 0 && (
                <>
                  {" · "}
                  <span className="text-warn-foreground">
                    {film.clips_stale} out of date since the still changed
                  </span>
                </>
              )}
            </p>
          </section>

          <section className="rounded border border-subtle p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="font-medium">
                Final
                {final?.state === "stale" && <StaleBadge />}
              </h2>
              <QuoteButton
                credits={finalCredits}
                balance={balance}
                busy={busy}
                disabled={!animated}
                busyLabel={
                  job?.kind === "render_final" && job.state === "running"
                    ? assemblingLabel(job)
                    : "Queued…"
                }
                label={final?.asset_id ? "Render it again" : "Render the film"}
                onClick={() => void start(() => renderFinal(id))}
              />
            </div>

            <p className="mt-2 text-sm text-body">
              The deliverable: your clips, cut together at 1920×1080 with the full audio.
            </p>

            {!animated && (
              <p role="status" className="mt-3 text-sm text-warn-foreground">
                {unanimated} of {film.shot_count} shots have no clip yet. Animate them
                first — a final render with gaps in it is not the film.
              </p>
            )}

            {final?.asset_id && (
              <div className="mt-4">
                <video
                  key={final.asset_id}
                  src={assetUrl(final.asset_id)}
                  controls
                  className="w-full rounded bg-black"
                />
                <div className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
                  <p className="text-xs text-muted">
                    {final.resolution} · {final.duration_sec ?? "?"}s
                    {final.state === "stale" &&
                      " · the film has changed since this was made"}
                  </p>
                  <a
                    href={assetDownloadUrl(final.asset_id)}
                    className="rounded border border-strong px-3 py-1.5 text-sm hover:bg-panel"
                  >
                    Download the film
                  </a>
                </div>
              </div>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
