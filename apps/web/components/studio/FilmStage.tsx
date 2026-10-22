"use client";

import { QuoteButton } from "@/components/QuoteButton";
import { StaleBadge } from "@/components/StaleBadge";
import { Empty, Stage } from "@/components/studio/Stage";
import {
  assetDownloadUrl,
  assetUrl,
  generateMusic,
  renderFinal,
  renderPreview,
  type FilmView,
  type ShotsView,
} from "@/lib/api";
import type { Studio } from "@/lib/useStudio";

/** One of the paid steps after the preview -- score, clips, final -- as a
 *  numbered row of one ruled list, the way the prototype draws them. */
function Step({
  num,
  title,
  aside,
  stale,
  lede,
  action,
  children,
}: {
  num: string;
  title: string;
  /** A short qualifier beside the title: "Optional", a resolution. */
  aside?: string;
  stale?: boolean;
  lede: string;
  action: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <li className="grid grid-cols-[48px_minmax(0,1fr)] gap-x-5 gap-y-3 px-5 py-[18px] md:grid-cols-[64px_minmax(0,1fr)_auto]">
      <span className="font-heading text-3xl font-semibold tabular-nums text-accent">{num}</span>
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h3 className="text-xl">{title}</h3>
          {aside && <span className="text-xs text-faint">{aside}</span>}
          {stale && <StaleBadge />}
        </div>
        <p className="mt-0.5 max-w-prose text-sm text-pretty text-body">{lede}</p>
        {children}
      </div>
      <div className="col-start-2 md:col-start-3">{action}</div>
    </li>
  );
}

export function FilmStage({
  studio,
  film,
  shots,
}: {
  studio: Studio;
  film: FilmView;
  shots: ShotsView | null;
}) {
  const { busy, job, balance, quotes } = studio;
  const musicCredits = quotes.generate_music;
  const clipCredits = quotes.generate_shot_clip;
  const finalCredits = quotes.render_final;

  const undrawn = film.shot_count - film.stills_ready;
  const ready = film.shot_count > 0 && undrawn === 0;
  const unanimated = film.shot_count - film.clips_ready;
  const animated = film.shot_count > 0 && unanimated === 0;
  const preview = film.preview;
  const final = film.final;

  // How many clips have to be paid for, from the film's own counts: a shot
  // with no clip and a shot whose clip went stale both need animating, and
  // the user is quoted for both together. Which *shots* those are comes
  // from the shot list, but the count on the button face does not depend on
  // it having loaded.
  const toAnimate = unanimated + film.clips_stale;
  const animateIds = (shots?.shots ?? [])
    .filter((shot) => shot.clip?.state !== "fresh")
    .map((shot) => shot.id);

  const rendering = (kind: string) =>
    busy && job?.kind === kind && job.state === "running"
      ? job.progress_pct === null
        ? "Assembling…"
        : `Assembling… ${job.progress_pct}%`
      : "Queued…";

  return (
    <Stage
      id="film"
      title="Film"
      lede="Watch the free preview as often as you like. Pay only when you want the real thing."
      summary={
        film.shot_count > 0 ? (
          <span>
            {film.stills_ready}/{film.shot_count} drawn · {film.clips_ready}/{film.shot_count}{" "}
            animated · {film.duration_sec.toFixed(1)}s
          </span>
        ) : null
      }
    >
      {film.shot_count === 0 ? (
        <Empty>No shots yet. Generate a script first, then draw the stills.</Empty>
      ) : (
        <div className="flex flex-col gap-7">
          <section
            aria-labelledby="preview-title"
            className="blueprint grid lg:grid-cols-[minmax(0,1.7fr)_minmax(260px,1fr)]"
          >
            {preview?.asset_id ? (
              <video
                key={preview.asset_id}
                src={assetUrl(preview.asset_id)}
                controls
                className="aspect-video w-full bg-black object-contain"
              />
            ) : (
              <div className="no-frame grid aspect-video place-items-center text-[13px] text-muted">
                <span className="bg-panel px-2 py-0.5">No preview yet</span>
              </div>
            )}

            <div className="flex flex-col gap-3 border-subtle p-5 max-lg:border-t lg:border-l">
              <div className="flex flex-wrap items-center gap-2">
                <h3 id="preview-title" className="text-2xl">
                  Preview
                </h3>
                <span className="tag bg-accent-tint text-warn-foreground">Free</span>
                {preview?.state === "stale" && <StaleBadge />}
              </div>
              <p className="text-sm text-pretty text-body">
                Your stills panned and zoomed, with the narration and score laid over them. Watch
                it as often as you like before spending anything on the real thing.
              </p>
              {!ready && (
                <p role="status" className="text-[13px] text-warn-foreground">
                  {undrawn} of {film.shot_count} shots still have no still. Draw them first — a
                  preview with gaps in it would not tell you anything about the film.
                </p>
              )}
              {preview?.asset_id && (
                <p className="text-[13px] tabular-nums text-muted">
                  {preview.resolution} · {preview.duration_sec ?? "?"}s
                  {preview.state === "stale" && " · the film has changed since this was made"}
                </p>
              )}
              <button
                onClick={() => void studio.start(() => renderPreview(studio.id))}
                disabled={busy || !ready}
                className="btn btn-primary blueprint mt-auto w-full"
              >
                {busy && job?.kind === "render_preview"
                  ? rendering("render_preview")
                  : preview?.asset_id
                    ? "Render it again · free"
                    : "Render the preview · free"}
              </button>
            </div>
          </section>

          <ol className="divide-y divide-subtle border border-subtle">
            <Step
              num="02"
              title="Score"
              aside="Optional"
              stale={film.music?.state === "stale"}
              lede="One instrumental track across the whole film, mixed under the narration. A preview renders without it."
              action={
                <QuoteButton
                  credits={musicCredits}
                  balance={balance}
                  busy={busy}
                  busyLabel={
                    job?.kind === "generate_music" && job.state === "running"
                      ? "Composing…"
                      : "Queued…"
                  }
                  label={film.music?.asset_id ? "Score it again" : "Score the film"}
                  onClick={() => void studio.start(() => generateMusic(studio.id))}
                />
              }
            >
              {film.music?.asset_id && (
                <audio
                  key={film.music.asset_id}
                  src={assetUrl(film.music.asset_id)}
                  controls
                  className="mt-3 h-9 w-full"
                />
              )}
            </Step>

            <Step
              num="03"
              title="Clips"
              aside={`${clipCredits} credits a shot`}
              lede="Each shot made into a real video clip, from its still if it has one and from the script alone if not. This is the expensive part, so watch the preview first and be happy with the pacing before you spend it."
              action={
                toAnimate > 0 ? (
                  <QuoteButton
                    credits={clipCredits * toAnimate}
                    balance={balance}
                    busy={busy}
                    busyLabel="Animating…"
                    label={
                      unanimated > 0
                        ? `Animate the ${toAnimate} remaining`
                        : `Re-animate ${film.clips_stale}`
                    }
                    onClick={() => studio.animateShots(animateIds)}
                  />
                ) : null
              }
            >
              <div className="mt-3 h-1 max-w-60 bg-panel" aria-hidden>
                <div
                  className="h-full bg-accent"
                  style={{ width: `${Math.round((film.clips_ready / film.shot_count) * 100)}%` }}
                />
              </div>
              <p role="status" className="mt-1.5 text-[13px] text-muted">
                <span className="tabular-nums">
                  {film.clips_ready} of {film.shot_count}
                </span>{" "}
                shots animated
                {film.clips_stale > 0 && (
                  <>
                    {" · "}
                    <span className="text-warn-foreground">
                      {film.clips_stale} out of date since the still changed
                    </span>
                  </>
                )}
              </p>
            </Step>

            <Step
              num="04"
              title="Final"
              aside="1920×1080"
              stale={final?.state === "stale"}
              lede="The deliverable: your clips cut together with the full audio."
              action={
                <QuoteButton
                  credits={finalCredits}
                  balance={balance}
                  busy={busy}
                  disabled={!animated}
                  busyLabel={rendering("render_final")}
                  label={final?.asset_id ? "Render it again" : "Render the film"}
                  onClick={() => void studio.start(() => renderFinal(studio.id))}
                />
              }
            >
              {!animated && (
                <p role="status" className="mt-1.5 text-[13px] text-warn-foreground">
                  {unanimated} of {film.shot_count} shots have no clip yet. Animate them first —
                  a final render with gaps in it is not the film.
                </p>
              )}
              {final?.asset_id && (
                <figure className="mt-3">
                  <video
                    key={final.asset_id}
                    src={assetUrl(final.asset_id)}
                    controls
                    className="aspect-video max-h-[60vh] w-full bg-black object-contain"
                  />
                  <figcaption className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
                    <span className="text-xs tabular-nums text-muted">
                      {final.resolution} · {final.duration_sec ?? "?"}s
                      {final.state === "stale" && " · the film has changed since this was made"}
                    </span>
                    <a href={assetDownloadUrl(final.asset_id)} className="btn">
                      Download the film
                    </a>
                  </figcaption>
                </figure>
              )}
            </Step>
          </ol>
        </div>
      )}
    </Stage>
  );
}
