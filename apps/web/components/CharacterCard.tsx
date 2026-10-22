"use client";

import { useCallback, useState } from "react";
import { QuoteButton } from "@/components/QuoteButton";
import { RowControls } from "@/components/RowControls";
import { StaleBadge } from "@/components/StaleBadge";
import {
  CHARACTER_ROLES,
  assetUrl,
  updateCharacter,
  type Character,
  type CharacterRole,
} from "@/lib/api";
import { useAutosave } from "@/lib/useAutosave";

export function CharacterCard({
  meldId,
  character: initial,
  sheetCredits,
  balance,
  busy,
  voices,
  onSaved,
  onGenerateSheet,
  onDelete,
  shotCount = null,
}: {
  meldId: string;
  character: Character;
  sheetCredits: number;
  balance: number | null;
  busy: boolean;
  voices: string[];
  onSaved: (character: Character) => void;
  onGenerateSheet: (character: Character) => void;
  onDelete: () => void;
  /** How many shots this character is in, once the shots are cast. */
  shotCount?: number | null;
}) {
  // The card owns the *editable* fields after mount, exactly as ShotCard
  // owns its shot: re-syncing from the parent on every refetch would yank
  // half-typed text out from under the user.
  const [character, setCharacter] = useState(initial);

  // The sheet is the exception, and it is read from the prop rather than
  // from the state above. It is server-owned -- nobody types it -- and a
  // generation finishing is precisely a refetch the user must see. Holding
  // it in local state meant a sheet appeared only after a manual page
  // reload, which is the failure this stage exists to avoid.
  const sheet = initial.sheet;

  // The whole character is the autosave value. useAutosave compares by
  // identity, so this only fires when `edit` below produces a new object.
  const onSave = useCallback(
    async (next: Character) => {
      const saved = await updateCharacter(meldId, next.id, {
        name: next.name,
        role: next.role,
        description: next.description,
        appearance: next.appearance,
        voice_id: next.voice_id,
      });
      onSaved(saved);
    },
    [meldId, onSaved],
  );

  const { status } = useAutosave({ value: character, onSave, delayMs: 800 });

  const edit = <K extends keyof Character>(field: K, value: Character[K]) =>
    setCharacter((current) => ({ ...current, [field]: value }));

  const drawing = sheet?.state === "generating";

  return (
    <li className="blueprint grid gap-5 p-5 sm:grid-cols-[180px_minmax(0,1fr)]">
      <div className="flex flex-col gap-2.5">
        <figure>
          <div className="relative aspect-square overflow-hidden border border-subtle bg-panel">
            {sheet?.asset_id ? (
              /* eslint-disable-next-line @next/next/no-img-element -- next/image
                 would proxy through the Next server, which has no session
                 cookie for the API. */
              <img
                src={assetUrl(sheet.asset_id)}
                alt={`Reference sheet for ${character.name}`}
                className={`size-full object-cover ${drawing ? "opacity-35" : ""}`}
              />
            ) : (
              <div className="no-frame absolute inset-0 grid place-items-center text-xs text-muted">
                <span className="bg-panel px-2 py-0.5">{drawing ? "Drawing…" : "No sheet yet"}</span>
              </div>
            )}
          </div>
          <figcaption className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
            Reference sheet
            {shotCount !== null && ` · used in ${shotCount} ${shotCount === 1 ? "shot" : "shots"}`}
            {/* A second badge, because the two mean different things: the
                character can be out of date with the story while their
                artwork is out of date with the character. */}
            {sheet?.state === "stale" && <StaleBadge />}
          </figcaption>
        </figure>

        <div className="border border-subtle">
          <QuoteButton
            flush
            credits={sheetCredits}
            balance={balance}
            busy={busy || drawing}
            busyLabel={drawing ? "Drawing…" : "Queued…"}
            label={sheet?.asset_id ? "Redraw sheet" : "Generate sheet"}
            onClick={() => onGenerateSheet(character)}
          />
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex items-center gap-2 text-xs text-muted">
          <span className="tag bg-accent-tint text-warn-foreground">
            Character {character.order_index + 1}
          </span>
          {character.state === "stale" && <StaleBadge />}
          <span className="ml-auto" role="status" aria-live="polite">
            {status === "saving" && "Saving…"}
            {status === "saved" && "Saved"}
            {status === "error" && <span className="text-danger">Couldn&apos;t save</span>}
          </span>
          {/* No move controls: a character's order_index decides the order
              their sheet is attached to an image call, so reordering the cast
              would silently invalidate every still they appear in. That is a
              real cost for a cosmetic change nobody has asked for. */}
          <RowControls
            label={character.name || `character ${character.order_index + 1}`}
            onDelete={onDelete}
            confirmMessage={`Delete ${
              character.name || "this character"
            }? Their sheet goes with it, they are removed from every shot, and the stills they appear in go out of date. Credits already spent are not refunded.`}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
          <label className="block">
            <span className="label">Name</span>
            <input
              value={character.name}
              onChange={(e) => edit("name", e.target.value)}
              className="input"
            />
          </label>

          <label className="block">
            <span className="label">Role</span>
            <select
              value={character.role}
              onChange={(e) =>
                // Safe: the options below are generated from CHARACTER_ROLES,
                // so the runtime value can only be one of the literal members.
                edit("role", e.target.value as CharacterRole)
              }
              className="input"
            >
              {CHARACTER_ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="block">
          <span className="label">Who they are — their part in the story</span>
          <textarea
            value={character.description}
            onChange={(e) => edit("description", e.target.value)}
            rows={2}
            className="input"
          />
        </label>

        <label className="block">
          <span className="label">
            Appearance — <strong className="font-medium">the only text the image model sees.</strong>{" "}
            Describe only what a camera would see.
          </span>
          <textarea
            value={character.appearance}
            onChange={(e) => edit("appearance", e.target.value)}
            rows={3}
            className="input"
          />
        </label>

        <label className="block">
          <span className="label">Voice — changing it re-voices their lines, sheet unchanged</span>
          <select
            value={character.voice_id ?? ""}
            onChange={(e) => edit("voice_id", e.target.value)}
            className="input"
          >
            {/* Present only until the list lands, and only when the
                character already has a voice this page has not heard of --
                a <select> whose value is absent from its options silently
                renders as the first option instead. */}
            {character.voice_id && !voices.includes(character.voice_id) && (
              <option value={character.voice_id}>{character.voice_id}</option>
            )}
            {voices.map((voice) => (
              <option key={voice} value={voice}>
                {voice}
              </option>
            ))}
          </select>
        </label>
      </div>
    </li>
  );
}
