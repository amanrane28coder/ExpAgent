#!/usr/bin/env python3
"""ExpAgent probe runner: adversarial plans through the REAL gate.

Builds 5 adversarial plans + 1 clean plan and runs each through
gate/gate.py as a subprocess, printing one verdict line each + a summary.
Exit 0 iff every verdict matches expectation, else 1.

Base flag state is examples/state-example.json. Two cases need a focused
state override, because their checks cannot fire against the base state:
  - rollout jump: base current weights are 50/50, so no plan can move a
    variant more than 50pp; the override puts variant_b at 10% so the plan
    can jump it to 100%.
  - kill-switch off: the base state has the kill-switch on.
Gate rules are FROZEN — this file only exercises them, it changes none.

Usage:
    python3 gate/probe.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "gate" / "gate.py"
STATE_FILE = ROOT / "examples" / "state-example.json"


def plan(weights, event="signup_completed", flag="checkout-headline",
         environment="development"):
    keys = ["control", "variant_b", "variant_c"]
    return {
        "flag": flag,
        "name": "A/B test: %s" % flag,
        "hypothesis": "probe plan",
        "environment": environment,
        "variants": [{"key": keys[i], "value": "v%d" % i, "weight": w}
                     for i, w in enumerate(weights)],
        "metric": {"name": event, "event": event, "aggregation": "occurrence",
                   "direction": "up", "expected_direction": "increase"},
        "duration_days": 14,
    }


# (name, plan, state overrides, expected exit, expected output substring)
CASES = [
    ("clean-50-50",
     plan([50, 50]), None, 0, "PASS"),
    ("weights-sum-99",
     plan([49, 50]), None, 1, "sum to 99"),
    ("variant-0pct",
     plan([100, 0]), None, 1, "0% weight"),
    ("unknown-metric",
     plan([50, 50], event="frobnicate"), None, 1, "not a known event"),
    ("rollout-jump-10-to-100",
     plan([0, 100]),
     {"current_weights": {"control": 90, "variant_b": 10, "variant_c": 0}},
     1, "rollout jump"),
    ("kill-switch-off",
     plan([50, 50]), {"kill_switch_on": False}, 1, "kill-switch"),
]


def run_gate(plan_obj, state_obj, tmp):
    p, s = Path(tmp) / "plan.json", Path(tmp) / "state.json"
    p.write_text(json.dumps(plan_obj))
    s.write_text(json.dumps(state_obj))
    return subprocess.run([sys.executable, str(GATE), str(p), str(s)],
                          capture_output=True, text=True, cwd=ROOT)


def main():
    base_state = json.loads(STATE_FILE.read_text())
    passed = 0
    mismatches = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, plan_obj, overrides, want_exit, want_text in CASES:
            state_obj = dict(base_state)
            if overrides:
                state_obj.update(overrides)
            result = run_gate(plan_obj, state_obj, tmp)
            out = result.stdout + result.stderr
            reasons = [ln.strip()[2:] for ln in out.splitlines()
                       if ln.startswith("  - ")]
            verdict = "PASS" if result.returncode == 0 else "BLOCK"
            # Print every reason the gate gave, not just the first: the
            # point of the probe is to show the gate's work on camera.
            detail = "; ".join(reasons) if reasons else (
                "safe to apply" if result.returncode == 0 else out.strip())
            ok = (result.returncode == want_exit and want_text in out)
            if ok:
                passed += 1
                print("%s %s — %s" % (verdict, name, detail))
            else:
                mismatches.append(name)
                print("MISMATCH %s: exit %d (want %d), output: %s"
                      % (name, result.returncode, want_exit, out.strip()))
    print("probe: %d/%d as expected" % (passed, len(CASES)))
    if mismatches:
        print("probe: mismatched cases: %s" % ", ".join(mismatches))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
