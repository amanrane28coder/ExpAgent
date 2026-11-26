#!/usr/bin/env python3
"""Tests for agent/status.py — real subprocess runs against a FAKE
`flagsmith` executable on PATH that records argv and returns a canned
experiment JSON shaped like the real one (experiment 663, verified live
2026-11-26: id, feature {id, name}, name, status "created", metrics
[{metric, metric_name}], experiment_rollout {enabled, rollout_percentage,
multivariate_feature_state_values}, started_at null).

The real CLI and the real Cloud project are never touched.
Set FAKE_FLAGSMITH_FAIL=1 to make every call exit 1.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "agent" / "status.py"

FAKE_SCRIPT = '''#!/usr/bin/env python3
"""Fake `flagsmith` for ExpAgent status tests: records argv, returns canned JSON."""
import json, os, sys

log_path = os.environ.get("FAKE_FLAGSMITH_LOG")
argv = sys.argv[1:]
if log_path:
    with open(log_path, "a") as f:
        f.write(json.dumps({"argv": argv}) + "\\n")
if os.environ.get("FAKE_FLAGSMITH_FAIL") == "1":
    print("fake flagsmith: simulated failure", file=sys.stderr)
    sys.exit(1)
# Canned experiment, shaped like the real experiment 663 (live 2026-11-26).
print(json.dumps({
    "id": 663,
    "feature": {"id": 305610, "name": "checkout-headline",
                "type": "MULTIVARIATE", "initial_value": "Buy now",
                "multivariate_options": [
                    {"id": 37289, "key": "variant_b",
                     "string_value": "Start free trial",
                     "default_percentage_allocation": 0.0}]},
    "name": "A/B test: checkout headline",
    "hypothesis": "The 'Start free trial' variant increases "
                  "signup_completed versus control.",
    "status": "created",
    "metrics": [{"id": 743, "metric": 648,
                 "metric_name": "signup_completed",
                 "aggregation": "occurrence", "direction": "up",
                 "expected_direction": "increase"}],
    "experiment_rollout": {
        "enabled": True, "rollout_percentage": 100.0,
        "feature_state_value": {"type": "string", "value": "control"},
        "multivariate_feature_state_values": [
            {"multivariate_feature_option": 37289,
             "percentage_allocation": 50.0}]},
    "started_at": None,
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


def run_status(tmp, fake_dir, args, extra_env=None):
    return subprocess.run([sys.executable, str(STATUS)] + args,
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

        # 1. real call: prints the summary from the canned experiment
        r = run_status(tmp, fake_dir, ["663"])
        check("status exits 0", r.returncode == 0, r.stderr)
        for want in ("663", "A/B test: checkout headline", "created",
                     "checkout-headline", "305610", "signup_completed",
                     "648", "100%", "variant_b", "37289", "50%",
                     "started_at"):
            check("summary names %s" % want, want in r.stdout, r.stdout)
        calls = read_calls(tmp)
        check("status calls flagsmith api once with the experiment path",
              len(calls) == 1
              and calls[0]["argv"][0] == "api"
              and "experiments/663/" in " ".join(calls[0]["argv"]),
              "calls: %s" % calls)

        # 2. --help: usage, no CLI call
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_status(tmp, fake_dir, ["--help"])
        check("--help exits 0", r.returncode == 0, r.stderr)
        check("--help prints usage", "usage:" in r.stdout, r.stdout)
        check("--help calls no flagsmith", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))

        # 3. --dry-run: prints the command, runs nothing
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_status(tmp, fake_dir, ["663", "--dry-run"])
        check("--dry-run exits 0", r.returncode == 0, r.stderr)
        check("--dry-run prints the api path",
              "experiments/663/" in r.stdout, r.stdout)
        check("--dry-run calls no flagsmith", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))

        # 4. failing CLI -> exit 2
        r = run_status(tmp, fake_dir, ["663"],
                       extra_env={"FAKE_FLAGSMITH_FAIL": "1"})
        check("failing CLI exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

        # 5. no id -> exit 2, usage on stderr
        r = run_status(tmp, fake_dir, [])
        check("missing id exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        check("missing id prints usage", "usage:" in r.stderr, r.stderr)

    print("test_status: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
