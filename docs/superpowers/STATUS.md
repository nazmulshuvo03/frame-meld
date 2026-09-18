# Where FrameMeld is, and what is left

**Written:** 2026-09-19
**Last commit:** `69f61f8`

A standing status note, written because the milestone plans and decision
records describe *what was done* and nobody had written down *what is left*.
Start here; the per-milestone records under `docs/superpowers/` have the
reasoning behind anything below that looks odd.

---

## The short version

All six milestones in the design spec are implemented, plus four gaps the
README listed as known. **All five provider slots now have a working adapter
behind them**, each verified against the real API — the gap this note was
originally written to describe is closed. See
[`providers-decisions.md`](providers-decisions.md).

What has *not* happened is a full pipeline run against real models. Every
adapter has been called in isolation; the end-to-end path has only ever run on
stubs, because doing it for real costs roughly $2–3 in video alone per film.
Every provider therefore still defaults to `stub`, and turning one on is a
deliberate edit.

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
| Providers | — | Lyria and Veo adapters written; all five slots verified live ([`providers-decisions.md`](providers-decisions.md)) |

**Gates, all green:** 689 backend tests + 6 opt-in skips · 85 frontend tests ·
`ruff check` / `ruff format --check` · `alembic check` reports no drift · `tsc`
and `eslint` on both the web app and the render service · `next build` ·
all three container images build.

**Verified end to end** against a real api, worker and render service: story →
script → cast → sheets → casting → stills → speech → music → free Preview →
clips → Final render → download. A 6-shot, 21.5-second film: Preview in 21 s
for 0 credits, 6 clips in 9 s for exactly 480, Final in 36 s for 60, and a
1920×1080 / h264 + aac / 932 KB file served as an attachment.

---

## Providers

All five slots have a verified adapter. Every one defaults to `stub`; set the
env var to turn a real model on.

| Stage | `*_PROVIDER` | Real adapter | Cost |
|---|---|---|---|
| LLM | `stub` \| `gemini` \| `openrouter` | `GeminiLLMProvider` | fractions of a cent |
| Images | `stub` \| `gemini` | `GeminiImageProvider` | ~$0.039 an image |
| Speech | `stub` \| `gemini` | `GeminiSpeechProvider` | ~$0.0013 a line |
| Music | `stub` \| `lyria` | `LyriaMusicProvider` | $0.08 a track |
| Video | `stub` \| `veo` | `VeoVideoProvider` | **$0.20–0.30 a clip** |

The five opt-in live tests are the cheapest way to confirm a key works:

```bash
FRAMEMELD_LIVE_LLM=1    pytest tests/test_providers_gemini.py -k real
FRAMEMELD_LIVE_SPEECH=1 pytest tests/test_providers_speech_gemini.py -k live
FRAMEMELD_LIVE_IMAGE=1  pytest tests/test_providers_image_gemini.py -k real    # ~$0.04
FRAMEMELD_LIVE_MUSIC=1  pytest tests/test_providers_music_lyria.py -k live     # ~$0.08
FRAMEMELD_LIVE_VIDEO=1  pytest tests/test_providers_video_veo.py -k live       # ~$0.20
```

**Two provider facts that constrain the product**, both found by probing and
both written up in `providers-decisions.md`:

- **Veo accepts only 4, 6 or 8 second clips** — and its own error message
  claims the range is "between 4 and 8, inclusive" while rejecting 5 and 7.
  Since spec §8 caps a shot at 5.0 s, there are exactly two usable tiers, and a
  4.1-second shot costs 50% more than a 4.0-second one.
- **Veo clips carry an audio track and cannot be asked not to.** Spec §8
  assumed silent clips. The composition mutes them; the film's audio comes from
  its audio timeline.

---

## What is left, in the order I would do it

### 1. One full pipeline run against real models
Each adapter has been called in isolation and the gates are green, but the
whole path — story → script → cast → sheets → stills → speech → music → clips →
Final — has only ever run on stubs.

Do it cheaply and in stages rather than all at once, because the video step
dominates:

1. `LLM_PROVIDER=gemini` alone. Free-ish, and it closes M4's open question:
   whether the casting pass reliably tells "seen in the frame" from
   "mentioned". Also confirms `SCRIPT_JSON_SCHEMA` against `responseSchema`.
2. Add `IMAGE_PROVIDER=gemini`. ~$0.04 a still. **Then watch one Preview** —
   the Ken Burns numbers in `render/spec.py` have still only ever moved a flat
   colour, and whether a slow push into a real still reads as cinematic or as a
   wobble is the one thing only a real image can answer.
3. Add speech and music. Pennies.
4. `VIDEO_PROVIDER=veo` last, on the **shortest film you can make**. At
   $0.20–0.30 a clip this is the only step that costs real money; a 6-shot film
   is ~$1.50 and a 30-shot one is ~$7.

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
