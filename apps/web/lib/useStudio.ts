"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  castShots,
  generateCast,
  generateClip,
  generateSheet,
  generateSpeech,
  generateStill,
  generateScript,
  getBalance,
  getCast,
  getFilm,
  getMeld,
  getPricing,
  getScript,
  getShots,
  getVoices,
  type CastView,
  type FilmView,
  type Job,
  type MeldDetail,
  type ScriptView,
  type ShotsView,
} from "@/lib/api";
import { useJobPolling } from "@/lib/useJobPolling";

/** Prices used only until `/credits/pricing` lands.
 *
 *  Never the source of truth — the API's table is, and the API is what
 *  refuses — but a button that reads "· undefined credits" for a beat on
 *  every load is worse than one that reads a number that is almost always
 *  right. Same trade the five separate pages each made; made once here.
 */
const FALLBACK_QUOTES: Record<string, number> = {
  generate_characters: 5,
  generate_character_image: 12,
  map_shot_characters: 5,
  generate_shot_image: 12,
  generate_speech: 2,
  generate_music: 24,
  generate_shot_clip: 80,
  render_final: 60,
};

export type Studio = ReturnType<typeof useStudio>;

/**
 * Everything the studio page knows, in one place.
 *
 * The five stages used to be five routes, each with its own copy of: load,
 * loadBalance, a jobId, an onSettled, a `start`, a `structural`, and a
 * per-page queue. That duplication was invisible while they were separate
 * screens and becomes incoherent the moment they share one scroll — two
 * stages could each think they owned "the" running job, and the balance
 * shown next to a Cast button could disagree with the one next to a Film
 * button on the same screen.
 *
 * So there is exactly one job in flight at a time for the whole page. That
 * is not a simplification of the old behaviour, it is what the old
 * behaviour already was: one arq worker, one `useJobPolling`, one Stop
 * button. Making it explicit is what lets the transport bar say what the
 * studio is doing without asking five components to agree.
 */
export function useStudio(id: string) {
  const router = useRouter();
  // Held in a ref, and this is not defensive dressing: every `useCallback`
  // below that can navigate would otherwise list `router` as a dependency,
  // and if the router object's identity is not stable across renders then
  // `classify` changes, so `loadAll` changes, so the load effect re-runs,
  // which sets state and renders again — a loop that refetches the whole
  // Meld forever. Next's own router is stable, so this never fired in the
  // browser; it fired instantly under a test that mocks `useRouter` as
  // `() => ({ replace })`. A hook should not depend on its caller
  // memoising something for it.
  const routerRef = useRef(router);
  useEffect(() => {
    routerRef.current = router;
  }, [router]);

  const [meld, setMeld] = useState<MeldDetail | null>(null);
  const [script, setScript] = useState<ScriptView | null>(null);
  const [cast, setCast] = useState<CastView | null>(null);
  const [shots, setShots] = useState<ShotsView | null>(null);
  const [film, setFilm] = useState<FilmView | null>(null);
  const [balance, setBalance] = useState<number | null>(null);
  const [quotes, setQuotes] = useState<Record<string, number>>(FALLBACK_QUOTES);
  const [voices, setVoices] = useState<string[]>([]);

  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  // A run of per-shot jobs, one at a time (M4-D5, M6-D11). One queue rather
  // than the Shots page's and the Film page's, because only one job runs at
  // a time anyway and two queues draining into one poller would interleave.
  const pending = useRef<{ ids: string[]; run: (shotId: string) => Promise<Job> } | null>(null);
  const [remaining, setRemaining] = useState(0);

  /** 401 means the session is gone; 404 means this Meld is not theirs.
   *  Anything else is a blip that must not masquerade as either. */
  const classify = useCallback((err: unknown, fallback: string) => {
    if (err instanceof ApiError && err.status === 401) routerRef.current.replace("/login");
    else if (err instanceof ApiError && err.status === 404) routerRef.current.replace("/melds");
    else setLoadError(fallback);
  }, []);

  const loadBalance = useCallback(
    () => getBalance().then((result) => setBalance(result.balance), () => {}),
    [],
  );

  /** Refetch every stage.
   *
   *  All of them, on any change, deliberately. A story edit marks shots
   *  stale, a character edit marks stills stale, a still marks the film
   *  stale — the staleness graph crosses every stage boundary, so there is
   *  no such thing as refreshing "just this section". Reproducing the
   *  server's arithmetic client-side to avoid five cheap GETs would be a
   *  second implementation of the staleness rules kept in step by hand,
   *  which is the trade each page already refused on its own.
   */
  //: A `.then()` chain rather than `async`/`await`, for the reason M2
  //: recorded when the Script page hit it: `react-hooks/set-state-in-effect`
  //: reads an awaited `setState` in a function the effect calls as a
  //: synchronous setState in the effect body. Inside a callback it is the
  //: same code and the rule is satisfied, which is the correct outcome —
  //: these really are responses to a request, not renders.
  const loadAll = useCallback(
    () =>
      Promise.all([getMeld(id), getScript(id), getCast(id), getShots(id), getFilm(id)]).then(
        ([meldResult, scriptResult, castResult, shotsResult, filmResult]) => {
          setMeld(meldResult);
          setScript(scriptResult);
          setCast(castResult);
          setShots(shotsResult);
          setFilm(filmResult);
          setLoadError(null);
        },
        (err) => classify(err, "Couldn't load this Meld. Check your connection and reload."),
      ),
    [id, classify],
  );

  useEffect(() => {
    void loadAll();
    void loadBalance();
    // Neither of these is worth a banner if it fails: the fallback prices
    // keep every button readable, and the voice list falls back to the one
    // value that must never be lost, the character's current voice.
    getPricing().then((pricing) => setQuotes({ ...FALLBACK_QUOTES, ...pricing.quotes }), () => {});
    getVoices(id).then(setVoices, () => {});
  }, [id, loadAll, loadBalance]);

  const drainQueue = useCallback(() => {
    // A local declaration rather than the useCallback calling itself: the
    // cache-hit branch has to continue straight to the next shot without
    // waiting for a poll that will never happen.
    function step() {
      const queue = pending.current;
      const next = queue?.ids.shift();
      setRemaining(queue?.ids.length ?? 0);
      if (queue === null || next === undefined) return;
      queue.run(next).then(
        (started) => {
          if (started.state === "succeeded") {
            void loadAll();
            step();
          } else {
            setJobId(started.id);
          }
        },
        (err) => {
          // One shot failing does not abandon the rest — that is the whole
          // argument for per-shot jobs. Typically a 402 or a missing sheet.
          if (err instanceof ApiError) setActionError(err.message);
          step();
        },
      );
    }
    step();
  }, [loadAll]);

  const onSettled = useCallback(
    (settled: Job) => {
      setJobId(null);
      if (settled.state === "failed") {
        setActionError(settled.error ?? "That failed. Anything charged was refunded.");
      } else {
        void loadAll();
      }
      // Either way: a settle or a refund may have moved the balance, and a
      // stale number next to a spend button is worse than no number.
      void loadBalance();
      drainQueue();
    },
    [drainQueue, loadAll, loadBalance],
  );

  const { job, cancel, cancelError } = useJobPolling({ meldId: id, jobId, onSettled });

  /** Start one job and watch it, or notice it was a cache hit. */
  const start = useCallback(
    async (request: () => Promise<Job>) => {
      setActionError(null);
      try {
        const started = await request();
        // A cache hit comes back already succeeded: these exact inputs were
        // generated before, so there is nothing to poll and nothing was
        // charged.
        if (started.state === "succeeded") await loadAll();
        else setJobId(started.id);
        await loadBalance();
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) routerRef.current.replace("/login");
        else if (err instanceof ApiError) setActionError(err.message);
        else setActionError("Couldn't start. Try again.");
        await loadBalance();
      }
    },
    [loadAll, loadBalance],
  );

  /** Run one per-shot action across many shots, one at a time. */
  const startRun = useCallback(
    (ids: string[], run: (shotId: string) => Promise<Job>) => {
      setActionError(null);
      pending.current = { ids: [...ids], run };
      setRemaining(ids.length);
      drainQueue();
    },
    [drainQueue],
  );

  /** A structural edit — add, delete, reorder — refetches rather than
   *  patching local state, because it renumbers siblings and can mark work
   *  stale two stages downstream. */
  const structural = useCallback(
    (run: () => Promise<unknown>) => () => {
      setActionError(null);
      void run().then(
        () => void loadAll(),
        (err) => {
          if (err instanceof ApiError) setActionError(err.message);
          else setActionError("Couldn't save that change. Try again.");
          void loadAll();
        },
      );
    },
    [loadAll],
  );

  // A trailing debounce in front of loadAll, for the inline field saves.
  // Those are already debounced at 800ms by useAutosave, but a user typing
  // through several fields would otherwise fire a five-request refresh per
  // field. One refresh once they stop is what the badges need.
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshStaleness = useCallback(() => {
    if (refreshTimer.current !== null) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      void loadAll();
    }, 600);
  }, [loadAll]);

  useEffect(
    () => () => {
      if (refreshTimer.current !== null) clearTimeout(refreshTimer.current);
    },
    [],
  );

  const stopEverything = useCallback(() => {
    // Drop the rest of the run too. onSettled drains the queue, so
    // cancelling one shot would otherwise start the next — the opposite of
    // what Stop means.
    pending.current = null;
    setRemaining(0);
    void cancel();
  }, [cancel]);

  return {
    id,
    meld,
    script,
    cast,
    shots,
    film,
    balance,
    quotes,
    voices,
    loadError,
    actionError,
    setActionError,
    job,
    busy: jobId !== null || remaining > 0,
    remaining,
    cancelError,
    stopEverything,
    loadAll,
    refreshStaleness,
    setScript,
    setCast,
    setMeld,

    // The actions, named for what the user is doing rather than for the
    // endpoint underneath.
    writeScript: () => void start(() => generateScript(id)),
    findCast: () => void start(() => generateCast(id)),
    drawSheet: (characterId: string) => void start(() => generateSheet(id, characterId)),
    castTheShots: () => void start(() => castShots(id)),
    drawStill: (shotId: string) => void start(() => generateStill(id, shotId)),
    recordSpeech: (shotId: string) => void start(() => generateSpeech(id, shotId)),
    drawStills: (ids: string[]) => startRun(ids, (shotId) => generateStill(id, shotId)),
    animateShots: (ids: string[]) => startRun(ids, (shotId) => generateClip(id, shotId)),
    start,
    structural,
  };
}
