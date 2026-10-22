"use client";

import { QuoteButton } from "@/components/QuoteButton";
import { StaleBadge } from "@/components/StaleBadge";
import { assetUrl, type ShotMedia } from "@/lib/api";

/** Whether a shot has anything to record: a line, and someone to say it. */
export function speakable(shot: ShotMedia): boolean {
  return shot.dialogue_kind !== "none" && shot.speech_text.trim() !== "";
}

/** A small square that is filled once the thing it names exists. */
function Made({ label, made }: { label: string; made: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        aria-hidden
        className={`size-[7px] border ${made ? "border-accent bg-accent" : "border-strong"}`}
      />
      {label}
      <span className="sr-only">{made ? " made" : " not made yet"}</span>
    </span>
  );
}

export function ShotMediaCard({
  shot,
  stillCredits,
  speechCredits,
  balance,
  busy,
  onGenerateStill,
  onGenerateSpeech,
}: {
  shot: ShotMedia;
  stillCredits: number;
  speechCredits: number;
  balance: number | null;
  busy: boolean;
  onGenerateStill: (shot: ShotMedia) => void;
  onGenerateSpeech: (shot: ShotMedia) => void;
}) {
  // Nothing on this card is editable, so unlike ShotCard and CharacterCard
  // it owns no state at all and reads everything from the prop. The text
  // lives on the Script page; this one is about what was generated from it,
  // and a generation finishing is precisely a refetch the user must see.
  const { still, audio, clip } = shot;
  const drawing = still?.state === "generating";
  const recording = audio?.state === "generating";
  const hasLine = speakable(shot);
  const number = String(shot.order_index + 1).padStart(2, "0");

  return (
    <li className="blueprint flex flex-col">
      <div className="relative aspect-video overflow-hidden border-b border-subtle bg-panel">
        {still?.asset_id ? (
          /* eslint-disable-next-line @next/next/no-img-element -- next/image
             would proxy through the Next server, which has no session
             cookie for the API. */
          <img
            src={assetUrl(still.asset_id)}
            alt={`Still for shot ${shot.order_index + 1}`}
            className={`size-full object-cover ${drawing ? "opacity-35" : ""}`}
          />
        ) : (
          <div className="no-frame absolute inset-0 grid place-items-center text-[13px] text-muted">
            <span className="bg-panel px-2 py-0.5">
              {drawing ? "Drawing…" : still?.state === "failed" ? "Failed" : "Not drawn yet"}
            </span>
          </div>
        )}
        <span className="absolute top-2 left-2 bg-background px-[7px] py-px font-heading text-sm font-semibold">
          {number}
        </span>
        <span className="absolute top-2 right-2 bg-background px-[7px] py-px text-xs tabular-nums">
          {Number(shot.duration_sec).toFixed(1)}s
        </span>
        {still?.state === "stale" && <StaleBadge className="absolute bottom-2 left-2" />}
      </div>

      <div className="flex flex-1 flex-col gap-2.5 p-3.5">
        <div className="flex flex-wrap gap-1.5">
          <span className="tag">{shot.camera}</span>
          <span className="tag">
            {shot.character_names.length > 0 ? shot.character_names.join(", ") : "nobody in frame"}
          </span>
        </div>

        <p className="text-sm text-pretty">{shot.image_prompt}</p>

        {hasLine && (
          <div className="flex flex-col gap-1.5 border-l border-accent pl-2.5 text-[13px] text-body">
            <p className="kicker text-accent-ink">
              {shot.dialogue_kind.replace("_", " ")}
              {audio?.voice_id ? ` · ${audio.voice_id}` : ""}
              {audio?.duration_sec ? ` · ${audio.duration_sec}s` : ""}
            </p>
            <p>“{shot.speech_text}”</p>
            {audio?.state === "stale" && <StaleBadge className="self-start" />}
            {audio?.asset_id ? (
              // A plain <audio controls>: the browser already has a player,
              // and building one would be a second thing to keep accessible.
              <audio
                controls
                src={assetUrl(audio.asset_id)}
                className="h-8 w-full"
                aria-label={`Audio for shot ${shot.order_index + 1}`}
              />
            ) : (
              <p className="text-xs text-faint">
                {recording ? "Recording…" : audio?.state === "failed" ? "Failed" : "No audio yet"}
              </p>
            )}
          </div>
        )}

        <div className="mt-auto flex gap-3.5 text-xs text-muted">
          <Made label="Still" made={Boolean(still?.asset_id)} />
          {hasLine && <Made label="Audio" made={Boolean(audio?.asset_id)} />}
          <Made label="Clip" made={Boolean(clip?.asset_id)} />
        </div>
      </div>

      <div className="grid auto-cols-fr grid-flow-col divide-x divide-subtle border-t border-subtle">
        {hasLine && (
          <QuoteButton
            flush
            credits={speechCredits}
            balance={balance}
            busy={busy || recording}
            busyLabel={recording ? "Recording…" : "Queued…"}
            label={audio?.asset_id ? "Re-record" : "Generate audio"}
            onClick={() => onGenerateSpeech(shot)}
          />
        )}
        <QuoteButton
          flush
          credits={stillCredits}
          balance={balance}
          busy={busy || drawing}
          busyLabel={drawing ? "Drawing…" : "Queued…"}
          label={still?.asset_id ? "Redraw still" : "Generate still"}
          onClick={() => onGenerateStill(shot)}
        />
      </div>
    </li>
  );
}
