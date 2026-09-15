# M4 decision record

Same purpose as [`m1-decisions.md`](m1-decisions.md), [`m2-decisions.md`](m2-decisions.md)
and [`m3-decisions.md`](m3-decisions.md): every judgement call made while executing M4, so a
future contributor can find out why without re-deriving it. Executed against
[`plans/2026-09-15-m4-shots.md`](plans/2026-09-15-m4-shots.md), with the backend gates run
throughout and a browser verification pass at the end.

## M4-D1 … M4-D10 (decided before implementation)

Recorded in full in the plan ("Decisions taken up front"). Summary:

- **M4-D1** — `shot_characters` gets its own job kind, `map_shot_characters`.
- **M4-D2** — a still keys on a narrow *visual* projection of the shot plus the sorted
  `content_hash` of every sheet it references.
- **M4-D3** — speech keys on its own narrow projection; `characters.voice_id` becomes
  user-editable.
- **M4-D4** — `audio_clips` lands in full per spec §5.3; only shot-owned speech is produced.
- **M4-D5** — no batch job; "Generate all" is N per-shot jobs run one at a time.
- **M4-D6** — a still is refused when a character in the shot has no sheet.
- **M4-D7** — `update_shot` recomputes staleness.
- **M4-D8** — the stub speech provider emits a real WAV.
- **M4-D9** — `SPEECH_PROVIDER` defaults to `stub`.
- **M4-D10** — reference sheets are read from storage per call, with no cache.

All ten survived execution. M4-D1 contradicts §5.4's job-kind list, which has been corrected
in place, the way M3 corrected §9.

---

## The expensive knowledge: the casting job invalidated its own cache

This is the one thing in M4 that was designed wrong and caught by a test rather than by
reasoning, and it would have been a recurring, invisible charge to every user.

`map_shot_characters` **writes** `shots.speaker_character_id`. That column is inside
`content_hash_for_shot`. The casting job's `input_hash` was, as first written, built from
those content hashes — so the moment the job succeeded it moved the very value its
idempotency key was derived from. Every subsequent press of "Re-cast the shots" was a cache
*miss*, took a fresh hold, and charged again for an answer that had not changed. The job
could never be free, and nothing about the code looked wrong.

`test_casting_twice_with_unchanged_inputs_is_free` found it on the first run. Nothing else
would have: every unit test of the hash passed, the job worked, the mapping was correct.

The fix is the same narrow-projection rule the milestone already applies twice.
`casting_hash_for_shot` is `{image_prompt, camera, speech_text}` — exactly the three fields
`build_casting_messages()` prints — and `casting_hash_for_character` is
`{name, role, description}`, likewise. Notably *not* `appearance`: the casting model decides
who is in a frame, not what they look like, so redescribing a face must not re-run and
re-charge the whole mapping.

**The generalisation, now three milestones deep:** an artifact's `input_hash` must cover
exactly the fields that reach its prompt, and nothing else. Too wide charges the user for
nothing (M3's sheet-versus-description bug); too wide *and self-referential* charges them
forever (this one); too narrow ships stale artwork. The rule is not "hash the parent's
content_hash", it is "hash the part of the parent this artifact is actually made from",
which is what spec §4.1 means by upstream's current content.

---

## Gaps and corrections found during execution

**`update_shot` never recomputed staleness, exactly as `update_character` had not.** M4-D7
predicted this from reading M3's decision record rather than from a browser session. Until
M4 nothing depended on a shot's content, so the call was not needed and was not made; a
still drawn from an old `image_prompt` would have gone on claiming to be fresh forever.
Same bug, same shape, one milestone apart — the argument for reading the previous
milestone's ledger before starting the next.

**Regenerating a script orphaned every audio clip.** `audio_clips.owner_id` is polymorphic
(spec §5.3: a clip hangs from a shot, or from the Meld when M5's music arrives), so it
carries no foreign key and nothing cascades it. `apply_script` deletes scenes and lets
Postgres cascade to shots, `shot_images` and `shot_characters` — but the recordings survived,
pointing at shot ids that no longer resolved. Fixed with an explicit delete in `apply_script`,
before the scene delete, and pinned by
`test_regenerating_the_script_takes_the_audio_with_it`.

**Autogenerate did not notice the new job kind.** Alembic does not diff the *body* of a
CHECK constraint, so adding `MAP_SHOT_CHARACTERS` to a `StrEnum` behind `enum_column()`
produced no migration operation at all — the new value would have been refused at runtime
with a constraint violation. The drop-and-recreate is hand-written in
`77f9548e4df1`, with the old and new value lists spelled out rather than imported from the
live enum, because a migration describes the schema at one point in history and an import
would silently rewrite it every time a later milestone adds a kind. This is the plan's
"read and correct autogenerate by hand" rule earning its keep for the second time.

**The stub LLM did not know the casting schema** — the identical trap M3 hit with the cast
schema. `StubLLMProvider` now branches on `json_schema["name"] == "shot_characters"` and
parses the cast names and shot numbers back out of the message
`build_casting_messages()` sent. The rotation deliberately leaves every fourth shot empty and
gives every third shot two characters, so a suite run always exercises the no-reference path
and the multi-reference path, which take different routes through the worker.

**`_load_refs` reads the clip/still row, never the live shot.** The row is what the hold was
priced against, and the user can edit the shot while the job is queued. Reading the shot in
the worker would let a job quietly record something the user never approved and never agreed
a price for. Pinned by `test_speech_reads_the_clip_row_not_the_live_shot`.

---

## Things that are deliberately not done

- **No reference-sheet cache** (M4-D10). A four-character shot is four `Storage.get` calls.
  M3's roadmap flagged a cache here as "worth measuring before building one"; it has not
  been measured, so there is no cache.
- **No batch job kind** (M4-D5). M3's roadmap expected M4 to be where `settle`'s
  variable-units path finally got exercised. It is not, and that remains the better answer —
  see the plan. The path is still unexercised and belongs to M6's resumable render.
- **`seed` on `shot_images` is declared and never set.** No configured provider accepts one.
- **`audio_clips` `music`/`sfx` kinds and the `meld` owner type are declared and never
  produced.** M5's music is the first writer.

---

## Verification

### Backend gates

- **Suite: 516 passed, 4 skipped** (up from M3's 410/3). The four skips are opt-in live-API
  calls: `FRAMEMELD_LIVE_LLM` for the two text adapters, `FRAMEMELD_LIVE_IMAGE` for the image
  adapter, and the new `FRAMEMELD_LIVE_SPEECH` for the TTS adapter.
- `ruff check src tests` and `ruff format --check src tests` clean.
- **Migration:** `alembic upgrade head` applied `77f9548e4df1` cleanly, and `alembic check`
  reports **"No new upgrade operations detected"** — no drift between the chain and the
  models.

### Frontend gates

- **29 tests passed** (up from 20): 11 `useAutosave`, 4 API client, 5 `QuoteButton`, and 9
  new `ShotMediaCard`.
- `tsc --noEmit`, `eslint` and `next build` all clean.

### Browser pass

Against a real backend, a real worker, and a fresh account. The image and speech providers
were the stubs (billing is still not enabled — see m3-decisions.md); the LLM is discussed
below.

| Check | Result |
|---|---|
| Shots page before casting | 11 shots in **film order** (scene 1's three, then scene 2's, then scene 3's five), with a banner explaining that casting comes first |
| Batch button quote | **"Draw the 11 remaining · 132 credits"** — 11 × 12, the price of the whole action on the button face |
| Cast the shots | characters appeared per shot via polling with **no reload**; balance 495 → 490, exactly the quote |
| Shots with nobody in them | rendered as "nobody in frame" — distinguishable from "not cast yet" |
| Still for a shot whose character has no sheet | **422**, naming Elena, balance unmoved (M4-D6) |
| Generate audio | player appeared without a reload, in **Elena's voice (Kore)**, 5.71 s; balance 490 → 488, exactly the quote |
| Still for a two-character shot | drawn from **2 references** (`ref_character_image_ids` length 2); balance −12 |
| Two stills | different `asset_id`s — no shared picture |
| **Edit a character's description only** | **nothing went stale, nothing was charged** — the two-hash rule, one artifact deeper than M3 |
| Edit that character's appearance | her sheet went stale; the other character's stayed fresh |
| **Redraw her sheet** | **exactly the still that referenced her went stale**; the still with only the other character stayed fresh, and both kept their images |
| "Draw the 9 remaining" | queue drained one job at a time to **11 drawn, 11 distinct assets**; balance arithmetic exact |
| Re-ask for an unchanged still | **202 with state `succeeded`, charged nothing** — spec §4.1's cache hit |
| Change a character's voice | their clip went stale, **no still moved, and their sheet stayed fresh** |
| A real provider 429 | 3 attempts with 2 s then 4 s backoff, then terminal — and the balance returned to **exactly** where it started |

That last row is worth its own note: it is spec §5.4's retry policy and §4.3's refund rule,
exercised against a genuine provider failure rather than an injected one, on the first
milestone where the new job kinds could have got either wrong.

### What is **not** verified

**The casting pass has never run against a live language model.** Gemini's free tier
answered `429` on every attempt across the session (`generate_content_free_tier_requests`),
including after a gap, so the browser pass ran on `LLM_PROVIDER=stub`. The mapping logic,
the prompt, the parser, the applier and every money path are covered by the suite and by the
browser pass, but **the quality of a real model's answer to `_CASTING_SYSTEM` is unknown** —
whether it reliably distinguishes "seen in the frame" from "mentioned", and whether it
returns one entry per shot. That is the same class of gap M3 recorded for the image adapter,
and it is the first thing to check when quota allows:

```bash
# with LLM_PROVIDER=gemini in .env, on a Meld with a script and a cast
POST /melds/{id}/shots/cast
```

**`GeminiSpeechProvider` has never made a successful call either.** Unlike image generation,
Gemini TTS *does* have a free tier, so this one is expected to work — but "expected to" is
exactly the assumption M2's `responseSchema` incident and M3's Imagen-4 probe both punished.
The adapter follows the documented `inlineData` + `audio/L16` shape, and the PCM-to-WAV
wrapping is the part most likely to be wrong. To close it:

```bash
FRAMEMELD_LIVE_SPEECH=1 conda run --no-capture-output -n framemeld \
    pytest tests/test_providers_speech_gemini.py -q
```

and correct `_extract_audio`/`_to_wav` against whatever comes back **before** trusting it
with a user's credits.

---

## Process notes

**The duplicate-process trap, for the third milestone running.** Switching
`LLM_PROVIDER` mid-session and restarting the worker left the *old* worker alive, and it won
the job — so a run that was supposed to prove a live Gemini call quietly went through the
stub, and the only tell was `cost_usd = 0` on the job row. m2-decisions and m3-decisions both
record this exact failure mode, and it recurred anyway. `taskkill` on the port's PID is not
enough: `conda run` wraps the process, so the child survives. Enumerate `arq.exe` and
`uvicorn.exe` directly, kill those, and confirm **exactly one of each** before trusting
anything the browser shows.

**Checking `cost_usd` is how you know which provider actually ran.** Cheaper and more
reliable than reading logs, and it is the reason M3-D6 put that column there.

**Observation, not an M4 defect:** under a dark OS theme the app renders dark-on-dark in
places, because every page uses fixed `text-neutral-*` colours with no dark-mode variants.
This predates M4 and affects the Story, Script and Cast pages identically. It is a
whole-app theming decision, not a Shots-page bug, and is left alone here rather than fixed
on one page.
