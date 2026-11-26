#!/usr/bin/env python3
"""ExpAgent flag creator: plan JSON -> multivariate flag via CLI v2.

Before this command, the flag was the one piece ExpAgent did not create:
checkout-headline was made by hand in the dashboard. Now the pipeline is
prompt -> plan -> gate -> create flag -> apply -> run -> results -> ship,
with the flag coming from the plan like everything else.

Mechanism (Flagsmith's own "Set Up an Experiment with a Coding Agent"
doc, verified against the live flag 305610): create the flag with the
control variant's VALUE as the flag value, then add each non-control
variation with its key and weight 0. Every variation needs a key — one
without a key records no exposures. `control` is reserved for the flag's
own value, so the flag's initial value IS the control copy ("Buy now"),
not the literal string "control".

    flagsmith feature create <flag> --value <control-value> --enabled
    flagsmith feature variant add <flag> --value <value> --key <key> \
        --weight 0

Idempotent: if the flag already exists it is reused (no re-create) and
only missing variants are added. A re-run on checkout-headline prints
"already exists (id 305610)" and exits 0. Reuse is verified, not assumed:
the existing flag's control value and every existing variant's value
must match the plan — a flag that exists with different copy is refused
(exit 2), never silently reused. And a lookup failure is not treated as
"not found": only a not-found error means the flag is absent; any other
CLI error (auth, network, crash) fails loudly instead of triggering a
create against an unknown state.

Dry-run is the default: it prints the exact commands and runs NOTHING.
A real create happens only with --approve, and only after a human has
approved the plan (the gate recommends; it approves nothing).

Usage:
    python3 agent/create_flag.py plan.json             # dry-run
    python3 agent/create_flag.py plan.json --approve  # real create

Every run appends one JSON line to audit.log (who, what, verdict,
feature id, option ids). The create/variant-add calls return text, not
JSON, so this command never parses their output — it re-reads the flag
with `feature get --json` to verify the result.

Exit codes: 0 created / dry-run printed / already exists, 2 error (bad
plan, CLI failure, verification failed).
"""

import datetime
import getpass
import json
import os
import shlex
import subprocess
import sys

DEFAULT_AUDIT_LOG = "audit.log"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def shell_cmd(argv):
    return " ".join(shlex.quote(a) for a in argv)


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


def run_flagsmith_text(argv):
    """Run one `flagsmith ...` call. Returns raw stdout text (no JSON).

    `feature create` / `feature variant add` print text, not JSON — never
    parse their output; verify with `feature get --json` instead.
    """
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
    return result.stdout.strip()


def get_feature(flag):
    """Read one flag via `feature get --json`. Returns the parsed dict,
    or None if the flag does not exist. Only a not-found error means
    "absent" — any other CLI failure is re-raised, so a lookup error is
    never mistaken for a missing flag."""
    try:
        return run_flagsmith_json(["flagsmith", "feature", "get",
                                   flag, "--json"])
    except RuntimeError as e:
        if is_not_found(e, flag):
            return None
        raise


# Substrings (lowercase) that mark a CLI error as "flag does not exist".
# Anything else — auth failure, network error, crash — is a real error.
# The message must also name the flag: an "environment not found" or
# "project not found" error is a broken environment, not a missing flag,
# and must fail loudly instead of triggering a create against it.
NOT_FOUND_MARKERS = ("404", "not found", "does not exist", "no feature")


def is_not_found(error, flag):
    msg = str(error).lower()
    return flag.lower() in msg and any(m in msg for m in NOT_FOUND_MARKERS)


def option_value(opt):
    """A variant option's value: `string_value` in the real CLI shape,
    `value` in fixtures. None when the shape carries no value."""
    value = opt.get("string_value")
    return value if value is not None else opt.get("value")


def verify_existing(current, control_value, variants):
    """Check an existing flag against the plan. Returns
    (mismatches, unverifiable): mismatches are hard refusals (the flag
    exists with different copy); unverifiable notes are fields the flag
    response did not carry, so they could not be checked."""
    mismatches = []
    unverifiable = []
    initial = current.get("initial_value")
    if initial is None:
        unverifiable.append("flag get returned no initial_value — "
                            "control value not verified")
    elif initial != control_value:
        mismatches.append("flag value is %r, plan control is %r"
                          % (initial, control_value))
    have_values = {}
    for opt in (current.get("variants", [])
                + current.get("multivariate_options", [])):
        if opt.get("key"):
            have_values[opt["key"]] = option_value(opt)
    for v in variants:
        if v["key"] not in have_values:
            continue  # missing variant: added later, not a mismatch
        existing = have_values[v["key"]]
        if existing is None:
            unverifiable.append("variant %s carries no value — "
                                "not verified" % v["key"])
        elif existing != v["value"]:
            mismatches.append("variant %s value is %r, plan says %r"
                              % (v["key"], existing, v["value"]))
    return mismatches, unverifiable


def option_ids_from(data):
    """{variant key: option id} from a `feature get --json` response.
    Real CLI v2.0.0 returns options under "variants" (verified live
    2026-11-26: flag 305610, variant_b id 37289); older shapes used
    "multivariate_options" — read both."""
    options = {}
    for opt in (data.get("variants", [])
                + data.get("multivariate_options", [])):
        if opt.get("key"):
            options[opt["key"]] = opt["id"]
    return options


def append_audit(path, entry):
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main(argv):
    args = argv[1:]
    if args and args[0] in ("--help", "-h"):
        print("usage: create_flag.py <plan.json> [--approve] "
              "[--audit-log FILE] [--who NAME]")
        return 0
    if not args or args[0].startswith("-"):
        print("usage: create_flag.py <plan.json> [--approve] "
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

    audit_log = opt_value("--audit-log", DEFAULT_AUDIT_LOG)
    who = opt_value("--who") or os.environ.get("USER") or getpass.getuser()

    try:
        with open(plan_path) as f:
            plan = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print("create_flag error: cannot read plan: %s" % e, file=sys.stderr)
        return 2

    try:
        control_value = next(v["value"] for v in plan["variants"]
                             if v["key"] == "control")
    except (KeyError, StopIteration):
        print("create_flag error: plan has no control variant",
              file=sys.stderr)
        return 2
    variants = [v for v in plan["variants"] if v["key"] != "control"]

    audit = {
        "ts": utcnow(),
        "who": who,
        "action": "create_flag" if approve else "dry-run",
        "verdict": None,
        "flag": plan["flag"],
        "feature_id": None,
        "option_ids": None,
    }

    create_cmd = ["flagsmith", "feature", "create", plan["flag"],
                  "--value", control_value, "--enabled"]
    variant_cmds = [["flagsmith", "feature", "variant", "add",
                     plan["flag"], "--value", v["value"],
                     "--key", v["key"], "--weight", "0"]
                    for v in variants]

    print("== ExpAgent create_flag: %s (%s)" % (plan["name"], plan["flag"]))
    print("== 1. create flag '%s' (control value %r)"
          % (plan["flag"], control_value))
    print("   %s" % shell_cmd(create_cmd))
    for cmd, v in zip(variant_cmds, variants):
        print("== 2. add variant %s (%r)" % (v["key"], v["value"]))
        print("   %s" % shell_cmd(cmd))

    if not approve:
        print("== DRY-RUN: no commands were executed. Re-run with "
              "--approve after a human approves this plan.")
        print("== (If the flag already exists, --approve reuses it and "
              "only adds missing variants.)")
        audit["verdict"] = "dry-run"
        append_audit(audit_log, audit)
        return 0

    # ---- real create: reuse the flag if it exists, add missing variants ----
    created = False
    added_any = False
    try:
        existing = get_feature(plan["flag"])
        if existing is None:
            out = run_flagsmith_text(create_cmd)
            print("== flag created: %s" % (out or plan["flag"]))
            created = True
        else:
            print("== flag '%s' already exists (id %s) — reusing it"
                  % (plan["flag"], existing.get("id")))
        current = get_feature(plan["flag"])
        if current is None:
            raise RuntimeError("flag '%s' still missing after create"
                               % plan["flag"])
        # Reuse is verified: a pre-existing flag with different copy is
        # refused here, before any variant is added.
        mismatches, unverifiable = verify_existing(
            current, control_value, variants)
        for note in unverifiable:
            print("== note: %s" % note)
        if mismatches:
            raise RuntimeError("existing flag '%s' does not match the "
                               "plan: %s — refusing to reuse it"
                               % (plan["flag"], "; ".join(mismatches)))
        feature_id = current.get("id")
        if not feature_id:
            raise RuntimeError("feature get for '%s' returned no id"
                               % plan["flag"])
        have = option_ids_from(current)
        missing = [v for v in variants if v["key"] not in have]
        for cmd, v in zip(variant_cmds, variants):
            if v["key"] not in have:
                out = run_flagsmith_text(cmd)
                print("== variant %s added: %s" % (v["key"], out or "ok"))
                added_any = True
        # Re-read to verify every variant landed with an id.
        verified = get_feature(plan["flag"])
        option_ids = option_ids_from(verified)
        still_missing = [v["key"] for v in variants
                         if v["key"] not in option_ids]
        if still_missing:
            raise RuntimeError("variant(s) missing after create: %s"
                               % ", ".join(still_missing))
    except RuntimeError as e:
        audit["verdict"] = "failed"
        audit["error"] = str(e)
        append_audit(audit_log, audit)
        print("create_flag error: %s" % e, file=sys.stderr)
        return 2

    audit["verdict"] = "approved" if (created or added_any) else "exists"
    audit["feature_id"] = feature_id
    audit["option_ids"] = option_ids
    append_audit(audit_log, audit)

    print("== FLAG READY: %s (id %s), variants %s"
          % (plan["flag"], feature_id, option_ids))
    print("== audit: appended to %s" % audit_log)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
