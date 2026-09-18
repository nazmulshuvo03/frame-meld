"use client";

import { use, useEffect, useState } from "react";
import { CastStage } from "@/components/studio/CastStage";
import { FilmStage } from "@/components/studio/FilmStage";
import { ScriptStage } from "@/components/studio/ScriptStage";
import { ShotsStage } from "@/components/studio/ShotsStage";
import { StoryStage } from "@/components/studio/StoryStage";
import { TransportBar, type StageId, type StageMark } from "@/components/studio/TransportBar";
import { useStudio } from "@/lib/useStudio";

const STAGE_IDS: StageId[] = ["story", "script", "cast", "shots", "film"];

/** Which stage the reader is currently in, for the nav's `aria-current`.
 *
 *  An IntersectionObserver rather than a scroll listener: it fires only
 *  when a boundary is crossed rather than on every pixel, and it does not
 *  need to know the sticky bar's height. The top band is discounted so a
 *  stage counts as "current" once it is properly on screen rather than the
 *  instant its first pixel appears under the bar.
 */
function useCurrentStage(ready: boolean): StageId {
  const [current, setCurrent] = useState<StageId>("story");

  useEffect(() => {
    if (!ready) return;
    const seen = new Map<StageId, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          seen.set(entry.target.id as StageId, entry.intersectionRatio);
        }
        let best: StageId = "story";
        let bestRatio = -1;
        for (const id of STAGE_IDS) {
          const ratio = seen.get(id) ?? 0;
          if (ratio > bestRatio) {
            bestRatio = ratio;
            best = id;
          }
        }
        setCurrent(best);
      },
      { rootMargin: "-120px 0px -40% 0px", threshold: [0, 0.25, 0.5, 1] },
    );
    for (const id of STAGE_IDS) {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [ready]);

  return current;
}

export default function StudioPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const studio = useStudio(id);
  const { meld, script, cast, shots, film, loadError, actionError } = studio;

  const ready = meld !== null && script !== null && cast !== null && shots !== null && film !== null;
  const current = useCurrentStage(ready);

  if (loadError) {
    return (
      <main className="mx-auto max-w-5xl p-8">
        <p role="alert" className="text-sm text-danger">
          {loadError}
        </p>
      </main>
    );
  }

  if (!ready) {
    return <main className="mx-auto max-w-5xl p-8 text-sm text-muted">Loading…</main>;
  }

  const stages: StageMark[] = [
    {
      id: "story",
      label: "Story",
      done: meld.story?.text.trim() ? 1 : 0,
      stale: 0,
    },
    {
      id: "script",
      label: "Script",
      done: script.scenes.reduce((total, scene) => total + scene.shots.length, 0),
      stale: script.stale_shots,
    },
    {
      id: "cast",
      label: "Cast",
      done: cast.characters.length,
      stale: cast.stale_characters + cast.stale_sheets,
    },
    {
      id: "shots",
      label: "Shots",
      done: film.stills_ready,
      stale: shots.stale_stills + shots.stale_audio,
    },
    {
      id: "film",
      label: "Film",
      done: film.final?.asset_id ? 1 : film.preview?.asset_id ? 1 : 0,
      stale:
        (film.final?.state === "stale" ? 1 : 0) + (film.preview?.state === "stale" ? 1 : 0),
    },
  ];

  return (
    <>
      <TransportBar
        title={meld.title}
        durationSec={film.duration_sec}
        shotCount={film.shot_count}
        balance={studio.balance}
        stages={stages}
        current={current}
        job={studio.job}
        busy={studio.busy}
        remaining={studio.remaining}
        cancelError={studio.cancelError}
        onStop={studio.stopEverything}
      />

      <main className="mx-auto w-full max-w-5xl flex-1 px-6">
        {actionError && (
          <p
            role="alert"
            className="mt-6 rounded border border-danger/40 bg-panel px-4 py-3 text-sm text-danger"
          >
            {actionError}
          </p>
        )}

        <StoryStage meldId={id} meld={meld} />
        <ScriptStage studio={studio} script={script} />
        <CastStage studio={studio} cast={cast} />
        <ShotsStage studio={studio} shots={shots} />
        <FilmStage studio={studio} film={film} shots={shots} />
      </main>
    </>
  );
}
