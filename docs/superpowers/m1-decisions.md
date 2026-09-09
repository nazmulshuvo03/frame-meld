# M1 decision record

Every judgement call made while executing M1's implementation plan, in the order
they were made. Each states what it cost if wrong.

These were decided by the coordinating agent during a subagent-driven execution
of `docs/superpowers/plans/2026-09-09-m1-foundation.md`. They are recorded here
rather than in a scratch file because they answer the questions a future
contributor will actually ask -- why there is a root `pytest.ini`, why
`canonical_hash` refuses non-JSON-native values instead of coercing them, why
`MeldOut` deliberately has no `story` field, why the session token is SHA-256
rather than argon2.

## How to read this

Rulings are prefixed by where they arose:

| Prefix | Stage |
|---|---|
| (unprefixed) | Setup, before Task 1 |
| `PF-` | The pre-flight cross-task conflict scan |
| `PD-` | A plan defect found mid-task |
| `TC-` | The toolchain decision (uv unavailable -> conda) |
| `R<n>-` | Task `<n>`'s review loop |
| `FR-` | The final whole-branch review and its fix wave |

Eight defects were found in `apps/web/lib/useAutosave.ts` across six review
rounds. Two of them were the coordinator's own: one in the original design, one
in a fix prescription. Every one either lost the user's typed text or reported it
saved when it was not, and none would have failed a test. That file is the
milestone's acceptance criterion, and it is worth reading its history before
changing it.

---

Ruling: git init + repo-local user.email, baseline commit on main, work on branch m1-foundation (not a separate worktree) — the plan hardcodes /f/FrameMeld paths throughout, the user is watching this directory in their IDE, and an empty repo has nothing to isolate from; a branch gives the reviewable/abandonable boundary that matters — cost if wrong: none material, the branch can be rebased or discarded.

Ruling: set user.email repo-locally rather than globally — global was unset and commits would fail; repo-local is scoped and reversible — cost if wrong: none, one config line.

Ruling: Task 1 Step 1 (git init) is already done by setup. Implementer told to skip it and keep the rest of Step 1 — cost if wrong: none, git init is idempotent.

Ruling PF-1: add "pydantic[email]>=2.9" to Task 1's pyproject dependencies. Task 7 uses pydantic EmailStr, which requires the email-validator package; without it Task 7 fails at import time and its malformed-email test cannot pass. The plan omitted it — cost if wrong: none, it is a required transitive dependency either way.

Ruling PF-2: Task 2's db fixture must construct its sessionmaker with join_transaction_mode="create_savepoint". Task 10's route handlers call session.commit(); a session bound to an outer connection transaction without this argument can end that outer transaction, breaking per-test isolation and making tests pass alone but fail together. This is the SQLAlchemy 2.0 documented recipe for joining an external transaction — cost if wrong: if unnecessary it is harmless; if omitted and needed, Task 10 produces confusing cross-test failures.

Ruling PF-3: Task 8's test_deleting_a_meld_cascades_to_its_story must load the story relationship before deleting the Meld (or assert via the DB-level ON DELETE CASCADE instead). Under asyncio, an ORM cascade over an unloaded relationship raises MissingGreenlet. The test as written also asserts nothing after the flush, so it is replaced with a version that loads the relationship and asserts the story row is gone — cost if wrong: a slightly different test of the same guarantee.

Ruling TC-1: asked the user rather than ruling, because both paths were ask-first territory
— one installs software system-wide (winget install uv), the other changes the toolchain
across all 15 tasks. User chose conda. Cost if wrong: none, the question was cheap and the
answer was theirs to give.

Ruling TC-2: conda supplies ONLY the Python interpreter; every project dependency is pip-
installed from pyproject.toml inside the env. I flagged conda/pip mixing as this option's
main risk, and this split is the mitigation — the breakage comes from installing the same
package from both channels, not from using both tools. Cost if wrong: if a dep later needs a
conda-only binary wheel, that one package moves to environment.yml.

Ruling TC-3: created conda env

Ruling TC-4: pinned the conda env to Python 3.12 (not the 3.13 already on the machine) to
match the plan's requires-python = ">=3.12" floor and the spec's stated stack. Cost if wrong:
none material; 3.13 also works for every dependency in the list.

Ruling PD-1: fix config.py with Annotated[list[str], NoDecode] plus a mode="before"
field_validator that comma-splits; keep .env.example human-readable as
CORS_ORIGINS=http://localhost:3000. Rejected the alternative (JSON array in .env) because
Docker, Kubernetes and every PaaS hand env vars as comma-separated strings, so JSON-only
would be a permanent papercut in production config. NoDecode is pydantic-settings' purpose-
built annotation for this; I verified it exists in the installed 2.15.0 and probed the exact
code against single-value, comma-list, and default cases before handing it over — all three
pass. Cost if wrong: 6 lines in one file, trivially revertible to the JSON-array form.

Ruling PD-2: recorded that

Ruling PD-3: removed README.md from Task 1's Files list; Task 15 remains its sole owner.
Implementer was right to leave it uncreated rather than fabricate content. Cost if wrong:
none, the README still gets written in Task 15.

Ruling PF-2/PF-3 promoted from dispatch notes into the plan text itself, so the briefs carry
them as requirements rather than as controller asides the reviewer cannot see. Verified
join_transaction_mode exists in the installed SQLAlchemy 2.0.52 before writing it into the
plan, same discipline as NoDecode. Cost if wrong: none, both edits are localised.

Ruling R2-1: both Important findings are correct and enter the fix loop. Finding 1 (unguarded
db teardown) matters more than it looks: under NullPool a leaked connection is a live Postgres
backend held to process exit, so a few failing tests can exhaust max_connections and cause
failures in unrelated later tests — the exact misattributed-failure mode this task exists to
prevent. Finding 2 (dependency_overrides.clear()) becomes actively harmful at Task 7, when auth
fixtures arrive and clear() would wipe a sibling's override. Both are localised try/finally
edits. Cost if wrong: a few lines of defensive structure in a test fixture.

Ruling R2-2: folding Minor 3 (AsyncGenerator return annotations) into the same fix round,
against the normal rule that Minors never enter the loop. It is on the same

Ruling R2-3: ELEVATING Minor 5 (assert test_database_url != database_url) into the fix round.
The reviewer scored it Minor as out-of-brief-scope, and by the rubric that is right, but the
hazard is irreversible: both URLs come from the same .env, and from Task 5 onward drop_all
against a mis-copied dev URL destroys the development database with no undo. Task 2 is the last
task that owns this file before models exist. A one-line guard against unrecoverable data loss
outranks scope purity. Cost if wrong: one assert.

Ruling R2-4: DEFERRING Minor 4 (asyncio_default_fixture_loop_scope unset; pytest-asyncio
unbounded floor) to the final review. Criterion: the reviewer verified both candidate values
fail LOUDLY at collection (ScopeMismatch at plugin.py:558 for "function"; cross-loop for
"session"), so this cannot produce a misattributed silent failure — which is the test I am using
to decide what is urgent. Recording the verified analysis here so the final review inherits it
rather than re-deriving it. Cost if wrong: a future pytest-asyncio 2.x upgrade fails visibly at
collection and someone reads this ledger entry.

Ruling R3-1: remove default=str entirely and let json.dumps raise TypeError on any
non-JSON-native value. One deletion closes BOTH Important findings — I verified set, datetime,
Decimal and UUID all raise cleanly afterwards, and that Task 9's actual payload
({"kind":"story","text":...}) still hashes fine. Chose fail-loudly over the reviewer's
suggested type-tagged encoding ({"__type__":"datetime",...}) on YAGNI grounds: we do not yet
know which non-native types this system needs, and inventing an encoding for all of them now
means designing against imagined requirements. When M2 genuinely needs to hash a UUID, the
TypeError fires at the call site during development and the developer writes str(uuid)
deliberately — visible and intentional — or we add type tagging then, against a real payload.
The asymmetry decides it: a crash in development costs minutes, a silent hash collision in
production means either failing to regenerate stale work or charging users to regenerate work
that was fine. Cost if wrong: callers must stringify explicitly, which is a compile-time-visible
inconvenience, not a correctness risk.

Ruling R3-2: this is the right moment to fix it — nothing consumes canonical_hash until Task 9,
so no payload shape has locked in around the current behaviour and no stored hash needs
migrating. Deferring past M2 would mean rehashing live artifacts. Cost if wrong: none.

Ruling R3-3: folding Minor 1 (tuple and list serialise identically) into the docstring, since
the docstring is being edited anyway. Not fixing it in code: a tuple and a list of the same
elements both mean "ordered sequence", so treating them alike is defensible — it just needs to
be stated rather than discovered. Cost if wrong: a docstring line.

Ruling R5-1: keep this deferred to the final review's fix wave rather than fixing it now,
despite having just demonstrated it. Three reasons: no remaining task touches config.py, so
there is no dispatch it can ride along with; controller-authored fixes skip review, which the
process forbids; and the final review already batches deferred minors into a single fix wave,
which is cheaper than a one-off dispatch now. It blocks nothing — every command in the plan and
the README cds to services/backend first. Cost if wrong: a developer running the suite from the
repo root gets a confusing ValidationError instead of a clear message, until the final wave.

Ruling R6-1: Minor 3 (resolve_session would AttributeError on a None token) needs no action —
I checked Task 7's plan code and BOTH call sites already guard it: deps.py line 1260 raises 401
on a falsy cookie, and the logout handler at line 1356 wraps revoke_session in

Ruling R6-2: FOLDING Minor 1 into Task 7 as two extra tests, not into Task 6. The gap is real —
I grepped Task 7's plan and confirmed nothing tests logout with a forged or absent cookie — but
it is logout-endpoint coverage, so Task 7 is its natural owner rather than a reopened Task 6.
The reviewer's reasoning is the point: a future refactor of revoke_session to scalar_one() would
turn every stale cookie into a 500 with the suite still green. Added both tests to the plan and
raised the counts (auth API 10 -> 12, total 69 -> 71). Cost if wrong: two cheap tests.

Ruling R6-3: DEFERRING Minor 2 (the test asserts token_hash != token but never that it IS the
SHA-256, so a consistent-but-wrong digest keeps all eight tests green) to the final review's fix
wave. Consistent with ruling R5-1 on config.py: it is a test-strength improvement, the code is
correct today, no later task touches test_sessions.py, and the final wave batches it at zero
marginal dispatch cost. Not elevated because it would take a deliberate edit to _hash_token — a
two-line documented function — to exploit, unlike the irreversible data loss that justified
elevating Task 2's Minor 5. Cost if wrong: the weaker assertion stands until the final wave.

Ruling R7-1: ELEVATING Minor 2 (logged_in fixture ignores both response statuses) into a fix
round. Every Meld test in Tasks 9 and 10 — 29 tests — depends on this fixture. A silent
signup/login failure would hand them an unauthenticated client, so they would fail with
confusing 401s pointing at Meld code instead of at the fixture. The misdiagnosis cost is
multiplied across the two largest remaining tasks, and the fix is two assert lines. Cost if
wrong: two asserts.

Ruling R7-2: ELEVATING Minor 3 (comment at router.py:63-64 overclaims) into the same round. It
says "the response cannot be used to enumerate registered emails", which is true of the body but
not of the response time. In security code a comment that overstates a guarantee is how the
measured 70ms gap becomes a real vulnerability later — someone reads it and treats login as
timing-safe. Three words, same fix round. Cost if wrong: a narrower comment.

Ruling R7-3: ELEVATING Minor 4 (check-then-insert TOCTOU on signup) into the same round,
against the reviewer's own "not on its own" recommendation. My reason for going further: this is
reachable by a user double-clicking the signup button, not only by a contrived race — two rapid
POSTs both pass the existence check, both insert, one gets IntegrityError and returns 500. A 500
on signup reads as a broken product. router.py is already open for R7-2, so the marginal cost is
one except clause. Data integrity was never at risk; the unique constraint holds either way.
Cost if wrong: one except IntegrityError branch.

Ruling R7-4: DEFERRING Minor 1 (ruff format --check fails) to the final review's fix wave.
Plan-mandated — my brief contained the 108-char line. Note ruff CHECK passes: no select is
configured so E501 is not in the default rule set, which is why pytest-only runs missed it. The
plan's acceptance criterion is

Ruling R8-1: all four arguments are required together — native_enum=False, create_constraint=True,
name=, values_callable=. Chose DB-level CHECK over the Python-side alternative (validate_strings=True)
because a CHECK protects against every writer, not just the ORM: raw SQL, a future worker, a
migration backfill.

Ruling R8-2: extracted the four-argument combination into a documented _enum_column() helper in
models/meld.py rather than repeating it three times. Justification against the premature-abstraction
rubric: three call sites in one file, four arguments each of which fails SILENTLY if omitted, and
more enum columns arriving in M2 (shots, characters, audio). Verbatim triplication of a config
block whose failure mode is invisible is worse than a named helper. Kept it local to meld.py
rather than a shared module — YAGNI until a second model file needs it, noted in the docstring.
Cost if wrong: one small function to relocate.

Ruling R8-3: updated the plan's Global Constraints AND added the convention to the spec, because
the plan's constraint text ("VARCHAR plus a CHECK constraint") described an outcome its own code
did not produce. Every later milestone adds enum columns, so leaving the incomplete recipe in the
authoritative documents would reproduce this defect in M2, M3 and beyond. Cost if wrong: none,
both documents now state the verified recipe.

Ruling R9-1: accept the app-side created_at, but require it be made consistent — created_at and
updated_at must come from ONE timestamp value in create_meld. As delivered, created_at is app
clock while updated_at is still DB clock via server_default, so a freshly created Meld can have
updated_at < created_at under host clock skew, and MeldOut exposes both to the dashboard. A
negative age reads as a bug. One line, in a function already being edited. Cost if wrong: two
timestamps assigned from one variable.

Ruling R9-2: rejected the alternative fix of changing the column server_default to
clock_timestamp(), despite it being the semantically better answer — created_at means "when the
row was created", which is statement time, whereas now() is transaction-start time and is subtly
wrong for any long transaction. Rejected on scope: it needs a third migration, and doing it for
melds.created_at alone leaves that column behaving differently from every sibling, while doing it
consistently reaches back into Task 5's closed tables. Recorded for M2 to consider properly.
Cost if wrong: created_at reflects app time rather than DB statement time; both are within
milliseconds and neither is user-visible.

Ruling R9-3: did NOT touch the db fixture to give each service call its own transaction, which
would also have "fixed" the test. That fixture's exception-safety and savepoint semantics cost a
full fix round in Task 2 and every later test depends on them. Changing transaction scope to
satisfy one ordering assertion would trade a real isolation guarantee for a cosmetic one. Cost if
wrong: none, the chosen fix is strictly smaller.

Ruling R9-4: adopt the reviewer's fix — assign updated_at explicitly in rename_meld and
soft_delete_meld so onupdate never applies, putting every Meld timestamp on one clock. This
completes R9-1 rather than replacing it; my original ruling was half a fix and I did not see the
transaction-scoped half. No model or migration change, and neither previously-rejected
alternative needs revisiting. Cost if wrong: two assignments.

Ruling R9-5: ELEVATING Minor 2 (save_story's redundant query). get_meld already eager-loads the
Story via selectinload, so

Ruling R9-6: ELEVATING Minor 3 (no ORDER BY tie-break). Adding Meld.id.desc() costs nothing and
makes the ordering a total order independent of clock resolution. Directly addresses the structural
fragility that produced this task's original failure — leaving ordering derived purely from
wall-clock values invites the same class of bug back. Cost if wrong: one ORDER BY term.

Ruling R9-7: ELEVATING Minor 4, but only for the soft-delete test. It asserts
meld.deleted_at is not None on the identity-mapped instance the function just mutated, so it would
pass even if the UPDATE never reached Postgres. "Nothing is destroyed by an edit" is the spec's
headline guarantee and the suite currently does not prove it — the implementer verified persistence
out-of-band with raw SQL, but that evidence is uncommitted. Adding db.expire + re-read makes the
suite guard it. Not extending this to the rename assertion: same weakness, but rename is not a spec
guarantee about data survival. Cost if wrong: three lines in one test.

Ruling R9-8: ACTING on Minor 6 (save_story does not bump meld.updated_at) as a design decision
rather than deferring it. Task 13's dashboard renders "updated {meld.updated_at}" on every card.
Without this, a user who spends an hour writing sees their Meld still labelled with its creation
date — a visible lie in the product, and precisely the field they would look at to find recent
work. Semantically, editing a Meld's story updates the Meld. Costs one assignment in the same
flush, no extra round trip. Cost if wrong: the dashboard's timestamp moves on story edits, which
is what a user expects.

Ruling R10-1: the plan's M1 acceptance criterion "ruff check src tests is clean" currently FAILS.
Recording it as a blocker for declaring M1 complete rather than quietly restating the criterion to
match reality. It must be green before the milestone is called done.

Ruling R10-2: fix all 4 in the final review's fix wave rather than now. They are pre-existing
(Tasks 2, 3, 5, 8), total roughly five lines across three files, and cosmetic — none affects
behaviour. A dedicated dispatch plus re-review cycle for five lines of lint costs more than the
final wave, which already carries the ruff-format items from Task 7. One coherent lint-and-format
commit. Cost if wrong: the criterion stays red until that wave, which is why R10-1 records it as a
blocker.

Ruling R10-3: the final wave must also add an explicit

Ruling R10-4: fix the Important finding with a _not_found() factory rather than inlining four
HTTPException constructions. Keeps ONE shared message string, which is the anti-enumeration
property — four inline copies invite one of them drifting, and a 404 whose text differs from its
siblings is exactly how existence leaks back in. Agreed with Important over Critical: no caller
can read another user's data or learn a Meld exists through it, and responses stay correct because
Starlette reads only status_code/detail/headers. But it is a real memory leak, and a future error
reporter would ship cross-user request state out of a module global. Cost if wrong: one factory
function.

Ruling R10-5: ELEVATING Minors 1 and 2 (three dead db.refresh calls). Both session factories are
expire_on_commit=False, so commit() expires nothing and there is nothing to reload — each mutating
request pays a pointless SELECT, including PUT /story on every 800ms autosave. Minor 2 is the
sharper half: router.py:54's refresh EXPIRES the eager-loaded story relationship, safe today only
because MeldOut has no story field. That is a live tripwire sitting one line from the invariant
schemas.py:16 exists to warn about. Deleting all three removes the tripwire and the round trips at
once, in a file already open for R10-4. Cost if wrong: three lines removed; behaviour-preserving
under the current session config.

Ruling R10-6: ELEVATING Minor 4 (cross-user tests assert no intermediate statuses). Same
reasoning I used to elevate the logged_in fixture assertions in Task 7, so consistency demands it
here: a failed alice signup would surface as KeyError: 'id' rather than a clear failure. These are
the two most load-bearing tests in the milestone; a confusing failure in them is the most expensive
kind. Soundness is not in question — the reviewer proved they cannot pass vacuously — this is
diagnosability only. Cost if wrong: four assertions.

Ruling R10-7: DEFERRING Minor 3 (stage/status typed str rather than the StrEnums). It would
narrow the OpenAPI schema to the legal values, which is better documentation, but M1's frontend
hand-writes its types in lib/api.ts rather than generating them from OpenAPI, so there is no
functional consequence this milestone. Revisit if type generation is ever introduced. Cost if
wrong: OpenAPI advertises free-form strings for two fields.

Ruling R10-8: DEFERRING Minor 5 (Db alias duplicated across two routers). Two copies is not yet
duplication worth an abstraction; the reviewer's own suggestion is that a third is the trigger to
hoist it into framemeld.db. Agreed. Cost if wrong: one more copy before it gets hoisted.

Ruling R10-9: leaving the one surviving

Ruling R11-1: commit the scaffold-generated AGENTS.md and CLAUDE.md. CLAUDE.md is an 11-byte

Ruling R11-2: carry the Next 16 facts into Tasks 12-14 dispatches — the version, the async-params
verification, Tailwind v4, and the instruction to consult node_modules/next/dist/docs/ for any API
they are unsure of. Three tasks are about to write React against this, and the alternative is each
one rediscovering the version gap independently. Cost if wrong: a few lines of dispatch context.

Ruling R11-3: DEFERRING Minor 1 (FastAPI 422 sends

Ruling R12-1: fix the accessibility finding. The error <p> has no role="alert" or aria-live, so a
screen-reader user who mistypes a password hears nothing change — the only signal is the button
silently re-enabling. This is the login form: a user who cannot perceive the error cannot get into
the product at all, which makes it the highest-stakes form in M1 for assistive tech. Plan-mandated;
the fix is one attribute. Cost if wrong: one attribute.

Ruling R12-2: fix finding 2 by improving the 409 MESSAGE, and explicitly REJECT the reviewer's
suggested auto-fallback (attempting login() when signup() returns 409 in the same submit). The
auto-fallback would turn the signup form into a second credential-testing path — enter a stranger's
email with a guessed password, and a 409 would trigger a login attempt. It grants no capability the
login form does not already offer, but it doubles the surface for password guessing, and rate
limiting is ALREADY a deferred M1 gap (no Redis until M2). Adding an unrate-limited second auth
path while the first one is also unrate-limited is the wrong direction. The message fix — "That
email is already registered. Try logging in instead." — resolves the user's actual confusion with
no new auth surface. Note a 409 can only arise on signup (login returns 401 or 200), so the shared
branch needs no mode check. Cost if wrong: a longer error string.

Ruling R13-1: fix all three Important. (A) gate the /login redirect on

Ruling R13-2: EXTENDING finding (B) to onLogout, which the reviewer did not flag but which is the
same defect class at higher stakes. logout() throwing means the session is NOT revoked server-side,
and the current code's router.replace never runs — so the user clicks "Log out", nothing happens,
and they may walk away from a shared machine believing they are signed out. Fixing it by
redirecting anyway would be worse: that pretends to log out while the cookie stays live. The honest
behaviour is to tell them it failed and keep them on the page. Cost if wrong: one try/catch.

Ruling R13-3: ELEVATING Minor 4 (no double-submit guard on the two create buttons). Unlike the
signup double-click — which the backend absorbs with IntegrityError -> 409 — both createMeld calls
here are legitimately valid, so a double-click creates TWO empty Melds and the user has to delete
one. The backend cannot dedupe this; only the UI can. I elevated the equivalent concern in Task 12,
so consistency demands it here. Cost if wrong: a

Ruling R13-4: FOLDING IN Minor 1 (rename input has no aria-label) — one attribute on an element
already being edited for ruling C. Consistent with R2-2's same-line reasoning.

Ruling R13-5: DEFERRING Minor 2 (focus not returned to the Rename trigger when the input
unmounts). Real for keyboard-only users — focus drops to <body> and they lose their place — but it
needs a ref and focus-after-unmount patterns are easy to get subtly wrong. Better done deliberately
with a11y testing than bolted onto a fix round. Cost if wrong: keyboard users lose focus position
on rename until it is addressed.

Ruling R13-6: DEFERRING Minor 3 (onBlur commitRename can race a same-card Delete click, issuing a
trailing PATCH against a just-deleted row). Reviewer assessed it benign — worst case a harmless
404 — and the fix would mean sequencing blur against click, which is fiddly for no user-visible
gain. Cost if wrong: a stray 404 in the network log.

Ruling R13-7: fix it by CLASSIFYING mutation errors rather than blanket-alerting, which is the
honest completion of fix 2 rather than a new feature. A blanket alert misreports two of the three
plausible failure modes: a 404 means the row is gone (this race, or another tab deleting it) and
the right response is to resync so the card disappears; a 401 means the session expired and the
right response is the login redirect, not "Couldn't rename this Meld". Only a genuine transient
failure warrants the alert. This also closes the reviewer's out-of-scope observation that a session
expiry mid-action shows a misleading message. Cost if wrong: a ten-line helper replacing two alert
calls.

Ruling R13-8: R13-6 is now SUPERSEDED. Recording that rather than quietly dropping it — the
deferral was defensible on the information I had, and it stopped being defensible the moment fix 2
changed the race's consequence. Worth noting as a pattern: deferring a Minor is a judgement about
its current impact, and a later fix in the same area can revoke it.

Ruling R14-0: do NOT add frontend test infrastructure (vitest + @testing-library/react + jsdom)
to make the autosave machine-verifiable. The plan has no frontend test harness by design, and
bolting four dev dependencies plus a config file onto the final implementation task is precisely
the unilateral scope expansion I have refused from implementers throughout this run. It is an M2
decision to be made deliberately, not slipped in at the end. Cost if wrong: the debounce rests on
code reading plus the user's own browser check rather than on a regression test — recorded, and to
be stated plainly in the final report rather than glossed.

Ruling R14-1: fix defect 1 with an

Ruling R14-2: fix defect 2 by only reporting "saved" when the acknowledged value is still the
latest. Requires a latestRef updated inside the effect — NOT mutated during render, which is
unsafe under concurrent React. Cost if wrong: status stays "saving" a moment longer in a race.

Ruling R14-3: EXTEND beforeunload to fire on "saving" as well as unsaved|error. This is the honest
completion of R14-2, not a separate feature: once "saved" is withheld during a race, the status in
that window is "saving", and leaving that unwarned would recreate the same silent data loss the fix
exists to prevent. A save genuinely in flight is also work that can be lost. Cost if wrong: the
browser warns during an in-flight request, which is accurate.

Ruling R14-4: also fix a THIRD defect I found while reasoning about R14-2 — when value returns to
equal savedRef (the user undoes an edit), the effect early-returns and leaves status stuck on
"unsaved". Safe direction, but it is a state lie AND it makes beforeunload nag for work that is not
pending. I told the implementer to keep that guard tightly scoped precisely so users do not learn
to dismiss it; a spurious nag undermines the one occasion it matters. Fixed with an updater-form
setStatus so

Ruling R14-5: fix BOTH before M1 can be called done. The milestone's acceptance criterion is "a
user types a story and finds the text intact on return"; B makes that false in ordinary use and A
makes the UI actively lie about it. Neither is scope creep — B is arguably a literal violation of
the criterion the plan was written to satisfy, and shipping either would mean reporting M1 complete
against a headline feature that silently loses work. Cost if wrong: roughly fifteen lines in one
hook.

Ruling R14-6: fix A with the saveTick else-branch, rejecting the inFlightRef alternative the
reviewer analysed. Relaxing the early return would fall through and fire a SECOND concurrent PUT
while the first is in flight, with no ordering guarantee — the server could land on the stale value.
saveTick fires strictly after the first request resolves, so saves stay serialized. Smaller and
safer. Accepted cost: a tick clears the pending timer and restarts the 800ms debounce.

Ruling R14-7: fix B with an unmount flush in a []-deps effect reading refs, fire-and-forget. A
fetch outlives the component on a client transition, and there is no UI left to report a failure
to. Rejected intercepting navigation via the router: Next exposes no clean hook for awaiting work
before a transition, and building one would be far more machinery than the problem warrants. Needs
an onSaveRef, since a []-deps effect would otherwise close over the first render's onSave. Cost if
wrong: one redundant idempotent PUT when a save was already in flight — same input_hash, no harm.

Ruling R14-8: this is the third consecutive round in which fixing one race exposed the next, and
that pattern is itself the finding. The instances are symptoms: a debounced autosave with
fire-and-forget requests and no serialization will keep generating ordering hazards, and patching
them one at a time is the "one more round will converge" trap. Fixing the STRUCTURE instead — route
every save, debounced or flushed, through a single promise chain so at most one request is in flight
and they apply in issue order. That eliminates the class rather than this instance, and it is FEWER
lines than tracking in-flight state and branching in the flush. Rejected the narrow fix (chain the
flush behind the current in-flight promise) despite it being smaller in diff terms, because it
leaves the invariant implicit and the next path added to this hook would have to rediscover it.
Cost if wrong: four lines of queue plumbing, and a queue that must not stall on a failed save —
handled by chaining on both settle paths.

Ruling R14-9: fix Residual 1 (the "saved" claim does not check the queue is empty, so a
type/type/revert sequence with a slow save can flash "Saved" while an older-valued PUT is still
queued). Self-correcting and narrow, and the reviewer confirmed the M1 acceptance path is NOT
exposed — a direct tab close keeps the hook mounted with status unsaved|saving and beforeunload
warns. Fixing anyway because it is a false "Saved", the class I have treated as Critical throughout
this task, and because the fix makes the status semantics SIMPLER to state rather than more complex:
"saved" comes to mean "the queue is empty and the text matches" instead of "the last thing I awaited
matched". Three lines. Cost if wrong: status lingers on "saving" a moment longer while later links
drain, which is accurate.

Ruling R14-10: fix Residual 3 (run captures

Ruling R14-11: DEFER Residual 2 (serialization now gates the flush behind the in-flight save, so a
tab closed immediately after a client transition may never dispatch it). Reviewer assessed the trade
as net-positive and I agree — it replaced an observed Critical reordering with a narrower
not-dispatched-in-time risk, and the correct mitigation is a request timeout or keepalive on the
flush, NOT a second concurrent path. That belongs with the api.ts fetch having no AbortSignal or
timeout at all, which is a shared concern for every caller. Both to the final review. Cost if
wrong: an unresponsive server can block the queue.

Ruling R14-12: record the cross-instance remount race ACCURATELY for M2, correcting the
implementer's framing. It reported the non-atomicity caveat as multi-tab; the reachable version is
same-tab: type, click "← My Melds" (flush enqueues), click straight back in — the new instance's
getMeld races the old instance's queued PUT, and if the GET wins the user sees their paragraph
missing and their next keystroke overwrites from a stale base. Pre-existing and genuinely outside a
per-instance queue's reach; needs an input_hash precondition on PUT or a shared queue. Plainly M2,
but it must not be filed under a narrower description than it deserves.

Ruling R14-13: accept the reviewer's recommendation that round 4 is the last on this file. The loop
converged rather than cycling: round 3 fixed the invariant structurally, round 4 fixed two residuals
found while CONFIRMING that invariant, and this round's re-review found nothing new and constructed
no fourth hazard from four attempts. Continuing would be looking for work. Cost if wrong: a residual
surfaces in the final whole-branch review, which is the designated net for exactly that.

Ruling FR-1: run ONE fix wave containing C1, I1, I3, I5, I6, the lint-and-format commit with an
explicit select and version pins, the six cheap fix-before-merge deferred items, and the
layout.tsx title. Defer the remaining Minors to M2 with the list recorded. Per the skill this is one
dispatch, not one per finding.

Ruling FR-2: accept the reviewer's ruff resolution in full — fix the four errors AND make the rule
set explicit and pinned AND restate the criterion. Fixing only the errors would re-arm the same trap
for M2, since the surprise was never the four errors but that "lint is clean" was a claim about an
unpinned implicit rule set. Also accept that DTZ001 must be silenced rather than fixed: that test
deliberately builds a naive datetime to prove canonical_hash rejects it, and passing tzinfo would
delete the test's point.

Ruling FR-3: adopt the M2 recommendations into the spec rather than leaving them to discovery —
the input_hash precondition on PUT /melds/{id}/story (it closes the cross-instance remount race,
makes autosave idempotent on retry, and M2's staleness engine needs the mechanism anyway), and the
CSRF position including the observation that SameSite ignores PORTS, so localhost:3000 ->
localhost:8000 is same-site and the dev setup never exercises the cross-site path at all. That last
one is a trap waiting for whoever first deploys web and api on different domains.

Ruling FR-4: adopt the process finding. Both cross-task defects have the same shape — a rule
established in one task's review did not propagate to its sibling (db.expire in the soft-delete test
but not the story tests; error classification in the dashboard but not the editor). Countermeasure:
when a review establishes a RULE rather than fixing an instance, append it to the plan's Global
Constraints in the same commit. Nine commits already amend the plan for plan errors; the mechanism
just never fired for review findings.

Ruling FR-5: record the I5 correction prominently rather than quietly. A reviewer's finding that
survives into a ledger becomes the project's memory, and this one was wrong about the mechanism. The
implementer was right to verify rather than accept it — which is the same standard I have been
holding reviewers to all run, applied back up the chain.

Ruling FR-6: apply the guard rather than parking it, despite the skill's "no second fix wave".
That prohibition exists to stop an unbounded review-fix-review cycle; this is a two-line correction
to a defect the wave itself introduced, precisely specified by the reviewer including the need to
hoist

Ruling FR-7: bundle the three stale-claim Minors with it, because they are the exact category the
wave was tasked with removing and shipping them would be self-defeating — test_models_meld.py:51's
docstring asserts the DISPROVEN I5 premise as fact; README says 72 tests when the suite is 74; and
README cites a "standalone simulation" as the debounce's evidence when that script was deleted after
use, so it points at evidence not in the repo.

Ruling FR-8: DEFER the design residual at useAutosave.ts:107-111 (reports "saved" while
pendingRef !== savedRef, i.e. while a request for that value is in flight). Inherent to the pending
semantics I asked for, and materially different from the two data-loss bugs: the in-flight request
carries the CURRENT text, so nothing is lost if it lands, and a failure flips to "error" and re-arms
the warning. Exposure is a tab closed inside one round trip. The tightening — resolve to "saving"
when pendingRef !== savedRef — is real and goes to M2 rather than a third pass at this hook.

Ruling FR-9: Q1 settled — KEEP the root pytest.ini. Reviewer's reasoning is better than my
instinct: the divergence class this milestone spent rounds closing is dangerous because it is
SILENT (two input_hash implementations, a constraint enforced at one layer and not another). This
duplication has exactly one failure mode and it is deafening — 59 collection errors on the first
async test, verified by moving the file aside. And consolidating would be worse: making the root
file authoritative requires deleting [tool.pytest.ini_options] from services/backend/pyproject.toml,
leaving the backend with NO pytest config whenever that directory is built or tested in isolation, a
Docker or CI context this repo will plausibly acquire. Taking the suggested

Ruling FR-10: I5 settled — the IMPLEMENTER was right and the whole-branch reviewer was wrong.
Settled two ways: a direct bind-processor probe showing Enum returns a plain str untouched when
validate_strings=False (so Meld(stage="nonsense") never raises LookupError at bind time, with or
without create_constraint), and end-to-end with create_constraint=False making BOTH enum tests go
red. The old test was never hollow. The new ORM-independent test still earns its place, because the
old one's accepted-exception tuple cannot say WHICH layer refused and would stay green if
enforcement silently migrated into Python. My ledger's earlier claim that the test could not fail is
superseded.

Ruling FR-11: preserve every ruling in the repository as docs/superpowers/m1-decisions.md before
deleting this workspace. The skill requires the full list reach the user and also requires the
workspace be deleted — taken literally that means 80-plus rulings pasted into one chat message,
which is not a form anyone can use or return to. Committing them makes them durable project history
answering exactly the questions a future contributor will ask ("why is there a root pytest.ini",
"why does canonical_hash refuse rather than coerce", "why does MeldOut deliberately omit story"),
and the final message summarises with a pointer. Cost if wrong: one extra document in docs/.
