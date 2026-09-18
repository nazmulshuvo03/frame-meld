"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CancelButton } from "@/components/CancelButton";
import { CreditBalance } from "@/components/CreditBalance";
import { QuoteButton } from "@/components/QuoteButton";
import { ShotMediaCard } from "@/components/ShotMediaCard";
import { useJobPolling } from "@/lib/useJobPolling";
import {
  ApiError,
  castShots,
  generateSpeech,
  generateStill,
  getBalance,
  getPricing,
  getShots,
  type Job,
  type ShotMedia,
  type ShotsView,
} from "@/lib/api";

//: Fallbacks used only until /credits/pricing lands -- see the Cast page
//: for why these exist and why they are never the source of truth.
const FALLBACK_CASTING_CREDITS = 5;
const FALLBACK_STILL_CREDITS = 12;
const FALLBACK_SPEECH_CREDITS = 2;

export default function ShotsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();

  const [shots, setShots] = useState<ShotsView | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [quotes, setQuotes] = useState<Record<string, number> | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // M4-D5: "Generate all" is N ordinary per-shot jobs run one at a time,
  // not a batch job. A queue in the page rather than a second polling hook:
  // one job is in flight at once, which reuses useJobPolling unchanged and
  // rate-limits the provider for free. A shot that fails refunds its own
  // credit and the rest still get their pictures.
  const pending = useRef<string[]>([]);
  const [remaining, setRemaining] = useState(0);

  const loadBalance = useCallback(
    () => getBalance().then((result) => setBalance(result.balance), () => {}),
    [],
  );

  const load = useCallback(
    () =>
      getShots(id).then(
        (result) => {
          setShots(result);
          setLoadError(null);
        },
        (err) => {
          // Mirrors the Cast page's classification: only a 401 means the
          // session is gone, only a 404 means this Meld is not theirs.
          if (err instanceof ApiError && err.status === 401) router.replace("/login");
          else if (err instanceof ApiError && err.status === 404) router.replace("/melds");
          else setLoadError("Couldn't load these shots. Check your connection and reload.");
        },
      ),
    [id, router],
  );

  useEffect(() => {
    void load();
    void loadBalance();
    getPricing().then((pricing) => setQuotes(pricing.quotes), () => {});
  }, [load, loadBalance]);

  const drainQueue = useCallback(() => {
    // A local function declaration rather than the useCallback calling
    // itself: a hook value cannot be referenced before it is declared, and
    // the cache-hit branch below has to continue straight to the next shot
    // without waiting for a poll that will never happen.
    function step() {
      const next = pending.current.shift();
      setRemaining(pending.current.length);
      if (next === undefined) return;
      generateStill(id, next).then(
        (started) => {
          if (started.state === "succeeded") {
            // A cache hit: nothing to wait for and nothing charged.
            void load();
            step();
          } else {
            setJobId(started.id);
          }
        },
        (err) => {
          // One shot failing does not abandon the rest -- that is the whole
          // argument for per-shot jobs (M4-D5). The message is kept so the
          // user learns why, typically a 402 or a missing sheet.
          if (err instanceof ApiError) setActionError(err.message);
          step();
        },
      );
    }
    step();
  }, [id, load]);

  const onSettled = useCallback(
    (job: Job) => {
      setJobId(null);
      if (job.state === "failed") {
        setActionError(job.error ?? "That generation failed. Your credits were refunded.");
      } else {
        void load();
      }
      void loadBalance();
      drainQueue();
    },
    [drainQueue, load, loadBalance],
  );

  const { job, cancel, cancelError } = useJobPolling({ meldId: id, jobId, onSettled });

  const start = async (request: () => Promise<Job>) => {
    setActionError(null);
    try {
      const started = await request();
      if (started.state === "succeeded") await load();
      else setJobId(started.id);
      await loadBalance();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
      else if (err instanceof ApiError) setActionError(err.message);
      else setActionError("Couldn't start generating. Try again.");
      await loadBalance();
    }
  };

  const onGenerateStill = useCallback(
    (shot: ShotMedia) => void start(() => generateStill(id, shot.id)),
    // `start` is redefined each render but closes over nothing that changes
    // between them except the setters, which are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id],
  );

  const onGenerateSpeech = useCallback(
    (shot: ShotMedia) => void start(() => generateSpeech(id, shot.id)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id],
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

  if (shots === null) {
    return <main className="p-8 text-sm text-muted">Loading…</main>;
  }

  const busy = jobId !== null || remaining > 0;
  const castingCredits = quotes?.map_shot_characters ?? FALLBACK_CASTING_CREDITS;
  const stillCredits = quotes?.generate_shot_image ?? FALLBACK_STILL_CREDITS;
  const speechCredits = quotes?.generate_speech ?? FALLBACK_SPEECH_CREDITS;

  const missing = shots.shots.filter((shot) => !shot.still?.asset_id);

  const onGenerateAll = () => {
    setActionError(null);
    pending.current = missing.map((shot) => shot.id);
    setRemaining(pending.current.length);
    drainQueue();
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col p-8">
      <header className="mb-6">
        <div className="flex items-baseline justify-between">
          <Link href={`/meld/${id}/cast`} className="text-sm text-muted hover:underline">
            ← Cast
          </Link>
          <Link href={`/meld/${id}/film`} className="text-sm text-muted hover:underline">
            Next up: Film →
          </Link>
        </div>
        <div className="mt-1 flex flex-wrap items-baseline justify-between gap-4">
          <h1 className="text-2xl font-semibold">Shots</h1>
          <div className="flex flex-wrap items-center gap-3">
            <CreditBalance balance={balance} />
            {busy && (
              <CancelButton
                job={job}
                onCancel={() => {
                  // Drop the rest of the run as well. onSettled drains the
                  // queue, so cancelling one shot would otherwise start the
                  // next one — the opposite of what Stop means.
                  pending.current = [];
                  setRemaining(0);
                  void cancel();
                }}
                error={cancelError}
              />
            )}
            <QuoteButton
              credits={castingCredits}
              balance={balance}
              busy={busy}
              busyLabel={job?.state === "running" ? "Reading the script…" : "Queued…"}
              label={shots.cast_into_shots ? "Re-cast the shots" : "Cast the shots"}
              onClick={() => void start(() => castShots(id))}
            />
          </div>
        </div>
        {shots.shots.length > 0 && (
          <p className="mt-1 text-sm text-muted">
            {shots.shots.length} {shots.shots.length === 1 ? "shot" : "shots"} ·{" "}
            {shots.shots.length - missing.length} drawn
          </p>
        )}
      </header>

      {shots.shots.length > 0 && !shots.cast_into_shots && (
        <div
          role="status"
          className="mb-6 rounded border border-subtle bg-panel p-4 text-sm text-body"
        >
          <strong className="font-medium">Nobody is in a shot yet.</strong> Cast the shots
          first — that is what tells each still which character sheets to draw from, and it
          is the only thing keeping faces consistent between frames.
        </div>
      )}

      {shots.stale_stills > 0 && (
        <div
          role="status"
          className="mb-4 rounded border border-warn-border bg-warn p-4 text-sm text-warn-foreground"
        >
          <strong className="font-medium">Something upstream changed.</strong>{" "}
          {shots.stale_stills} {shots.stale_stills === 1 ? "still is" : "stills are"} out of
          date. Nothing has been deleted — redraw the ones you want.
        </div>
      )}

      {shots.stale_audio > 0 && (
        <div
          role="status"
          className="mb-6 rounded border border-warn-border bg-warn p-4 text-sm text-warn-foreground"
        >
          <strong className="font-medium">A line or a voice changed.</strong>{" "}
          {shots.stale_audio} {shots.stale_audio === 1 ? "clip is" : "clips are"} out of date.
          Nothing has been deleted — re-record the ones you want.
        </div>
      )}

      {actionError && (
        <p role="alert" className="mb-6 text-sm text-danger">
          {actionError}
        </p>
      )}

      {shots.shots.length === 0 ? (
        <p className="text-sm text-muted">
          No shots yet. Generate a script first, then come back to draw them.
        </p>
      ) : (
        <>
          {missing.length > 0 && (
            <div className="mb-6 flex items-center justify-end gap-3">
              {remaining > 0 && (
                <span role="status" className="text-sm text-muted">
                  {remaining} left…
                </span>
              )}
              <QuoteButton
                credits={stillCredits * missing.length}
                balance={balance}
                busy={busy}
                busyLabel="Drawing…"
                label={`Draw the ${missing.length} remaining`}
                onClick={onGenerateAll}
              />
            </div>
          )}

          <ol className="flex flex-col gap-6">
            {shots.shots.map((shot) => (
              <ShotMediaCard
                key={shot.id}
                shot={shot}
                stillCredits={stillCredits}
                speechCredits={speechCredits}
                balance={balance}
                busy={busy}
                onGenerateStill={onGenerateStill}
                onGenerateSpeech={onGenerateSpeech}
              />
            ))}
          </ol>
        </>
      )}
    </main>
  );
}
