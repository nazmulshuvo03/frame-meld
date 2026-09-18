# Where FrameMeld is, and what is left

**Written:** 2026-09-19
**Last commit:** `bb4e7cc`

A standing status note, written because the milestone plans and decision
records describe *what was done* and nobody had written down *what is left*.
Start here; the per-milestone records under `docs/superpowers/` have the
reasoning behind anything below that looks odd.

---

## The short version

All six milestones in the design spec are implemented, plus four gaps the
README listed as known. The one thing missing is the part the product is
named for: **no generative model has ever been called.** Every stage runs on a
stub that produces the real media format, so the pipeline, the money, the
staleness graph and the render service are all real and verified end to end,
and the pictures are flat colours.

That is a billing problem, not an engineering one — see *What is blocked*.

---

## Done

| | Commit | What |
|---|---|---|
| **M1** Foundation | — | Signup, Melds, prose editor with debounced autosave, soft delete |
| **M2** Script | — | Story → scenes → shots; the staleness engine |
| **M3** Cast | — | Character extraction and sheets; credits end to end; storage; moderation |
| **M4** Shots | `8354756` | Stills conditioned on sheets, speech per shot, the first multi-parent hash |
| **M5** Preview | `83eee20` | Scene spec, the Remotion render service, signed asset URLs, free Preview, music |
| **M6** Film | `b8db0a0` | Clips per shot, the Final render at 1080p, the download |
| Dark theme | `e1ece44` | Nine semantic tokens; the app was dark-on-dark under a dark OS theme |
| Structural editing | `e7f8c6d` | Add / delete / reorder scenes, shots and characters |
| Job cancellation | `13678f1` | Stop a queued generation and get the hold back |
| Deployment | `bb4e7cc` | Dockerfiles for all four services, compose profile, secure cookie, proxy headers |

**Gates, all green:** 650 backend tests + 4 opt-in skips · 85 frontend tests ·
`ruff check` / `ruff format --check` · `alembic check` reports no drift · `tsc`
and `eslint` on both the web app and the render service · `next build` ·
all three container images build.

**Verified end to end** against a real api, worker and render service: story →
script → cast → sheets → casting → stills → speech → music → free Preview →
clips → Final render → download. A 6-shot, 21.5-second film: Preview in 21 s
for 0 credits, 6 clips in 9 s for exactly 480, Final in 36 s for 60, and a
1920×1080 / h264 + aac / 932 KB file served as an attachment.

---

## What is blocked, and on what

**Nothing here is waiting on code.**

| Stage | Adapter | Status |
|---|---|---|
| LLM | `GeminiLLMProvider`, `OpenRouterLLMProvider` | Built. Gemini's free tier answered 429 on every attempt across four milestones |
| Images | `GeminiImageProvider` | Built, **never returned a success**. No free tier; this key's project has no billing |
| Speech | `GeminiSpeechProvider` | Built, **never returned a success**. A free tier does exist, so this is the cheapest one to close |
| Music | — | **No adapter.** Lyria is paid; ACE-Step is the commercially-safe self-hosted option |
| Video | — | **No adapter.** Wan 2.2 or Veo 3.1 Lite, both billed per second |

The music and video adapters were deliberately not written (M5-D2, M6-D9):
this codebase already carries two paid adapters whose success path has never
executed, and a third and fourth written blind against documentation would be
the same mistake twice more. The `Protocol` + registry + stub for each is in
place, so each is a two-line registry change plus one adapter file.

**To unblock:** enable billing on the Gemini key, or point `LLM_PROVIDER` at
OpenRouter's paid tier. Then, cheapest first:

1. `FRAMEMELD_LIVE_SPEECH=1 pytest tests/test_providers_speech_gemini.py` —
   free tier, so this one needs no billing at all. Correct `_extract_audio` /
   `_to_wav` against what actually comes back.
2. `FRAMEMELD_LIVE_IMAGE=1 pytest tests/test_providers_image_gemini.py` —
   needs billing. ~$0.04 an image.
3. Run the casting pass against a live model and read its answers: whether it
   reliably tells "seen in the frame" from "mentioned" is untested.
4. Write the music adapter, then the video adapter.

---

## What is left, in the order I would do it

### 1. The first real generation (blocked on billing)
Above. Everything else is cosmetic next to this: the product's whole claim is
untested until one real image comes back.

**Expect it to find bugs.** Three specific ones to watch for:

- The Ken Burns numbers in `render/spec.py` have only ever moved a flat
  rectangle. Whether a slow push into a real still reads as cinematic or as a
  wobble is unjudged.
- A clip that comes back at 4.8 s where the spec asked for 5.0 will leave a
  black frame at the cut. `OffthreadVideo` in a `Series.Sequence` has never
  seen a clip whose length disagreed with its slot.
- Gemini's `responseSchema` has never been confirmed to accept
  `SCRIPT_JSON_SCHEMA`'s exact nested shape (M2's open question, still open).

### 2. Stripe (spec §10, deliberately deferred)
A film costs ~2,900 credits against a 500-credit signup grant, so
`grant_credits` is no longer a convenience — it is the only way to finish one.
The hard part (the ledger, quote/hold/settle, metering) is done and tested;
checkout is a webhook and a `purchase` ledger row, and that enum value
already exists.

### 3. Nothing renders at the product's stated size
Everything so far has been 6 shots and 21.5 seconds. Spec §1 says 10–30 shots
and 1–3 minutes. `job_timeout` is `RENDER_TIMEOUT_SECONDS + 120` on the
extrapolation that a 30-shot 1080p Final stays inside ten minutes — that is
arithmetic, not a measurement. Also untested at that size: the render
service's single-render promise chain under two concurrent users.

### 4. Render progress
The render service tracks a progress fraction; the worker reads it only to
decide whether to keep waiting, and the job row has nowhere to put it. The
button says "Assembling…" until the film appears. Honest at 36 seconds, not at
whatever a 30-shot 1080p render costs.

### 5. Smaller, genuinely optional
- **Orphan sweep.** Bytes are written before the asset row, so a rolled-back
  transaction leaves a file nobody references. The intended failure direction,
  but nothing collects them. Same for render temp files.
- **Reference-sheet cache.** A four-character shot reads four sheets from
  storage per still. Flagged as "worth measuring before building" since M3 and
  still not measured.
- **S3/R2.** `Storage` is a two-method protocol with one implementation.
  Signed URLs (M5-D1) removed the reason this was urgent.
- **Per-clip regenerate.** The Film page has one batch button (M6-D11).
- **Transitions.** `transition_in` has been in the contract since M5 and is
  always `{"kind": "cut"}`. A dissolve is a composition change, not a spec
  change.
- **`settle`'s variable-units path is permanently unexercised.** Every
  billable action here is one indivisible unit of work. It stays because
  deleting it would be a regression against spec §4.3.

### Not planned
Everything else in spec §10: lip-sync, burned-in captions, per-character LoRA
training, collaboration, films over 3 minutes, a mobile app. Plus email
verification and password reset.

---

## How to pick it up

```bash
docker compose up -d --wait                     # Postgres + Redis
cd services/backend && conda run -n framemeld uvicorn framemeld.api_main:app --port 8000
cd services/backend && conda run -n framemeld arq framemeld.worker.WorkerSettings
cd services/render  && npm start                # :8790, downloads Chrome on first run
cd apps/web         && npm run dev              # :3000
```

`ASSET_SIGNING_SECRET` must be set in `.env` or every render fails. Top up
with `python -m framemeld.scripts.grant_credits you@example.com 5000` —
signup grants 500 and a film costs ~2,900.

**Two traps that have each cost an hour.** `config.py` caches settings with
`@lru_cache`, so a process left running from before an `.env` edit keeps the
old values and looks exactly like a staleness bug — and `taskkill` on the port
is not enough, because `conda run` wraps the process. Kill `arq.exe` and
`uvicorn.exe` by name and confirm exactly one of each. A job's `cost_usd` is
the cheapest way to tell which provider actually ran: zero means the stub.
