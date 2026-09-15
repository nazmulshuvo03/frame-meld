"use client";

import { useCallback, useState } from "react";
import { QuoteButton } from "@/components/QuoteButton";
import { StaleBadge } from "@/components/StaleBadge";
import {
  CHARACTER_ROLES,
  assetUrl,
  updateCharacter,
  type Character,
  type CharacterRole,
} from "@/lib/api";
import { useAutosave } from "@/lib/useAutosave";

const FIELD =
  "w-full rounded border border-neutral-200 p-2 text-sm outline-none focus:border-neutral-400";

export function CharacterCard({
  meldId,
  character: initial,
  sheetCredits,
  balance,
  busy,
  voices,
  onSaved,
  onGenerateSheet,
}: {
  meldId: string;
  character: Character;
  sheetCredits: number;
  balance: number | null;
  busy: boolean;
  voices: string[];
  onSaved: (character: Character) => void;
  onGenerateSheet: (character: Character) => void;
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
    <li className="rounded border border-neutral-200 p-4">
      <div className="mb-3 flex items-center gap-2 text-xs text-neutral-500">
        <span>Character {character.order_index + 1}</span>
        {character.state === "stale" && <StaleBadge />}
        <span className="ml-auto" role="status" aria-live="polite">
          {status === "saving" && "Saving…"}
          {status === "saved" && "Saved"}
          {status === "error" && <span className="text-red-600">Couldn&apos;t save</span>}
        </span>
      </div>

      <div className="flex flex-col gap-4 sm:flex-row">
        <div className="flex-1">
          <label className="mb-2 block">
            <span className="mb-1 block text-xs text-neutral-500">Name</span>
            <input
              value={character.name}
              onChange={(e) => edit("name", e.target.value)}
              className={FIELD}
            />
          </label>

          <label className="mb-2 block">
            <span className="mb-1 block text-xs text-neutral-500">Role</span>
            <select
              value={character.role}
              onChange={(e) =>
                // Safe: the options below are generated from CHARACTER_ROLES,
                // so the runtime value can only be one of the literal members.
                edit("role", e.target.value as CharacterRole)
              }
              className={FIELD}
            >
              {CHARACTER_ROLES.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>

          <label className="mb-2 block">
            <span className="mb-1 block text-xs text-neutral-500">
              Who they are — their part in the story
            </span>
            <textarea
              value={character.description}
              onChange={(e) => edit("description", e.target.value)}
              rows={2}
              className={FIELD}
            />
          </label>

          <label className="mb-2 block">
            <span className="mb-1 block text-xs text-neutral-500">
              Voice — reads this character&apos;s lines. Changing it marks their audio out of
              date, and leaves their sheet alone.
            </span>
            <select
              value={character.voice_id ?? ""}
              onChange={(e) => edit("voice_id", e.target.value)}
              className={FIELD}
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

          <label className="block">
            <span className="mb-1 block text-xs text-neutral-500">
              Appearance — <strong className="font-medium">this is what gets drawn.</strong>{" "}
              Describe only what a camera would see; the image model has not read your story.
            </span>
            <textarea
              value={character.appearance}
              onChange={(e) => edit("appearance", e.target.value)}
              rows={4}
              className={FIELD}
            />
          </label>
        </div>

        <div className="flex w-full flex-col items-end gap-2 sm:w-56">
          <div className="flex w-full items-center justify-end gap-2 text-xs text-neutral-500">
            {/* A second badge, because the two mean different things: the
                character can be out of date with the story while their
                artwork is out of date with the character. */}
            {sheet?.state === "stale" && <StaleBadge />}
          </div>

          {sheet?.asset_id ? (
            /* eslint-disable-next-line @next/next/no-img-element -- next/image
               would proxy through the Next server, which has no session
               cookie for the API. */
            <img
              src={assetUrl(sheet.asset_id)}
              alt={`Reference sheet for ${character.name}`}
              className="w-full rounded border border-neutral-200 bg-neutral-50"
            />
          ) : (
            <div className="flex aspect-square w-full items-center justify-center rounded border border-dashed border-neutral-200 text-xs text-neutral-400">
              {drawing ? "Drawing…" : "No sheet yet"}
            </div>
          )}

          <QuoteButton
            credits={sheetCredits}
            balance={balance}
            busy={busy || drawing}
            busyLabel={drawing ? "Drawing…" : "Queued…"}
            label={sheet?.asset_id ? "Redraw sheet" : "Generate sheet"}
            onClick={() => onGenerateSheet(character)}
          />
        </div>
      </div>
    </li>
  );
}
