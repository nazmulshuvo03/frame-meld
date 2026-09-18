# M5 decision record

Same purpose as [`m1-decisions.md`](m1-decisions.md) through [`m4-decisions.md`](m4-decisions.md):
every judgement call made while executing M5, so a future contributor can find out why
without re-deriving it. Executed against
[`plans/2026-09-18-m5-preview.md`](plans/2026-09-18-m5-preview.md), with the backend and
frontend gates run throughout and a full end-to-end pass against the real services at the
end.

## M5-D1 … M5-D12 (decided before implementation)

Recorded in full in the plan. Summary:

- **M5-D1** — asset URLs are HMAC-signed; storage stays local disk and R2 stays deferred.
- **M5-D2** — music is a stub provider only; no Lyria adapter.
- **M5-D3** — the music prompt is derived from the Meld, not authored, and gets no column.
- **M5-D4** — a Preview is refused when any shot has no finished still.
- **M5-D5** — motion is derived from `camera` and position; `motion_prompt` is not read.
- **M5-D6** — Preview renders at 1280×720, 30 fps.
- **M5-D7** — the Preview render takes no hold and has no quote.
- **M5-D8** — `renders.input_hash` is computed over the spec with content hashes in place of
  the signed URLs.
- **M5-D9** — the render service keeps finished files in a temp directory.
- **M5-D10** — the worker polls the render service inside the job.
- **M5-D11** — one Remotion composition serves both kinds.
- **M5-D12** — music is one track across the film, ducked under the speech.

Eleven survived execution unchanged. **M5-D11 was narrowed**, and that is recorded below.

---

## M5-D11, corrected: the composition does not branch on `clip_url` yet

As written, M5-D11 said the composition would read a shot's clip when the spec carried one,
so that M6 added nothing but a source swap. The branch is three lines and it was tempting.
It is not there.

The argument against it is the same one M5-D2 makes about Lyria, and it would have been
inconsistent to accept one and not the other: nothing in M5 can produce a `clip_url`, so the
branch could never run, could never be tested, and would sit in the render path looking
maintained. This codebase already carries two provider adapters whose success path has never
executed; adding a third never-executed path *inside the renderer* — the one component whose
failures are silent, because a wrong film still renders — is the worst place to put one.

What the decision actually bought is still bought: `clip_url` is in the contract on both
sides, `kind` is on the spec, and the Python assembler writes `clip_url: null` today. M6
adds a conditional in `Film.tsx` and a clip lookup in `build_scene_spec`, and changes
nothing else. The claim that has been dropped is only that the branch exists now.

---

## The expensive knowledge: the render is the first artifact whose own hash cannot contain what it is sent

Every generated artifact before this one keys on a hash of its inputs, and its inputs are
rows. A render's input is a *document* — the scene spec — and that document contains signed
URLs, and a signature contains an expiry timestamp. Hash the document as sent and every
render is unique: the idempotency key never matches, the cache never hits, and a button
whose entire abuse control is "an unchanged film starts no work" (M5-D7) starts work every
single time.

This is M4's casting bug seen from the other side. There, the job moved a value its own key
was derived from. Here, the key would be derived from a value that moves on its own, without
anybody touching the film. Both produce the same symptom — a cache that can never hit — and
in both cases every unit test of the hash function passes, because the hash function is
fine.

The fix is that `SceneSpec` produces two documents from one walk (M5-D8): `to_request()`,
with signed URLs, and `to_hash_payload()`, with each artifact's `content_hash` in the same
position. They are written next to each other, field for field, deliberately — the failure
this shape is guarding against is the two drifting into describing different films, and
adjacency is the cheapest guard available.

`test_the_hash_is_stable_across_two_assemblies_of_the_same_film` is the test that would have
caught it. So would the browser pass, but a full render later and with the cause much harder
to see.

**The generalisation, now four milestones deep:** an artifact's `input_hash` must cover
exactly the facts that reach its producer and nothing else — not the *encoding* of those
facts, and not anything the producer writes. A signed URL, a timestamp, a row id and a
generated field are all the same kind of mistake.

---

## Gaps and corrections found during execution

**The asset route could not take a signature while `CurrentUser` was a dependency.** FastAPI
resolves dependencies before the handler body, so `CurrentUser` raises 401 before any code
that could check a signature runs. The session is now resolved inside the handler instead.
That is a small change with a real consequence: the 401-versus-404 decision moved from a
shared dependency into one route, so it had to be made deliberately rather than inherited.

It was made *conservatively*. The first version answered 404 to an unauthenticated caller,
reasoning that a 404 tells an anonymous caller nothing. `test_serving_requires_authentication`
— written in M3 — failed, which was the correct outcome: the old answer leaked nothing
either (401 is returned whether or not the asset exists), so there was no security argument
for the change, only a preference. The 401 stands, and the route now has an explicit comment
saying why each of its two "no" answers is the code it is.

**`renders` needed no enum migration at all, and that is worth noticing.** M5 is the first
milestone to write `generate_music`, `render_preview`, `AudioKind.MUSIC` and
`AudioOwnerType.MELD`, and every one of those values was already in its CHECK constraint —
`JobKind` because M2 declared spec §5.4's full list, the audio ones because M4-D4 declared
them when the table was created. The contrast with M4, where `map_shot_characters` was *not*
on that list and cost a hand-written drop-and-recreate, is the whole case for declaring enum
values ahead of the code that writes them.

**Port 8787 was taken.** The render service's first default collided with a process already
bound to `127.0.0.1:8787` on the development machine, and the symptom was the service
appearing to start, `netstat` showing it listening on `0.0.0.0:8787`, and every request
being answered by something else entirely. It listens on 8790. Worth recording only because
"the service is up and answering, with the wrong answers" cost more time to diagnose than a
refused connection would have.

**Node strips types but does not rewrite specifiers.** Node 25 runs `.ts` directly, which is
why the render service needs no build step — but it does not resolve a `./render.js` import
to `render.ts` the way a bundler does. Every relative import in `services/render` carries its
real extension (`./types.ts`, `./Film.tsx`), and `allowImportingTsExtensions` is on. This is
the one thing about that service that looks wrong to a TypeScript reader and is right.

**Testing a page needed `await act(async () => …)`.** The Film page is the first *page*
component with a test — M1–M4 tested hooks and components only. React 19's `use()` on the
route params suspends, and inside Testing Library's synchronous act scope that promise never
resolves, so the tree sits on the Suspense fallback until the test times out with an empty
DOM and no error. The helper in `test/filmPage.test.tsx` carries the explanation, because
the symptom looks like a broken mock rather than a scheduling problem.

---

## Things that are deliberately not done

- **No Lyria, ACE-Step or any real music model** (M5-D2). `MUSIC_PROVIDER=stub` is the only
  name the registry resolves, and it raises on anything else rather than falling back.
- **No `clip_url` branch in the composition** — see the M5-D11 correction above.
- **No S3/R2** (M5-D1). Local disk plus signed URLs is what the render service needs. The
  `Storage` protocol is untouched, so R2 remains a two-file addition.
- **No transitions but cuts.** `transition_in` is in the contract and always `{"kind":
  "cut"}`. A dissolve is a composition change, not a spec change, which is why the field
  exists now.
- **No progress reporting to the user.** The render service tracks and serves a progress
  fraction; the worker reads it only to decide whether to keep waiting, and the job row has
  nowhere to put it. A 30-shot preview renders in well under a minute, so the button saying
  "Assembling…" is honest. It will not be at M6.
- **No `Final` render, and `RenderKind.FINAL` is declared and never produced.** Same rule as
  every other enum value declared a milestone early.
- **`renders.params` and `renders.credits_spent` are written as their defaults.** A preview
  costs nothing (M5-D7) and has no provider parameters to record; the columns exist because
  spec §5.3's standard set is the standard set.
- **No orphan sweep for stranded render files.** The render service drops its temp directory
  contents on restart and sweeps its own index after 30 minutes; a file whose worker died
  between "done" and "downloaded" is left on disk. Same intended failure direction as M3's
  asset-before-row ordering.

---

## Verification

### Backend gates

- **Suite: 591 passed, 4 skipped** (up from M4's 516/4). The four skips are the same opt-in
  live-API calls; M5 adds no new ones, because it adds no new network adapter.
- `ruff check src tests` and `ruff format --check src tests` clean.
- **Migration:** `alembic upgrade head` applied `ca0a9cfdfb6b` cleanly, and `alembic check`
  reports **"No new upgrade operations detected"**.

### Frontend gates

- **38 tests passed** (up from 29): the previous 29 plus 9 new `FilmPage`.
- `tsc --noEmit`, `eslint` and `next build` all clean; `/meld/[id]/film` is in the route
  table.

### Render service gates

- `tsc --noEmit` clean.
- An isolated end-to-end probe — two PNGs and two WAVs served over plain HTTP, a scene spec
  posted, polled, and the MP4 downloaded — produced a **3.56 s, 640×360, h264 + aac** file,
  and a spec claiming `version: 99` was refused with **400** rather than rendered on a guess.

### The full stack, end to end

A real api, a real worker, the real render service and a fresh account, with the image,
speech and music providers on their stubs and `LLM_PROVIDER=stub` (Gemini's free tier
answered 429 on the first attempt of the session, exactly as it did throughout M4).

| Check | Result |
|---|---|
| Story → script → cast → sheets → casting → 6 stills → 2 speech clips | every job `succeeded` |
| Film page before rendering | **6 / 6 shots drawn, 21.5 s** |
| Score the film | `succeeded`; one `audio_clips` row with `owner_type = meld`, `kind = music` — the polymorphic owner M4-D4 declared, finally written |
| Render the preview | **21 s**, and the balance moved by **0 credits** (M5-D7) |
| The file | **910,341 bytes**, `video/mp4`, and `getVideoMetadata` reports **21.55 s, 1280×720, h264 + aac** — the narration and the score are both in it |
| How the render service read the stills | **10 signed asset fetches, every one 200**, from a process with no session cookie and no database (M5-D1) |
| Press Render again, unchanged | **`succeeded` immediately, no render ran** — spec §4.1's cache hit, which is this action's entire abuse control |
| Edit one shot's duration | the preview went **stale and kept its file** — spec §4.4, at the last node in the graph |

That last pair is the milestone in two rows: a free button that does nothing when nothing
changed, and an edit that invalidates the film without destroying it.

### What is **not** verified

**No real music model has ever been called, and none exists in this codebase** (M5-D2). That
is a deliberate absence rather than an untested path — `get_music_provider` raises on any
name but `stub` — but it means the first real `MusicProvider` will be written against
documented behaviour, exactly as the image and speech adapters were, and should be probed
before it is trusted with credits.

**The composition has never rendered a real AI still.** Every frame in every render so far
came from `solid_png`, which is a flat colour. The Ken Burns numbers (M5-D5) are therefore
*unjudged*: the arithmetic is tested, and a solid rectangle panning across a frame proves
the transform is applied, but whether a slow push into a real generated image reads as
cinematic or as a wobble is a question only a real still can answer. The first thing to do
when an image key is available is watch one preview and adjust `_MOTION` in
`render/spec.py`.

**The render has never run against a Meld at the product's stated size.** Six shots and 21.5
seconds is a third of the low end of spec §1's 10–30 shots and 1–3 minutes. `job_timeout` is
set to `render_timeout_seconds + 120` on the assumption that a 30-shot preview stays well
inside ten minutes; at 21 seconds for six shots that is a safe assumption, but it is an
extrapolation, not a measurement.

---

## Process notes

**The duplicate-process trap did not recur, because it was checked for.** m2-, m3- and
m4-decisions all record it. This time `tasklist /FI "IMAGENAME eq arq.exe"` and the same for
`uvicorn.exe` were run after every restart, confirming exactly one of each before trusting
anything. It took ten seconds per restart and it is now the only reason that is not a fourth
entry in this section.

**The first end-to-end run failed on a Gemini 429 and that was useful.** It proved the flow
script's failure reporting worked before it had anything real to report, and it is the fourth
consecutive milestone in which Gemini's free tier has been unavailable when wanted. Treat
`LLM_PROVIDER=gemini` as unusable for a verification pass and set it deliberately when
probing the model itself.
