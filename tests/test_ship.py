#!/usr/bin/env python3
"""Tests for agent/ship.py — real subprocess runs against a FAKE
`flagsmith` executable on PATH that records argv + stdin and returns
canned JSON shaped like the real API (experiment 685, verified live
2026-11-26; update-flag read/write shaped like Flagsmith's own docs,
verified live 2026-11-26 when the legacy featurestates PUT 400'd on
this v2-versioned environment).

The fake dispatches on argv:
  - "experiments/<id>/" -> the experiment (status from FAKE_EXP_STATUS,
    default "running"; "completed" for the ship path; rollout decision
    from FAKE_ROLLOUT_DECISION / FAKE_ROLLOUT_VARIANT, default
    "variant"/37289 when completed, None/None when running).
  - "__future__/.../features/<id>/" (no --method) -> the flag state in
    the environment (variant_b at weight 50).
  - "--method PATCH" -> records stdin, echoes the new weights back as
    the updated flag state.

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
if "--method" in argv and "PATCH" in argv:
    # Echo the new weights back as the updated flag state, the way the
    # real update-flag endpoint responds (complete flag state).
    body = json.loads(stdin) if stdin else {}
    variants = (body.get("environment_default") or {}).get("variants") or []
    print(json.dumps({
        "environment_default": {
            "enabled": True,
            "value": {"type": "string", "value": "Buy now"},
            "variants": variants},
        "segment_overrides": []}))
    sys.exit(0)
joined = " ".join(argv)
if "experiments/" in joined:
    status = os.environ.get("FAKE_EXP_STATUS", "running")
    # Real shape (verified live 2026-11-26 on experiment 699): a
    # completed experiment always records a rollout decision; a
    # running one records none. Override with FAKE_ROLLOUT_DECISION /
    # FAKE_ROLLOUT_VARIANT (empty string forces None).
    if "FAKE_ROLLOUT_DECISION" in os.environ:
        decision = os.environ["FAKE_ROLLOUT_DECISION"] or None
        variant = os.environ.get("FAKE_ROLLOUT_VARIANT")
        variant = int(variant) if variant else None
    else:
        decision = "variant" if status == "completed" else None
        variant = 37289 if decision == "variant" else None
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
        "rollout_decision": decision, "rollout_variant": variant,
        "started_at": "2026-11-26T15:56:13.789522Z",
        "ended_at": None,
    }))
    sys.exit(0)
if "__future__" in joined and "/features/" in joined:
    # Update-flag GET: the flag's state in the environment, variant_b
    # at weight 50 (matches the real shape in Flagsmith's docs).
    print(json.dumps({
        "environment_default": {
            "enabled": True,
            "value": {"type": "string", "value": "Buy now"},
            "variants": [{"id": 37289, "weight": 50.0}]},
        "segment_overrides": []}))
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
        check("dry-run sends no PATCH",
              all("PATCH" not in c["argv"] for c in read_calls(tmp)),
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
        puts = [c for c in read_calls(tmp) if "PATCH" in c["argv"]]
        check("approve sends exactly one PATCH", len(puts) == 1,
              "calls: %s" % read_calls(tmp))
        if puts:
            body = json.loads(puts[0]["stdin"])
            check("PATCH sets variant_b to weight 100",
                  body == {"environment_default": {"variants": [
                      {"id": 37289, "weight": 100}]}},
                  "body: %s" % puts[0]["stdin"])
            check("PATCH targets the update-flag endpoint",
                  "__future__/environments/" in " ".join(puts[0]["argv"])
                  and "/features/305610/" in " ".join(puts[0]["argv"]),
                  "argv: %s" % puts[0]["argv"])
        check("approve prints after allocations from the response",
              "after: {'variant_b': 100}" in r.stdout, r.stdout)
        audit_rows = read_audit(tmp)
        check("approve appends one audit line", len(audit_rows) == 1,
              "audit: %s" % audit_rows)
        if audit_rows:
            check("audit line is a ship by aman",
                  audit_rows[0]["action"] == "ship"
                  and audit_rows[0]["who"] == "aman"
                  and audit_rows[0]["winner"] == "variant_b"
                  and audit_rows[0]["via"] == "update-flag (__future__)",
                  "audit: %s" % audit_rows)

        # 4. control winner: all options to 0 (decision must be
        # "control" or the mismatch check refuses it)
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_ship(tmp, fake_dir,
                     ["685", "--winner", "control", "--approve",
                      "--audit-log", audit],
                     extra_env={"FAKE_EXP_STATUS": "completed",
                                "FAKE_ROLLOUT_DECISION": "control"})
        check("control winner exits 0", r.returncode == 0, r.stderr)
        puts = [c for c in read_calls(tmp) if "PATCH" in c["argv"]]
        if puts:
            variants = json.loads(
                puts[0]["stdin"])["environment_default"]["variants"]
            check("control win sets variant_b weight to 0",
                  variants == [{"id": 37289, "weight": 0}],
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
        check("unknown winner sends no PATCH",
              all("PATCH" not in c["argv"] for c in read_calls(tmp)),
              "calls: %s" % read_calls(tmp))
        check("unknown winner writes no audit",
              len(read_audit(tmp)) == before_n)

        # 5b. rollout-decision mismatch: refused, no PUT, no audit
        mismatch_cases = [
            ("variant decision, control winner",
             {"FAKE_ROLLOUT_DECISION": "variant",
              "FAKE_ROLLOUT_VARIANT": "37289"}, "control"),
            ("control decision, variant winner",
             {"FAKE_ROLLOUT_DECISION": "control"}, "variant_b"),
            ("disable decision, any winner",
             {"FAKE_ROLLOUT_DECISION": "disable"}, "variant_b"),
            ("variant decision, wrong option id",
             {"FAKE_ROLLOUT_DECISION": "variant",
              "FAKE_ROLLOUT_VARIANT": "99999"}, "variant_b"),
            ("completed, no recorded decision",
             {"FAKE_ROLLOUT_DECISION": ""}, "variant_b"),
            ("unknown decision value",
             {"FAKE_ROLLOUT_DECISION": "sideways"}, "variant_b"),
        ]
        for label, extra, winner in mismatch_cases:
            Path(tmp, "calls.jsonl").unlink(missing_ok=True)
            before_n = len(read_audit(tmp))
            env = {"FAKE_EXP_STATUS": "completed"}
            env.update(extra)
            r = run_ship(tmp, fake_dir,
                         ["685", "--winner", winner, "--approve",
                          "--audit-log", audit],
                         extra_env=env)
            check("mismatch refused: %s (exit 1)" % label,
                  r.returncode == 1, "exit %d: %s"
                  % (r.returncode, r.stdout))
            check("mismatch sends no PATCH: %s" % label,
                  all("PATCH" not in c["argv"] for c in read_calls(tmp)),
                  "calls: %s" % read_calls(tmp))
            check("mismatch writes no audit: %s" % label,
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
