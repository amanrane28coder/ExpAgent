#!/usr/bin/env python3
"""ExpAgent ship: roll the winning variation out to 100% of users.

This is the second human gate. The agent never decides the winner: the
human reads the computed results in the Flagsmith dashboard (Refresh
results; ship rule: >=95% chance to be best, credible interval not
crossing zero), ends the experiment there, and names the winner here.
The agent only executes the rollout — and refuses when the preconditions
are not met.

Preconditions (refusals, exit 1, nothing is changed):
  - the experiment must be "completed" (ended in the dashboard, which
    freezes results and unlocks the flag). A running/paused/created
    experiment is refused: the agent will not ship a live test.
  - --winner must be "control" or a variant key on the flag.
  - --winner must match the experiment's recorded rollout decision
    (rollout_decision / rollout_variant, set when the experiment is
    completed in the dashboard). A "disable" decision, a missing
    decision, or an unknown decision value is refused: shipping a
    different winner would contradict what was decided.

Mechanism: GET the flag's current state, then PATCH the variant weights
to winner 100 / everything else 0 (a control win sets all options to 0,
so everyone gets the base value). The write goes through Flagsmith's
experimental update-flag endpoint
(`PATCH api/__future__/environments/<key>/features/<feature-id>/`),
NOT the legacy `featurestates/<id>/` PUT: this environment uses
Feature Versioning v2, and the legacy PUT returns 400 there ("This
environment uses v2 feature versioning"). The update-flag endpoint is
versioning-agnostic — same request on v1 and v2 environments, and it
publishes a new version per call on v2. The published state is read back
from the PATCH response and verified against the intended allocations
before the audit line is written.

Dry-run by default: without --approve it prints the PATCH it would send
and changes nothing. With --approve it PATCHes and appends one audit line.

Usage:
    python3 agent/ship.py <experiment-id> --winner variant_b [--approve]
    python3 agent/ship.py 685 --winner variant_b --approve --who aman
    python3 agent/ship.py --help          # usage, no CLI call

Exit codes: 0 shipped (or dry-run printed), 1 refusal, 2 error.
"""

import datetime
import getpass
import json
import os
import subprocess
import sys

DEFAULT_ENV_KEY = "MHSyYjVPRHPkaYLkdRHaCS"  # toggle-wars, Development
DEFAULT_AUDIT_LOG = "audit.log"

USAGE = ("usage: ship.py <experiment-id> --winner <key> [--approve] "
         "[--env-key KEY] [--audit-log FILE] [--who NAME]\n"
         "       ship.py --help")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def run_flagsmith(argv, stdin_json=None):
    """Run one `flagsmith ...` call. Returns parsed JSON stdout."""
    kwargs = {}
    if stdin_json is None:
        kwargs["stdin"] = subprocess.DEVNULL
    result = subprocess.run(
        argv, input=stdin_json, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError("flagsmith %s failed (exit %d): %s"
                           % (" ".join(argv[1:]), result.returncode,
                              result.stderr.strip() or result.stdout.strip()))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("flagsmith %s returned non-JSON output: %r"
                           % (" ".join(argv[1:]), result.stdout[:200]))


def append_audit(path, entry):
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv):
    args = argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE)
        return 0
    if not args or args[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2

    exp_id = args[0]
    approve = "--approve" in args

    def opt_value(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    winner = opt_value("--winner")
    if not winner:
        print("ship error: --winner <key> is required "
              "(control or a variant key)", file=sys.stderr)
        return 2
    env_key = opt_value("--env-key", DEFAULT_ENV_KEY)
    audit_log = opt_value("--audit-log", DEFAULT_AUDIT_LOG)
    who = opt_value("--who") or os.environ.get("USER") or getpass.getuser()

    # ---- 1. read the experiment; it must be completed ----
    exp_path = ("api/v1/environments/%s/experiments/%s/"
                % (env_key, exp_id))
    try:
        exp = run_flagsmith(["flagsmith", "api", exp_path])
    except RuntimeError as e:
        print("ship error: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(exp, dict):
        print("ship error: experiment %s returned unexpected data: %r"
              % (exp_id, exp), file=sys.stderr)
        return 2
    status = exp.get("status")
    if status != "completed":
        print("REFUSED: experiment %s is %s — ExpAgent only ships a "
              "completed experiment. Read the results in the dashboard, "
              "end the experiment there, then ship."
              % (exp_id, status))
        return 1
    feature = exp.get("feature") or {}
    feature_id = feature.get("id")
    if not feature_id:
        print("ship error: experiment %s carries no feature id" % exp_id,
              file=sys.stderr)
        return 2
    options = {}
    for opt in feature.get("multivariate_options") or []:
        if opt.get("key") and opt.get("id") is not None:
            options[opt["key"]] = opt["id"]
    if winner != "control" and winner not in options:
        print("REFUSED: --winner %r is not control or a variant on %s "
              "(variants: %s)"
              % (winner, feature.get("name", "?"),
                 ", ".join(sorted(options)) or "none"))
        return 1

    # ---- 1b. the winner must match the recorded rollout decision ----
    # Completing an experiment records the decision on the experiment
    # itself (rollout_decision: control | variant | disable, plus
    # rollout_variant when "variant"). Shipping a different winner
    # would contradict what was decided in the dashboard, so refuse.
    decision = exp.get("rollout_decision")
    rollout_variant = exp.get("rollout_variant")
    if decision is None:
        print("REFUSED: experiment %s is completed but records no "
              "rollout decision — ExpAgent only ships the winner the "
              "dashboard recorded. End the experiment with a decision, "
              "then ship." % exp_id)
        return 1
    if decision == "disable":
        print("REFUSED: experiment %s was completed with rollout "
              "decision 'disable' — the flag was disabled, so there is "
              "no winner to ship." % exp_id)
        return 1
    if decision == "control":
        if winner != "control":
            print("REFUSED: experiment %s recorded rollout decision "
                  "'control', but --winner is %r. Ship the recorded "
                  "winner or re-decide in the dashboard."
                  % (exp_id, winner))
            return 1
    elif decision == "variant":
        if rollout_variant is None or options.get(winner) != rollout_variant:
            print("REFUSED: experiment %s recorded rollout decision "
                  "'variant' (option %s), but --winner %r does not "
                  "match it. Ship the recorded winner or re-decide in "
                  "the dashboard."
                  % (exp_id, rollout_variant, winner))
            return 1
    else:
        print("REFUSED: experiment %s records an unknown rollout "
              "decision %r — refusing rather than guessing."
              % (exp_id, decision))
        return 1

    # ---- 2. read the flag's current weights (versioning-agnostic) ----
    # The update-flag GET returns the whole flag state in the
    # environment: environment_default (enabled, value, variants with
    # weights) + segment_overrides. No featurestates call: the legacy
    # list shape (bare-int feature ids) already bit us once, and the
    # update-flag read is the same endpoint family as the write.
    flag_path = ("api/__future__/environments/%s/features/%s/"
                 % (env_key, feature_id))
    try:
        flag = run_flagsmith(["flagsmith", "api", flag_path])
    except RuntimeError as e:
        print("ship error: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(flag, dict):
        print("ship error: flag %s returned unexpected data: %r"
              % (feature_id, flag), file=sys.stderr)
        return 2
    env_default = flag.get("environment_default") or {}
    before = {}
    for v in env_default.get("variants") or []:
        opt_id = v.get("id")
        key = None
        for k, oid in options.items():
            if oid == opt_id:
                key = k
        before[key or opt_id] = v.get("weight")
    # A variant the experiment knows but the flag state does not list
    # carries no weight: unallocated weight serves the default value.
    for k, oid in options.items():
        if k not in before and oid not in before:
            before[k] = 0

    # ---- 3. new allocations: winner 100, everything else 0 ----
    winner_option = options.get(winner)  # None for control
    patch_variants = []
    after = {}
    for k, oid in options.items():
        weight = 100 if (winner_option is not None
                         and oid == winner_option) else 0
        patch_variants.append({"id": oid, "weight": weight})
        after[k] = weight
    patch_json = json.dumps({"environment_default":
                             {"variants": patch_variants}})

    print("== ExpAgent ship: experiment %s (%s) -> winner %s"
          % (exp_id, exp.get("name", "?"), winner))
    print("   flag: %s (id %s)"
          % (feature.get("name", "?"), feature_id))
    print("   before: %s" % before)
    print("   after:  %s" % after)
    if not approve:
        print("   dry-run: re-run with --approve to ship. No change made.")
        return 0

    try:
        updated = run_flagsmith(["flagsmith", "api", flag_path,
                                 "--method", "PATCH", "--input", "-"],
                                patch_json)
    except RuntimeError as e:
        print("ship error: %s" % e, file=sys.stderr)
        return 2
    # Verify the published state from the PATCH response, don't assume
    # it: the response carries the flag's complete state in the
    # environment. A variant missing from it carries weight 0.
    if not isinstance(updated, dict):
        updated = {}
    resp_default = updated.get("environment_default") or {}
    resp_after = {k: 0 for k in options}
    for v in resp_default.get("variants") or []:
        opt_id = v.get("id")
        key = None
        for k, oid in options.items():
            if oid == opt_id:
                key = k
        resp_after[key or opt_id] = v.get("weight")
    if resp_after != after:
        print("ship error: server returned allocations %s, expected %s"
              % (resp_after, after), file=sys.stderr)
        return 2
    append_audit(audit_log, {
        "ts": utcnow(),
        "who": who,
        "action": "ship",
        "verdict": "approved",
        "via": "update-flag (__future__)",
        "flag": feature.get("name"),
        "experiment_id": exp_id,
        "winner": winner,
        "before": before,
        "after": after,
    })
    print("== SHIPPED: %s now gets 100%% of traffic on %s"
          % (winner, feature.get("name", "?")))
    print("== rollback: PATCH the flag with variants %s"
          % [{"id": oid, "weight": before.get(k, 0)}
             for k, oid in options.items()])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
