# Provider decision record — closing the generative gap

**Date:** 2026-09-19
**Preceded by:** [`m6-decisions.md`](m6-decisions.md)

Same purpose as [`m1-decisions.md`](m1-decisions.md) through
[`m6-decisions.md`](m6-decisions.md), for the work that is not a milestone in spec §9:
wiring real generative models to a pipeline that had only ever run on stubs.

STATUS.md opened with *"no generative model has ever been called."* That is no longer
true. Billing was enabled on the Gemini key, and all five provider slots now have a
working adapter behind them.

---

## What changed

| Slot | Before | After |
|---|---|---|
| LLM | adapter built, never succeeded | **verified live**, unchanged |
| Images | adapter built blind, never succeeded | **verified live**, unchanged |
| Speech | adapter built blind, never succeeded | **verified live**, one real bug found (below) |
| Music | **no adapter** (M5-D2) | `LyriaMusicProvider`, verified live |
| Video | **no adapter** (M6-D9) | `VeoVideoProvider`, verified live |

The two adapters written blind — images and speech — both worked on their first real
call. `_extract_image`, `_extract_audio` and `_to_wav` needed no correction. That is
worth recording because M3 and M4 both flagged them as the most likely thing to be
wrong, and the documented response shapes turned out to be accurate.

---

## The probe came first, and it is the only reason the adapters are right

M3-D11's rule — probe before writing adapter code — was followed, and it earned its keep
four times over. Every one of the following would have been a wrong assumption baked into
code, and none of them is in any documentation the adapter would have been written from.

**1. `gemini-2.5-flash` is retired for new users.** It answers 404 with a pointer to
`gemini-3.6-flash`. It is still in `rates.py` because a model can be retired for *new*
users while existing integrations keep working, and deleting a price does not help
anyone still on it.

**2. The `gemini-flash-lite-latest` alias still resolves to `gemini-3.5-flash-lite`,**
which is in the rate table. M3 wrote both the alias and the resolved id into `rates.py`
precisely because that mapping can move; it had not moved. No change needed — but this
was checked rather than assumed, because a silent $0 cost is exactly what the check
exists to prevent.

**3. Lyria takes no duration.** `MusicProvider.compose` has a `duration_sec` parameter
because spec §3.3 reasoned "a score has to fit a film that already exists". Lyria has no
such parameter, and a call for a 21-second film returned **174 seconds** of MP3. The
parameter is still validated (zero is refused) but is not sent. M5-D12 already made the
score one track laid across the whole film, so the composition trims — the design
absorbed this without a change.

**4. Lyria returns MP3, not WAV** — see the bug below.

**5. Veo accepts only *even* durations.** This is the expensive one, and it is covered in
its own section.

---

## The expensive knowledge: Veo's duration ladder, and the error message that lies

Probed one value at a time:

| Requested | Result |
|---|---|
| 3 | 400 — "provide a value between 4 and 8, inclusive" |
| 4 | **accepted** |
| 5 | 400 — "provide a value between 4 and 8, inclusive" |
| 6 | **accepted** |
| 7 | 400 — "provide a value between 4 and 8, inclusive" |
| 8 | **accepted** |
| 9 | 400 — same |

Only 4, 6 and 8 are legal. The error text is not merely incomplete, it is **actively
wrong**: it states a range that includes two values it rejects. An adapter written from
that message would have sent 5 for every five-second shot and failed on every one of
them, and the failure would have looked like a quota or a transport problem rather than
a contract problem.

This collides head-on with spec §8, which caps `shots.duration_sec` at 5.0 because every
image-to-video model caps a clip at about five seconds. Both constraints are real, and
together they leave this product exactly **two** usable tiers: 4 seconds for a shot of
4.0 or less, 6 for anything above it.

**The resolution is to round up, and the direction is the whole point.** `duration_tier`
returns the shortest legal duration that is *not shorter* than the shot. The composition
gives each shot exactly its own `duration_sec` worth of frames and truncates the rest, so:

- a clip **longer** than its slot costs some bytes and nothing else;
- a clip **shorter** than its slot leaves a **black frame at the cut**.

That second one is the failure m6-decisions.md named as the most likely first bug against
a real provider: *"a clip that comes back at 4.8 s where the spec asked for 5.0 will leave
a black frame at the cut."* It was right that this is where the danger is, and rounding up
is the direction that cannot produce it. `test_the_tier_is_never_shorter_than_the_shot`
walks every tenth of a second from 0.1 to 8.0 and pins it.

**It is also a margin decision.** Veo bills per second of the tier *generated*, not of the
shot. A 4.2-second shot costs $0.30, not $0.20 — a 50% jump for two tenths of a second of
script. `usd_for_video` is therefore called with the tier, not with `duration_sec`;
costing it on the shot's own length would under-report the only call in this product
expensive enough to matter. At 80 credits ($0.80) retail, both tiers keep their margin,
and `test_a_clip_still_carries_the_margin_spec_section_7_assumes` asserts it.

---

## The expensive knowledge: Veo returns audio, and spec §8 assumed it would not

Spec §8 lists "Video models return **silent** clips" as a constraint flowing from the
video decision, with the consequence that "narration/dialogue/music are laid over in
Remotion". The entire audio design rests on that sentence.

Veo 3.1 returns an **AAC track**, and `generateAudio: false` is refused outright:

```
`generateAudio` isn't supported by this model.
```

So the clip arrives with model-invented ambience on it, and `OffthreadVideo` plays a
clip's audio by default. Left alone, every Final render would have mixed Veo's guess at
what the scene sounds like *underneath the narration the user wrote* — and it would have
rendered successfully, which is the worst kind of wrong.

**The fix is `muted` on `OffthreadVideo`, in the composition, not in the adapter.** That
placement is the decision. Stripping the audio in the Veo adapter would have worked for
Veo and left the next provider free to reintroduce the bug; worse, it would have required
ffmpeg in production, which M6-D8 deliberately avoided when it put `imageio-ffmpeg` in the
`[dev]` extra so the stub could be honest without becoming a deployment dependency.

A film's audio comes from its audio timeline, whatever the clip carries. That is an
invariant of the design, so it lives where the film is assembled.

---

## The bug this found: the music job hardcoded WAV

`run_generate_music` stored its result with `mime=WAV_MIME, ext="wav"` — a constant,
written when the only music provider was a stub that emitted WAV. Lyria returns MP3.

Left alone, every real score would have been stored as MP3 bytes under a `.wav` filename
with `Content-Type: audio/wav`, and the asset route serves that header straight to
Remotion's decoder. The mime now comes from the provider's own `raw["mimeType"]`.

This is the same shape as M6's stub bug, from the other side. There, a stub that ignored
an input made that input look inert. Here, a stub that only ever produced one format made
the format look like a constant — and the code encoded that accident as a literal. **A
stub's incidental properties become assumptions in the code around it**, and they are
invisible until a second implementation disagrees.

The related gap is *not* a bug: `assets.duration_sec` is null for every MP3 and MP4,
because `wav_duration` reads WAV only. Nothing reads a music clip's duration — M5-D12
makes the score one track trimmed by the composition — so the column stays null rather
than growing an MP3 parser for a number no one asks for.

---

## The bug this found: speech was costed in the wrong unit, at the wrong rate

`usd_for_speech(model, characters)` multiplied a single per-model number by
`len(text)`. Both halves were wrong, and they were wrong in a way that produced a
plausible-looking figure, which is why four milestones of tests passed over it:

- The published $10.00 is per million **audio output tokens**, not per million input
  characters.
- Text input is a **separate and much cheaper** rate ($0.50 per million tokens).

A live call settles it: `usageMetadata` reports `promptTokenCount` against modality TEXT
and `candidatesTokenCount` against modality AUDIO. The adapter now passes both, exactly
as the text adapter already did. For a one-line narration the old form under-reported by
roughly half, and the error grew with line length, because characters and audio tokens do
not move together.

The test that asserted the old behaviour (`test_the_cost_is_priced_per_input_character`)
was **pinning the bug**. It is rewritten rather than deleted, against the token counts a
real response carries.

This is `jobs.cost_usd`, which M3-D6 established is margin observability and never the
billing input — so no user was ever charged wrongly. What was wrong was the number the
business would have read to decide whether the product makes money.

---

## Things that are deliberately not done

- **No trimming of over-length clips.** A 4.2-second shot gets a 6-second clip and the
  composition plays the first 4.2 seconds. Trimming server side would need ffmpeg in
  production to save some disk, and the frames being discarded are ones nobody sees.
- **No 1080p clips.** Veo refuses 1080p at the 4-second tier, and 720p is what the
  Preview already renders and what the stub already emits. The Final composition is
  1920×1080 and upscales, as it always did for stub clips.
- **`aspectRatio` is not sent.** A probe with no parameters answered 1280×720 from a 16:9
  still, so the default is already right. A parameter that was never confirmed to work is
  a parameter that fails on somebody else's shot.
- **Only `veo-3.1-lite-generate-preview` is priced.** The other two Veo tiers exist on
  this key and the adapter can be pointed at either, but their per-second prices were not
  read off the pricing page. Unpriced costs zero and logs a warning, which is a *visible*
  gap; a guessed price is an invisible one.
- **No ACE-Step, Wan 2.2 or any self-hosted model.** Spec §6 still names those as the
  production target for licence reasons. Gemini remains what §6.3 says it is: an interim,
  swappable local-development choice. Both registries raise on an unknown name rather
  than falling back.
- **Providers still default to `stub` in `.env`.** Every default in `config.py` and every
  value in the committed `.env` is `stub`. Turning a real model on is a deliberate edit,
  which is the correct default for a repository whose test suite runs hundreds of times.

---

## Verification

### Backend gates

- **Suite: 689 passed, 6 skipped** (up from M6's 650/4). The six skips are the opt-in
  live-API calls — the four that existed, plus `FRAMEMELD_LIVE_MUSIC` and
  `FRAMEMELD_LIVE_VIDEO`.
- `ruff check src tests` and `ruff format --check src tests` clean.
- `alembic check` reports **"No new upgrade operations detected"**. No migration was
  needed: adding providers touches no schema.

### Frontend and render gates

- **85 frontend tests** pass; `tsc --noEmit` and `eslint` clean.
- Render service `tsc --noEmit` clean with `muted` in place.

### Live calls

Each adapter was exercised against the real API once, through its opt-in test:

| Adapter | Result |
|---|---|
| `GeminiLLMProvider` | `test_the_real_api_answers` passed; alias resolved to `gemini-3.5-flash-lite` |
| `GeminiSpeechProvider` | returned a readable WAV on the **first ever** successful call |
| `GeminiImageProvider` | returned a decodable image on the **first ever** successful call |
| `LyriaMusicProvider` | 174-second MP3, 4.2 MB, ~$0.08 |
| `VeoVideoProvider` | 6.000 s, 1280×720, h264 + **aac**, 3.8 MB |

### What is **not** verified

**No full pipeline has been driven end to end against real models.** Every adapter has
been called in isolation and the gates are green, but the story → script → cast → sheets →
stills → speech → music → clips → Final path has only ever run on stubs. That run costs
roughly $2–3 in Veo alone for a six-shot film, and was deliberately not made.

**The Ken Burns numbers are still unjudged.** `render/spec.py`'s `_MOTION` has still only
ever moved a flat colour. A Preview over real stills is the cheap way to judge it —
stills are already affordable — and it has not been done.

**The casting pass has still never run against a live model.** Whether it reliably
distinguishes "seen in the frame" from "mentioned" remains the open question M4 recorded.

**The 80-credit clip price has still never been checked against a real bill.** The
arithmetic now rests on a *verified* $0.05/second rather than on spec §7's estimate, which
is better — but a rate card is not an invoice.
