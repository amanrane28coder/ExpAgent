#!/usr/bin/env python3
"""ExpAgent applier: plan JSON -> Flagsmith metric + experiment via CLI v2.

Write path is `flagsmith api ...` (CLI v2 + REST API) per the 2026-11-18
correction — the MCP server cannot start experiments. Payload shapes are the
proven-live ones from pre-work step 4 (metric id 344, experiment id 397).

Dry-run is the default: it prints the exact commands and payloads and runs
NOTHING. A real apply happens only with --approve, and only after a human
has approved the plan (the gate recommends; it approves nothing).

Usage:
    python3 agent/apply.py plan.json                        # dry-run
    python3 agent/apply.py plan.json --approve             # real apply
    python3 agent/apply.py plan.json --approve --feature-id 301244
    python3 agent/apply.py plan.json --approve --state state.json --who aman

Every run appends one JSON line to audit.log (who, what, verdict, timestamp,
before/after weights) and prints a rollback command. The rollback deletes the
created experiment (it is created, never started — starting locks the flag).

Exit codes: 0 applied / dry-run printed, 2 error (bad plan, CLI failure,
missing feature/option ids).
"""

import datetime
import getpass
import json
import os
import shlex
import subprocess
import sys

DEFAULT_ENV_KEY = "MHSyYjVPRHPkaYLkdRHaCS"  # toggle-wars, Development
DEFAULT_AUDIT_LOG = "audit.log"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def shell_cmd(argv, stdin_json=None):
    """Render the exact command a human could paste to reproduce the call."""
    parts = [shlex.quote(a) for a in argv]
    if stdin_json is not None:
        return "echo %s | %s" % (shlex.quote(stdin_json), " ".join(parts))
    return " ".join(parts)


def metric_payload(plan):
    metric = plan["metric"]
    return {
        "name": metric["name"],
        "aggregation": metric.get("aggregation", "occurrence"),
        "direction": metric.get("direction", "up"),
        "definition": {"version": 1, "event": metric["event"]},
    }


def experiment_payload(plan, feature_id, metric_id, option_ids):
    """option_ids: {variant_key: multivariate option id} for non-control."""
    rollout = {
        "enabled": True,
        "rollout_percentage": 100,
        "feature_state_value": {"type": "string", "value": "control"},
        "multivariate_feature_state_values": [
            {"multivariate_feature_option": option_ids[v["key"]],
             "percentage_allocation": v["weight"]}
            for v in plan["variants"] if v["key"] != "control"
        ],
    }
    return {
        "feature": feature_id,
        "name": plan["name"],
        "hypothesis": plan["hypothesis"],
        "metrics": [{"metric": metric_id,
                     "expected_direction":
                         plan["metric"].get("expected_direction",
                                            "increase")}],
        "experiment_rollout": rollout,
    }


def run_flagsmith(argv, stdin_json=None):
    """Run one `flagsmith ...` call. Returns parsed JSON stdout."""
    kwargs = {}
    if stdin_json is None:
        # Never inherit the caller's stdin: a child that reads it would
        # block forever on a pipe that never closes.
        kwargs["stdin"] = subprocess.DEVNULL
    result = subprocess.run(
        argv,
        input=stdin_json,  # str: text=True below
        capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError("flagsmith %s failed (exit %d): %s"
                           % (" ".join(argv[1:]), result.returncode,
                              result.stderr.strip() or result.stdout.strip()))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("flagsmith %s returned non-JSON output: %r"
                           % (" ".join(argv[1:]), result.stdout[:200]))


def resolve_feature(flag, env_key):
    """Resolve feature id + variant option ids via `flagsmith feature get`."""
    data = run_flagsmith(["flagsmith", "feature", "get", flag, "--json"])
    feature_id = data.get("id")
    if not feature_id:
        raise RuntimeError("feature get for '%s' returned no id" % flag)
    options = {}
    # Real CLI v2.0.0 `feature get --json` returns the options under
    # "variants" (verified live 2026-11-26: flag 305610, variant_b id
    # 37289). Older shapes used "multivariate_options" — read both.
    for opt in (data.get("variants", [])
                + data.get("multivariate_options", [])):
        if opt.get("key"):
            options[opt["key"]] = opt["id"]
    return feature_id, options


def append_audit(path, entry):
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv):
    args = argv[1:]
    if not args or args[0].startswith("-"):
        print("usage: apply.py <plan.json> [--approve] [--env-key KEY] "
              "[--feature-id ID] [--option-ids JSON] [--state FILE] "
              "[--audit-log FILE] [--who NAME]", file=sys.stderr)
        return 2

    plan_path = args[0]
    approve = "--approve" in args

    def opt_value(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    env_key = opt_value("--env-key", DEFAULT_ENV_KEY)
    audit_log = opt_value("--audit-log", DEFAULT_AUDIT_LOG)
    who = opt_value("--who") or os.environ.get("USER") or getpass.getuser()
    state_path = opt_value("--state")

    try:
        with open(plan_path) as f:
            plan = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print("apply error: cannot read plan: %s" % e, file=sys.stderr)
        return 2

    before = None
    if state_path:
        try:
            with open(state_path) as f:
                before = json.load(f).get("current_weights")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print("apply error: cannot read state: %s" % e, file=sys.stderr)
            return 2
    after = {v["key"]: v["weight"] for v in plan["variants"]}

    metric_path = ("api/v1/environments/%s/experiment-metrics/"
                   % env_key)
    experiment_path = "api/v1/environments/%s/experiments/" % env_key
    metric_json = json.dumps(metric_payload(plan))

    audit = {
        "ts": utcnow(),
        "who": who,
        "action": "apply" if approve else "dry-run",
        "verdict": None,
        "flag": plan["flag"],
        "plan": plan["name"],
        "metric_id": None,
        "experiment_id": None,
        "before": before,
        "after": after,
    }

    # ---- resolve feature id + option ids (real mode only) ----
    feature_id = opt_value("--feature-id")
    if isinstance(feature_id, str) and feature_id.isdigit():
        # --feature-id arrives as a string; the API expects an integer.
        feature_id = int(feature_id)
    option_ids = None
    if opt_value("--option-ids"):
        try:
            option_ids = json.loads(opt_value("--option-ids"))
        except json.JSONDecodeError as e:
            print("apply error: bad --option-ids JSON: %s" % e,
                  file=sys.stderr)
            return 2
    if approve and (feature_id is None or option_ids is None):
        try:
            fid, oids = resolve_feature(plan["flag"], env_key)
        except RuntimeError as e:
            audit["verdict"] = "failed"
            audit["error"] = str(e)
            append_audit(audit_log, audit)
            print("apply error: %s" % e, file=sys.stderr)
            return 2
        feature_id = feature_id or fid
        option_ids = option_ids or oids
    if feature_id is None:
        feature_id = "<feature-id>"
    if option_ids is None:
        option_ids = {v["key"]: "<option-id:%s>" % v["key"]
                      for v in plan["variants"] if v["key"] != "control"}

    missing = [v["key"] for v in plan["variants"]
               if v["key"] != "control" and v["key"] not in option_ids]
    if missing:
        print("apply error: no multivariate option id for variant(s): %s"
              % ", ".join(missing), file=sys.stderr)
        return 2

    exp_payload = experiment_payload(plan, feature_id, "<metric-id>",
                                     option_ids)
    metric_cmd = shell_cmd(["flagsmith", "api", metric_path, "--input", "-"],
                           metric_json)

    print("== ExpAgent apply: %s (%s)" % (plan["name"], plan["flag"]))
    print("== 1. create metric '%s'" % plan["metric"]["name"])
    print("   %s" % metric_cmd)
    print("   payload: %s" % metric_json)

    if not approve:
        exp_cmd = shell_cmd(
            ["flagsmith", "api", experiment_path, "--input", "-"],
            json.dumps(exp_payload))
        print("== 2. create experiment (NOT started — starting locks "
              "the flag)")
        print("   %s" % exp_cmd)
        print("   payload: %s" % json.dumps(exp_payload))
        print("== DRY-RUN: no commands were executed. Re-run with "
              "--approve after a human approves this plan.")
        print("== rollback (after a real apply): "
              "flagsmith api api/v1/environments/%s/experiments/"
              "<experiment-id>/ --method DELETE" % env_key)
        audit["verdict"] = "dry-run"
        append_audit(audit_log, audit)
        return 0

    # ---- real apply: metric first, then the experiment with its real id ----
    try:
        metric_resp = run_flagsmith(
            ["flagsmith", "api", metric_path, "--input", "-"], metric_json)
        metric_id = metric_resp.get("id")
        if not metric_id:
            raise RuntimeError("metric create returned no id: %r"
                               % (metric_resp,))
        exp_payload["metrics"][0]["metric"] = metric_id
        exp_cmd = shell_cmd(
            ["flagsmith", "api", experiment_path, "--input", "-"],
            json.dumps(exp_payload))
        print("== metric created: id %s" % metric_id)
        print("== 2. create experiment (NOT started — starting locks "
              "the flag)")
        print("   %s" % exp_cmd)
        print("   payload: %s" % json.dumps(exp_payload))
        exp_resp = run_flagsmith(
            ["flagsmith", "api", experiment_path, "--input", "-"],
            json.dumps(exp_payload))
        experiment_id = exp_resp.get("id")
        if not experiment_id:
            raise RuntimeError("experiment create returned no id: %r"
                               % (exp_resp,))
    except RuntimeError as e:
        audit["verdict"] = "failed"
        audit["error"] = str(e)
        append_audit(audit_log, audit)
        print("apply error: %s" % e, file=sys.stderr)
        return 2

    audit["verdict"] = "approved"
    audit["metric_id"] = metric_id
    audit["experiment_id"] = experiment_id
    append_audit(audit_log, audit)

    print("== APPLIED: metric id %s, experiment id %s (status: created, "
          "not started)" % (metric_id, experiment_id))
    print("== rollback: flagsmith api api/v1/environments/%s/experiments/"
          "%s/ --method DELETE" % (env_key, experiment_id))
    print("== audit: appended to %s" % audit_log)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
