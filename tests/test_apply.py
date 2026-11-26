#!/usr/bin/env python3
"""Tests for agent/apply.py — real subprocess runs against a FAKE `flagsmith`
executable on PATH that records its argv/stdin and returns canned JSON.

The real CLI and the real Cloud project are never touched. The fake answers:
  - `flagsmith feature get <flag> --json` -> {"id": 301244, variants [...]}
    (the REAL CLI v2.0.0 shape, verified live 2026-11-26: options live
    under "variants", each with id/value/weight/key — NOT
    "multivariate_options", which is what the first version of this fake
    wrongly returned and what hid the resolver bug until the real run.)
  - `flagsmith api .../featurestates/?feature=<id>` -> [feature state]
    (current weights: variant_b 50, variant_c 0, control implied 50)
  - `flagsmith api .../experiment-metrics/` (GET) -> [metric defs]
    (the gate's known-events source: signup_completed, checkout_completed)
  - `flagsmith api .../experiment-metrics/ --input -` -> {"id": 344}
  - `flagsmith api .../experiments/ --input -` -> {"id": 397}
Set FAKE_FLAGSMITH_FAIL=1 to make every call exit 1.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPLY = ROOT / "agent" / "apply.py"

FAKE_SCRIPT = '''#!/usr/bin/env python3
"""Fake `flagsmith` for ExpAgent tests: records argv+stdin, returns canned JSON."""
import json, os, sys

log_path = os.environ.get("FAKE_FLAGSMITH_LOG")
argv = sys.argv[1:]
# Only api calls carry a JSON body on stdin; `feature get` has none, and
# reading stdin unconditionally can block on an inherited pipe.
stdin = sys.stdin.read() if argv and argv[0] == "api" else ""
if log_path:
    with open(log_path, "a") as f:
        f.write(json.dumps({"argv": argv, "stdin": stdin}) + "\\n")
if os.environ.get("FAKE_FLAGSMITH_FAIL") == "1":
    print("fake flagsmith: simulated failure", file=sys.stderr)
    sys.exit(1)
if os.environ.get("FAKE_FLAGSMITH_FAIL_EXPERIMENTS") == "1" \
        and "/experiments/" in " ".join(argv) \
        and "experiment-metrics" not in " ".join(argv):
    print("fake flagsmith: simulated experiment failure",
          file=sys.stderr)
    sys.exit(1)
if os.environ.get("FAKE_FLAGSMITH_FAIL_METRIC_DELETE") == "1" \
        and "--method" in argv and "DELETE" in argv \
        and "experiment-metrics" in " ".join(argv):
    print("fake flagsmith: simulated metric-delete failure",
          file=sys.stderr)
    sys.exit(1)
if argv[:2] == ["feature", "get"]:
    print(json.dumps({"id": 301244, "name": argv[2],
                      "type": "multivariate",
                      "default_value": "Buy now", "enabled": False,
                      "variants": [
                          {"id": 36378, "key": "variant_b",
                           "value": "Start free trial", "weight": 0},
                          {"id": 36379, "key": "variant_c",
                           "value": "Get started today", "weight": 0}]}))
elif "featurestates" in " ".join(argv):
    # GET .../featurestates/?feature=<id> -> the environment feature
    # state: current weights live here (variant_b 50, variant_c 0,
    # control implied 50). The shape is the real Flagsmith
    # featurestate: "feature" is a bare int, multivariate values carry
    # "percentage_allocation".
    print(json.dumps([{"id": 1, "feature": 301244, "enabled": True,
                       "feature_state_value": "Buy now",
                       "multivariate_feature_state_values": [
                           {"multivariate_feature_option": 36378,
                            "percentage_allocation": 50},
                           {"multivariate_feature_option": 36379,
                            "percentage_allocation": 0}]}]))
elif "experiment-metrics" in " ".join(argv):
    if "--input" in argv:
        print(json.dumps({"id": 344, "name": "signup_completed"}))
    else:
        # GET .../experiment-metrics/ -> the metric definitions, the
        # source of the gate's known-events list.
        print(json.dumps([
            {"id": 344, "name": "signup_completed",
             "definition": {"version": 1, "event": "signup_completed"}},
            {"id": 345, "name": "checkout_completed",
             "definition": {"version": 1, "event": "checkout_completed"}}]))
elif "experiments" in " ".join(argv):
    print(json.dumps({"id": 397, "name": "A/B test: checkout headline",
                      "status": "created"}))
else:
    print("{}")
'''

PLAN = {
    "flag": "checkout-headline",
    "name": "A/B test: checkout headline",
    "hypothesis": "The 'Start free trial' variant increases "
                  "signup_completed versus control.",
    "environment": "development",
    "variants": [{"key": "control", "value": "Buy now", "weight": 50},
                 {"key": "variant_b", "value": "Start free trial",
                  "weight": 50}],
    "metric": {"name": "signup_completed", "event": "signup_completed",
               "aggregation": "occurrence", "direction": "up",
               "expected_direction": "increase"},
    "duration_days": 14,
    "sdk_snippet": "// snippet",
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


def run_apply(tmp, fake_dir, extra_args, extra_env=None, plan=PLAN):
    plan_path = Path(tmp) / "plan.json"
    plan_path.write_text(json.dumps(plan))
    args = [sys.executable, str(APPLY), str(plan_path),
            "--audit-log", str(Path(tmp) / "audit.log")]
    args += extra_args
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT,
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

        # 1. dry-run: prints commands, runs nothing, logs a dry-run verdict
        r = run_apply(tmp, fake_dir, [])
        check("dry-run exits 0", r.returncode == 0, r.stderr)
        check("dry-run calls no flagsmith", read_calls(tmp) == [],
              "calls: %s" % read_calls(tmp))
        check("dry-run prints metric command",
              "experiment-metrics" in r.stdout and "flagsmith api" in r.stdout)
        check("dry-run prints experiment payload",
              '"feature": 301244' not in r.stdout  # no id resolved in dry-run
              and "multivariate_feature_state_values" in r.stdout)
        audit = read_audit(tmp)
        check("dry-run appends audit line with verdict dry-run",
              len(audit) == 1 and audit[0]["verdict"] == "dry-run"
              and audit[0]["after"] == {"control": 50, "variant_b": 50},
              "audit: %s" % audit)

        # 2. --approve with explicit ids: metric then experiment, rollback shown
        r = run_apply(tmp, fake_dir,
                      ["--approve", "--feature-id", "301244",
                       "--option-ids", '{"variant_b": 36378}'])
        check("approve exits 0", r.returncode == 0, r.stderr)
        calls = read_calls(tmp)
        writes = [c for c in calls if "--input" in c["argv"]]
        check("approve makes exactly 2 write calls", len(writes) == 2,
              "calls: %s" % calls)
        reads = [c for c in calls if "--input" not in c["argv"]]
        check("approve gates first: featurestates + metrics reads precede "
              "the writes, and the full option map is fetched",
              len(calls) == 5
              and any("featurestates" in " ".join(c["argv"])
                      for c in reads)
              and any("experiment-metrics" in " ".join(c["argv"])
                      for c in reads)
              and any(c["argv"][:2] == ["feature", "get"] for c in reads)
              and all(reads.index(c) < calls.index(writes[0])
                      for c in reads),
              "calls: %s" % calls)
        if len(writes) == 2:
            metric_call, exp_call = writes
            check("metric call payload has occurrence/up/event",
                  json.loads(metric_call["stdin"])["definition"]["event"]
                  == "signup_completed")
            exp_payload = json.loads(exp_call["stdin"])
            check("experiment payload uses feature 301244 + metric 344",
                  exp_payload["feature"] == 301244
                  and exp_payload["metrics"][0]["metric"] == 344,
                  "payload: %s" % exp_payload)
            check("experiment rollout: variant_b 50, control rest",
                  exp_payload["experiment_rollout"]
                  ["multivariate_feature_state_values"]
                  == [{"multivariate_feature_option": 36378,
                       "percentage_allocation": 50}])
            check("experiment rollout control value is the control copy",
                  exp_payload["experiment_rollout"]["feature_state_value"]
                  == {"type": "string", "value": "Buy now"},
                  "payload: %s" % exp_payload)
        check("approve prints APPLIED with ids 344/397",
              "metric id 344" in r.stdout and "experiment id 397" in r.stdout,
              r.stdout)
        check("approve prints rollback with experiment id",
              "experiments/397/" in r.stdout and "--method DELETE" in r.stdout)
        audit = read_audit(tmp)
        check("approve appends audit line with verdict approved + ids",
              len(audit) == 2 and audit[1]["verdict"] == "approved"
              and audit[1]["metric_id"] == 344
              and audit[1]["experiment_id"] == 397,
              "audit: %s" % audit)

        # 3. --approve resolving feature/option ids via `feature get`
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        r = run_apply(tmp, fake_dir, ["--approve"])
        check("approve resolves ids via feature get, exits 0",
              r.returncode == 0, r.stderr)
        calls = read_calls(tmp)
        check("feature get called first",
              len(calls) == 5 and calls[0]["argv"][:2] == ["feature", "get"],
              "calls: %s" % calls)
        check("gate reads live state after the resolution",
              len(calls) == 5
              and "featurestates" in " ".join(calls[1]["argv"])
              and "experiment-metrics" in " ".join(calls[2]["argv"]),
              "calls: %s" % calls)
        exp_calls = [c for c in calls if c["argv"][0] == "api"
                     and "--input" in c["argv"]
                     and "/experiments/" in " ".join(c["argv"])]
        if len(exp_calls) == 1:
            exp_payload = json.loads(exp_calls[0]["stdin"])
            check("resolved experiment uses option id 36378",
                  exp_payload["experiment_rollout"]
                  ["multivariate_feature_state_values"][0]
                  ["multivariate_feature_option"] == 36378)

        # 4. failing CLI -> exit 2, audit line verdict failed
        r = run_apply(tmp, fake_dir, ["--approve"],
                      extra_env={"FAKE_FLAGSMITH_FAIL": "1"})
        check("failing CLI exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        audit = read_audit(tmp)
        check("failing CLI appends audit line with verdict failed",
              audit and audit[-1]["verdict"] == "failed",
              "audit: %s" % audit)

        # 5. metric succeeds, experiment fails -> exit 2, audit names the
        #    orphan metric (regression: the id was lost, cf. 649/672),
        #    and the orphan is deleted best-effort, not left behind
        r = run_apply(tmp, fake_dir, ["--approve"],
                      extra_env={"FAKE_FLAGSMITH_FAIL_EXPERIMENTS": "1"})
        check("experiment failure exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        audit = read_audit(tmp)
        calls = read_calls(tmp)
        check("experiment failure audit names orphan metric 344",
              audit and audit[-1]["verdict"] == "failed"
              and audit[-1]["metric_id"] == 344,
              "audit: %s" % audit)
        check("experiment failure prints the orphan metric id",
              "metric 344" in r.stderr, r.stderr)
        deletes = [c for c in calls if "--method" in c["argv"]
                   and "DELETE" in c["argv"]
                   and "experiment-metrics/344/" in " ".join(c["argv"])]
        check("experiment failure deletes the orphan metric",
              len(deletes) == 1, "calls: %s" % calls)
        check("experiment failure audits the orphan as deleted",
              audit and audit[-1].get("orphan_metric") == "deleted",
              "audit: %s" % audit)

        # 6. metric delete fails too -> exit 2, audit says not_deleted
        r = run_apply(tmp, fake_dir, ["--approve"],
                      extra_env={"FAKE_FLAGSMITH_FAIL_EXPERIMENTS": "1",
                                 "FAKE_FLAGSMITH_FAIL_METRIC_DELETE": "1"})
        check("delete failure still exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        audit = read_audit(tmp)
        check("delete failure audits the orphan as not_deleted",
              audit and audit[-1].get("orphan_metric") == "not_deleted",
              "audit: %s" % audit)
        check("delete failure prints the orphan metric id",
              "metric 344" in r.stderr, r.stderr)

        # 7. gate enforcement: a plan the gate would BLOCK cannot be
        #    applied with --approve — exit 1, audit verdict refused, and
        #    NO write calls (no metric, no experiment).
        blocked_plan = dict(PLAN)
        blocked_plan["metric"] = dict(PLAN["metric"])
        blocked_plan["metric"]["event"] = "frobnicate"
        Path(tmp, "calls.jsonl").unlink(missing_ok=True)
        audit_before = len(read_audit(tmp))
        r = run_apply(tmp, fake_dir, ["--approve"],
                      plan=blocked_plan)
        check("gate refusal exits 1", r.returncode == 1,
              "exit %d" % r.returncode)
        calls = read_calls(tmp)
        check("gate refusal makes no write calls",
              not [c for c in calls if "--input" in c["argv"]],
              "calls: %s" % calls)
        audit = read_audit(tmp)
        check("gate refusal appends audit line with verdict refused",
              len(audit) == audit_before + 1
              and audit[-1]["verdict"] == "refused"
              and audit[-1]["reason"] == "gate blocked this plan"
              and any("not a known event" in reason
                      for reason in audit[-1].get("reasons", [])),
              "audit: %s" % audit)
        check("gate refusal prints the block reasons",
              "REFUSED: gate blocked this plan" in r.stdout
              and "not a known event" in r.stdout, r.stdout)

        # 8. a plan without a control variant fails cleanly — exit 2,
        #    one-line error, no traceback (regression: uncaught
        #    StopIteration from the control lookup).
        no_control = dict(PLAN)
        no_control["variants"] = [
            {"key": "variant_b", "value": "Start free trial", "weight": 50},
            {"key": "variant_c", "value": "Get started today", "weight": 50}]
        r = run_apply(tmp, fake_dir, [], plan=no_control)
        check("no-control plan exits 2", r.returncode == 2,
              "exit %d" % r.returncode)
        check("no-control plan prints a one-line error, no traceback",
              "apply error: plan has no control variant" in r.stderr
              and "Traceback" not in r.stderr, r.stderr)

    print("test_apply: %d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
