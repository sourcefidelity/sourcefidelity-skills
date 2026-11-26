# Reliability and single-review optimization plan (R57–R59)

**Status:** active sub-plan of `docs/PLAN.md` section 1.
**Triggers:** the 23 Nov TV reruns — a high-reasoning run failed at step 87 with HTTP 400
`Content Exists Risk` and could not restart; the max-reasoning run completed at 19m 32s / 24.4M prompt
tokens / $0.1948, with judgments in the same band as the earlier hand-written run.

A row is done only when its acceptance check passes and the command/result is recorded in
`docs/STATE.md` or `CHANGELOG.md`.

**Transport vs content (24 Nov):** the live batch run failed at turn 415 with `TRANSPORT` and resumed
after the VPN was disabled. That is a network-path failure, not content moderation. R57 must keep the
classes separate: `TRANSPORT` recovers when the path is fixed; `CONTENT_BLOCKED` does not recover by
retrying the transcript; a refusal is a model finish. Add a proxy/VPN health note to the error and do
not conflate the three.

**R57 note:** reference implementations of the error classification, refusal mapping and request
projection, with five passing tests and an adapter patch, live in the workspace `dsh-fixes/` directory
(README, `src/errors.mjs`, `test/run.mjs`, `patches/0001-content-blocked-and-refusal.patch`). They are
not applied to the packaged app. R57c/R57e need the agent loop and remain blocked.

**Reversion (24 Nov):** R58a is reverted; the direct review is the default again. R58b and R58d remain as the opt-in experiment; R58c, R58e and R59a–c stay. R60 sets the re-adoption bar. The plan and evidence are in [single-review-reversion.md](single-review-reversion.md).

**Benchmark (26 Nov):** stored direct review `twf-20261123` scores **19/20** verdicts but fails the
required C1/C2 B findings; bounded `twf-bounded-20261124b` scores **15/20** and misses C4/C16. R60’s
bar is not met, so `--bounded` stays opt-in.

**Progress (24 Nov):** R58a, R58c, R58e, R59a–c done. R58e: `ingest_open.py --rebuild`
staged the six fetched TV texts, marked their works confirmed and rebuilt the task; the
copy then emitted 16 packets (one per occurrence with a window, max 3,999 bytes, 53.6 KB
total) instead of 4. The four remaining occurrences are the unavailable works (R3 wrong
book, R10 unreachable, McChesney absent) and carry recorded limitations. R58b still needs
the 16 responses and a rendered review to meet its acceptance; R58 remains open until that
end-to-end run meets the limits.

## R57 — Turn-failure containment (external: DSH harness)

The installed DSH is the packaged Desktop app; sources live inside `app.asar` and no checkout is present
on this machine. These rows are specified against the installed adapter modules and are delivered as a
patch plus a fixture-driven test harness (`ELECTRON_RUN_AS_NODE`). Applying the patch needs a DSH source
checkout or an authorized rebuild; the skill-side rows below must not depend on it.

| ID | Work | Status | Reasoning | Acceptance |
|---|---|---|---|---|
| R57 | Containment parent | Blocked on a DSH source checkout | Max | A provider content rejection or a model refusal never bricks a session and is never reported as a protocol error. |
| R57a | Refusal stop reason | In progress | Max | Map `refusal` (and `pause_turn`) in `@deepseek-ai/dsh-llm-deepseek` `stopReason()` to a typed refusal finish. Fixture with `stop_reason: refusal` ends the turn with a refusal and a notice; no `MALFORMED_RESPONSE`. |
| R57b | `CONTENT_BLOCKED` classification | In progress | Max | In `providerError()`, HTTP 400 `INVALID_REQUEST` whose message contains `Content Exists Risk` maps to `CONTENT_BLOCKED`, keeps the request id, is excluded from the retry set, and carries actionable guidance ("this transcript was rejected; retrying it cannot succeed"). Adapter unit test. |
| R57c | Repeated-block recovery | Blocked on a DSH source checkout | Max | After two identical `CONTENT_BLOCKED` turns, ordinary retry is suppressed and the error offers resume from the work root in a new session (or a fork at the last accepted event). Two-turn blocked fixture; no third retry. |
| R57d | Request projection | In progress | Max | Tool results older than K steps or above N bytes are digested (short head/tail plus a truncation marker) before the request, preserving tool-call/result pairing. A TV-sized transcript loses at least 40% of prompt tokens; stored-fixture judgments unchanged; pair validation green. |
| R57e | Recovery retry with projection | Blocked on a DSH source checkout | Max | On `CONTENT_BLOCKED`, retry once with every tool result digested. Synthetic poisoned transcript succeeds on the retry and records projection; a second failure returns the actionable `CONTENT_BLOCKED`. |

## R58 — Single-review bounded default

Make the per-claim bounded path the default for `/sourcefidelity-review`; the long-context path becomes
fallback-only when no packets or evidence exist.

| ID | Work | Status | Reasoning | Acceptance |
|---|---|---|---|---|
| R58 | Bounded default parent | Reverted | Max | On the TV paper the review completes against the R59a gold set with total prompt < 10M, wall < 12 min, max single request < 100k tokens, and no full source text in the main context. |
| R58a | Bounded routing in `SKILL.md` | Reverted | Max | After preparation, when packet-ready claims exist: `one_paper --bounded`, then `--collect-bounded`, then `--finalize`. A fresh transcript shows those calls and no `_work/open/*.txt` reads in the main context. |
| R58b | Per-claim worker orchestration | Experimental (opt-in) | Max | One worker per claim under the 4 KB `BOUNDED_WORKER.md` contract, concurrency 4–8, responses under `bounded-responses/`; the main agent receives no source text. TV run: 20 responses, 20 recorded judgments, main context ≤ 20 packets + 20 responses. |
| R58c | Binding reading gate | Done | Max | A gate finding must be adopted into findings or rebutted with evidence in the record; a silent downgrade to a note fails validation. Fix the two known detector faults: C1 patchwriting (real overlap passed) and C11 reported viewpoint (wrongly declined). The validator rejects the current TV gate record's silent drops (C2 B, C15 A, C18 A/B). |
| R58d | Source-reading and tool budget | Experimental (opt-in) | High | Replace ad-hoc grep/tail over full texts with window/packet commands and compact locator facts; cap tool calls and request size. The TV run has no full-source read and stays inside the R58 limits. |
| R58e | Evidence-window coverage before emission | Done | Max | Build or recover a window for every occurrence, or record a reason (`source_unavailable`, `no_relevant_full_text`); the stored TV root emits packets for only 4 of 20 occurrences because 16 have no window. Acceptance: `--bounded` emits one packet per occurrence with a window, and every other occurrence carries a recorded reason. |

## R59 — Optimization measurement

| ID | Work | Status | Reasoning | Acceptance |
|---|---|---|---|---|
| R60 | Re-adoption bar for the bounded single review | Planned | High | The bounded path returns to the default only after a fresh TV run shows at least 18/20 gold verdicts, a complete bibliography/HL section, measured wall time at or below 16 minutes and cost at or below $0.10, no full source read in the main context, and two consecutive runs within one verdict. Until then `--bounded` stays opt-in. See [single-review-reversion.md](single-review-reversion.md). |
| R59 | Measurement parent | In progress | High | The benchmark scores every build on the TV paper and reports verdict agreement, wall time, tokens and max request. |
| R59a | Adjudicated gold set | Done | High | `_benchmarks/twf-gold.json` freezes the 20 TV verdicts and the disputed items (C1, C2, C5, C9, C11, C15, C16, C17, C18, U2) with evidence pointers; it validates and is referenced by R59b. |
| R59b | Review benchmark runner | Done | High | `scripts/benchmark_review.py --findings <path> --gold <path>` reports agreement, required findings, uncited passages, counts and run metrics; exits nonzero below the R58 thresholds. Runs offline on the stored TV findings. |
| R59c | Token metrics plumbing | Done | High | `optimization_metrics.json` records input/cached/output tokens from `review_metrics.json` instead of `null`, with no paper or source text. A real run reports non-null tokens; fixture test. |

## Rationale

- **Prevention beats retry.** The long, source-heavy request is both the cost driver and the moderation
  exposure. The bounded path keeps source text out of the main transcript, so it fixes both at once.
- **Gate adoption is the missing link.** The gate already proposed the correct C2/C15/C18 findings on the
  TV run; the review downgraded them to notes. Binding adoption is cheaper than a better detector.
- **No optimization claim without a benchmark.** The stored runs show wall time and tokens rising while
  verdicts stay in the same band; only a fixed gold set and per-run metrics can settle whether a change
  helps.
