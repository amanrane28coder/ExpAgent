#!/usr/bin/env python3
"""Tests for agent/create_flag.py — real subprocess runs against a FAKE
`flagsmith` executable on PATH that records argv and keeps flag state in
a JSON file (FAKE_FLAG_STATE), so `feature get` reflects what `feature
create` / `feature variant add` did — like the real CLI.

The fake dispatches on argv:
  - "feature get <flag> --json" -> the state file if it names that flag,
    else exit 1 (flag does not exist).
  - "feature create <flag> --value <val> --enabled" -> writes the state
    file (id 999001, initial_value <val>, no variants) and prints TEXT,
    not JSON — the command under test must never parse this output.
  - "feature variant add <flag> --value <val> --key <key> --weight 0" ->
    appends the variant to the state file and prints text.

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
CREATE_FLAG = ROOT / "agent" / "create_flag.py"

FAKE_SCRIPT = '''#!/usr/bin/env python3
"""Fake `flagsmith` for ExpAgent create_flag tests."""
import json, os, sys

log_path = os.environ.get("FAKE_FLAGSMITH_LOG")
state_path = os.environ.get("FAKE_FLAG_STATE")
argv = sys.argv[1:]
if log_path:
    with open(log_path, "a") as f:
        f.write(json.dumps({"argv": argv}) + "\\n")
if os.environ.get("FAKE_FLAGSMITH_FAIL") == "1":
    print("fake flagsmith: simulated failure", file=sys.stderr)
    sys.exit(1)

def opt(name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None

def load_state():
    if state_path and os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)
    return None

def save_state(state):
    with open(state_path, "w") as f:
        json.dump(state, f)

if argv[:2] == ["feature", "get"]:
    state = load_state()
    if state is not None and state.get("name") == argv[2]:
        print(json.dumps(state))
        sys.exit(0)
    print("fake flagsmith: feature %s not found" % argv[2],
          file=sys.stderr)
    sys.exit(1)
if argv[:2] == ["feature", "create"]:
    save_state({"id": 999001, "name": argv[2], "type": "MULTIVARIATE",
                "initial_value": opt("--value"),
                "multivariate_options": []})
    print("created flag %s" % argv[2])
    sys.exit(0)
if argv[:3] == ["feature", "variant", "add"]:
    state = load_state()
    if state is None:
        print("fake flagsmith: feature %s not found" % argv[3],
              file=sys.stderr)
        sys.exit(1)
    state["multivariate_options"].append({
        "id": 900000 + len(state["multivariate_options"]) + 1,
        "key": opt("--key"), "string_value": opt("--value"),
        "default_percentage_allocation": 0.0})
    save_state(state)
    print("added variant %s" % opt("--key"))
    sys.exit(0)
print("fake flagsmith: unexpected call %r" % (argv,), file=sys.stderr)
sys.exit(1)
'''

PLAN = {
    "flag": "checkout-subhead",
    "name": "A/B test: checkout subhead",
    "hypothesis": "The 'New version' variant increases "
                  "signup_completed versus control.",
    "environment": "development",
    "variants": [
        {"key": "control", "value": "Current version", "weight": 50},
        {"key": "variant_b", "value": "New version", "weight": 50},
    ],
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
    env["FAKE_FLAGSMITH_LOG"] = str(Path(tmp) / "calls.jsonl")
    env["FAKE_FLAG_STATE"] = str(Path(tmp) / "flag-state.json")
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


def run_create(tmp, fake_dir, args, extra_env=None):
    return subprocess.run([sys.executable, str(CREATE_FLAG)] + args,
                          capture_output=True, text=True, cwd=ROOT,
                          env=env_with_fake(tmp, fake_dir, extra_env))


def seed_state(tmp, with_variant=True):
    state = {"id": 999001, "name": "checkout-subhead",
             "type": "MULTIVARIATE", "initial_value": "Current version",
             "multivariate_options": []}
    if with_variant:
        state["multivariate_options"].append(
            {"id": 900001, "key": "variant_b",
             "string_value": "New version",
             "default_percentage_allocation": 0.0})
    Path(tmp, "flag-state.json").write_text(json.dumps(state))


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
        plan_path = Path(tmp) / "plan.json"
        plan_path.write_text(json.dumps(PLAN))
        audit = str(Path(tmp) / "audit.log")

        # 1. dry-run: prints both commands, runs nothing
        r = run_create(tmp, fake_dir,
                       [str(plan_path), "--audit-log", audit])
        check("dry-run exits 0", r.returncode == 0, r.stderr)
        check("dry-run prints feature create with the control value",
              "feature create checkout-subhead" in r.stdout
              and "Current version" in r.stdout, r.stdout)
        check("dry-run prints variant add with key + weight 0",
              "feature variant add checkout-subhead" in r.stdout
              and "variant_b" in r.stdout and "--weight 0" in r.stdout,
              r.stdout)
        check("dry-run makes no CLI call", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))
        check("dry-run appends a dry-run audit line",
              len(read_audit(tmp)) == 1
              and read_audit(tmp)[0]["verdict"] == "dry-run")

        # 2. approve, flag absent: create + variant add + verify
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        Path(tmp, "audit.log").unlink(missing_ok=True)
        r = run_create(tmp, fake_dir,
                       [str(plan_path), "--approve", "--audit-log", audit,
                        "--who", "aman"])
        check("approve exits 0", r.returncode == 0, r.stderr)
        check("approve prints FLAG READY with the id",
              "FLAG READY" in r.stdout and "999001" in r.stdout, r.stdout)
        calls = read_calls(tmp)
        creates = [c for c in calls if c["argv"][:2] == ["feature", "create"]]
        adds = [c for c in calls
                if c["argv"][:3] == ["feature", "variant", "add"]]
        check("approve creates the flag once", len(creates) == 1,
              "calls: %s" % calls)
        if creates:
            check("create uses the control value + --enabled",
                  "--value" in creates[0]["argv"]
                  and creates[0]["argv"][creates[0]["argv"].index(
                      "--value") + 1] == "Current version"
                  and "--enabled" in creates[0]["argv"],
                  "argv: %s" % creates[0]["argv"])
        check("approve adds the variant once", len(adds) == 1,
              "calls: %s" % calls)
        if adds:
            check("variant add has key + value + weight 0",
                  adds[0]["argv"][adds[0]["argv"].index("--key") + 1]
                  == "variant_b"
                  and adds[0]["argv"][adds[0]["argv"].index("--value") + 1]
                  == "New version"
                  and adds[0]["argv"][adds[0]["argv"].index("--weight") + 1]
                  == "0",
                  "argv: %s" % adds[0]["argv"])
        audit_rows = read_audit(tmp)
        check("approve appends one approved audit line",
              len(audit_rows) == 1
              and audit_rows[0]["action"] == "create_flag"
              and audit_rows[0]["verdict"] == "approved"
              and audit_rows[0]["feature_id"] == 999001
              and audit_rows[0]["option_ids"] == {"variant_b": 900001},
              "audit: %s" % audit_rows)

        # 3. approve, flag exists with variant: reuses, adds nothing
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        Path(tmp, "audit.log").unlink(missing_ok=True)
        seed_state(tmp, with_variant=True)
        r = run_create(tmp, fake_dir,
                       [str(plan_path), "--approve", "--audit-log", audit])
        check("existing flag exits 0", r.returncode == 0, r.stderr)
        check("existing flag prints already exists",
              "already exists" in r.stdout and "999001" in r.stdout,
              r.stdout)
        calls = read_calls(tmp)
        check("existing flag makes no create or variant-add call",
              all(c["argv"][:2] != ["feature", "create"]
                  and c["argv"][:3] != ["feature", "variant", "add"]
                  for c in calls),
              "calls: %s" % calls)
        audit_rows = read_audit(tmp)
        check("existing flag audits verdict exists",
              len(audit_rows) == 1
              and audit_rows[0]["verdict"] == "exists"
              and audit_rows[0]["feature_id"] == 999001,
              "audit: %s" % audit_rows)

        # 4. approve, flag exists but variant missing: adds only the variant
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        Path(tmp, "audit.log").unlink(missing_ok=True)
        seed_state(tmp, with_variant=False)
        r = run_create(tmp, fake_dir,
                       [str(plan_path), "--approve", "--audit-log", audit])
        check("missing-variant exits 0", r.returncode == 0, r.stderr)
        calls = read_calls(tmp)
        check("missing-variant makes no create call",
              all(c["argv"][:2] != ["feature", "create"] for c in calls),
              "calls: %s" % calls)
        adds = [c for c in calls
                if c["argv"][:3] == ["feature", "variant", "add"]]
        check("missing-variant adds the variant once", len(adds) == 1,
              "calls: %s" % calls)
        audit_rows = read_audit(tmp)
        check("missing-variant audits approved",
              len(audit_rows) == 1
              and audit_rows[0]["verdict"] == "approved",
              "audit: %s" % audit_rows)

        # 5. failing CLI -> exit 2
        r = run_create(tmp, fake_dir,
                       [str(plan_path), "--approve", "--audit-log", audit],
                       extra_env={"FAKE_FLAGSMITH_FAIL": "1"})
        check("failing CLI exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

        # 6. --help: usage, no CLI call
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_create(tmp, fake_dir, ["--help"])
        check("--help exits 0", r.returncode == 0, r.stderr)
        check("--help prints usage", "usage:" in r.stdout, r.stdout)
        check("--help calls no flagsmith", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))

        # 7. missing plan / bad plan / no control variant -> exit 2
        r = run_create(tmp, fake_dir, [str(Path(tmp) / "nope.json")])
        check("missing plan exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        bad = Path(tmp) / "bad.json"
        bad.write_text("{not json")
        r = run_create(tmp, fake_dir, [str(bad)])
        check("bad plan JSON exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        no_control = dict(PLAN)
        no_control["variants"] = [
            {"key": "variant_b", "value": "New version", "weight": 100}]
        no_control_path = Path(tmp) / "no-control.json"
        no_control_path.write_text(json.dumps(no_control))
        r = run_create(tmp, fake_dir, [str(no_control_path)])
        check("plan without control exits 2", r.returncode == 2,
              "exit %d" % r.returncode)

    print("test_create_flag: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
