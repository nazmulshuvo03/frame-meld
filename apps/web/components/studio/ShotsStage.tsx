"use client";

import { useState } from "react";
import { QuoteButton } from "@/components/QuoteButton";
import { ShotMediaCard, speakable } from "@/components/ShotMediaCard";
import { Empty, Notice, Stage } from "@/components/studio/Stage";
import type { ShotMedia, ShotsView } from "@/lib/api";
import type { Studio } from "@/lib/useStudio";

const FILTERS: { id: string; label: string; keep: (shot: ShotMedia) => boolean }[] = [
  { id: "all", label: "All", keep: () => true },
  { id: "undrawn", label: "Not drawn", keep: (shot) => !shot.still?.asset_id },
  { id: "silent", label: "No audio", keep: (shot) => speakable(shot) && !shot.audio?.asset_id },
];

export function ShotsStage({ studio, shots }: { studio: Studio; shots: ShotsView }) {
  const { busy, job, balance, quotes } = studio;
  const castingCredits = quotes.map_shot_characters;
  const stillCredits = quotes.generate_shot_image;
  const speechCredits = quotes.generate_speech;
  const casting = busy && job?.kind === "map_shot_characters";
  const [filter, setFilter] = useState(FILTERS[0]);

  const undrawn = shots.shots.filter(FILTERS[1].keep);
  const unvoiced = shots.shots.filter(FILTERS[2].keep);

  return (
    <Stage
      id="shots"
      title="Shots"
      lede="Every shot drawn as a still and voiced. Each still is drawn from the sheets of whoever is in that shot, so the cast has to be settled first."
      summary={
        shots.shots.length > 0 ? (
          <span>
            {shots.shots.length} {shots.shots.length === 1 ? "shot" : "shots"} ·{" "}
            {shots.shots.length - undrawn.length} drawn ·{" "}
            {shots.shots.filter(speakable).length - unvoiced.length} voiced
          </span>
        ) : null
      }
      action={
        shots.shots.length > 0 ? (
          <>
            <fieldset className="seg">
              <legend className="sr-only">Show</legend>
              {FILTERS.map((option) => (
                <label key={option.id} className="seg-opt">
                  <input
                    type="radio"
                    name="shot-filter"
                    checked={filter.id === option.id}
                    onChange={() => setFilter(option)}
                  />
                  {option.label}
                </label>
              ))}
            </fieldset>
            <QuoteButton
              credits={castingCredits}
              balance={balance}
              busy={busy}
              busyLabel={casting && job?.state === "running" ? "Reading…" : "Queued…"}
              label={shots.cast_into_shots ? "Cast them again" : "Cast the shots"}
              onClick={studio.castTheShots}
            />
            {undrawn.length > 0 && (
              <QuoteButton
                credits={stillCredits * undrawn.length}
                balance={balance}
                busy={busy}
                busyLabel="Drawing…"
                label={`Draw the ${undrawn.length} remaining`}
                onClick={() => studio.drawStills(undrawn.map((shot) => shot.id))}
              />
            )}
            {unvoiced.length > 0 && (
              <QuoteButton
                credits={speechCredits * unvoiced.length}
                balance={balance}
                busy={busy}
                busyLabel="Recording…"
                label={`Voice ${unvoiced.length} ${unvoiced.length === 1 ? "shot" : "shots"}`}
                onClick={() => studio.recordSpeeches(unvoiced.map((shot) => shot.id))}
              />
            )}
          </>
        ) : null
      }
      notices={
        <>
          {shots.shots.length > 0 && !shots.cast_into_shots && (
            <Notice tone="quiet">
              <strong className="font-medium">Nobody is in a shot yet.</strong> Cast the shots
              first — that is what tells each still which character sheets to draw from, and
              it is the only thing keeping faces consistent between frames.
            </Notice>
          )}
          {shots.stale_stills > 0 && (
            <Notice>
              <strong className="font-medium">Something upstream changed.</strong>{" "}
              {shots.stale_stills} {shots.stale_stills === 1 ? "still is" : "stills are"} out
              of date. Nothing has been deleted — redraw the ones you want.
            </Notice>
          )}
          {shots.stale_audio > 0 && (
            <Notice>
              <strong className="font-medium">A line or a voice changed.</strong>{" "}
              {shots.stale_audio} {shots.stale_audio === 1 ? "clip is" : "clips are"} out of
              date. Nothing has been deleted — re-record the ones you want.
            </Notice>
          )}
        </>
      }
    >
      {shots.shots.length === 0 ? (
        <Empty>Write the script first, then come back to draw each shot.</Empty>
      ) : (
        <ol className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-7">
          {shots.shots.filter(filter.keep).map((shot) => (
            <ShotMediaCard
              key={shot.id}
              shot={shot}
              stillCredits={stillCredits}
              speechCredits={speechCredits}
              balance={balance}
              busy={busy}
              onGenerateStill={(target) => studio.drawStill(target.id)}
              onGenerateSpeech={(target) => studio.recordSpeech(target.id)}
            />
          ))}
        </ol>
      )}
    </Stage>
  );
}
