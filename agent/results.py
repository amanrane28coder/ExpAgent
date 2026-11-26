#!/usr/bin/env python3
"""ExpAgent results: read one experiment and print the decision checkpoint.

Calls the Flagsmith API through CLI v2 (`flagsmith api ...`), the same
pattern as agent/status.py. Read-only — it never creates, starts, ends,
or ships anything.

What it prints: the experiment's status, feature, metric(s), and — when the
API response carries them — the rollout decision (`rollout_decision` /
`rollout_variant`). Computed results (win probability, lift, enrolled
users) are NOT in the experiment API response: Flagsmith computes them on
demand in the dashboard (Refresh results, at most once every 5 minutes).
This command says so plainly instead of inventing numbers, and prints the
ship rule so the human knows what to look for.

Ship rule (Flagsmith docs): a variation at >=95% chance to be best is the
recommendation to roll out. If the 95% credible interval crosses zero, the
result is inconclusive — do not ship.

Usage:
    python3 agent/results.py <experiment-id>
    python3 agent/results.py 685 --env-key MHSyYjVPRHPkaYLkdRHaCS
    python3 agent/results.py --help          # usage, no CLI call

Exit codes: 0 printed, 2 error (bad args, CLI failure, bad JSON).
"""

import json
import subprocess
import sys

DEFAULT_ENV_KEY = "MHSyYjVPRHPkaYLkdRHaCS"  # toggle-wars, Development

USAGE = ("usage: results.py <experiment-id> [--env-key KEY]\n"
         "       results.py --help")


def run_flagsmith(argv):
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


def summarize(exp):
    """Render the decision checkpoint for one experiment dict."""
    lines = []
    lines.append("experiment %s: %s"
                 % (exp.get("id", "?"), exp.get("name", "?")))
    lines.append("  status: %s (started_at: %s, ended_at: %s)"
                 % (exp.get("status", "?"),
                    exp.get("started_at") or "—",
                    exp.get("ended_at") or "—"))
    feature = exp.get("feature") or {}
    lines.append("  feature: %s (id %s)"
                 % (feature.get("name", "?"), feature.get("id", "?")))
    metrics = exp.get("metrics") or []
    if metrics:
        lines.append("  metrics: %s"
                     % "; ".join("%s (id %s, %s, %s, expect %s)"
                                 % (m.get("metric_name", "?"),
                                    m.get("metric", "?"),
                                    m.get("aggregation", "?"),
                                    m.get("direction", "?"),
                                    m.get("expected_direction", "?"))
                                 for m in metrics))
    else:
        lines.append("  metrics: none")
    decision = exp.get("rollout_decision")
    variant = exp.get("rollout_variant")
    if decision is not None or variant is not None:
        lines.append("  rollout decision: %s (variant: %s)"
                     % (decision, variant))
    # Computed results are dashboard-only; never invent them.
    lines.append("  computed results: not in this API response — open the "
                 "experiment in Flagsmith Cloud and click Refresh results "
                 "(at most once every 5 minutes).")
    lines.append("  ship rule: a variation at >=95% chance to be best is "
                 "the recommendation; a 95% credible interval crossing "
                 "zero means inconclusive — do not ship.")
    status = exp.get("status")
    if status == "running":
        lines.append("  decision: none yet — experiment still running.")
    elif status == "completed":
        lines.append("  decision: results frozen — read the dashboard, "
                     "then ship with agent/ship.py --winner <key> "
                     "--approve.")
    elif status == "paused":
        lines.append("  decision: none yet — experiment paused.")
    else:
        lines.append("  decision: none yet — status %s." % status)
    return "\n".join(lines)


def main(argv):
    args = argv[1:]
    if "--help" in args or "-h" in args:
        print(USAGE)
        return 0
    if not args or args[0].startswith("-"):
        print(USAGE, file=sys.stderr)
        return 2

    exp_id = args[0]

    def opt_value(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    env_key = opt_value("--env-key", DEFAULT_ENV_KEY)
    path = ("api/v1/environments/%s/experiments/%s/"
            % (env_key, exp_id))
    try:
        exp = run_flagsmith(["flagsmith", "api", path])
    except RuntimeError as e:
        print("results error: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(exp, dict):
        print("results error: experiment %s returned unexpected data: %r"
              % (exp_id, exp), file=sys.stderr)
        return 2
    print(summarize(exp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
