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

/** A row inside the Film stage. Three of them — preview, clips, final — and
 *  they are peers, so unlike the stages themselves they really are cards. */
function Panel({
  title,
  stale,
  lede,
  action,
  children,
}: {
  title: string;
  stale?: boolean;
  lede: string;
  action: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <section className="rounded border border-subtle p-5">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-medium">
            {title}
            {stale && <StaleBadge />}
          </h3>
          <p className="mt-1 max-w-prose text-sm text-body">{lede}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
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
    .filter((shot) => shot.still?.asset_id && shot.clip?.state !== "fresh")
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
      lede="Watch a free preview as often as you like. Pay only when you want the real thing."
      summary={
        film.shot_count > 0 ? (
          <span className="font-mono tabular-nums">
            {film.stills_ready}/{film.shot_count} drawn · {film.clips_ready}/{film.shot_count}{" "}
            animated · {film.duration_sec.toFixed(1)}s
          </span>
        ) : null
      }
    >
      {film.shot_count === 0 ? (
        <Empty>No shots yet. Generate a script first, then draw the stills.</Empty>
      ) : (
        <div className="flex flex-col gap-5">
          <Panel
            title="Preview"
            stale={preview?.state === "stale"}
            lede="Your stills panned and zoomed, with the narration and score over them. It costs nothing, so watch it as often as you like before spending anything on the real thing."
            action={
              <button
                onClick={() => void studio.start(() => renderPreview(studio.id))}
                disabled={busy || !ready}
                className="rounded bg-accent px-4 py-2 text-sm text-accent-foreground disabled:opacity-50"
              >
                {busy && job?.kind === "render_preview"
                  ? rendering("render_preview")
                  : preview?.asset_id
                    ? "Render it again · free"
                    : "Render the preview · free"}
              </button>
            }
          >
            {!ready && (
              <p role="status" className="mt-3 text-sm text-warn-foreground">
                {undrawn} of {film.shot_count} shots still have no still. Draw them above
                first — a preview with gaps in it would not tell you anything about the film.
              </p>
            )}
            {preview?.asset_id && (
              <figure className="mt-4">
                <video
                  key={preview.asset_id}
                  src={assetUrl(preview.asset_id)}
                  controls
                  className="max-h-[60vh] w-full rounded bg-black object-contain"
                />
                <figcaption className="mt-2 font-mono text-xs tabular-nums text-muted">
                  {preview.resolution} · {preview.duration_sec ?? "?"}s
                  {preview.state === "stale" && " · the film has changed since this was made"}
                </figcaption>
              </figure>
            )}
          </Panel>

          <Panel
            title="Score"
            stale={film.music?.state === "stale"}
            lede="One instrumental track across the whole film, mixed under the narration. Optional — a preview renders without it."
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
                className="mt-4 w-full"
              />
            )}
          </Panel>

          <Panel
            title="Clips"
            lede={`Each still animated into a real video clip. This is the expensive part — ${clipCredits} credits a shot — so watch the preview first and be happy with the pacing before you spend it.`}
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
            <p role="status" className="mt-3 text-sm text-muted">
              <span className="font-mono tabular-nums">
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
          </Panel>

          <Panel
            title="Final"
            stale={final?.state === "stale"}
            lede="The deliverable: your clips cut together at 1920×1080 with the full audio."
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
              <p role="status" className="mt-3 text-sm text-warn-foreground">
                {unanimated} of {film.shot_count} shots have no clip yet. Animate them first —
                a final render with gaps in it is not the film.
              </p>
            )}
            {final?.asset_id && (
              <figure className="mt-4">
                <video
                  key={final.asset_id}
                  src={assetUrl(final.asset_id)}
                  controls
                  className="max-h-[60vh] w-full rounded bg-black object-contain"
                />
                <figcaption className="mt-2 flex flex-wrap items-baseline justify-between gap-3">
                  <span className="font-mono text-xs tabular-nums text-muted">
                    {final.resolution} · {final.duration_sec ?? "?"}s
                    {final.state === "stale" && " · the film has changed since this was made"}
                  </span>
                  <a
                    href={assetDownloadUrl(final.asset_id)}
                    className="rounded border border-strong px-3 py-1.5 text-sm hover:bg-panel"
                  >
                    Download the film
                  </a>
                </figcaption>
              </figure>
            )}
          </Panel>
        </div>
      )}
    </Stage>
  );
}
