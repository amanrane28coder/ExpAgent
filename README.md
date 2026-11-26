# ExpAgent — prompt-to-experiment agent (Toggle Wars, Track 2)

One prompt in, one checked experiment plan out — applied only after a human
approves, with an audit log and a rollback command. Release Gate's pattern,
applied to Flagsmith experiments.

**Track 2: AI Agents and Feature Flags — $1,000.** Judging: Flagsmith CLI/API
or MCP; saves real time; a human approves changes; clear output.

## What it does

```
prompt ──► plan (agent/plan.py) ──► gate (gate/gate.py) ──► human approves
                                                              │
                                                              ▼
                                              apply (agent/apply.py --approve)
                                              metric + experiment via CLI v2
                                              + audit log line + rollback cmd
```

The stock Flagsmith setup prompt creates a flag + metric + experiment. It does
not validate the plan, show a diff, guard a bad split, or leave an audit trail
or rollback. That is what ExpAgent adds: the **checked plan + approval + log**,
not the creation call.

## One-command run

```sh
./tests/run_tests.sh          # full suite (plan + gate + apply + probe + status)
```

End-to-end on one prompt (dry-run — nothing touches Flagsmith):

```sh
python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks" > /tmp/plan.json
python3 gate/gate.py /tmp/plan.json examples/state-example.json
python3 agent/apply.py /tmp/plan.json            # dry-run: prints commands
python3 agent/apply.py /tmp/plan.json --approve  # real apply (needs flagsmith login)
python3 gate/probe.py                            # 5 adversarial + 1 clean plan
python3 agent/status.py 663                      # read one experiment (needs login)
```

## Example prompts and expected verdicts

| # | Prompt | Plan | Gate (vs `examples/state-example.json`) |
|---|--------|------|------------------------------------------|
| 1 | `A/B test checkout headline, 50/50, measure sign-ups, 2 weeks` | flag `checkout-headline`, 50/50, event `signup_completed`, 14 days | PASS |
| 2 | `A/B test pricing page, 70/30, measure checkouts, 1 week` | flag `pricing-page`, 70/30, event `checkout_completed`, 7 days | PASS |
| 3 | `Multivariate test onboarding flow, 40/30/30, measure trial starts, 3 weeks` | flag `onboarding-flow`, 40/30/30, event `trial_started`, 21 days | PASS |

The three prompts live in `examples/`; the gate verdicts above are what
`tests/test_plan.py` and `tests/test_gate.py` assert.

## The gate (deterministic, exit 0 PASS / 1 BLOCK / 2 error)

`python3 gate/gate.py plan.json state.json` — pure function, no network.
BLOCKs on:

- variant weights not summing to 100 (e.g. 99)
- any variant at 0%
- metric event not in the flag state's known-events list
- any variant moving more than 50pp vs its current weight
- kill-switch flag off
- a staging-only flag changed in production

The gate recommends only — it approves nothing. A human approves.

## The probe runner (adversarial plans through the real gate)

`python3 gate/probe.py` builds 5 adversarial plans + 1 clean plan and runs
each through `gate/gate.py` as a subprocess, printing one verdict line each
plus a summary. Exit 0 iff every verdict matches expectation, else 1.

| Case | Expected |
|---|---|
| `clean-50-50` | PASS |
| `weights-sum-99` | BLOCK — weights sum to 99, not 100 |
| `variant-0pct` | BLOCK — a variant has 0% weight |
| `unknown-metric` | BLOCK — metric event is not a known event |
| `rollout-jump-10-to-100` | BLOCK — rollout jump for variant_b: 10% -> 100% (>50pp) |
| `kill-switch-off` | BLOCK — kill-switch flag is off |

Base state is `examples/state-example.json`. The jump and kill-switch cases
use a focused state override, because those checks cannot fire against the
base state (its weights are 50/50 and its kill-switch is on).

Gate rules are FROZEN — the probe exercises them, it changes none. One
crash the probe caught and fixed (rule unchanged, reason text unchanged):
the 0%-variant reason string was `"a variant has 0% weight: %s" % ...`,
and the bare `%` made %-formatting raise `ValueError` — any 0% plan
crashed the gate with a traceback instead of a BLOCK verdict. It is now
`0%%`, so the reason prints. The old `test_gate.py` 0% case had passed
only because the traceback echoed the reason string.

## The applier (CLI v2 + REST API — NOT MCP)

Per the 2026-11-18 correction: the Flagsmith MCP server cannot start
experiments, so the write path is `flagsmith api ...`. Payload shapes are the
proven-live ones from pre-work step 4 (free tier, experiment 397 created,
not started):

- metric: `POST api/v1/environments/<env-key>/experiment-metrics/` —
  `{"name", "aggregation": "occurrence", "direction": "up",
    "definition": {"version": 1, "event": "<event>"}}`
- experiment: `POST api/v1/environments/<env-key>/experiments/` — feature id,
  `metrics: [{"metric": <id>, "expected_direction": "increase"}]`,
  `experiment_rollout` enabled at 100% with control as the base value and
  `multivariate_feature_state_values` carrying each variant's
  `percentage_allocation`.

Dry-run (default) prints the exact commands and payloads and runs nothing.
`--approve` runs them: metric first, then the experiment with the real metric
id. The experiment is created, **never started** — starting locks the flag.
Every run appends one JSON line to `audit.log` (who, what, verdict,
timestamp, before/after weights) and prints a rollback command
(`--method DELETE` on the created experiment).

The applier resolves the feature id and variant option ids with
`flagsmith feature get <flag> --json` (or take `--feature-id` /
`--option-ids`). It never touches experiment 397 (pre-work, status created).

The generated SDK snippet (`sdk_snippet` in every plan, saved as
`demo-app/snippet.js`) carries the real toggle-wars Development
environment key — a client-side SDK key, public by design, not a secret
and not a Master API key.

## The status command (read one experiment)

`python3 agent/status.py <experiment-id>` reads one experiment through
CLI v2 and prints a short summary: id, name, status, feature, metrics
with names, rollout (enabled, percentage, variant allocations), started_at.

```sh
python3 agent/status.py 663
# experiment 663: A/B test: checkout headline
#   status: created (started_at: —)
#   feature: checkout-headline (id 305610)
#   metrics: signup_completed (id 648, occurrence, up, expect increase)
#   rollout: enabled, 100% — control: 50% (rest), variant_b (option 37289): 50%
```

Read-only — it never creates, starts, or deletes anything.
`--dry-run` prints the command and runs nothing; `--help` prints usage.
Exit 0 on success, 2 on error.

## The live demo (demo-app/)

`demo-app/index.html` is the checkout page the experiment drives. It reads
flag `checkout-headline` and tracks event `signup_completed` — the flag and
metric ExpAgent manages. Serve it and open it in two tabs with different
identities to see two variants:

```sh
cd demo-app && python3 -m http.server 8000
# http://localhost:8000/?identity=alice
# http://localhost:8000/?identity=bob
```

The page shows the identity in use, the flag value, and which variant it
is (`Buy now` = control, `Start free trial` = variant_b); clicking the
button sends `signup_completed`. `?identity=` defaults to `demo-user`.

The live flip needs a RUNNING experiment: the flag returns the control
value for every identity until the experiment is started (starting locks
the flag — see "Still to do" below). Do not claim the flip works until the
experiment is started and two identities show two variants.

## Layout

```
agent/plan.py    prompt -> plan JSON (stdlib only)
agent/apply.py   plan -> flagsmith api calls, --approve only
agent/status.py  experiment id -> short human-readable summary (read-only)
gate/gate.py     deterministic checks, exit 0/1/2
gate/probe.py    5 adversarial + 1 clean plan through the real gate
demo-app/        static checkout page + the SDK snippet plan.py generates
examples/        3 prompts + a flag-state fixture for the gate
tests/           test_plan.py, test_gate.py, test_apply.py,
                 test_probe.py, test_status.py, run_tests.sh
audit.log        appended by apply.py runs (created on first run)
```

## Tests

Release Gate style: real subprocess runs, no mocks for plan/gate. The applier
tests run against a fake `flagsmith` executable on PATH that records argv/stdin
and returns canned ids — the real CLI and the real Cloud project are never
touched. Adversarial cases covered: weights sum 99 → BLOCK, 0% variant →
BLOCK, unknown metric → BLOCK, rollout jump >50pp → BLOCK, kill-switch off →
BLOCK, prod + staging-only flag → BLOCK, clean plan → PASS, failing CLI →
apply exits 2 with a `failed` audit line. The probe runner
(`gate/probe.py`, covered by `tests/test_probe.py`) runs all six cases
through the real gate in one command. The status tests
(`tests/test_status.py`) run `agent/status.py` against a fake `flagsmith`
returning a canned experiment shaped like the real experiment 663, and
assert `--help` / `--dry-run` make no CLI call.

## Timing (measured)

Agent path for prompt 1 — plan + gate + apply dry-run — measured with the
shell `time` builtin, 3 runs, from the repo root:

| Run | Real time |
|---|---|
| 1 | 0.125s |
| 2 | 0.118s |
| 3 | 0.118s |

**Median: 0.118s.** That is compute only: parsing the prompt, running the
gate, and printing the dry-run apply commands. It does not include the
human reading and approving the plan, or the real Flagsmith API calls on a
live apply. The honest video claim is: prompt in, checked plan out in
~0.1s of compute; a human reads it and approves in under a minute.

The manual comparison is the brief's ~10-minute estimate for the same four
objects by hand — dashboard clicks for flag + variant + metric + experiment
+ SDK snippet — **not** a fresh measurement. Do not present it as measured.

## Video shot list (~2 min) — full script in VIDEO-SCRIPT.md

1. Cold open: the flip — two tabs (`?identity=alice` "Buy now",
   `?identity=carol` "Start free trial").
2. Problem: hand setup = flag + variants + metric + split + SDK code,
   ~10 min of dashboard clicks (brief's estimate), one wrong weight breaks
   the test.
3. Prompt: one sentence in, plan out — flag, variants, metric, split,
   code. The agent proposes; the human approves.
4. Gate: the probe BLOCKs bad plans (weights 99, 0% variant, unknown
   metric) with the reason shown — and it caught a real bug in itself
   (0% crash, fixed).
5. Human approves; agent applies; `status.py` shows experiment 685
   running; audit log shows the decision; Flagsmith Cloud dashboard shows
   the 50/50 split.
6. Live flip explained; a click sends `signup_completed`.
7. Rollback: one command deletes the experiment (proven on 663, audit log
   shows it). Close: "One prompt, checked, approved, applied. Repo on
   screen."

## Status — what is done, what needs Aman's terminal

Done (all verified in this repo, no real Flagsmith calls in tests):

- Day 1 core: prompt parser, deterministic gate, CLI applier, demo app.
- Day 2 loop, live 2026-11-26: plan 1 gated PASS, applied in Aman's
  terminal (metric 648, experiment 663, created, not started), audit line
  appended, rollback DELETE proven (663 verified gone; list shows only
  pre-work experiment 397). Orphan metric 649 exists from a duplicate
  re-apply (harmless, same as the pre-work 344/345 pair).
- Probe runner + status command + live demo wiring + measured timing
  (this round).

Still to do — Aman's terminal only (the CLI keychain is unavailable over
SSH, so these cannot run from here):

1. Re-apply plan 1 (experiment 663 was rolled back, so this creates a NEW
   experiment), then START it — starting locks the flag, and the live flip
   needs it running. Then check the status and the live flip.

```
export PATH=$HOME/.local/bin:$PATH
cd ~/expagent
python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks" > /tmp/plan1.json
python3 gate/gate.py /tmp/plan1.json examples/state-example.json
python3 agent/apply.py /tmp/plan1.json --approve --state examples/state-example.json --who aman
```

Take the new experiment id from the apply output, then start it and check
it (the start call follows the same `--input` POST pattern as the proven
metric/experiment creates; it has not been run yet):

```
echo '{}' | flagsmith api api/v1/environments/MHSyYjVPRHPkaYLkdRHaCS/experiments/<new-id>/start/ --input -
python3 agent/status.py <new-id>
```

Then the live flip check: serve `demo-app/` (`python3 -m http.server`
from that dir) and open `http://localhost:8000/?identity=alice` and
`http://localhost:8000/?identity=bob` — one tab should show "Buy now",
the other "Start free trial". If both show the same copy, the rollout has
not taken effect yet — wait a minute and reload.

2. Record the video (shot list above) and submit Track 2 before
   Sun Nov 29, 20:00 IST.
