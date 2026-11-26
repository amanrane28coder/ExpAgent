#!/usr/bin/env python3
"""ExpAgent state: live flag state -> gate-ready state JSON.

The gate (`gate/gate.py`) checks a plan against a flag's CURRENT weights
and the project's known events. A hand-written fixture goes stale: if
the live flag's weights drift, the rollout-jump verdict is wrong, and a
new event is blocked until someone edits the fixture. This command
reads the live state from Flagsmith and writes the JSON the gate
consumes, so the documented flow gates against fresh state, not a
fixture:

    python3 agent/state.py checkout-headline --out /tmp/state-live.json
    python3 gate/gate.py /tmp/plan.json /tmp/state-live.json

Sources (all live, via CLI v2):
  - `flagsmith feature get <flag> --json` -> feature id + option key/id
  - `flagsmith api .../featurestates/?feature=<id>` -> the environment
    feature state -> current weights (control = 100 - variant weights),
    current value, enabled
  - `flagsmith api .../experiment-metrics/` -> known events, from the
    events on the project's metrics

Slow-moving metadata (kill-switch lists, staging lists, timestamps)
cannot be read live, so they come from an optional --state base file
(the fixture, or a previous output of this command). Without a base,
they default to kill-switch on, empty lists, no timestamps — the stale
check is then skipped with a note, exactly as the gate does for a
state without timestamps. known_events is the union of the live metric
events and the base file's list, so a first-ever metric event on a
fresh project is not blocked when the base already names it.

`apply.py` imports `build_state` from this file and re-gates every
--approve against the same live state, so the gate is enforced, not
just documented.

Usage:
    python3 agent/state.py <flag> [--env-key KEY] [--state BASE.json]
        [--out FILE] [--environment NAME]

Exit codes: 0 state written, 2 error (CLI failure, flag not found,
no feature state, unreadable base file).
"""

import json
import os
import subprocess
import sys

DEFAULT_ENV_KEY = "MHSyYjVPRHPkaYLkdRHaCS"  # toggle-wars, Development


def run_flagsmith_json(argv):
    """Run one `flagsmith ...` call. Returns parsed JSON stdout."""
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("flagsmith CLI not found — install CLI v2 "
                           "(https://docs.flagsmith.com/clients/cli)")
    if result.returncode != 0:
        raise RuntimeError("flagsmith %s failed (exit %d): %s"
                           % (" ".join(argv[1:]), result.returncode,
                              result.stderr.strip() or result.stdout.strip()))
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("flagsmith %s returned non-JSON output: %r"
                           % (" ".join(argv[1:]), result.stdout[:200]))


def as_list(data, what):
    """A list response, or a single object wrapped in a list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise RuntimeError("%s returned unexpected data: %r" % (what, data))


def option_ids_from(data):
    """{variant key: option id} from a `feature get --json` response.
    Real CLI v2.0.0 returns options under "variants"; older shapes used
    "multivariate_options" — read both."""
    options = {}
    for opt in (data.get("variants", [])
                + data.get("multivariate_options", [])):
        if opt.get("key"):
            options[opt["key"]] = opt["id"]
    return options


def build_state(flag, env_key=DEFAULT_ENV_KEY, base=None,
                feature_id=None, option_ids=None):
    """Build the gate-ready state dict for a flag from live data.
    `base` is an optional dict (a fixture or a previous output) that
    supplies the slow-moving metadata. Raises RuntimeError on any
    failure — a state file that cannot be trusted is not written."""
    if feature_id is None or option_ids is None:
        feature = run_flagsmith_json(["flagsmith", "feature", "get",
                                      flag, "--json"])
        feature_id = feature.get("id")
        if not feature_id:
            raise RuntimeError("feature get for '%s' returned no id"
                               % flag)
        option_ids = option_ids_from(feature)
    id_to_key = {oid: k for k, oid in option_ids.items()}

    states = as_list(run_flagsmith_json(
        ["flagsmith", "api",
         "api/v1/environments/%s/featurestates/?feature=%s"
         % (env_key, feature_id)]), "featurestates")
    entry = next((e for e in states
                  if isinstance(e, dict) and e.get("feature") == feature_id),
                 None)
    if entry is None:
        raise RuntimeError("no feature state for flag '%s' (id %s) in "
                           "environment %s" % (flag, feature_id, env_key))
    weights = {}
    total_variant = 0
    # The caller's option map may be partial (a plan only names its own
    # variants, and apply.py resolves only those). If the live state
    # carries an option id the map does not know, fetch the full map
    # before giving up — the state file must carry the flag's full live
    # weights, not just the plan's.
    live_ids = {mv.get("multivariate_feature_option")
                for mv in entry.get("multivariate_feature_state_values")
                or []}
    if not live_ids <= set(id_to_key):
        feature = run_flagsmith_json(["flagsmith", "feature", "get",
                                      flag, "--json"])
        option_ids = option_ids_from(feature)
        id_to_key = {oid: k for k, oid in option_ids.items()}
    for mv in entry.get("multivariate_feature_state_values") or []:
        oid = mv.get("multivariate_feature_option")
        key = id_to_key.get(oid)
        if key is None:
            raise RuntimeError("live state has unknown option id %s "
                               "(feature %s)" % (oid, flag))
        w = mv.get("percentage_allocation", 0) or 0
        weights[key] = w
        total_variant += w
    weights["control"] = 100 - total_variant
    raw_value = entry.get("feature_state_value")
    if isinstance(raw_value, dict):
        value = raw_value.get("value")
    else:
        value = raw_value

    metrics = as_list(run_flagsmith_json(
        ["flagsmith", "api",
         "api/v1/environments/%s/experiment-metrics/" % env_key]),
        "experiment-metrics")
    live_events = set()
    for m in metrics:
        if isinstance(m, dict):
            event = (m.get("definition") or {}).get("event")
            if event:
                live_events.add(event)

    base = base or {}
    known = sorted(live_events | set(base.get("known_events") or []))
    return {
        "flag": flag,
        "environment": base.get("environment", "development"),
        "feature_id": feature_id,
        "current_weights": weights,
        "current_value": value,
        "enabled": entry.get("enabled"),
        "known_events": known,
        "kill_switch_on": base.get("kill_switch_on", True),
        "kill_switch_flags": base.get("kill_switch_flags", []),
        "staging_only_flags": base.get("staging_only_flags", []),
        "production_only_flags": base.get("production_only_flags", []),
        "last_evaluated_at": base.get("last_evaluated_at"),
        "last_changed_at": base.get("last_changed_at"),
    }


def main(argv):
    args = argv[1:]
    if args and args[0] in ("--help", "-h"):
        print("usage: state.py <flag> [--env-key KEY] [--state BASE.json] "
              "[--out FILE] [--environment NAME]")
        return 0
    if not args or args[0].startswith("-"):
        print("usage: state.py <flag> [--env-key KEY] [--state BASE.json] "
              "[--out FILE] [--environment NAME]", file=sys.stderr)
        return 2

    flag = args[0]

    def opt_value(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    env_key = opt_value("--env-key", DEFAULT_ENV_KEY)
    base_path = opt_value("--state")
    out_path = opt_value("--out")
    environment = opt_value("--environment")

    base = None
    if base_path:
        try:
            with open(base_path) as f:
                base = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print("state error: cannot read base state: %s" % e,
                  file=sys.stderr)
            return 2
        if not isinstance(base, dict):
            print("state error: base state must be a JSON object",
                  file=sys.stderr)
            return 2
    if environment:
        base = dict(base or {})
        base["environment"] = environment

    try:
        state = build_state(flag, env_key, base)
    except RuntimeError as e:
        print("state error: %s" % e, file=sys.stderr)
        return 2

    text = json.dumps(state, indent=2, sort_keys=True)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text + "\n")
        print("state written to %s" % out_path)
    else:
        print(text)
    print("flag %s (id %s): weights %s, known events %s"
          % (flag, state["feature_id"], state["current_weights"],
             ", ".join(state["known_events"]) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
