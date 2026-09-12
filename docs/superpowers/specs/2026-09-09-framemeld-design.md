# FrameMeld — Design Spec

**Date:** 2026-09-09
**Status:** Approved for implementation planning
**Author:** nazmul.alom@mustaruusu.com (with Claude)

---

## 1. What we are building

FrameMeld is a web app that turns a written story into a short AI-generated film. The
user writes prose; AI drafts a script and cast; the user edits and approves everything;
AI generates character images, shot images, voice, and music; then each shot image is
animated by an image-to-video model and assembled into a finished MP4.

The user is in control at every stage. AI drafts, the human approves.

**Product shape:** a real SaaS with accounts, prepaid credits, and per-call metering.

**Target output:** 1–3 minutes, 10–30 shots per film.

---

## 2. Core concept: the Meld

A **Meld** is the container a user creates, names, and moves through all five stages.
Users have many Melds, each parked at a different stage. `/meld/{id}`, table `melds`.

The word "storyboard" is deliberately avoided as the container name, because the
storyboard is one artifact *inside* a Meld (stage 2's output).

### The five stages

| # | Stage | User sees | AI does |
|---|-------|-----------|---------|
| 1 | **Story** | A prose editor | nothing — the user writes |
| 2 | **Script** | Scenes, each broken into shots with an image prompt, a motion prompt, and narration/dialogue | reads the story, writes the shot list |
| 3 | **Cast** | Character list with canonical descriptions and a locked "character sheet" image each | writes descriptions, generates sheets |
| 4 | **Shots** | One still image + one audio clip per shot | generates each still using character sheets as visual references; generates speech |
| 5 | **Film** | A free **Preview** draft, then the paid **Final** render | animates each still into a real video clip, assembles clips + audio into an MP4 |

Users move backwards freely. Editing stage 2 never deletes stage 4 work — it marks the
affected artifacts **stale** (see section 4.1).

---

## 3. Architecture

Four services plus three pieces of infrastructure.

| Service | Runtime | Responsibility | Must NOT |
|---------|---------|----------------|----------|
| **web** | Next.js (App Router, TypeScript) | UI, editors, autosave, progress display | call an AI provider; touch the database |
| **api** | Python / FastAPI | auth, CRUD, staleness recomputation, credit quote/hold/settle, job enqueue | call an AI provider; run long work in-request |
| **worker** | Python (arq on Redis) | run jobs, call AI providers through adapters, upload assets, settle credits | serve HTTP; contain UI logic |
| **render** | Node + Remotion | take a fully-resolved scene spec, return an MP4 | read the database; know about users, credits, or auth |

| Infrastructure | Choice | Why |
|---|---|---|
| Database | Postgres | relational, JSONB for provider params, source of truth |
| Queue | Redis (arq) | simple, already needed for caching and rate limits |
| Object storage | S3-compatible (Cloudflare R2) | cheap egress matters when serving video |

### 3.1 Why the render service is deliberately dumb

The Node service is the only place we are forced to use a second language (Remotion is
React). To keep the cross-language contract small, it is stateless and has no database
access. It receives one versioned JSON document — the **scene spec** — containing signed
URLs for every asset it needs, and returns an MP4.

Two endpoints:

- `POST /render` — body is a scene spec; returns `{ render_id, status }`
- `GET /render/{render_id}` — returns `{ status, progress, output_url?, error? }`

### 3.2 Scene spec (the Python-to-Node contract)

Versioned. The worker resolves everything before sending; the render service resolves
nothing.

```json
{
  "version": 1,
  "kind": "preview",
  "fps": 30,
  "width": 1920,
  "height": 1080,
  "music_url": "https://.../music.mp3",
  "shots": [
    {
      "id": "shot_01",
      "duration_sec": 4.5,
      "still_url": "https://.../shot01.png",
      "clip_url": null,
      "motion": { "kind": "kenburns", "from": [0, 0, 1.0], "to": [0.03, 0.02, 1.12] },
      "transition_in": { "kind": "cut" },
      "audio": [
        { "url": "https://.../narr01.mp3", "start_sec": 0.2, "gain_db": 0 }
      ]
    }
  ]
}
```

- `kind: "preview"` → `still_url` + `motion` are used, `clip_url` is null.
- `kind: "final"` → `clip_url` is used, `motion` is ignored.

The two render modes share one spec shape and one Remotion composition, differing only in
whether a shot's visual source is a still or a clip. This is what makes Preview cheap to
build.

### 3.3 Provider adapters

Every AI call goes through one narrow adapter interface. Model names live in
configuration, never in code. Swapping a provider is a config change.

```python
class ProviderResult:
    bytes_: bytes | None
    text: str | None
    cost_usd: Decimal
    provider: str
    model: str
    raw: dict          # provider response, stored for debugging

class LLMProvider:      def complete(self, messages, json_schema) -> ProviderResult
class ImageProvider:    def generate(self, prompt, refs: list[str], params) -> ProviderResult
class VideoProvider:    def animate(self, image_url, motion_prompt, duration_sec, params) -> ProviderResult
class SpeechProvider:   def speak(self, text, voice_id, params) -> ProviderResult
class MusicProvider:    def compose(self, prompt, duration_sec, params) -> ProviderResult
```

Each adapter is responsible for reporting its own `cost_usd`. That number feeds credit
settlement, so it must be real, not estimated.

---

## 4. Key design decisions

### 4.1 Content-addressed artifacts and explicit staleness

**This is the central architectural bet.** It is cheap to build in from the start and
near-impossible to retrofit.

**Two hashes, not one.** This distinction is load-bearing and the original
version of this spec got it wrong — see the note at the end of this section.

| Column | On which rows | Meaning |
|---|---|---|
| `content_hash` | every artifact a user can edit | SHA-256 of the canonical JSON of the artifact's **own current fields** — what it *says* right now |
| `input_hash` | every *generated* artifact | SHA-256 of the canonical JSON of what **produced** it: the `content_hash` of each upstream artifact, plus the provider, model and params |

Generated rows additionally carry:

| Column | Meaning |
|---|---|
| `state` | `empty` / `generating` / `fresh` / `stale` / `failed` |
| `provider`, `model` | what actually made it |
| `params` (JSONB) | seed, resolution, guidance, etc. |
| `credits_spent` | shown in the UI |
| `asset_id` | the file in object storage |

**Downstream depends on upstream's `content_hash`, never its `input_hash`.**
That is the whole point. `input_hash` records provenance and does not change
when a human edits the row; `content_hash` does. Wiring downstream to
provenance would mean a user could rewrite a shot's prompt and its image
would never be marked stale.

Two properties fall out of this:

1. **Idempotent generation.** A job is keyed on `(kind, target_id, input_hash)`. Running
   it again with an unchanged hash is a cache hit: instant, zero credits.
2. **Explicit staleness.** When an upstream artifact changes, downstream hashes recompute
   and no longer match what is stored, so those rows flip to `stale`. They are **not**
   auto-regenerated, because regeneration costs money. The UI surfaces it:
   *"Elena changed. 14 shot images are out of date — refresh for 42 credits."*

**Implementation:** a single `recompute_staleness(meld_id)` function in the api service,
called in the same transaction as any edit. At 30 shots per Meld this is a trivial
in-memory graph walk; no incremental machinery needed.

**Dependency graph:**

```
story ──> scene ──> shot ──> shot_image ──> shot_clip
                        └──> audio_clip (speech)

story ──> character ──> character_image
                              │
                              └──> (used as a reference by shot_image)

shot_clip + audio_clip + music ──> render
```

A `shot_image`'s `input_hash` includes the **`content_hash`** of every
`character_image` it used as a reference, and of the `shot` it depicts. That is the link
that catches "the character changed" — the case that otherwise silently ships a film
where a face changes halfway through.

> **Correction, made while planning M2.** Earlier versions of this section said a
> downstream artifact's hash includes its upstream's `input_hash`. That is wrong, and
> this section's own example is what disproves it: *"Elena changed"* means a human
> **edited** Elena's description. Editing does not change the row's `input_hash` —
> that records which generation produced the row, not what the row now says. Under the
> old rule those 14 shot images would never have been marked stale, and the headline
> feature would have silently failed in exactly the case it was written for. Downstream
> depends on `content_hash`.
>
> One consequence for code already shipped: M1's `stories.input_hash` was always a
> *content* hash — a story has no generation behind it, the user simply wrote it. M2
> renames that column to `content_hash` rather than leaving the name lying about what
> it holds.

### 4.2 Preview vs Final render

Image-to-video is roughly 7× the cost of everything else combined (section 7). Users must
not spend $6 blind.

| | **Preview** | **Final** |
|---|---|---|
| Visual source | the shot stills, animated by Remotion (pan/zoom/parallax) | a real AI video clip per shot |
| Audio | full — narration, dialogue, music, SFX | identical |
| Cost | ~free | ~$6 per film |
| Time | seconds | minutes |
| Purpose | iterate on pacing, shot order, audio, script | the deliverable |

Preview is not a throwaway feature — it is the approval gate that makes the credit
system humane, and it reuses the same scene spec and Remotion composition as Final.

### 4.3 Credits: quote, hold, settle

Every billable action follows three steps:

1. **Quote** — the api computes the cost from a versioned pricing table and returns it.
   The UI shows it *before* the user clicks. Never surprise the user.
2. **Hold** — a negative `credit_ledger` row of kind `hold`, linked to the job. If the
   balance is insufficient, the job is refused before any provider is called.
3. **Settle** — the worker finishes and writes a `settle` row for the difference between
   the held estimate and the adapter's real reported cost. A failed job writes a full
   `refund`.

**The balance is never a mutable integer.** It is `SUM(delta)` over an append-only
`credit_ledger`. Boring, auditable, and impossible to corrupt by a lost update.

**Unit:** 1 credit = $0.01 retail. A full film is roughly 2,500–3,000 credits.

### 4.4 Everything is saved, always

An explicit product requirement:

- Autosave on debounce (~800 ms after typing stops) for every text field.
- A user may have many Melds open, each parked at any stage.
- Close the tab mid-sentence; reopen next week; land exactly where you left off.
- Nothing is destroyed by an edit. Downstream work goes `stale`, never deleted, so
  reverting an edit restores the cache hit and costs nothing.

### 4.5 Content moderation

A public SaaS that generates images and video needs a gate. Minimum viable version:

- Prompt-level check on the story text at stage 2 and on every image/video prompt,
  using the LLM provider with a classification schema.
- Blocked prompts fail the job **before** a hold is taken, with a clear reason.
- `melds.moderation_state` records `pending` / `passed` / `flagged` so flagged content
  can be reviewed rather than silently deleted.

Not a full trust-and-safety system — just enough that a bad prompt cannot reach a
provider and get the account banned.

---

## 5. Data model

### 5.1 Identity and billing

**`users`**
`id` uuid pk · `email` unique · `password_hash` · `created_at` · `deleted_at`

**`credit_ledger`** (append-only; balance = `SUM(delta)` per user)
`id` uuid pk · `user_id` fk · `delta` int (negative = spend) ·
`kind` enum(`purchase`, `grant`, `hold`, `settle`, `refund`) · `job_id` fk nullable ·
`meld_id` fk nullable · `description` · `created_at`

Index: `(user_id, created_at)`.

### 5.2 The Meld and its content

**`melds`**
`id` uuid pk · `user_id` fk · `title` · `stage` enum(`story`, `script`, `cast`, `shots`, `film`) ·
`status` enum(`draft`, `complete`, `archived`) · `aspect_ratio` · `style_preset` ·
`moderation_state` · `created_at` · `updated_at` · `deleted_at`

**`stories`** (1:1 with meld; a separate table so revisions can be added later)
`id` · `meld_id` fk unique · `text` · `content_hash` · `updated_at`

**`scenes`**
`id` · `meld_id` fk · `order_index` · `heading` · `summary` · `location` ·
`time_of_day` · `content_hash` · `input_hash` · `state`

**`shots`**
`id` · `scene_id` fk · `order_index` ·
`image_prompt` text · `motion_prompt` text ·
`duration_sec` numeric (**constraint: ≤ 5.0**) ·
`camera` enum(`wide`, `medium`, `close`, `insert`, `establishing`) ·
`dialogue_kind` enum(`none`, `narration`, `off_screen`, `on_screen`) ·
`speech_text` text · `speaker_character_id` fk nullable ·
`content_hash` · `input_hash` · `state`

**`shot_characters`** (join table — which characters appear in a shot)
`shot_id` fk · `character_id` fk · pk `(shot_id, character_id)`

A real join table rather than a `uuid[]` column, because this relation drives both
reference-image selection and staleness propagation, so it needs referential integrity —
a deleted character must not leave a dangling id inside a shot. (By contrast
`shot_images.ref_character_image_ids` below *is* an array, because that one is a
historical record of what was actually used, not a live relation.)

This join table is what makes a shot go stale when a character changes.

**`characters`**
`id` · `meld_id` fk · `name` · `role` · `description` text ·
`appearance` text (the canonical, hashable visual description) ·
`voice_id` · `content_hash` · `input_hash` · `state`

### 5.3 Generated artifacts

The five generated-artifact tables (`character_images`, `shot_images`, `shot_clips`,
`audio_clips`, `renders`) each carry the standard set from section 4.1:
`input_hash`, `state`, `provider`, `model`, `params`, `credits_spent`, `asset_id`.

`assets` at the end of this section is **not** a generated-artifact table — it is the
file registry the `asset_id` foreign keys point at, and carries none of those columns.

**`character_images`**
`id` · `character_id` fk · `kind` enum(`sheet`, `portrait`, `full_body`, `expression`) ·
`is_canonical` bool · *+ standard set*

**`shot_images`** — the still, and the first frame of the clip
`id` · `shot_id` fk · `seed` · `ref_character_image_ids` uuid[] · *+ standard set*

**`shot_clips`** — the real AI video
`id` · `shot_id` fk · `source_image_id` fk → shot_images · `duration_sec` · *+ standard set*

**`audio_clips`**
`id` · `owner_type` enum(`shot`, `meld`) · `owner_id` uuid ·
`kind` enum(`narration`, `dialogue`, `music`, `sfx`) · `text` · `voice_id` ·
`duration_sec` · *+ standard set*

**`renders`**
`id` · `meld_id` fk · `kind` enum(`preview`, `final`) · `scene_spec` jsonb ·
`duration_sec` · `resolution` · *+ standard set*

**`assets`**
`id` uuid pk · `user_id` fk · `storage_key` · `mime` · `bytes` · `sha256` ·
`width` · `height` · `duration_sec` · `created_at`

Deduplication by `sha256` is a later optimisation, not v1.

### 5.4 Jobs

**`jobs`**
`id` uuid pk · `meld_id` fk · `kind` (see below) · `target_table` · `target_id` ·
`input_hash` · `idempotency_key` **unique** = `hash(kind, target_id, input_hash)` ·
`state` enum(`queued`, `running`, `succeeded`, `failed`, `cancelled`) ·
`attempts` int · `error` text · `credits_held` int · `created_at` ·
`started_at` · `finished_at`

The unique `idempotency_key` is the mechanism that makes double-clicking Generate free
rather than expensive.

**Job kinds:**
`generate_script` · `generate_characters` · `generate_character_image` ·
`generate_shot_image` · `generate_speech` · `generate_music` ·
`render_preview` · `generate_shot_clip` · `render_final`

**Retry policy:** 3 attempts with exponential backoff on transport and 5xx errors.
Provider content refusals are terminal — no retry, full credit refund, reason shown.

---

## 6. AI providers

Chosen for cost and for licences that are safe to sell against.

| Stage | v1 choice | Licence | Cost | Upgrade path |
|---|---|---|---|---|
| Script, characters, moderation | OpenRouter free tier (Qwen3, Llama 3.3 70B) | free | $0 | Claude Haiku 4.5 ($1/$5 per MTok) → Sonnet 5 ($2/$10) |
| Character sheets | FLUX.1 schnell | **Apache 2.0** | ~$0.003/img | FLUX.2 pro, Seedream 4 |
| Shot stills (character-consistent) | **Qwen-Image-Edit** (accepts reference images) | **Apache 2.0** | ~$0.02/img | FLUX.1 Kontext Pro (~$0.04), Seedream 4 Edit (up to 10 refs) |
| Speech | **Kokoro-82M**, self-hosted (runs on CPU) | **Apache 2.0** | ~$0 | Chatterbox (MIT, voice cloning from ~5 s) → ElevenLabs |
| Music / SFX | **ACE-Step 1.5** | **MIT**, explicit commercial grant | ~$0.008/track | Stable Audio 3 |
| **Image-to-video** | **Wan 2.2** via a hosted API | **Apache 2.0** | ~$0.12–0.28/clip | LTX-2.5 (faster), Seedance 2.0 (cheaper, closed) |

### 6.1 Licence traps (recorded so they are not rediscovered painfully)

- **FLUX.1 dev / FLUX.1 Kontext dev** weights are **non-commercial**. Usable through a
  host that holds a commercial agreement with Black Forest Labs; **not** self-hostable
  for a paid product.
- **MusicGen** weights are **CC-BY-NC**. Do not ship. ACE-Step 1.5 is the
  commercial-safe equivalent.
- **LTX-2.5**'s community licence is free commercially only **under $10M annual revenue** —
  fine now, revisit on growth.
- **Wan 2.2** is Apache 2.0 with no revenue cap, which is why it is the v1 default: the
  hosted API can be replaced by self-hosted weights with no licence change and no code
  change.

### 6.2 Free-tier caveats

- OpenRouter free models are rate-limited to roughly 20 req/min and 200 req/day, and the
  free roster changes without notice. Adequate for development, not for users. Budget a
  small monthly LLM spend from first real traffic.
- Because free tiers vanish, the provider adapter boundary (section 3.3) is a
  requirement, not a nicety.

---

## 7. Cost model

Per finished film, roughly 2.5 minutes / 30 shots:

| Item | Cost |
|---|---|
| Script + characters (LLM) | $0.05 |
| Character sheets (5 × FLUX schnell) | $0.02 |
| Shot stills (30 × Qwen-Image-Edit) | $0.60 |
| Narration + dialogue (Kokoro, self-hosted) | ~$0.00 |
| Music (ACE-Step) | $0.01 |
| **Video clips (30 × Wan 2.2 @ ~$0.20)** | **$6.00** |
| Render compute | $0.20 |
| **Total** | **≈ $6.90** |

Everything except the video clips totals ~$0.90, and it is all reusable across Preview
iterations. The $6 is spent once, when the user commits to the Final render.

**Implied pricing:** ~$25–30 per finished film, or the credit equivalent
(2,500–3,000 credits).

---

## 8. Constraints imposed by image-to-video

These flow backwards from stage 5 into stages 2–4, which is why the video decision had to
be settled before the data model.

| Constraint | Cause | Consequence |
|---|---|---|
| Shots must be ≤ 5 s | hard clip-length limit on every current I2V model | `shots.duration_sec` has a DB constraint; the script generator's JSON schema enforces it |
| Every shot needs a **motion prompt** distinct from its image prompt | I2V models take image + motion description | one extra field per shot, authored by the LLM in stage 2 |
| Video models return **silent** clips | no audio in Wan 2.2 output | narration/dialogue/music are laid over in Remotion; the audio design is unchanged |
| **Lip-sync is unsolved cheaply** | I2V models do not sync mouths to our TTS | v1 biases the script toward narration, reaction shots, and off-screen dialogue; `dialogue_kind` exists to express this. A lip-sync step is a post-v1 addition |

Lip-sync is the honest weak spot of this design. It is contained: it affects prompt
strategy and one enum, not the architecture.

---

## 9. Milestones

Each milestone is independently demonstrable. AI spend stays near zero until M3.

### M1 — Foundation
Signup/login, create and list Melds, prose story editor with debounced autosave, soft
delete.
**Done when:** a user signs up, creates a Meld, types a story, closes the tab, and finds
the text intact on return.
**AI spend:** $0

### M2 — Script
Story → scenes → shots, with image prompt, motion prompt, duration, camera, and
narration/dialogue per shot. Full editing of every field. **Staleness engine live**
(`input_hash` + `recompute_staleness`), with an "out of date" badge in the UI.
**Done when:** a story generates an editable shot list; editing the story marks the
script stale without destroying it.
**AI spend:** free tier

### M3 — Cast
Character extraction with canonical descriptions; character sheet image generation.
**Credit system live** end to end: pricing table, quote, hold, settle, refund, ledger.
**Done when:** a quote is shown before generating, the balance moves correctly, and a
failed generation refunds in full.
**AI spend:** ~$0.02/Meld

### M4 — Shots
Shot still generation using character sheets as references. Speech generation per shot.
Per-shot regenerate. Staleness propagates from characters through to shot stills.
**Done when:** every shot has a still whose characters visibly match their sheets, plus
an audio clip; editing a character marks exactly the right stills stale.
**AI spend:** ~$0.60/Meld

### M5 — Preview
Scene spec assembly, the Remotion render service, animated-stills Preview render with
full audio and music.
**Done when:** a user watches a complete, watchable draft of their film for free.
**AI spend:** ~$0

### M6 — Film
Wan 2.2 clip generation per shot (still → clip), Final render assembling clips + audio,
download.
**Done when:** a user pays credits, waits, and downloads a real AI-generated MP4.
**AI spend:** ~$6/Meld

**M5 is the first genuinely impressive demo, and it costs almost nothing to reach.**

---

## 10. Out of scope for v1

Designed around, deliberately not built:

| Deferred | Why it is safe to defer |
|---|---|
| **Stripe checkout** | The hard part is the ledger and metering (M3). Grant credits manually until M6 works; checkout is a ~2-day addition. |
| **Lip-sync** | Its own pipeline step. `dialogue_kind` already exists to route around it. |
| **Burned-in captions** | Remotion handles this well once the audio timeline exists; a modest later add. |
| **Per-character LoRA training** | A whole training subsystem. Reference conditioning ships first; a `training_kind` on `characters` can be added without schema surgery. |
| **Collaboration / sharing** | No multi-user Melds in v1. |
| **Films longer than 3 minutes** | Would force chunked, resumable rendering. |
| **Mobile app** | Responsive web only. |

---

## 11. Open risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Lip-sync absent** | high (perceived quality) | Script strategy biases to narration and reaction shots. Revisit after M6 with real output. |
| **Character drift between clips** | high | Stills are approved before any clip is generated, and each clip is conditioned on its approved still — so drift is bounded to within a single 5 s clip. |
| **Video model price volatility** | medium | Adapter boundary plus an Apache-2.0 default whose weights can be self-hosted if hosted prices rise. |
| **Free LLM tiers disappearing** | medium | Adapter boundary; paid fallback configured from day one. |
| **Three runtimes is real ops cost** | medium | The render service is stateless with a two-endpoint contract; the scene spec is versioned. |
| **Unit economics at ~$7/film cost** | medium | Preview absorbs iteration for free; Final is a single deliberate spend with a quote shown first. |
| **Abuse / NSFW generation** | medium | Prompt moderation before any hold is taken (section 4.5); `moderation_state` for review. |
| **Long render times hurting UX** | low | Job rows carry progress; the UI polls. Users already accept waiting for video. |

---

## 12. Decisions log

Recorded so they are not relitigated.

| Decision | Choice |
|---|---|
| Product shape | Real SaaS with users |
| Container noun | **Meld** |
| Video engine | Real image-to-video (Wan 2.2), with a free stills-based Preview as the draft gate |
| Stack | Next.js + Python/FastAPI + Node/Remotion render service |
| Character consistency | Reference-image conditioning (no fine-tuning) |
| Billing | Prepaid credits, metered per AI call, append-only ledger |
| Project size | 1–3 min, 10–30 shots |
| v1 audio | Narration + dialogue + music + SFX (no captions) |
| Architecture bet | Content-addressed artifacts with explicit staleness |

All decisions dated 2026-09-09.

---

## 13. Sources

Pricing and licence facts were verified on 2026-09-09 and should be re-checked before
each provider is wired up.

- [Cheapest AI video generation APIs 2026](https://www.atlascloud.ai/blog/guides/cheapest-ai-video-generation-api-2026)
- [Wan 2.2 pricing: open weights vs API vs hosted](https://flowith.io/blog/wan-ai-pricing-2026-free-open-weight-vs-api-vs-hosted/)
- [LTX-2.5 open weights announcement](https://venturebeat.com/technology/ltx-2-5-can-generate-a-10-second-ai-video-from-an-image-in-just-6-8-seconds-on-nvidia-superchips-and-its-open-weights)
- [LTX model licence](https://ltx.io/model/license)
- [Cheapest image generation models](https://www.siliconflow.com/articles/the-cheapest-image-gen-models)
- [Image model pricing comparison](https://pricepertoken.com/image/cheapest)
- [Best open-source TTS 2026](https://findskill.ai/blog/best-open-source-tts-2026/)
- [Kokoro vs XTTS vs Chatterbox](https://localaimaster.com/blog/kokoro-vs-xtts-vs-chatterbox)
- [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5)
- [Best open-source AI music models](https://boppy.me/blog/best-open-source-ai-music-models)
- [OpenRouter free models list](https://costgoat.com/pricing/openrouter-free-models)
- Claude model IDs and pricing: from the bundled `claude-api` skill reference (cached 2026-06-24)
