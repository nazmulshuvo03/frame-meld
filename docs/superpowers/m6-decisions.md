# M6 decision record

Same purpose as [`m1-decisions.md`](m1-decisions.md) through [`m5-decisions.md`](m5-decisions.md):
every judgement call made while executing M6, so a future contributor can find out why
without re-deriving it. Executed against
[`plans/2026-09-18-m6-film.md`](plans/2026-09-18-m6-film.md), with the backend, frontend and
render-service gates run throughout and a full end-to-end pass against the real services at
the end.

This is the last milestone in spec §9.

## M6-D1 … M6-D11 (decided before implementation)

Recorded in full in the plan. Summary:

- **M6-D1** — a clip keys on its source still's `content_hash` plus `{motion_prompt, duration_sec}`.
- **M6-D2** — a clip is refused with no still, and allowed with a *stale* still.
- **M6-D3** — a clip is 80 credits and the Final render 60.
- **M6-D4** — still no batch job; N per-shot jobs, one at a time.
- **M6-D5** — the composition's `clip_url` branch lands now.
- **M6-D6** — Final renders at 1920×1080.
- **M6-D7** — a Final is refused unless every shot has a clip with bytes.
- **M6-D8** — the stub video provider emits a real MP4, encoded by ffmpeg.
- **M6-D9** — `VIDEO_PROVIDER` defaults to `stub`; no Wan or Veo adapter is written.
- **M6-D10** — download is a `?download=1` flag on the asset route.
- **M6-D11** — clips are driven from the Film page with one batch button.

All eleven survived execution. One of them, M6-D8, was **wrong in a way a test caught**, and
that is the entry below.

---

## The expensive knowledge: a stub that ignores an input makes the input look inert

M6-D8 said the stub video provider should derive its output from the still's bytes, "so two
shots with the same motion prompt and different pictures must not produce the same clip".
That reasoning is right and the conclusion was half a conclusion: it keyed on the image
*only*, so the same still given two different motion prompts produced byte-identical video.

`test_animating_a_shot_marks_the_final_stale_and_leaves_the_preview_fresh` failed, and the
failure is worth reading carefully, because the thing that broke was not the stub. The chain
is: a clip's `content_hash` is a hash of its bytes → the Final render's `input_hash` covers
every clip's `content_hash` → so a re-animation that produced identical bytes left the Final
render **legitimately fresh**. Every layer behaved correctly. The staleness engine reported,
accurately, that nothing had changed — because under that stub, nothing had.

A real image-to-video model reads both the image and the instruction. A stub that reads one
of them does not merely produce boring output; it silently asserts that the other input has
no effect, and everything downstream of the bytes — the hash, the staleness walk, the
"re-animate" button, the user's 80 credits — then agrees with it. The bug would have shipped
as "changing a motion prompt and paying to re-animate does not mark the film out of date",
which is a money bug wearing a rendering bug's clothes.

The fix is one line: `sha256(image + b"\x00" + motion_prompt.encode())`. The rule it is worth
stating, though, is broader, and it is the fourth variation on the hashing rule this project
keeps re-learning:

> **A stub must depend on every input the real thing depends on.** M3's `solid_png`, M4-D8's
> WAV and M5-D2's music stub were all about the *format* being real. This one is about the
> *dependencies* being real. A stub with a narrower input set than the adapter it stands in
> for will make some upstream edit look like it has no consequences — and in a system whose
> entire architecture is "what depends on what", that is the one lie that corrupts
> everything built on top of it.

---

## Gaps and corrections found during execution

**`QuoteButton` conflated "busy" with "not allowed yet".** The Final render button is
disabled until every shot has a clip, and the only way to disable a `QuoteButton` was to pass
`busy`, which makes it read "Queued…". So a user who had not animated anything saw a button
claiming work was queued, with no queue and no explanation. A `disabled` prop now separates
the two. Small, but it is the kind of thing that makes an app feel broken: the label was
describing a state the app was not in.

**`test_an_unpriced_kind_raises_rather_than_defaulting_to_free` named `RENDER_FINAL`.** It
was written in M3 and picked an arbitrary unpriced kind to prove `quote_for` raises rather
than quoting zero. M6 priced that kind, so the test failed — correctly, and by design: a
newly-free billable action is exactly what that test exists to catch. It now names
`RENDER_PREVIEW`, which is the one action that is deliberately and *permanently* unpriced
(M5-D7), so it asserts the behaviour and the decision together and cannot be invalidated by a
later milestone pricing something.

The companion test, `test_every_billable_job_kind_has_a_price`, had been checking two kinds
since M3 while five more were added around it. It now checks all eight.

**The suite ran out of credits, which was the pricing working.** At 80 credits a clip, a
six-shot film costs more than the 500-credit signup grant, so the first `animated` fixture
run 402'd. That is spec §7's arithmetic arriving in the test suite rather than a fixture bug
— a whole film is meant to cost 2,500–3,000 credits — so the fixture grants rather than the
price being lowered.

**`_run_render` replaced two near-identical functions before they existed.** M5 shipped
`request_preview` and `run_render_preview`; M6 needed the same pair for Final, differing in
three places, all of which were already data (size, visual source, whether money changes
hands). Two copies would have been two places for the idempotency rule and the
hash-mismatch check to drift. The Preview path also gained a `_close_hold` call it does not
need, deliberately — it is a no-op at zero credits, and calling it unconditionally keeps
"every terminal job closes its hold" a single greppable line rather than a rule with an
exception.

**A NUL byte got written into the source.** A patch script emitted `b"<NUL>"` instead of
`b"\x00"` and Python refused to import the module. Recorded only because the error —
`SyntaxError: source code string cannot contain null bytes` — points at the *test* file that
imports it rather than the file that contains it.

---

## Things that are deliberately not done

- **No real video model, and no adapter for one** (M6-D9). `VIDEO_PROVIDER=stub` is the only
  name the registry resolves, and it raises on anything else. Spec §6 names Wan 2.2 via a
  hosted API for production; §6.3 names Veo 3.1 Lite for local development. Both are billed
  per second and this key has no billing.
- **No per-clip regenerate control** (M6-D11). The batch button queues every shot whose clip
  is missing or stale. A per-shot control is a second copy of `ShotMediaCard` for an action
  taken on the whole film.
- **No moderation on the clip job.** The still it animates already passed the gate when it
  was drawn, and `motion_prompt` describes camera movement rather than content. A second
  classification of an approved picture would be a second chance to refuse work the user has
  already paid to produce.
- **`settle`'s variable-units path is still unexercised, and now permanently.** M3's roadmap
  expected M6's clip generation to be where it earned its keep. It is not, because M6-D4 kept
  the per-shot job shape — and after four milestones the honest conclusion is that every
  billable action in this product is one indivisible unit of work. The path stays, because it
  is the correct implementation of spec §4.3 and deleting it would be a regression against
  the spec, but it is a known-unexercised branch rather than a deferral.
- **Only cuts, still.** `transition_in` has been in the contract since M5 and is always
  `{"kind": "cut"}`.
- **No render progress.** Same as M5: the render service tracks a fraction, the worker reads
  it only to decide whether to keep waiting, and the job row has nowhere to put it. A 36-second
  Final render makes "Assembling…" honest; a 30-shot one at 1080p may not.
- **Stripe is still absent** (spec §10). A film now costs ~2,900 credits and the signup grant
  is 500, so `grant_credits` is not merely convenient any more — it is the only way to finish
  a film.

---

## Verification

### Backend gates

- **Suite: 624 passed, 4 skipped** (up from M5's 591/4). The four skips are the same opt-in
  live-API calls; M6 adds no new network adapter and so no new one.
- `ruff check src tests` and `ruff format --check src tests` clean.
- **Migration:** `alembic upgrade head` applied `782fe8813ebd` cleanly, and `alembic check`
  reports **"No new upgrade operations detected"**. No enum widening was needed:
  `generate_shot_clip` and `render_final` have been in `job_kind`'s CHECK since M2 declared
  spec §5.4's full list — the third milestone in a row where declaring ahead cost nothing.

### Frontend gates

- **45 tests passed** (up from 38): the previous 38 plus 7 new Film-page tests covering the
  clip gate, the batch quote, stale clips, one-at-a-time animation, and the download link.
- `tsc --noEmit`, `eslint` and `next build` all clean.

### Render service gates

- `tsc --noEmit` clean with the `clip_url` branch in place.

### The full stack, end to end

A real api, a real worker, the real render service and a fresh account, with every provider
on its stub and `LLM_PROVIDER=stub`.

| Check | Result |
|---|---|
| Story → script → cast → sheets → casting → 6 stills → speech → music → preview | every job `succeeded` |
| **Final render before any clip exists** | **422**, "6 of 6 shots have no clip yet", and the balance **did not move** |
| 6 clips | **9 s**, cost **480 credits** — 6 × 80, exactly the quote |
| Final render | **36 s**, cost **60 credits** |
| The file | **1920×1080, 21.55 s, h264 + aac**, 932,437 bytes |
| Download | served as `attachment; filename="framemeld-<id>.mp4"` (M6-D10) |
| The Preview afterwards | **still fresh** — a Preview is made of stills and a Final of clips, so they have genuinely different parents |
| Press Render again, unchanged | **`succeeded` immediately**, no render ran |
| Rewrite one motion prompt and re-animate | the Final went **stale and kept its file**; the Preview **stayed fresh** |

That last row is the whole architecture in one line: an edit two nodes upstream invalidated
exactly one of the two films, and destroyed neither.

### What is **not** verified

**No video model has ever been called, and none exists in this codebase** (M6-D9). Spec §9's
"downloads a real AI-generated MP4" is met except for the word *AI*: every part of the
sentence — the job, the money, the hash chain, the composition, the download — is real, and
the clips are flat colours from ffmpeg. This is the same deliberate absence as M5's music,
and it is the first thing to close when a billed key exists.

**The Final composition has never rendered a real generated clip.** `OffthreadVideo` works,
the clips decode and the timing is right, but every frame so far has been a solid colour.
Whether a real 5-second clip sits correctly in its Series.Sequence — whether a model's clip
that comes back at 4.8 s instead of 5.0 s leaves a black frame at the cut — is untested, and
it is the most likely first bug with a real provider.

**Nothing has been rendered at the product's stated size.** Six shots and 21.5 seconds
against spec §1's 10–30 shots and 1–3 minutes. A 30-shot 1080p Final render is roughly five
times this one's work; `job_timeout` is `RENDER_TIMEOUT_SECONDS + 120` on the extrapolation
that it stays inside ten minutes.

**The 80-credit price has never been checked against a real bill.** M6-D3's arithmetic comes
from spec §7's $6.00-of-$6.90 estimate, itself verified on 2026-09-09 and flagged there as
needing re-checking before a provider is wired up. Re-check it before charging anyone.

---

## Process notes

**The duplicate-process check held again.** `tasklist /FI "IMAGENAME eq arq.exe"` before and
after every restart, confirming exactly one arq and one uvicorn. Two milestones now without
the failure m2-, m3- and m4-decisions each record.

**The worker's function list is a useful smoke test.** `Starting worker for 10 functions: …`
in the first line of the log is the cheapest confirmation that a new job kind is actually
registered — a task the router enqueues and the worker does not know about fails silently as
a job stuck in `queued`.
