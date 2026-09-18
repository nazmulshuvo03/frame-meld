"use client";

import { CharacterCard } from "@/components/CharacterCard";
import { QuoteButton } from "@/components/QuoteButton";
import { Empty, Notice, Stage } from "@/components/studio/Stage";
import { addCharacter, deleteCharacter, type CastView, type Character } from "@/lib/api";
import type { Studio } from "@/lib/useStudio";

export function CastStage({ studio, cast }: { studio: Studio; cast: CastView }) {
  const { id, busy, job, balance, quotes, voices } = studio;
  const castCredits = quotes.generate_characters;
  const sheetCredits = quotes.generate_character_image;
  const reading = busy && job?.kind === "generate_characters";

  const onFindCast = () => {
    if (
      cast.characters.some((character) => character.sheet?.asset_id) &&
      !window.confirm(
        "Finding the cast again replaces every character, and the sheets you have " +
          "already paid for will be deleted. Continue?",
      )
    ) {
      return;
    }
    studio.findCast();
  };

  return (
    <Stage
      id="cast"
      title="Cast"
      lede="Who is in the film, and what each of them looks like. A sheet is the reference every still of that character is drawn from — it is what keeps a face the same from shot to shot."
      summary={
        cast.characters.length > 0 ? (
          <span className="font-mono tabular-nums">
            {cast.characters.length}{" "}
            {cast.characters.length === 1 ? "character" : "characters"} ·{" "}
            {cast.characters.filter((c) => c.sheet?.asset_id).length} drawn
          </span>
        ) : null
      }
      action={
        <QuoteButton
          credits={castCredits}
          balance={balance}
          busy={busy}
          busyLabel={reading && job?.state === "running" ? "Reading…" : "Queued…"}
          label={cast.characters.length ? "Find the cast again" : "Find the cast"}
          onClick={onFindCast}
        />
      }
      notices={
        <>
          {cast.stale_characters > 0 && (
            <Notice>
              <strong className="font-medium">Your story changed.</strong>{" "}
              {cast.stale_characters}{" "}
              {cast.stale_characters === 1 ? "character is" : "characters are"} out of date.
              Nothing has been deleted — find the cast again, or keep editing by hand.
            </Notice>
          )}
          {cast.stale_sheets > 0 && (
            <Notice>
              <strong className="font-medium">A character changed.</strong> {cast.stale_sheets}{" "}
              {cast.stale_sheets === 1 ? "sheet is" : "sheets are"} out of date. Nothing has
              been deleted — redraw the ones you want.
            </Notice>
          )}
        </>
      }
    >
      {cast.characters.length === 0 ? (
        <Empty>
          Write a story above, then find the cast to get characters you can edit and draw.
        </Empty>
      ) : (
        <>
          <ol className="grid gap-6">
            {cast.characters.map((character: Character) => (
              <CharacterCard
                key={character.id}
                meldId={id}
                character={character}
                sheetCredits={sheetCredits}
                balance={balance}
                busy={busy}
                voices={voices}
                onSaved={(saved) => {
                  studio.setCast((current) =>
                    current === null
                      ? current
                      : {
                          ...current,
                          characters: current.characters.map((c) =>
                            c.id === saved.id ? saved : c,
                          ),
                        },
                  );
                  // Editing an appearance marks that character's sheet stale
                  // and every still they appear in with it. See ScriptStage.
                  studio.refreshStaleness();
                }}
                onGenerateSheet={(target) => studio.drawSheet(target.id)}
                onDelete={studio.structural(() => deleteCharacter(id, character.id))}
              />
            ))}
          </ol>

          <button
            type="button"
            onClick={studio.structural(() => addCharacter(id))}
            className="mt-6 rounded border border-subtle px-3 py-2 text-sm text-muted hover:bg-panel"
          >
            + Add a character
          </button>
        </>
      )}
    </Stage>
  );
}
