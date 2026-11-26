#!/usr/bin/env python3
"""ExpAgent planner: plain-English prompt -> experiment plan JSON.

Stdlib only. No network, no pip. Reads a prompt from argv, --file, or stdin
and prints one plan JSON object to stdout.

Usage:
    python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks"
    python3 agent/plan.py --file examples/prompt-1.txt
    echo "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks" | python3 agent/plan.py

Exit codes: 0 plan produced, 2 could not parse the prompt.
"""

import json
import re
import sys

# Event names the demo app / Flagsmith project actually sends. The gate
# rejects any metric event not in this list (via the flag-state file).
KNOWN_EVENTS = {
    "signup": "signup_completed",
    "sign-up": "signup_completed",
    "signups": "signup_completed",
    "sign-ups": "signup_completed",
    "checkout": "checkout_completed",
    "purchase": "checkout_completed",
    "order": "checkout_completed",
    "trial": "trial_started",
    "click": "cta_clicked",
    "page view": "page_viewed",
}

# Default display copy per variant key, used when the prompt gives no quoted
# values. Headline tests get checkout copy; anything else gets neutral copy.
DEFAULT_VALUES = {
    "headline": ["Buy now", "Start free trial", "Get started today"],
    "default": ["Current version", "New version", "New version 2"],
}

VARIANT_KEYS = ["control", "variant_b", "variant_c", "variant_d"]


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return slug.strip("-") or "experiment-flag"


def parse_weights(prompt):
    """Return a list of integer weights, or None if the prompt has no split."""
    m = re.search(r"(\d+)\s*/\s*(\d+)(?:\s*/\s*(\d+))?(?:\s*/\s*(\d+))?", prompt)
    if not m:
        return None
    return [int(g) for g in m.groups() if g is not None]


def parse_duration_days(prompt):
    m = re.search(r"(\d+)\s*(weeks?|days?)", prompt.lower())
    if not m:
        return 14
    n, unit = int(m.group(1)), m.group(2)
    return n * 7 if unit.startswith("week") else n


def parse_metric_event(prompt):
    # Prefer the "measure X" clause: the subject itself may contain a
    # keyword (e.g. "checkout headline") that is not the metric.
    m = re.search(r"measure\s+([^,;]+)", prompt, re.I)
    clause = m.group(1).lower() if m else prompt.lower()
    for keyword in ("sign-up", "signup", "sign-ups", "signups",
                    "checkout", "purchase", "order",
                    "trial", "click", "page view"):
        if keyword in clause:
            return KNOWN_EVENTS[keyword]
    return None


def parse_subject(prompt):
    m = re.search(r"(?:a/b|multivariate)?\s*test\s+([^,;]+)", prompt, re.I)
    if not m:
        m = re.search(r"experiment\s+(?:on|for)\s+([^,;]+)", prompt, re.I)
    if not m:
        return None
    subject = m.group(1).strip()
    # Drop trailing split/metric/duration fragments if the subject ran long.
    subject = re.split(r"\s+(?:\d+\s*/|measure|for|with)\b", subject, maxsplit=1)[0]
    return subject.strip() or None


def parse_quoted_values(prompt):
    """['Buy now' vs 'Start free trial'] -> ['Buy now', 'Start free trial']."""
    m = re.search(r'"([^"]+)"\s+vs\s+"([^"]+)"(?:\s+vs\s+"([^"]+)")?', prompt)
    if not m:
        return None
    return [g for g in m.groups() if g is not None]


def parse_environment(prompt):
    lowered = prompt.lower()
    if "production" in lowered or re.search(r"\bprod\b", lowered):
        return "production"
    if "staging" in lowered:
        return "staging"
    return "development"


# Development environment key for the toggle-wars project. This is a
# client-side SDK key — public by design (it ships in the demo page and in
# every generated snippet). It is NOT a secret and NOT a Master API key.
DEV_ENV_KEY = "MHSyYjVPRHPkaYLkdRHaCS"


def build_sdk_snippet(flag, event):
    return (
        "// ExpAgent-generated snippet — flag: %s, metric event: %s\n"
        "import flagsmith from '@flagsmith/flagsmith';\n"
        "\n"
        "await flagsmith.init({\n"
        "  environmentID: '%s', // toggle-wars Development env key\n"
        "  api: 'https://edge.api.flagsmith.com/api/v1/',\n"
        "});\n"
        "\n"
        "// Read the flag value (variant copy) for this identity:\n"
        "const value = flagsmith.getValue('%s');\n"
        "document.getElementById('cta').textContent = value;\n"
        "\n"
        "// Record the conversion event when it happens:\n"
        "flagsmith.track('%s');\n" % (flag, event, DEV_ENV_KEY, flag, event)
    )


def plan_from_prompt(prompt):
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("empty prompt")

    subject = parse_subject(prompt)
    if not subject:
        raise ValueError("could not determine what to test (expected e.g. "
                         "'A/B test checkout headline, 50/50, ...')")
    flag = slugify(subject)

    event = parse_metric_event(prompt)
    if not event:
        raise ValueError("could not determine the metric event (expected e.g. "
                         "'measure sign-ups' / 'measure checkouts' / "
                         "'measure trial starts')")

    weights = parse_weights(prompt)
    if weights is None:
        if re.search(r"3-variant|three variants|multivariate", prompt, re.I):
            weights = [34, 33, 33]
        else:
            weights = [50, 50]
    if len(weights) > len(VARIANT_KEYS):
        raise ValueError("too many variants (max %d)" % len(VARIANT_KEYS))

    quoted = parse_quoted_values(prompt)
    if quoted and len(quoted) >= len(weights):
        values = quoted[:len(weights)]
    else:
        defaults = (DEFAULT_VALUES["headline"] if "headline" in subject.lower()
                    else DEFAULT_VALUES["default"])
        values = defaults[:len(weights)]

    variants = [
        {"key": VARIANT_KEYS[i], "value": values[i], "weight": weights[i]}
        for i in range(len(weights))
    ]

    name = "A/B test: %s" % subject
    hypothesis = ("The '%s' variant increases %s versus control."
                  % (values[1], event))

    return {
        "flag": flag,
        "name": name,
        "hypothesis": hypothesis,
        "environment": parse_environment(prompt),
        "variants": variants,
        "metric": {
            "name": event,
            "event": event,
            "aggregation": "occurrence",
            "direction": "up",
            "expected_direction": "increase",
        },
        "duration_days": parse_duration_days(prompt),
        "sdk_snippet": build_sdk_snippet(flag, event),
    }


def main(argv):
    prompt = None
    if len(argv) > 1 and argv[1] == "--file":
        if len(argv) < 3:
            print("usage: plan.py --file <prompt-file>", file=sys.stderr)
            return 2
        with open(argv[2]) as f:
            prompt = f.read()
    elif len(argv) > 1:
        prompt = " ".join(argv[1:])
    else:
        prompt = sys.stdin.read()

    try:
        plan = plan_from_prompt(prompt)
    except ValueError as e:
        print("plan error: %s" % e, file=sys.stderr)
        return 2
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
