# M2 (Script) — status

**As of:** 2026-09-13
**Branch:** `m2-script` at `8656d7c` · merge-base with `main`: `9ba8ffa`
**Plan:** [`plans/2026-09-10-m2-script.md`](plans/2026-09-10-m2-script.md)
**Spec:** [`specs/2026-09-09-framemeld-design.md`](specs/2026-09-09-framemeld-design.md)

Work was stopped partway through. This file records where it stopped so it can be picked
up without re-deriving anything.

---

## Summary

**The backend half of M2 is finished and tested. The UI half is not built, so the
milestone is not yet demoable.**

A story generates an editable shot list through a background worker; editing the story
marks exactly the affected scenes and shots stale without destroying a word of them; and
reverting the edit clears the staleness at no cost. All of that is exercised by tests.
None of it is reachable from a browser yet.

- **247 backend tests pass**, 2 skipped (both live API calls — OpenRouter and Gemini, see
  *Unverified* below).
- `ruff check`, `ruff format --check` clean.
- Frontend `tsc --noEmit`, `npm run lint`, `npm run build` clean. **No frontend test suite
  exists yet** — that is Task 12.

## Tasks

| # | Task | State |
|---|---|---|
| 1 | Redis, settings, login rate limiting | done |
| 2 | `stories.input_hash` → `content_hash`; shared `enum_column` | done |
| 3 | `scenes` / `shots` / `jobs` tables + migration | done |
| 4 | content hashes + the staleness engine | done |
| 5 | story-save precondition (409) + staleness wiring | done |
| 6 | provider adapter interface + deterministic stub | done |
| 7 | idempotent job creation + queue boundary | done |
| 8 | script generation: prompt, parser, arq worker | done |
| 9 | script API (read, generate, poll, edit) | done |
| 10 | real OpenRouter adapter | done |
| 11 | API client: request timeout + script calls | code done, **re-review outstanding** |
| 12 | Vitest harness + `useAutosave` regression tests | not started |
| 13 | Script page: generate, poll, read | not started |
| 14 | inline editing, staleness badges, conflict banner | not started |
| 15 | close-out: README, decisions log, browser verification | not started |
| 16 | Gemini LLM adapter + default provider swap | done |

Tasks 1–10 each passed a spec-compliance and code-quality review before being marked done.

## What M2 promised, and what is left

The spec's done-when is: *a story generates an editable shot list; editing the story marks
the script stale without destroying it.*

The generating, the editing and the staleness all work at the API. What is missing is the
Script page (Task 13) and its inline editors and badges (Task 14) — so a user cannot see
or do any of it. Task 12 (the frontend test harness) also closes the last of the four gaps
M1 left open; the other three are closed.

## Unverified

**The real model has never been called.** `OPENROUTER_API_KEY` is not set, so the single
live test skips. Every error path in the adapter is covered offline through
`httpx.MockTransport`, but one thing cannot be settled without a key:

`SCRIPT_JSON_SCHEMA` (in `services/backend/src/framemeld/script/generation.py`) pairs
`strict: true` with `minItems` / `maxItems` / `minimum` / `maximum`. Those keywords sit on
OpenAI's historically-unsupported list for strict structured output, and models proxied
through OpenRouter vary further — Gemini rejects `additionalProperties` outright. If the
configured model refuses the schema, the failure appears the first time a real user
presses Generate, not in the test suite.

To settle it: put a key in `.env` (free tier, no card, from openrouter.ai) and run

```bash
cd services/backend
FRAMEMELD_LIVE_LLM=1 conda run --no-capture-output -n framemeld \
  pytest tests/test_providers_openrouter.py -k real -v
```

**The same question, unresolved differently, now applies to Gemini too.** `LLM_PROVIDER`
defaults to `gemini` as of Task 16 (see *Notable decisions* below). The "Gemini rejects
`additionalProperties`" note above was observed through OpenRouter's proxy layer, which
may reshape the schema before it reaches the model — it is not evidence about Gemini's
own native `generationConfig.responseSchema` handling, which has never been called
either. `GEMINI_API_KEY` is present but empty in `.env`; settle it with
`FRAMEMELD_LIVE_LLM=1 conda run --no-capture-output -n framemeld pytest tests/test_providers_gemini.py -k real -v`
once a real key is in place.

**Task 11's fix round was never re-reviewed.** The code and its fixes are committed and
the gates are green, but the scoped re-review that closes the loop was not dispatched.

## Notable decisions made during execution

Full reasoning for all of these, with what each costs if wrong, is in the execution ledger
(see *Where the detail lives*).

- **Downstream depends on upstream's `content_hash`, never its `input_hash`.** The spec
  originally said the opposite; its own worked example disproved it. Under the old rule a
  human editing a character would never have marked the images generated from them stale —
  the headline feature failing silently in exactly the case it exists for. The spec was
  corrected before any code was written.
- **`script_input_hash` reads the *current* provider configuration.** Staleness and job
  idempotency are therefore the same function, so a row shown as stale can never turn out
  to be a silent cache hit. The cost: changing `LLM_MODEL` marks every existing script
  stale — which is true, and operator-initiated.
- **A user edit never changes a row's `state`.** Editing a shot moves its `content_hash`
  and nothing else; the row holds what the user asked for, so it is not out of date.
- **Regeneration replaces rather than merges.** Matching new shots to old ones would guess
  at an intent the user has not expressed. Their edits are protected by staleness *never
  regenerating on its own*.
- **The frontend test harness is Vitest, not `node --test`** as M1's close-out sketched.
  Node's type stripping does not handle JSX.
- **The login rate limiter fails open if Redis is unreachable**, logging a warning. A Redis
  outage already means the job queue is down; making it a total login outage as well trades
  a small security window for a large availability loss.
- **`LLM_PROVIDER` default switched from `openrouter` to `gemini` (Task 16, 2026-09-13).**
  The developer has a Gemini API key with a $10/month Cloud credit attached; Gemini's free
  tier covers Flash-Lite text generation with no rate-limit surprises like OpenRouter's free
  roster, and its native structured-output support is a better fit for
  `SCRIPT_JSON_SCHEMA`'s `strict`/`minItems`/`maxItems` combination — see the spec's new
  section 6.3. The OpenRouter adapter is untouched and still reachable via
  `LLM_PROVIDER=openrouter`; nothing about the swap required touching the registry's
  contract, which is the point of the adapter boundary. Added `GeminiLLMProvider`
  (`framemeld/providers/gemini.py`) mirroring `OpenRouterLLMProvider`'s error-mapping
  conventions, plus `tests/test_providers_gemini.py` (25 tests, offline via
  `httpx.MockTransport`, same live-API opt-in pattern gated on `FRAMEMELD_LIVE_LLM`).
  **New fixture needed:** the test suite reads `LLM_PROVIDER` from the same `.env` file a
  developer's own dev server uses, so switching that file's default off `stub` would have
  silently broken every test touching the registry — fixed with an autouse
  `_stub_llm_by_default` fixture in `conftest.py` that forces `settings.llm_provider =
  "stub"` unless a test explicitly overrides it. This was a latent gap the swap exposed,
  not something introduced by it.
  **Real cost is not wired up.** `GeminiLLMProvider` always reports `cost_usd=Decimal("0")`
  — Gemini's `usageMetadata` gives token counts, never a dollar figure the way
  OpenRouter's `usage.cost` does, and M2 has no credit ledger to feed regardless. M3 must
  add a real per-token price table before any Gemini-billed call can be settled against a
  user's balance.
  **Still unverified:** whether Gemini actually accepts `SCRIPT_JSON_SCHEMA`'s exact shape
  (see spec 6.3) — nobody has made a real call yet. `GEMINI_API_KEY` is present but empty
  in `.env`; add the real key, then run
  `FRAMEMELD_LIVE_LLM=1 conda run --no-capture-output -n framemeld pytest tests/test_providers_gemini.py -k real -v`.

## Where the detail lives

The execution ledger — every ruling, every review finding, every commit range — is at
`.superpowers/sdd/2026-09-10-m2-script/progress.md`, along with pre-generated task briefs
for 12–15 and the per-task reports.

**That directory is git-ignored and `git clean -fdx` will destroy it.** This file is the
durable summary; the ledger is the detail. If the ledger is gone, `git log` on this branch
still carries the work — each commit message explains its own reasoning.
