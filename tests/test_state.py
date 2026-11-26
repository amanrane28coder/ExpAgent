#!/usr/bin/env python3
"""Tests for agent/state.py — real subprocess runs against a FAKE
`flagsmith` executable on PATH that returns canned JSON.

The fake answers:
  - `flagsmith feature get <flag> --json` -> {"id": 301244, variants [...]}
  - `flagsmith api .../featurestates/?feature=301244` -> [feature state]
    (variant_b 50, variant_c 0, control implied 50, value "Buy now")
  - `flagsmith api .../experiment-metrics/` (GET) -> [metric defs]
Set FAKE_FLAGSMITH_FAIL=1 to make every call exit 1, or
FAKE_FLAGSMITH_MISSING=1 to make `feature get` report the flag as
not found.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "agent" / "state.py"
GATE = ROOT / "gate" / "gate.py"

FAKE_SCRIPT = '''#!/usr/bin/env python3
"""Fake `flagsmith` for state tests: records argv, returns canned JSON."""
import json, os, sys

argv = sys.argv[1:]
if os.environ.get("FAKE_FLAGSMITH_FAIL") == "1":
    print("fake flagsmith: simulated failure", file=sys.stderr)
    sys.exit(1)
if argv[:2] == ["feature", "get"]:
    if os.environ.get("FAKE_FLAGSMITH_MISSING") == "1":
        print("fake flagsmith: feature %s not found" % argv[2],
              file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"id": 301244, "name": argv[2],
                      "type": "multivariate",
                      "default_value": "Buy now", "enabled": False,
                      "variants": [
                          {"id": 36378, "key": "variant_b",
                           "value": "Start free trial", "weight": 0},
                          {"id": 36379, "key": "variant_c",
                           "value": "Get started today", "weight": 0}]}))
elif "featurestates" in " ".join(argv):
    # The real list endpoint paginates: {"count", "results": [...]}.
    # The fake must return that shape — a bare list hid the unwrap
    # bug until the first live run (2026-11-27).
    print(json.dumps({"count": 1, "results": [
        {"id": 1, "feature": 301244, "enabled": True,
         "feature_state_value": "Buy now",
         "multivariate_feature_state_values": [
             {"multivariate_feature_option": 36378,
              "percentage_allocation": 50},
             {"multivariate_feature_option": 36379,
              "percentage_allocation": 0}]}]}))
elif "experiment-metrics" in " ".join(argv):
    print(json.dumps({"count": 2, "results": [
        {"id": 344, "name": "signup_completed",
         "definition": {"version": 1, "event": "signup_completed"}},
        {"id": 345, "name": "checkout_completed",
         "definition": {"version": 1, "event": "checkout_completed"}}]}))
else:
    print("{}")
'''

PLAN_50_50 = {
    "flag": "checkout-headline",
    "name": "A/B test: checkout headline",
    "hypothesis": "test",
    "environment": "development",
    "variants": [{"key": "control", "value": "Buy now", "weight": 50},
                 {"key": "variant_b", "value": "Start free trial",
                  "weight": 50}],
    "metric": {"name": "signup_completed", "event": "signup_completed",
               "aggregation": "occurrence", "direction": "up",
               "expected_direction": "increase"},
    "duration_days": 14,
}


def make_fake(tmp):
    fake_dir = Path(tmp) / "fakebin"
    fake_dir.mkdir()
    fake = fake_dir / "flagsmith"
    fake.write_text(FAKE_SCRIPT)
    fake.chmod(0o755)
    return fake_dir


def env_with_fake(tmp, fake_dir, extra=None):
    env = dict(os.environ)
    env["PATH"] = str(fake_dir) + os.pathsep + env["PATH"]
    if extra:
        env.update(extra)
    return env


def run_state(tmp, fake_dir, extra_args, extra_env=None):
    return subprocess.run(
        [sys.executable, str(STATE)] + extra_args,
        capture_output=True, text=True, cwd=ROOT,
        env=env_with_fake(tmp, fake_dir, extra_env))


def main():
    passed = failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            print("PASS %s" % name)
            passed += 1
        else:
            print("FAIL %s: %s" % (name, detail))
            failed += 1

    with tempfile.TemporaryDirectory() as tmp:
        fake_dir = make_fake(tmp)

        # 1. live state: weights from the feature state, events from
        #    the metric definitions, control implied as the rest.
        out_path = str(Path(tmp) / "state-live.json")
        r = run_state(tmp, fake_dir,
                      ["checkout-headline", "--out", out_path])
        check("state exits 0", r.returncode == 0, r.stderr)
        state = json.loads(Path(out_path).read_text())
        check("state weights are the live 50/50 (control implied)",
              state["current_weights"]
              == {"control": 50, "variant_b": 50, "variant_c": 0},
              "state: %s" % state)
        check("state known events come from the metric definitions",
              state["known_events"]
              == ["checkout_completed", "signup_completed"],
              "state: %s" % state)
        check("state carries the feature id + value + enabled",
              state["feature_id"] == 301244
              and state["current_value"] == "Buy now"
              and state["enabled"] is True,
              "state: %s" % state)
        check("state defaults the slow metadata (kill-switch on)",
              state["kill_switch_on"] is True
              and state["staging_only_flags"] == [],
              "state: %s" % state)

        # 2. the written state gates the 50/50 plan PASS through the
        #    real gate — the flow state.py exists for, end to end.
        plan_path = Path(tmp) / "plan.json"
        plan_path.write_text(json.dumps(PLAN_50_50))
        r = subprocess.run([sys.executable, str(GATE), str(plan_path),
                            out_path],
                           capture_output=True, text=True, cwd=ROOT)
        check("live state gates the 50/50 plan PASS", r.returncode == 0,
              r.stdout + r.stderr)

        # 3. a --state base merges its slow metadata + known events;
        #    live weights still win over the base's stale ones.
        base_path = Path(tmp) / "base.json"
        base_path.write_text(json.dumps({
            "flag": "checkout-headline",
            "environment": "development",
            "current_weights": {"control": 90, "variant_b": 10,
                                "variant_c": 0},
            "kill_switch_on": False,
            "known_events": ["trial_started"],
            "staging_only_flags": ["internal-debug-flag"]}))
        out2 = str(Path(tmp) / "state2.json")
        r = run_state(tmp, fake_dir,
                      ["checkout-headline", "--state", str(base_path),
                       "--out", out2])
        check("base merge exits 0", r.returncode == 0, r.stderr)
        state2 = json.loads(Path(out2).read_text())
        check("base merge: live weights win over stale base weights",
              state2["current_weights"]
              == {"control": 50, "variant_b": 50, "variant_c": 0},
              "state: %s" % state2)
        check("base merge: kill-switch + staging list come from the base",
              state2["kill_switch_on"] is False
              and state2["staging_only_flags"] == ["internal-debug-flag"],
              "state: %s" % state2)
        check("base merge: known events are the union of live + base",
              state2["known_events"]
              == ["checkout_completed", "signup_completed",
                  "trial_started"],
              "state: %s" % state2)

        # 4. missing flag -> exit 2, clean one-line error, no traceback.
        r = run_state(tmp, fake_dir, ["checkout-headline"],
                      extra_env={"FAKE_FLAGSMITH_MISSING": "1"})
        check("missing flag exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        check("missing flag prints a one-line error, no traceback",
              "state error:" in r.stderr and "Traceback" not in r.stderr,
              r.stderr)

        # 5. failing CLI -> exit 2, clean one-line error.
        r = run_state(tmp, fake_dir, ["checkout-headline"],
                      extra_env={"FAKE_FLAGSMITH_FAIL": "1"})
        check("failing CLI exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        check("failing CLI prints a one-line error, no traceback",
              "state error:" in r.stderr and "Traceback" not in r.stderr,
              r.stderr)

    print("test_state: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
