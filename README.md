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
./tests/run_tests.sh          # full suite (plan + gate + apply)
```

End-to-end on one prompt (dry-run — nothing touches Flagsmith):

```sh
python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks" > /tmp/plan.json
python3 gate/gate.py /tmp/plan.json examples/state-example.json
python3 agent/apply.py /tmp/plan.json            # dry-run: prints commands
python3 agent/apply.py /tmp/plan.json --approve  # real apply (needs flagsmith login)
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

## Layout

```
agent/plan.py    prompt -> plan JSON (stdlib only)
agent/apply.py   plan -> flagsmith api calls, --approve only
gate/gate.py     deterministic checks, exit 0/1/2
demo-app/        static checkout page + the SDK snippet plan.py generates
examples/        3 prompts + a flag-state fixture for the gate
tests/           test_plan.py, test_gate.py, test_apply.py, run_tests.sh
audit.log        appended by apply.py runs (created on first run)
```

## Tests

Release Gate style: real subprocess runs, no mocks for plan/gate. The applier
tests run against a fake `flagsmith` executable on PATH that records argv/stdin
and returns canned ids — the real CLI and the real Cloud project are never
touched. Adversarial cases covered: weights sum 99 → BLOCK, 0% variant →
BLOCK, unknown metric → BLOCK, rollout jump >50pp → BLOCK, kill-switch off →
BLOCK, prod + staging-only flag → BLOCK, clean plan → PASS, failing CLI →
apply exits 2 with a `failed` audit line.

## Day 2 still to do

- Approve/apply/log loop end-to-end against the real Cloud project (Aman's
  terminal: `flagsmith login` already done; CLI v2.0.0 on the Mac).
- Wire the demo app to a real experiment and show the variant flip live.
- Rollback command verified against a real created experiment.
- README timing claim (prompt-to-approved-plan ~1 min vs manual ~10 min)
  measured live for the video.
- Sync this staging tree to the Mac master copy (`~/expagent/`) and push to
  github.com/amanrane28coder/ExpAgent — tunnel was down on Day 1, do not push
  from here.
