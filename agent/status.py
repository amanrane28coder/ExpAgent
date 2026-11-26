#!/usr/bin/env python3
"""ExpAgent status: read one experiment and print a short summary.

Calls the Flagsmith API through CLI v2 (`flagsmith api ...`), the same
pattern as agent/apply.py. Read-only — it never creates, starts, or
deletes anything.

Usage:
    python3 agent/status.py <experiment-id>
    python3 agent/status.py 663 --env-key MHSyYjVPRHPkaYLkdRHaCS
    python3 agent/status.py 663 --dry-run   # print the command, run nothing
    python3 agent/status.py --help          # usage, no CLI call

Exit codes: 0 summary printed, 2 error (bad args, CLI failure, bad JSON).
"""

import json
import subprocess
import sys

DEFAULT_ENV_KEY = "MHSyYjVPRHPkaYLkdRHaCS"  # toggle-wars, Development

USAGE = ("usage: status.py <experiment-id> [--env-key KEY] [--dry-run]\n"
         "       status.py --help")


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


def format_percent(value):
    """100.0 -> '100%', 50.5 -> '50.5%', None -> '—'."""
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return "%d%%" % int(value)
    return "%s%%" % value


def summarize(exp):
    """Render the human-readable summary lines for one experiment dict."""
    lines = []
    lines.append("experiment %s: %s"
                 % (exp.get("id", "?"), exp.get("name", "?")))
    started = exp.get("started_at") or "—"
    lines.append("  status: %s (started_at: %s)"
                 % (exp.get("status", "?"), started))
    feature = exp.get("feature") or {}
    lines.append("  feature: %s (id %s)"
                 % (feature.get("name", "?"), feature.get("id", "?")))
    metrics = exp.get("metrics") or []
    if metrics:
        parts = []
        for m in metrics:
            parts.append("%s (id %s, %s, %s, expect %s)"
                         % (m.get("metric_name", "?"), m.get("metric", "?"),
                            m.get("aggregation", "?"), m.get("direction", "?"),
                            m.get("expected_direction", "?")))
        lines.append("  metrics: %s" % "; ".join(parts))
    else:
        lines.append("  metrics: none")
    rollout = exp.get("experiment_rollout") or {}
    if rollout:
        allocs = rollout.get("multivariate_feature_state_values") or []
        # Map option id -> variant key from the feature's own options.
        # Real `feature get --json` returns options under "variants";
        # older shapes used "multivariate_options" — read both.
        key_by_option = {}
        for opt in (feature.get("variants") or []) + \
                (feature.get("multivariate_options") or []):
            if opt.get("id") is not None and opt.get("key"):
                key_by_option[opt["id"]] = opt["key"]
        parts = []
        allocated = 0
        for a in allocs:
            pct = a.get("percentage_allocation", 0)
            allocated += pct if isinstance(pct, (int, float)) else 0
            opt_id = a.get("multivariate_feature_option")
            key = key_by_option.get(opt_id)
            if key:
                parts.append("%s (option %s): %s"
                             % (key, opt_id, format_percent(pct)))
            else:
                parts.append("option %s: %s"
                             % (opt_id, format_percent(pct)))
        base = (rollout.get("feature_state_value") or {}).get("value")
        if base is not None:
            rest = 100 - allocated
            parts.insert(0, "%s: %s (rest)"
                         % (base, format_percent(rest)))
        lines.append("  rollout: %s, %s — %s"
                     % ("enabled" if rollout.get("enabled") else "disabled",
                        format_percent(rollout.get("rollout_percentage")),
                        ", ".join(parts) if parts else "no allocations"))
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
    dry_run = "--dry-run" in args

    def opt_value(name, default=None):
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return default

    env_key = opt_value("--env-key", DEFAULT_ENV_KEY)
    path = ("api/v1/environments/%s/experiments/%s/"
            % (env_key, exp_id))
    cmd = ["flagsmith", "api", path]

    if dry_run:
        print(" ".join(cmd))
        print("dry-run: no command was executed.")
        return 0

    try:
        exp = run_flagsmith(cmd)
    except RuntimeError as e:
        print("status error: %s" % e, file=sys.stderr)
        return 2
    if not isinstance(exp, dict):
        print("status error: experiment %s returned unexpected data: %r"
              % (exp_id, exp), file=sys.stderr)
        return 2
    print(summarize(exp))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
