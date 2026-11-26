#!/usr/bin/env python3
"""Tests for gate/gate.py — real subprocess runs on temp files, no mocks.

Each case writes a plan + flag-state JSON to temp files, runs the gate, and
asserts the exit code (0 PASS / 1 BLOCK / 2 error) and that the output names
the reason.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "gate" / "gate.py"

BASE_STATE = {
    "flag": "checkout-headline",
    "environment": "development",
    "current_weights": {"control": 50, "variant_b": 50, "variant_c": 0},
    "kill_switch_on": True,
    "known_events": ["checkout_completed", "signup_completed",
                     "cta_clicked", "trial_started", "page_viewed"],
    "staging_only_flags": ["internal-debug-flag"],
}


def plan(weights, event="signup_completed", flag="checkout-headline",
         environment="development"):
    keys = ["control", "variant_b", "variant_c"]
    return {
        "flag": flag,
        "name": "A/B test: %s" % flag,
        "hypothesis": "test",
        "environment": environment,
        "variants": [{"key": keys[i], "value": "v%d" % i, "weight": w}
                     for i, w in enumerate(weights)],
        "metric": {"name": event, "event": event, "aggregation": "occurrence",
                   "direction": "up", "expected_direction": "increase"},
        "duration_days": 14,
    }


def state(**overrides):
    s = dict(BASE_STATE)
    s.update(overrides)
    return s


CASES = [
    # (name, plan, state, expected exit, expected output substring)
    ("clean 50/50 passes",
     plan([50, 50]), state(), 0, "PASS"),
    ("weights sum 99 blocks",
     plan([49, 50]), state(), 1, "sum to 99"),
    ("weights sum 101 blocks",
     plan([51, 50]), state(), 1, "sum to 101"),
    ("0% variant blocks",
     plan([100, 0]), state(), 1, "0% weight"),
    ("unknown metric blocks",
     plan([50, 50], event="frobnicate"), state(), 1, "not a known event"),
    ("rollout jump >50pp blocks",
     plan([10, 90]),
     state(current_weights={"control": 90, "variant_b": 10}), 1,
     "rollout jump"),
    ("rollout jump exactly 50pp passes",
     plan([50, 50]),
     state(current_weights={"control": 100, "variant_b": 0}), 0, "PASS"),
    ("kill-switch off blocks",
     plan([50, 50]), state(kill_switch_on=False), 1, "kill-switch"),
    ("prod + staging-only flag blocks",
     plan([50, 50], flag="internal-debug-flag", environment="production"),
     state(), 1, "staging-only"),
    ("prod + normal flag passes",
     plan([50, 50], environment="production"), state(), 0, "PASS"),
    ("3-variant clean passes",
     plan([40, 30, 30]), state(), 0, "PASS"),
]


def run_gate(plan_obj, state_obj):
    with tempfile.TemporaryDirectory() as tmp:
        p, s = Path(tmp) / "plan.json", Path(tmp) / "state.json"
        p.write_text(json.dumps(plan_obj))
        s.write_text(json.dumps(state_obj))
        return subprocess.run([sys.executable, str(GATE), str(p), str(s)],
                              capture_output=True, text=True, cwd=ROOT)


def main():
    passed = failed = 0
    for name, p, s, want_exit, want_text in CASES:
        result = run_gate(p, s)
        out = result.stdout + result.stderr
        if result.returncode == want_exit and want_text in out:
            print("PASS %s (exit %d)" % (name, result.returncode))
            passed += 1
        else:
            print("FAIL %s: exit %d (want %d), output: %s"
                  % (name, result.returncode, want_exit, out.strip()))
            failed += 1

    # error cases: malformed JSON -> 2, missing file -> 2
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "bad.json"
        bad.write_text("{not json")
        good = Path(tmp) / "good.json"
        good.write_text(json.dumps(state()))
        r = subprocess.run([sys.executable, str(GATE), str(bad), str(good)],
                           capture_output=True, text=True, cwd=ROOT)
        if r.returncode == 2:
            print("PASS malformed plan JSON -> exit 2")
            passed += 1
        else:
            print("FAIL malformed plan JSON: exit %d" % r.returncode)
            failed += 1
        r = subprocess.run([sys.executable, str(GATE), str(tmp) + "/nope.json",
                            str(good)], capture_output=True, text=True,
                           cwd=ROOT)
        if r.returncode == 2:
            print("PASS missing plan file -> exit 2")
            passed += 1
        else:
            print("FAIL missing plan file: exit %d" % r.returncode)
            failed += 1

    print("test_gate: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
