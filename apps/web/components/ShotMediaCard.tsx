"use client";

import { QuoteButton } from "@/components/QuoteButton";
import { StaleBadge } from "@/components/StaleBadge";
import { assetUrl, type ShotMedia } from "@/lib/api";

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
  const { still, audio } = shot;
  const drawing = still?.state === "generating";
  const recording = audio?.state === "generating";
  const speakable = shot.dialogue_kind !== "none" && shot.speech_text.trim() !== "";

  return (
    <li className="rounded border border-subtle p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>Shot {shot.order_index + 1}</span>
        <span className="rounded bg-panel px-2 py-0.5">{shot.camera}</span>
        {shot.character_names.length > 0 ? (
          <span>{shot.character_names.join(", ")}</span>
        ) : (
          <span className="italic">nobody in frame</span>
        )}
      </div>

      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="w-full sm:w-64">
          <div className="mb-2 flex items-center justify-end gap-2">
            {still?.state === "stale" && <StaleBadge />}
          </div>
          {still?.asset_id ? (
            /* eslint-disable-next-line @next/next/no-img-element -- next/image
               would proxy through the Next server, which has no session
               cookie for the API. */
            <img
              src={assetUrl(still.asset_id)}
              alt={`Still for shot ${shot.order_index + 1}`}
              className="w-full rounded border border-subtle bg-panel"
            />
          ) : (
            <div className="flex aspect-video w-full items-center justify-center rounded border border-dashed border-subtle text-xs text-faint">
              {drawing ? "Drawing…" : still?.state === "failed" ? "Failed" : "No still yet"}
            </div>
          )}
        </div>

        <div className="flex flex-1 flex-col gap-3">
          <p className="text-sm text-body">{shot.image_prompt}</p>

          {speakable && (
            <div className="rounded border border-subtle bg-panel p-3">
              <p className="mb-2 text-xs text-muted">
                {shot.dialogue_kind.replace("_", " ")}
                {audio?.voice_id ? ` · ${audio.voice_id}` : ""}
                {audio?.duration_sec ? ` · ${audio.duration_sec}s` : ""}
              </p>
              <p className="mb-2 text-sm text-body">{shot.speech_text}</p>
              {audio?.state === "stale" && <StaleBadge className="mb-2 inline-block" />}
              {audio?.asset_id ? (
                // A plain <audio controls>: the browser already has a
                // player, and building one would be a second thing to keep
                // accessible.
                <audio
                  controls
                  src={assetUrl(audio.asset_id)}
                  className="w-full"
                  aria-label={`Audio for shot ${shot.order_index + 1}`}
                />
              ) : (
                <p className="text-xs text-faint">
                  {recording ? "Recording…" : audio?.state === "failed" ? "Failed" : "No audio yet"}
                </p>
              )}
            </div>
          )}

          <div className="flex flex-wrap items-start justify-end gap-3">
            {speakable && (
              <QuoteButton
                credits={speechCredits}
                balance={balance}
                busy={busy || recording}
                busyLabel={recording ? "Recording…" : "Queued…"}
                label={audio?.asset_id ? "Re-record" : "Generate audio"}
                onClick={() => onGenerateSpeech(shot)}
              />
            )}
            <QuoteButton
              credits={stillCredits}
              balance={balance}
              busy={busy || drawing}
              busyLabel={drawing ? "Drawing…" : "Queued…"}
              label={still?.asset_id ? "Redraw still" : "Generate still"}
              onClick={() => onGenerateStill(shot)}
            />
          </div>
        </div>
      </div>
    </li>
  );
}
