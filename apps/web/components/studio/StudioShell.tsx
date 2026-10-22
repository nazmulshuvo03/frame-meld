"use client";

import { createContext, useContext, useState, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { speakable } from "@/components/ShotMediaCard";
import { StageFooter } from "@/components/studio/StageFooter";
import { StudioHeader } from "@/components/studio/StudioHeader";
import { StudioSidebar, type BudgetRow, type StageMark } from "@/components/studio/StudioSidebar";
import { stageFromPath, stageIndex, timecode } from "@/components/studio/stages";
import {
  logout,
  renameMeld,
  type CastView,
  type FilmView,
  type MeldDetail,
  type ScriptView,
  type ShotsView,
} from "@/lib/api";
import { useStudio, type Studio } from "@/lib/useStudio";

const StudioContext = createContext<Studio | null>(null);

/** The studio, with every stage's data known to be loaded.
 *
 *  The shell renders its children only once all five loads have landed, so a
 *  page can read `script` or `film` without a null check of its own. Throwing
 *  rather than returning null is for the one way this goes wrong: a page
 *  rendered outside the shell. */
export function useReadyStudio(): {
  studio: Studio;
  meld: MeldDetail;
  script: ScriptView;
  cast: CastView;
  shots: ShotsView;
  film: FilmView;
} {
  const studio = useContext(StudioContext);
  if (!studio?.meld || !studio.script || !studio.cast || !studio.shots || !studio.film) {
    throw new Error("useReadyStudio was used outside a loaded StudioShell.");
  }
  return {
    studio,
    meld: studio.meld,
    script: studio.script,
    cast: studio.cast,
    shots: studio.shots,
    film: studio.film,
  };
}

function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

/**
 * The frame around the five pages: header, pipeline sidebar, Back/Next
 * footer, and the studio state they share.
 *
 * It lives in the route's layout, so it survives moving between stages. That
 * is what keeps a running job's progress, the credit balance and the loaded
 * Meld in place while the page itself changes underneath.
 */
export function StudioShell({ id, children }: { id: string; children: ReactNode }) {
  const studio = useStudio(id);
  const { meld, script, cast, shots, film, loadError, actionError, notice, quotes } = studio;
  const current = stageFromPath(usePathname());
  const router = useRouter();

  // Which way the last move went, so the new page can slide in from the side
  // it lives on. Adjusted during render rather than in an effect: it is
  // derived from the route, and an effect would paint one frame of the wrong
  // animation first.
  const index = stageIndex(current);
  const [previousIndex, setPreviousIndex] = useState(index);
  const [direction, setDirection] = useState<"next" | "back">("next");
  if (index !== previousIndex) {
    setDirection(index > previousIndex ? "next" : "back");
    setPreviousIndex(index);
  }

  if (loadError) {
    return (
      <main className="mx-auto max-w-5xl p-8">
        <p role="alert" className="text-sm text-danger">
          {loadError}
        </p>
      </main>
    );
  }

  if (!meld || !script || !cast || !shots || !film) {
    return <main className="mx-auto max-w-5xl p-8 text-sm text-muted">Loading…</main>;
  }

  const words = meld.story?.text.trim() ? meld.story.text.trim().split(/\s+/).length : 0;
  const scriptShots = script.scenes.reduce((total, scene) => total + scene.shots.length, 0);
  const lines = shots.shots.filter(speakable);
  const unvoiced = lines.filter((shot) => !shot.audio?.asset_id).length;
  const rendered = Boolean(film.final?.asset_id) && film.final?.state !== "stale";

  const stages: StageMark[] = [
    { id: "story", label: "Story", status: plural(words, "word"), done: words > 0, stale: 0 },
    {
      id: "script",
      label: "Script",
      status: scriptShots
        ? `${plural(scriptShots, "shot")} · ${timecode(film.duration_sec)}`
        : "Not written yet",
      done: scriptShots > 0,
      stale: script.stale_shots,
    },
    {
      id: "cast",
      label: "Cast",
      status: cast.characters.length ? plural(cast.characters.length, "character") : "Nobody yet",
      done: cast.characters.length > 0,
      stale: cast.stale_characters + cast.stale_sheets,
    },
    {
      id: "shots",
      label: "Shots",
      status: `${film.stills_ready}/${film.shot_count} drawn · ${lines.length - unvoiced} voiced`,
      done: film.shot_count > 0 && film.stills_ready === film.shot_count && unvoiced === 0,
      stale: shots.stale_stills + shots.stale_audio,
    },
    {
      id: "film",
      label: "Film",
      status: rendered ? "Rendered" : `${film.clips_ready}/${film.shot_count} animated`,
      done: rendered,
      stale: (film.final?.state === "stale" ? 1 : 0) + (film.preview?.state === "stale" ? 1 : 0),
    },
  ];

  // What finishing the film still costs, from the same quotes the buttons
  // show. Clips count the stale ones too, as the Film stage's button does.
  const undrawn = film.shot_count - film.stills_ready;
  const toAnimate = film.shot_count - film.clips_ready + film.clips_stale;
  const quote = (kind: string) => quotes[kind] ?? 0;
  const budget: BudgetRow[] | null =
    film.shot_count === 0
      ? null
      : [
          { label: `Draw ${plural(undrawn, "still")}`, credits: undrawn * quote("generate_shot_image") },
          { label: `Voice ${plural(unvoiced, "shot")}`, credits: unvoiced * quote("generate_speech") },
          { label: `Animate ${plural(toAnimate, "clip")}`, credits: toAnimate * quote("generate_shot_clip") },
          { label: "Final render", credits: rendered ? 0 : quote("render_final") },
        ].filter((row) => row.credits > 0);

  return (
    <StudioContext.Provider value={studio}>
      <StudioHeader
        title={meld.title}
        durationSec={film.duration_sec}
        shotCount={film.shot_count}
        balance={studio.balance}
        job={studio.job}
        busy={studio.busy}
        remaining={studio.remaining}
        cancelError={studio.cancelError}
        onStop={studio.stopEverything}
        onRename={(title) => {
          studio.setMeld((current) => (current ? { ...current, title } : current));
          renameMeld(id, title).catch(() => {
            studio.setActionError("Couldn't rename this Meld. Try again.");
            void studio.loadAll();
          });
        }}
        onLogout={() => {
          logout().then(
            () => router.replace("/login"),
            () => studio.setActionError("Couldn't log out. Try again."),
          );
        }}
      />

      <div className="grid flex-1 lg:grid-cols-[248px_minmax(0,1fr)]">
        <StudioSidebar
          meldId={id}
          stages={stages}
          current={current}
          budget={budget}
          balance={studio.balance}
        />

        <div className="flex min-w-0 flex-col">
          {/* overflow-x-clip: the slide-in starts off to one side, and must
              not give the page a horizontal scrollbar while it arrives. */}
          <main className="w-full flex-1 overflow-x-clip px-5 pt-7 pb-10 lg:px-10">
            {actionError && (
              <p role="alert" className="mb-4 border border-danger/40 px-4 py-3 text-sm text-danger">
                {actionError}
              </p>
            )}

            {notice && (
              <p role="status" className="mb-4 border border-subtle bg-panel px-4 py-3 text-sm text-body">
                {notice}
              </p>
            )}

            <div key={current} className={direction === "next" ? "slide-next" : "slide-back"}>
              {children}
            </div>
          </main>

          <StageFooter meldId={id} current={current} />
        </div>
      </div>
    </StudioContext.Provider>
  );
}
