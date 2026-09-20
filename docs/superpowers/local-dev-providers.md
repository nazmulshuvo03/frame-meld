# Local development providers — decision record

**Date:** 2026-09-20
**Branch:** `feat/local-dev-providers`
**Preceded by:** [`providers-decisions.md`](providers-decisions.md)

Same purpose as [`m1-decisions.md`](m1-decisions.md) through [`m6-decisions.md`](m6-decisions.md)
and [`providers-decisions.md`](providers-decisions.md): a record of what was probed, what it
cost, and what the code now encodes because of it.

`providers-decisions.md` closed the generative gap by wiring five real Gemini adapters.
This record opens a second, parallel path: **local models for the development loop**, so
the pipeline can be run and re-run without spending anything, while every hosted provider
stays exactly as it was.

---

## Why, given every provider already defaults to `stub`

The stubs make the test suite free, and they were built to emit real media formats so
nothing downstream can ship broken. What they cannot do is produce anything a person can
*judge*:

- `StubImageProvider` returns a 64-pixel flat colour.
- `StubSpeechProvider` returns a sine tone whose length is `len(text) / 14.0`.
- `StubVideoProvider` encodes a flat colour derived from the still's bytes.

So a Preview render proves the pipeline works and shows a slideshow of coloured squares
with beeping over it. Every question about pacing, composition, or whether a cut lands is
unanswerable without paying. The point of this branch is to make the free Preview watchable.

**Nothing is removed.** Each of the three local providers is a new branch in an existing
registry, reached only when its `*_PROVIDER` setting names it. `stub` and `gemini` behave
exactly as they did.

---

## What runs where

| Slot | Development | Production | Why |
|---|---|---|---|
| LLM | Qwen3-8B via Docker Model Runner | Gemini / OpenRouter | OpenAI-compatible; zero new code |
| Speech | Kokoro-82M in a container | Gemini TTS | New adapter |
| Images | SDXL-Turbo via ComfyUI | Gemini image | New adapter |
| Music | *(stub)* | Lyria | One flat $0.08 per track; not worth an adapter |
| Video | *(stub)* | Veo 3.1 Lite | See "Why video stays stub" |

---

## The LLM needed no adapter, and that is the whole argument for the seam

`OpenRouterLLMProvider` posts to `{OPENROUTER_BASE_URL}/chat/completions`, which is the
shape Docker Model Runner and Ollama both serve. Pointing it at a local model is four
lines of `.env`. M2's decision to put the base URL in settings rather than hard-coding
OpenRouter's host paid for itself here, four milestones later.

Two probes were needed before it could be trusted.

### 1. `json_schema` works on llama.cpp-backed servers

The adapter sends `response_format: {"type": "json_schema", ...}`, and every script, cast
and casting pass depends on that constraint holding. It was not safe to assume a local
server honours it — llama.cpp implements structured output through GBNF grammar, which is
a different mechanism from a hosted provider's.

It holds. `finish_reason: "stop"`, and the content parses:

```json
{"title": "Keeper of the Light"}
```

### 2. Qwen3 is a reasoning model, and that is a cost problem

The same request, twice, against `Qwen3-8B-Q4_K_M` on an RTX 3060:

| | thinking on | `/no_think` |
|---|---|---|
| completion tokens | 649 | **16** |
| wall clock | 53.9 s | **2.98 s** |
| generation rate | 12.3 tok/s | 39.4 tok/s |

640 of those 649 tokens were `reasoning_content` — the model deliberating between
"The Lighthouse Warden" and "Echoes of the Lighthouse" for a single-field response. On a
30-shot script that overhead is not a tax, it is a wall.

The switch is the literal token `/no_think` in the prompt. No OpenAI-compatible server
exposes it as a parameter, so it has to travel in the message. Hence `LLM_SYSTEM_SUFFIX`:
appended to `system` in the adapter, empty by default, so no hosted provider changes
behaviour.

It also turned up a **misleading error message of our own**. With thinking on and a token
budget, the reply comes back `finish_reason: "length"` with `content` empty — and the
adapter reports *"The script was too long for the model to finish. Try a shorter story."*
That diagnosis is wrong: the budget went to thinking, not to the script, and shortening
the story would not help. Left as-is, because it is correct for the hosted providers it
was written for and `/no_think` removes the condition locally.

### 3. `_TIMEOUT` 120 s → 300 s

At 39 tok/s a 30-shot script is roughly 90 seconds of generation. The old 120-second
budget would have failed ordinary scripts as `ProviderTransportError("Could not reach the
language model")` while the model was working correctly — a transport error for a
throughput problem, which is the hardest kind of wrong message to debug. Costs a hosted
provider nothing: a call that was going to fail simply takes longer to say so.

---

## The expensive knowledge: Kokoro's WAV header lies

Kokoro serves an OpenAI-compatible `/v1/audio/speech`, and the obvious call asks for
`response_format: "wav"`. The bytes that come back are a real, playable WAV at 24 kHz
mono 16-bit — byte-format identical to what `providers/speech/wav.py` already declares.

It is also a **streaming** WAV. The RIFF and `data` chunk sizes are `0xFFFFFFFF`, because
the header is written before the audio exists:

```
RIFF size field 4294967295
```

`assets/service.py::wav_duration` measures a clip with `wave.getnframes()`. For a
1.48-second line of narration that arithmetic gives:

```
4294967295 / 2 / 24000 = 89478.49 seconds
```

No exception. No failed job. It fits `Numeric(8, 2)` without overflow. The row records
24.8 hours of narration for a one-second line, and the first symptom would be a Preview
render that never finishes — with every job reporting success.

This is worse than the defect it was meant to fix. `StubSpeechProvider`'s
`len(text) / 14.0` is a guess, but it is a guess of the right order of magnitude; this is
wrong by a factor of 60,000.

**The fix costs nothing: ask for `pcm`.** Kokoro then returns headerless samples — exactly
what Gemini TTS returns — and `wrap_pcm16` writes a correct header. Measured after the
change: 1.48 s, as the audio actually is. `test_it_asks_for_pcm_and_never_for_wav` pins
both halves, including what the broken header would have measured, so an edit that
switches the format back has to delete a test that explains why.

M3-D11's rule — probe before writing adapter code — found this. It would not have been
visible in any response the adapter inspected, only in a duration column three layers
downstream.

### Voices were already planned for

`providers/speech/voices.py` said, four milestones ago:

> a future Kokoro adapter (spec section 6) maps them rather than the database being
> rewritten — a voice id is part of a character's content_hash, and migrating one would
> invalidate every audio clip in the system.

So `characters.voice_id` still holds `"Kore"` whichever provider is configured, and the
mapping lives in the adapter. Kokoro happens to ship four voices under Gemini's own names
(`af_kore`, `am_puck`, `am_fenrir`, `af_aoede`); the other four are matched on the gender
Gemini publishes, which is the only property of a voice this product depends on.

---

## Images: ComfyUI, and two things that are properties of the checkpoint

### Cold start is 107 seconds, steady state is 2.18

| | time |
|---|---|
| first prompt after container start | **107.44 s** |
| sampling within that first prompt | 1.28 s |
| every prompt after | **2.18 s** at 1024×1024 |

The 106 seconds are a 6.9 GB checkpoint loading from disk. `_GENERATION_BUDGET_SEC` is
therefore sized at 300 s against the cold start, not the steady state: a budget tight
enough to suit warm runs would fail the first run of every day. The first image job after
a container restart will look hung. It is not.

At 2.18 s a 30-shot film's worth of stills costs about a minute, for nothing.

### Turbo draws in one step, and the default settings ruin it

SDXL-Turbo is distilled. It wants `steps: 1, cfg: 1.0`; ComfyUI's ordinary defaults
(20 steps, cfg 8.0) produce a burned, over-saturated image. **The failure is visual, not
an error** — the job succeeds and the picture is wrong.

These live as constants in the adapter rather than in `params`, deliberately. `SHEET_PARAMS`
and `STILL_PARAMS` carry `size` and a prompt version, and both are inside the content hash
of every sheet and still in the database. Adding sampler settings to them would invalidate
existing rows to record something that is a fact about the checkpoint, not about the request.

### `refs` is accepted and ignored, on purpose

`ImageProvider.generate` takes `refs: list[bytes]` — each character's approved sheet, which
is how a face stays the same across shots. Honouring that locally needs IP-Adapter nodes
and a substantially larger workflow.

It is ignored here, and `test_refs_are_accepted_and_ignored` says so out loud. Character
consistency is a question about the **production** provider; the development loop is for
timing, composition and whether the pipeline holds together. Anything that looked like
character consistency from a local checkpoint would be a coincidence worth trusting even
less than an obvious absence.

### The seed is derived from the prompt

Same reasoning as `voice_for_index`: the staleness engine compares hashes, so an unchanged
prompt has to give an unchanged picture. A random seed would make every still look like
work to redo on every pass. A hosted model is not reproducible either — but a development
provider that is makes a cache hit visible, which is exactly what one wants to be able to see.

---

## Why video stays stub

The tempting move is a local image-to-video model (Wan 2.1 1.3B, LTX-Video) through the
same ComfyUI container. It was rejected, and not on quality grounds:

**A 5-second clip on a 12 GB card takes 5–10 minutes.** A 30-shot film is therefore hours
per iteration, against 0.2 s per clip for `StubVideoProvider`. Local video makes the
development loop slower by four orders of magnitude — the opposite of what this branch is for.

The free watchable path already exists and does not need a video provider at all: a
**Preview** render reads `still_url` with `clip_url: null` and animates the stills through
`motion` (spec §4.2). Once the stills are real — which is what the ComfyUI adapter
delivers — the Preview *is* the development artifact. Real motion is a question to answer
by running one 3-shot Meld against Veo for roughly $0.60, a handful of times, not on every loop.

Music stays stub for a duller reason: Lyria bills a flat $0.08 per track regardless of
length, and an adapter to avoid eight cents is not worth its own maintenance.

---

## What this cost, and what it saves

Per 30-shot film, from `rates.py`:

| Slot | Hosted | Local |
|---|---|---|
| Video | $6.00–9.00 | *(unchanged — still stub in dev)* |
| Images | ~$1.20 | **$0** |
| Speech | <$0.20 | **$0** |
| LLM | ~$0.02 | **$0** |

The money was never the point — video dominates and video is untouched. The point is that
the loop is now unlimited: regenerate a cast, rewrite a script, redraw thirty stills as
many times as the work needs, and watch the result, without a credit moving.

---

## Running it

Three containers, none of which the application requires:

```bash
docker model pull ai/qwen3
docker run -d --name kokoro --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:latest
docker run -d --name comfyui --gpus all -p 8188:8188 \
  -v F:/ai-models/comfyui:/app/models \
  -v F:/FrameMeld/checkpoints:/app/models/checkpoints \
  ghcr.io/jemeyer/comfyui:latest
```

`sd_xl_turbo_1.0_fp16.safetensors` goes in `checkpoints/`, which `.gitignore` excludes —
7 GB of weights does not belong in a working tree.

Then the `.env` block in `.env.example` under "Local development providers".

**Do not use `ghcr.io/ai-dock/comfyui`.** It is built for GPU cloud: it fronts ComfyUI
with a Caddy auth proxy and opens Cloudflare quick tunnels to publish the machine to the
public internet, retrying every 64 seconds. That is a reasonable default for a rented GPU
and an unreasonable one for a development machine.

### VRAM

12 GB does not hold everything at once — Qwen3-8B is ~5 GB and SDXL-Turbo ~7 GB. They are
used at different stages (Story/Script versus Cast/Shots), so this only matters if one
refuses to let go. Docker Model Runner unloads on idle; with Ollama, set
`OLLAMA_KEEP_ALIVE=30s`.

### From inside compose

`api` and `worker` run in containers, so `localhost` means the container. Use
`host.docker.internal` (or `model-runner.docker.internal` for Docker Model Runner).

---

## Going back

Set the three `*_PROVIDER` values to `stub` or `gemini` and restart. No migration, no data
change, no code path removed — which was the constraint this branch was written under.
