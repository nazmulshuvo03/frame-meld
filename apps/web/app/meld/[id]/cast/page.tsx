"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CharacterCard } from "@/components/CharacterCard";
import { CancelButton } from "@/components/CancelButton";
import { CreditBalance } from "@/components/CreditBalance";
import { QuoteButton } from "@/components/QuoteButton";
import { useJobPolling } from "@/lib/useJobPolling";
import {
  addCharacter,
  ApiError,
  deleteCharacter,
  generateCast,
  generateSheet,
  getBalance,
  getCast,
  getPricing,
  getVoices,
  type CastView,
  type Character,
  type Job,
} from "@/lib/api";

//: Fallbacks used only until /credits/pricing lands. Never the source of
//: truth -- the API's table is -- but a button that renders "· undefined
//: credits" for a beat on every load is worse than one that renders a
//: number that is almost always right.
const FALLBACK_CAST_CREDITS = 5;
const FALLBACK_SHEET_CREDITS = 12;

export default function CastPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [cast, setCast] = useState<CastView | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [quotes, setQuotes] = useState<Record<string, number> | null>(null);
  const [voices, setVoices] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadBalance = useCallback(
    () => getBalance().then((result) => setBalance(result.balance), () => {}),
    [],
  );

  const load = useCallback(
    () =>
      getCast(id).then(
        (result) => {
          setCast(result);
          setLoadError(null);
        },
        (err) => {
          // Mirrors the Script page's classification: only a 401 means the
          // session is gone, only a 404 means this Meld is not theirs.
          if (err instanceof ApiError && err.status === 401) router.replace("/login");
          else if (err instanceof ApiError && err.status === 404) router.replace("/melds");
          else setLoadError("Couldn't load this cast. Check your connection and reload.");
        },
      ),
    [id, router],
  );

  useEffect(() => {
    void load();
    void loadBalance();
    // A price list failure is not worth a banner: the fallbacks above keep
    // the buttons readable, and the API is still the one that refuses.
    getPricing().then((pricing) => setQuotes(pricing.quotes), () => {});
    // Same trade as the price list: a failure is not worth a banner. The
    // voice <select> falls back to showing only the character's current
    // voice, which is the one value that must never be lost.
    getVoices(id).then(setVoices, () => {});
  }, [id, load, loadBalance]);

  const onSettled = useCallback(
    (job: Job) => {
      setJobId(null);
      if (job.state === "failed") {
        setActionError(job.error ?? "That generation failed. Your credits were refunded.");
      } else {
        void load();
      }
      // Either way: a settle or a refund may have moved the balance, and a
      // stale number next to a spend button is worse than no number.
      void loadBalance();
    },
    [load, loadBalance],
  );

  const { job, cancel, cancelError } = useJobPolling({ meldId: id, jobId, onSettled });

  const start = async (request: () => Promise<Job>) => {
    setActionError(null);
    try {
      const started = await request();
      if (started.state === "succeeded") {
        // A cache hit: these exact inputs were generated before, so there
        // is nothing to wait for and nothing was charged.
        await load();
      } else {
        setJobId(started.id);
      }
      await loadBalance();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else if (err instanceof ApiError) setActionError(err.message);
      else setActionError("Couldn't start generating. Try again.");
      await loadBalance();
    }
  };

  const onGenerateCast = async () => {
    if (
      cast !== null &&
      cast.characters.some((character) => character.sheet?.asset_id) &&
      !window.confirm(
        "Regenerating the cast replaces every character, and the sheets you have " +
          "already paid for will be deleted. Continue?",
      )
    ) {
      return;
    }
    await start(() => generateCast(id));
  };

  const onGenerateSheet = useCallback(
    (character: Character) => void start(() => generateSheet(id, character.id)),
    // `start` is redefined each render but closes over nothing that changes
    // between them except the setters, which are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id],
  );

  const onSaved = useCallback(
    (saved: Character) =>
      setCast((current) =>
        current === null
          ? current
          : {
              ...current,
              characters: current.characters.map((c) => (c.id === saved.id ? saved : c)),
            },
      ),
    [],
  );

  if (loadError) {
    return (
      <main className="p-8">
        <p role="alert" className="text-sm text-danger">
          {loadError}
        </p>
      </main>
    );
  }

  if (cast === null) {
    return <main className="p-8 text-sm text-muted">Loading…</main>;
  }

  const busy = jobId !== null;
  const castCredits = quotes?.generate_characters ?? FALLBACK_CAST_CREDITS;
  const sheetCredits = quotes?.generate_character_image ?? FALLBACK_SHEET_CREDITS;

  // Every structural edit refetches rather than patching local state, for
  // the reason the Script page gives: deleting a character renumbers the
  // rest, clears them out of every shot, and marks the stills they appear in
  // stale. Reproducing that here would be a second implementation of the
  // staleness rules, kept in step by hand.
  const structural = (run: () => Promise<unknown>) => () => {
    setActionError(null);
    void run().then(
      () => void load(),
      (err) => {
        if (err instanceof ApiError) setActionError(err.message);
        else setActionError("Couldn't save that change. Try again.");
        void load();
      },
    );
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6">
        <div className="flex items-baseline justify-between">
          <Link href={`/meld/${id}/script`} className="text-sm text-muted hover:underline">
            ← Script
          </Link>
          <Link href={`/meld/${id}/shots`} className="text-sm text-muted hover:underline">
            Next up: Shots →
          </Link>
        </div>
        <div className="mt-1 flex items-baseline justify-between gap-4">
          <h1 className="text-2xl font-semibold">Cast</h1>
          <div className="flex items-center gap-3">
            <CreditBalance balance={balance} />
            {busy && (
              <CancelButton job={job} onCancel={() => void cancel()} error={cancelError} />
            )}
            <QuoteButton
              credits={castCredits}
              balance={balance}
              busy={busy}
              busyLabel={job?.state === "running" ? "Reading your story…" : "Queued…"}
              label={cast.characters.length ? "Regenerate cast" : "Generate cast"}
              onClick={onGenerateCast}
            />
          </div>
        </div>
        {cast.characters.length > 0 && (
          <p className="mt-1 text-sm text-muted">
            {cast.characters.length}{" "}
            {cast.characters.length === 1 ? "character" : "characters"}
          </p>
        )}
      </header>

      {cast.stale_characters > 0 && (
        <div
          role="status"
          className="mb-4 rounded border border-warn-border bg-warn p-4 text-sm text-warn-foreground"
        >
          <strong className="font-medium">Your story changed.</strong>{" "}
          {cast.stale_characters}{" "}
          {cast.stale_characters === 1 ? "character is" : "characters are"} out of date.
          Nothing has been deleted — regenerate when you&apos;re ready, or keep editing by
          hand.
        </div>
      )}

      {cast.stale_sheets > 0 && (
        <div
          role="status"
          className="mb-6 rounded border border-warn-border bg-warn p-4 text-sm text-warn-foreground"
        >
          <strong className="font-medium">A character changed.</strong> {cast.stale_sheets}{" "}
          {cast.stale_sheets === 1 ? "sheet is" : "sheets are"} out of date. Nothing has been
          deleted — redraw the ones you want.
        </div>
      )}

      {actionError && (
        <p role="alert" className="mb-6 text-sm text-danger">
          {actionError}
        </p>
      )}

      {cast.characters.length === 0 ? (
        <p className="text-sm text-muted">
          No cast yet. Generate one from your story to get characters you can edit and draw.
        </p>
      ) : (
        <ol className="flex flex-col gap-6">
          {cast.characters.map((character) => (
            <CharacterCard
              key={character.id}
              meldId={id}
              character={character}
              sheetCredits={sheetCredits}
              balance={balance}
              busy={busy}
              voices={voices}
              onSaved={onSaved}
              onGenerateSheet={onGenerateSheet}
              onDelete={structural(() => deleteCharacter(id, character.id))}
            />
          ))}
        </ol>
      )}

      {cast.characters.length > 0 && (
        <button
          type="button"
          onClick={structural(() => addCharacter(id))}
          className="mt-6 self-start rounded border border-subtle px-3 py-2 text-sm text-muted hover:bg-panel"
        >
          + Add a character
        </button>
      )}
    </main>
  );
}
