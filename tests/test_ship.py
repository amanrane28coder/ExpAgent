#!/usr/bin/env python3
"""Tests for agent/ship.py — real subprocess runs against a FAKE
`flagsmith` executable on PATH that records argv + stdin and returns
canned JSON shaped like the real API (experiment 685, verified live
2026-11-26; featurestates list shaped like the real one).

The fake dispatches on argv:
  - "experiments/<id>/" -> the experiment (status from FAKE_EXP_STATUS,
    default "running"; "completed" for the ship path).
  - "featurestates/" (no --method) -> list with the feature state for
    checkout-headline (id 9001, variant_b at 50) plus one other flag.
  - "--method PUT" -> records stdin, echoes it back as the updated doc.

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
SHIP = ROOT / "agent" / "ship.py"

FAKE_SCRIPT = '''#!/usr/bin/env python3
"""Fake `flagsmith` for ExpAgent ship tests."""
import json, os, sys

log_path = os.environ.get("FAKE_FLAGSMITH_LOG")
argv = sys.argv[1:]
stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
if log_path:
    with open(log_path, "a") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\\n")
if os.environ.get("FAKE_FLAGSMITH_FAIL") == "1":
    print("fake flagsmith: simulated failure", file=sys.stderr)
    sys.exit(1)
if "--method" in argv and "PUT" in argv:
    # Echo the PUT body back as the updated feature state.
    print(stdin)
    sys.exit(0)
joined = " ".join(argv)
if "experiments/" in joined:
    status = os.environ.get("FAKE_EXP_STATUS", "running")
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
        "status": status,
        "metrics": [{"id": 769, "metric": 674,
                     "metric_name": "signup_completed",
                     "aggregation": "occurrence", "direction": "up",
                     "expected_direction": "increase"}],
        "rollout_decision": None, "rollout_variant": None,
        "started_at": "2026-11-26T15:56:13.789522Z",
        "ended_at": None,
    }))
    sys.exit(0)
if "featurestates/" in joined:
    print(json.dumps([
        # Real `featurestates/` list shape (verified live 2026-11-26):
        # "feature" is a bare int id, NOT a nested dict. The dict shape
        # below (state 9002) is the environment-document shape — ship.py
        # must read both.
        {"id": 9001, "feature": 305610,
         "enabled": True,
         "feature_state_value": {"type": "string", "value": "Buy now"},
         "multivariate_feature_state_values": [
             {"multivariate_feature_option": 37289,
              "percentage_allocation": 50.0}]},
        {"id": 9002, "feature": {"id": 301244, "name": "checkout-cta"},
         "enabled": True,
         "feature_state_value": {"type": "string", "value": "Buy now"},
         "multivariate_feature_state_values": []},
    ]))
    sys.exit(0)
print("fake flagsmith: unexpected call %r" % (argv,), file=sys.stderr)
sys.exit(1)
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


def read_audit(tmp):
    log = Path(tmp) / "audit.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def run_ship(tmp, fake_dir, args, extra_env=None):
    return subprocess.run([sys.executable, str(SHIP)] + args,
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
        audit = str(Path(tmp) / "audit.log")

        # 1. running experiment: REFUSED, nothing written, no PUT
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "variant_b", "--approve",
                      "--audit-log", audit])
        check("running experiment refused (exit 1)",
              r.returncode == 1, "exit %d: %s" % (r.returncode, r.stdout))
        check("refusal names the status",
              "REFUSED" in r.stdout and "running" in r.stdout, r.stdout)
        calls = read_calls(tmp)
        check("refusal makes only the experiment GET",
              len(calls) == 1 and "experiments/685/" in " ".join(
                  calls[0]["argv"]), "calls: %s" % calls)
        check("refusal writes no audit", read_audit(tmp) == [])

        # 2. completed, dry-run: prints the PUT, sends nothing
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "variant_b",
                      "--audit-log", audit],
                     extra_env={"FAKE_EXP_STATUS": "completed"})
        check("dry-run exits 0", r.returncode == 0, r.stderr)
        check("dry-run prints winner 100",
              "'variant_b': 100" in r.stdout, r.stdout)
        check("dry-run prints before allocations",
              "'variant_b': 50" in r.stdout, r.stdout)
        check("dry-run sends no PUT",
              all("PUT" not in c["argv"] for c in read_calls(tmp)),
              "calls: %s" % read_calls(tmp))
        check("dry-run writes no audit", read_audit(tmp) == [])

        # 3. completed + approve: PUTs winner 100, audits
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "variant_b", "--approve",
                      "--audit-log", audit, "--who", "aman"],
                     extra_env={"FAKE_EXP_STATUS": "completed"})
        check("approve exits 0", r.returncode == 0, r.stderr)
        check("approve prints SHIPPED",
              "SHIPPED" in r.stdout, r.stdout)
        check("approve prints rollback with before allocations",
              "rollback" in r.stdout and "'variant_b': 50" in r.stdout,
              r.stdout)
        puts = [c for c in read_calls(tmp) if "PUT" in c["argv"]]
        check("approve sends exactly one PUT", len(puts) == 1,
              "calls: %s" % read_calls(tmp))
        if puts:
            body = json.loads(puts[0]["stdin"])
            allocs = body["multivariate_feature_state_values"]
            check("PUT sets variant_b to 100",
                  allocs == [{"multivariate_feature_option": 37289,
                              "percentage_allocation": 100}],
                  "body: %s" % puts[0]["stdin"])
            check("PUT targets featurestates/9001/",
                  "featurestates/9001/" in " ".join(puts[0]["argv"]),
                  "argv: %s" % puts[0]["argv"])
            check("PUT keeps the base value",
                  body["feature_state_value"] == {"type": "string",
                                                  "value": "Buy now"},
                  "body: %s" % puts[0]["stdin"])
            check("PUT keeps the feature id as the bare int",
                  body["feature"] == 305610,
                  "body: %s" % puts[0]["stdin"])
        audit_rows = read_audit(tmp)
        check("approve appends one audit line", len(audit_rows) == 1,
              "audit: %s" % audit_rows)
        if audit_rows:
            check("audit line is a ship by aman",
                  audit_rows[0]["action"] == "ship"
                  and audit_rows[0]["who"] == "aman"
                  and audit_rows[0]["winner"] == "variant_b",
                  "audit: %s" % audit_rows)

        # 4. control winner: all options to 0
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "control", "--approve",
                      "--audit-log", audit],
                     extra_env={"FAKE_EXP_STATUS": "completed"})
        check("control winner exits 0", r.returncode == 0, r.stderr)
        puts = [c for c in read_calls(tmp) if "PUT" in c["argv"]]
        if puts:
            allocs = json.loads(
                puts[0]["stdin"])["multivariate_feature_state_values"]
            check("control win sets variant_b to 0",
                  allocs == [{"multivariate_feature_option": 37289,
                              "percentage_allocation": 0}],
                  "body: %s" % puts[0]["stdin"])

        # 5. unknown winner: refused, no PUT
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        before_n = len(read_audit(tmp))
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "bogus", "--approve",
                      "--audit-log", audit],
                     extra_env={"FAKE_EXP_STATUS": "completed"})
        check("unknown winner refused (exit 1)",
              r.returncode == 1, "exit %d" % r.returncode)
        check("unknown winner sends no PUT",
              all("PUT" not in c["argv"] for c in read_calls(tmp)),
              "calls: %s" % read_calls(tmp))
        check("unknown winner writes no audit",
              len(read_audit(tmp)) == before_n)

        # 6. missing --winner -> exit 2
        r = run_ship(tmp, fake_dir, ["685", "--approve"])
        check("missing --winner exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

        # 7. --help: usage, no CLI call
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_ship(tmp, fake_dir, ["--help"])
        check("--help exits 0", r.returncode == 0, r.stderr)
        check("--help prints usage", "usage:" in r.stdout, r.stdout)
        check("--help calls no flagsmith", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))

        # 8. failing CLI -> exit 2
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "variant_b", "--approve",
                      "--audit-log", audit],
                     extra_env={"FAKE_FLAGSMITH_FAIL": "1"})
        check("failing CLI exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

    print("test_ship: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
