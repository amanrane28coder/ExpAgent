#!/usr/bin/env python3
"""Tests for agent/results.py — real subprocess runs against a FAKE
`flagsmith` executable on PATH that records argv and returns a canned
experiment JSON shaped like the real one (experiment 685, verified live
2026-11-26: id, feature {id, name, initial_value, multivariate_options},
name, status "running", metrics, rollout_decision/variant null).

The real CLI and the real Cloud project are never touched.
Set FAKE_FLAGSMITH_FAIL=1 to make every call exit 1.
Set FAKE_EXP_COMPLETED=1 for a completed experiment with a rollout
decision instead of the running one.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "agent" / "results.py"

FAKE_SCRIPT = '''#!/usr/bin/env python3
"""Fake `flagsmith` for ExpAgent results tests."""
import json, os, sys

log_path = os.environ.get("FAKE_FLAGSMITH_LOG")
argv = sys.argv[1:]
if log_path:
    with open(log_path, "a") as f:
        f.write(json.dumps({"argv": argv}) + "\\n")
if os.environ.get("FAKE_FLAGSMITH_FAIL") == "1":
    print("fake flagsmith: simulated failure", file=sys.stderr)
    sys.exit(1)
completed = os.environ.get("FAKE_EXP_COMPLETED") == "1"
print(json.dumps({
    "id": 685,
    "feature": {"id": 305610, "name": "checkout-headline",
                "type": "MULTIVARIATE", "initial_value": "Buy now",
                "multivariate_options": [
                    {"id": 37289, "key": "variant_b",
                     "string_value": "Start free trial",
                     "default_percentage_allocation": 0.0}]},
    "name": "A/B test: checkout headline",
    "hypothesis": "The 'Start free trial' variant increases "
                  "signup_completed versus control.",
    "status": "completed" if completed else "running",
    "metrics": [{"id": 769, "metric": 674,
                 "metric_name": "signup_completed",
                 "aggregation": "occurrence", "direction": "up",
                 "expected_direction": "increase"}],
    "rollout_decision": "variant_b" if completed else None,
    "rollout_variant": "variant_b" if completed else None,
    "started_at": "2026-11-26T15:56:13.789522Z",
    "ended_at": "2026-11-26T16:00:00.000000Z" if completed else None,
}))
'''


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
    env["FAKE_FLAGSMITH_LOG"] = str(Path(tmp) / "calls.jsonl")
    if extra:
        env.update(extra)
    return env


def read_calls(tmp):
    log = Path(tmp) / "calls.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def run_results(tmp, fake_dir, args, extra_env=None):
    return subprocess.run([sys.executable, str(RESULTS)] + args,
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

        # 1. running experiment: status + honest "no computed results"
        r = run_results(tmp, fake_dir, ["685"])
        check("results exits 0", r.returncode == 0, r.stderr)
        for want in ("685", "A/B test: checkout headline", "running",
                     "checkout-headline", "305610", "signup_completed",
                     "674", "not in this API response",
                     "Refresh results", "95% chance to be best",
                     "decision: none yet"):
            check("running output names %s" % want, want in r.stdout,
                  r.stdout)
        check("running output invents no rollout decision",
              "rollout decision" not in r.stdout, r.stdout)
        calls = read_calls(tmp)
        check("results calls flagsmith api once with the experiment path",
              len(calls) == 1
              and calls[0]["argv"][0] == "api"
              and "experiments/685/" in " ".join(calls[0]["argv"]),
              "calls: %s" % calls)

        # 2. completed experiment: prints the rollout decision + ship hint
        r = run_results(tmp, fake_dir, ["685"],
                        extra_env={"FAKE_EXP_COMPLETED": "1"})
        check("completed exits 0", r.returncode == 0, r.stderr)
        for want in ("completed", "rollout decision: variant_b",
                     "ship with agent/ship.py"):
            check("completed output names %s" % want, want in r.stdout,
                  r.stdout)

        # 3. --help: usage, no CLI call
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_results(tmp, fake_dir, ["--help"])
        check("--help exits 0", r.returncode == 0, r.stderr)
        check("--help prints usage", "usage:" in r.stdout, r.stdout)
        check("--help calls no flagsmith", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))

        # 4. failing CLI -> exit 2
        r = run_results(tmp, fake_dir, ["685"],
                        extra_env={"FAKE_FLAGSMITH_FAIL": "1"})
        check("failing CLI exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

        # 5. no id -> exit 2, usage on stderr
        r = run_results(tmp, fake_dir, [])
        check("missing id exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        check("missing id prints usage", "usage:" in r.stderr, r.stderr)

    print("test_results: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
