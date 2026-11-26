#!/usr/bin/env python3
"""Tests for agent/plan.py — real subprocess runs, no mocks.

Each case runs `python3 agent/plan.py` on a prompt and checks the plan JSON
on stdout (exit code, flag, weights, metric event, duration). Error cases
check exit code 2.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "agent" / "plan.py"
EXAMPLES = ROOT / "examples"

CASES = [
    # (prompt file, expected flag, expected weights, expected event, days)
    ("prompt-1-ab-checkout-headline.txt", "checkout-headline", [50, 50],
     "signup_completed", 14),
    ("prompt-2-7030-pricing-page.txt", "pricing-page", [70, 30],
     "checkout_completed", 7),
    ("prompt-3-multivariate-onboarding.txt", "onboarding-flow", [40, 30, 30],
     "trial_started", 21),
]

ERROR_CASES = [
    ("", "empty prompt"),
    ("hello world", "no subject/metric"),
    ("A/B test checkout headline, 50/50, measure frobnicate, 2 weeks",
     "unknown metric"),
]


def run_plan(prompt):
    return subprocess.run(
        [sys.executable, str(PLAN), prompt],
        capture_output=True, text=True, cwd=ROOT)


def check_case(name, prompt, flag, weights, event, days):
    result = run_plan(prompt)
    if result.returncode != 0:
        return "exit %d, stderr: %s" % (result.returncode,
                                        result.stderr.strip())
    try:
        plan = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return "stdout is not JSON: %s" % e
    if plan.get("flag") != flag:
        return "flag %r != %r" % (plan.get("flag"), flag)
    got_weights = [v["weight"] for v in plan.get("variants", [])]
    if got_weights != weights:
        return "weights %r != %r" % (got_weights, weights)
    if plan.get("metric", {}).get("event") != event:
        return "event %r != %r" % (plan.get("metric", {}).get("event"), event)
    if plan.get("duration_days") != days:
        return "duration %r != %r" % (plan.get("duration_days"), days)
    if "sdk_snippet" not in plan or flag not in plan["sdk_snippet"]:
        return "sdk_snippet missing or does not name the flag"
    return None


def main():
    passed = failed = 0
    for fname, flag, weights, event, days in CASES:
        prompt = (EXAMPLES / fname).read_text().strip()
        err = check_case(fname, prompt, flag, weights, event, days)
        if err:
            print("FAIL %s: %s" % (fname, err))
            failed += 1
        else:
            print("PASS %s -> flag=%s weights=%s event=%s %dd"
                  % (fname, flag, weights, event, days))
            passed += 1
    for prompt, label in ERROR_CASES:
        result = run_plan(prompt)
        if result.returncode == 2:
            print("PASS error case (%s): exit 2" % label)
            passed += 1
        else:
            print("FAIL error case (%s): exit %d, expected 2"
                  % (label, result.returncode))
            failed += 1
    print("test_plan: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
