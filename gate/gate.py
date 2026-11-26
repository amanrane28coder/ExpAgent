#!/usr/bin/env python3
"""ExpAgent gate: deterministic checks on a plan + flag state.

Release Gate style: pure checks, no network, no Flagsmith calls.
Reads plan JSON and flag-state JSON files, prints a verdict, exits:
    0  PASS  — plan is safe to apply (still needs a human to approve it)
    1  BLOCK — one or more checks failed; reasons printed
    2  ERROR — unreadable/missing file or malformed JSON

Usage:
    python3 gate/gate.py plan.json state.json

Flag-state JSON shape (see examples/state-example.json):
    {
      "flag": "checkout-headline",
      "environment": "development",
      "current_weights": {"control": 50, "variant_b": 50, "variant_c": 0},
      "kill_switch_on": true,
      "known_events": ["checkout_completed", "signup_completed", ...],
      "staging_only_flags": ["internal-debug-flag"]
    }

Checks (all deterministic, order is stable):
  1. variant weights sum to 100
  2. no variant at 0%
  3. metric event is in the known-events list
  4. no variant moves more than 50pp vs its current weight
  5. kill-switch flag is on
  6. a staging-only flag is not changed in production
"""

import json
import sys

MAX_ROLLOUT_JUMP_PP = 50


def check(plan, state):
    """Return a list of block reasons (empty = PASS)."""
    reasons = []

    variants = plan.get("variants")
    if not isinstance(variants, list) or not variants:
        return ["plan has no variants"]
    weights = []
    for v in variants:
        if not isinstance(v, dict) or "key" not in v or "weight" not in v:
            return ["plan variant is missing 'key' or 'weight'"]
        weights.append(v["weight"])
    if any(not isinstance(w, (int, float)) for w in weights):
        return ["variant weights must be numbers"]

    total = sum(weights)
    if total != 100:
        reasons.append("variant weights sum to %s, not 100" % total)
    if any(w == 0 for w in weights):
        reasons.append("a variant has 0% weight: %s"
                       % ", ".join(v["key"] for v in variants
                                   if v["weight"] == 0))

    event = (plan.get("metric") or {}).get("event")
    known = state.get("known_events", [])
    if event not in known:
        reasons.append("metric event '%s' is not a known event "
                       "(known: %s)" % (event, ", ".join(known) or "none"))

    current = state.get("current_weights", {})
    for v in variants:
        old = current.get(v["key"], 0)
        if abs(v["weight"] - old) > MAX_ROLLOUT_JUMP_PP:
            reasons.append("rollout jump for %s: %s%% -> %s%% (>%dpp)"
                           % (v["key"], old, v["weight"],
                              MAX_ROLLOUT_JUMP_PP))

    if not state.get("kill_switch_on", True):
        reasons.append("kill-switch flag is off")

    if (plan.get("environment") == "production"
            and plan.get("flag") in state.get("staging_only_flags", [])):
        reasons.append("flag '%s' is staging-only, cannot change it in "
                       "production" % plan.get("flag"))

    return reasons


def load_json(path):
    with open(path) as f:
        return json.load(f)


def main(argv):
    if len(argv) != 3:
        print("usage: gate.py <plan.json> <state.json>", file=sys.stderr)
        return 2
    try:
        plan = load_json(argv[1])
        state = load_json(argv[2])
    except FileNotFoundError as e:
        print("gate error: %s" % e, file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print("gate error: malformed JSON in %s: %s"
              % (e.doc if hasattr(e, "doc") else "input", e), file=sys.stderr)
        return 2
    if not isinstance(plan, dict) or not isinstance(state, dict):
        print("gate error: plan and state must be JSON objects",
              file=sys.stderr)
        return 2

    reasons = check(plan, state)
    if not reasons:
        print("PASS: plan '%s' (%s) is safe to apply"
              % (plan.get("name", "?"), plan.get("flag", "?")))
        return 0
    print("BLOCK: %d check(s) failed" % len(reasons))
    for r in reasons:
        print("  - %s" % r)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
