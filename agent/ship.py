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

Mechanism (verified against Flagsmith's own integration test): GET the
feature state for the flag in the environment, set the winner's
multivariate allocation to 100 and every other option to 0 (a control
win sets all options to 0, so everyone gets the base value), then PUT the
whole feature-state document back to
`api/v1/environments/<key>/featurestates/<id>/`. The previous allocations
are printed as the rollback.

Dry-run by default: without --approve it prints the PUT it would send
and changes nothing. With --approve it PUTs and appends one audit line.

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


def _feature_id(value):
    """Feature reference in a featurestate: the list endpoint returns a
    bare int id, the environment document returns a nested dict. The
    real `featurestates/` list returns the int (caught live 2026-11-26:
    ship.py crashed on `f.get("id")` because feature was 306078, not
    {"id": 306078})."""
    if isinstance(value, dict):
        return value.get("id")
    return value


def _option_id(value):
    """multivariate_feature_option: int id in the list endpoint, nested
    dict in the environment document. Read both, write neither (the PUT
    sends the document back as received)."""
    if isinstance(value, dict):
        return value.get("id")
    return value


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

    # ---- 2. read the feature state for the flag ----
    states_path = "api/v1/environments/%s/featurestates/" % env_key
    try:
        states = run_flagsmith(["flagsmith", "api", states_path])
    except RuntimeError as e:
        print("ship error: %s" % e, file=sys.stderr)
        return 2
    if isinstance(states, dict) and "results" in states:
        states = states["results"]
    if not isinstance(states, list):
        print("ship error: featurestates returned unexpected data: %r"
              % (states,), file=sys.stderr)
        return 2
    state = None
    for s in states:
        if _feature_id(s.get("feature")) == feature_id:
            state = s
            break
    if state is None:
        print("ship error: no feature state for feature %s in this "
              "environment" % feature_id, file=sys.stderr)
        return 2
    state_id = state.get("id")
    mv_values = state.get("multivariate_feature_state_values") or []
    before = {}
    for v in mv_values:
        opt_id = _option_id(v.get("multivariate_feature_option"))
        key = None
        for k, oid in options.items():
            if oid == opt_id:
                key = k
        before[key or opt_id] = v.get("percentage_allocation")

    # ---- 3. new allocations: winner 100, everything else 0 ----
    winner_option = options.get(winner)  # None for control
    new_doc = json.loads(json.dumps(state))  # deep copy
    after = {}
    for v in new_doc.get("multivariate_feature_state_values") or []:
        opt_id = _option_id(v.get("multivariate_feature_option"))
        key = None
        for k, oid in options.items():
            if oid == opt_id:
                key = k
        new_pct = 100 if (winner_option is not None
                          and opt_id == winner_option) else 0
        v["percentage_allocation"] = new_pct
        after[key or opt_id] = new_pct
    put_path = ("api/v1/environments/%s/featurestates/%s/"
                % (env_key, state_id))
    put_json = json.dumps(new_doc)

    print("== ExpAgent ship: experiment %s (%s) -> winner %s"
          % (exp_id, exp.get("name", "?"), winner))
    print("   flag: %s (id %s), feature state id %s"
          % (feature.get("name", "?"), feature_id, state_id))
    print("   before: %s" % before)
    print("   after:  %s" % after)
    if not approve:
        print("   dry-run: re-run with --approve to ship. No change made.")
        return 0

    try:
        run_flagsmith(["flagsmith", "api", put_path,
                       "--method", "PUT", "--input", "-"], put_json)
    except RuntimeError as e:
        print("ship error: %s" % e, file=sys.stderr)
        return 2
    append_audit(audit_log, {
        "ts": utcnow(),
        "who": who,
        "action": "ship",
        "verdict": "approved",
        "flag": feature.get("name"),
        "experiment_id": exp_id,
        "winner": winner,
        "before": before,
        "after": after,
    })
    print("== SHIPPED: %s now gets 100%% of traffic on %s"
          % (winner, feature.get("name", "?")))
    print("== rollback: re-PUT the feature state with allocations %s"
          % before)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
