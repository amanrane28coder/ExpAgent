# ExpAgent

Prompt-to-experiment agent for Flagsmith. Turn one sentence into a complete,
checked experiment plan — and apply it only after a human approves.

Track 2 — AI Agents and Feature Flags, Toggle Wars (WeMakeDevs × Flagsmith),
Nov 26–29, 2026. Team Void, solo.

## How it works

1. **Plan** — one prompt in plain English → flag (variants + weights),
   metric/event, traffic split, target environment, SDK snippet.
2. **Gate** — deterministic checks. BLOCK on weights ≠ 100, a 0% variant,
   an unknown metric, a rollout jump > 50 pp, a kill-switch turned off.
3. **Approve + apply + log** — a human approves the plan; only then does the
   agent create the flag + metric + experiment and append the decision to an
   audit log, with a rollback command.

## Run

(One-command run — added during hacking, Nov 26.)

## Pre-work (done)

- Flagsmith Cloud project "toggle-wars" (id 55539), Development environment.
- Flag `checkout-cta` (id 301244), metric `checkout_completed` (id 344),
  experiment "Checkout CTA wording test" (id 397, created, NOT started).
- Free-tier Experimentation verified working.
