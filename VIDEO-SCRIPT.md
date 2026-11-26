# ExpAgent demo video — recording script (~2 min)

Record **tomorrow (Fri Nov 27)**. Silent screen capture first (Cmd+Shift+5),
voiceover separately, merge with ffmpeg. Bump the terminal font to 16–18pt
before the plan/probe/audit shots — small terminal text is unreadable on
video. Do NOT re-run live writes on camera — replay the verified runs below. Current live state: experiment **685 running**
(metric 674), flag `checkout-headline`, demo pair **alice (control) + carol
(variant_b)**. Demo server is running detached on port 8000; if the browser
can't reach it, restart with `cd ~/expagent/demo-app && nohup python3 -m
http.server 8000 > /tmp/demo-server.log 2>&1 &`.

## Why this cut

The judges score four things: Use of Flagsmith, Impact, Creativity, Demo
video. This cut is built to hit all four: it opens on the flip (the most
visual proof Flagsmith is doing real work), it leads with the track's own
question — where the agent stops and asks a person — and it proves impact
with a real bug the gate caught, not just the time claim. It also shows the
full lifecycle now: the agent creates the flag from the plan, reports
results and ships the winner — and it refuses to ship without a conclusive
result and a human's call, which is the safety story. The ~10-minute manual
figure is the brief's estimate, not a measurement — say "about ten
minutes" and never "measured". Do not claim a winner: the experiment is
running but has no traffic yet, so there are no results to report.

## Narration (read over the shots, ~2 min)

**Shot 1 — cold open, the flip.** "Alice sees Buy now. Carol sees Start
free trial. Same page, same flag — different variant. This is ExpAgent."

**Shot 2 — problem.** "Setting up that test by hand means a flag, its
variants, a metric, the split, and the SDK code — four objects, about ten
minutes of dashboard clicks, and one wrong weight silently breaks the test."

**Shot 3 — prompt to plan.** "ExpAgent takes one sentence — A/B test
checkout headline, fifty-fifty, measure sign-ups, two weeks — and turns it
into a plan: the flag, the variants, the metric, the split, and the snippet.
The agent creates the flag from the plan — this one already exists, so it
reuses it. The plan runs in about a tenth of a second. The interesting part
isn't what the agent does on its own — it's where it stops and asks a
person."

**Shot 4 — gate blocks.** "Before anything reaches Flagsmith, a
deterministic gate checks the plan. A 99-percent split, a zero-percent
variant, an unknown metric — all blocked, with the reason printed. The gate
even caught a real bug in itself during testing — it crashed on
zero-percent plans instead of blocking them. We fixed it; the rule never
changed."

**Shot 5 — approve and apply.** "A human reads the plan and approves it.
Only then does the agent apply it — metric and experiment created through
the Flagsmith API — and every decision goes into an audit log. The
experiment is created, not started, until a human starts it."

**Shot 6 — flip explained, metric fires.** "Once it's running, the flag
drives the page — that's the flip you saw at the start. One click sends the
signup_completed event the plan measures."

**Shot 7 — results and the refusal to ship.** "The experiment is still
running, so there is no winner to report — and the agent won't invent
one. The results command says so plainly: the numbers live in the
dashboard, and the ship rule is ninety-five percent chance to be best.
Ask it to ship now and it refuses — the experiment is still running. It
only ships a completed experiment, with a human naming the winner."

**Shot 8 — rollback and close.** "And if it's wrong, one command deletes
the experiment. We proved the rollback on the first run, and the audit log
shows it. One prompt, checked, approved, applied. ExpAgent — the gate, the
approval, the audit, and the rollback around every Flagsmith experiment."

## Shots (what to capture, in order)

1. **Cold open — the flip.** Browser, two tabs side by side (or cut
   between them): `http://localhost:8000/?identity=alice` → "Buy now",
   `http://localhost:8000/?identity=carol` → "Start free trial". No
   narration over it yet — let the two buttons land, then start talking.
2. **Problem** — terminal with the plan command (shot 3 covers the visual;
   narration runs over the plan output). No separate capture.
3. **Prompt → plan → flag** — run live (safe, no Flagsmith write):
   `python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks"`
   Show the plan JSON: flag, variants, metric, split, snippet. Then
   `python3 agent/create_flag.py /tmp/plan.json --approve` — the flag
   already exists (id 305610), so it reuses it and adds nothing. Safe and
   idempotent; do NOT create a second flag on camera.
4. **Gate BLOCK** — run live (safe, local only): `python3 gate/probe.py`
   Show the 99-weights plan BLOCKing with its reason, and the 0% case
   BLOCKing (not crashing — that was the bug the probe caught).
5. **Approve + apply + status** — do NOT re-apply (experiment 685 is
   active; a re-apply 409s). Show the already-run evidence instead:
   - `python3 agent/status.py 685` (read-only, safe) — shows running.
   - `tail -3 audit.log` — shows the approved apply line.
   - Flagsmith Cloud dashboard, experiment 685 open — status running,
     50/50 split visible. (Sign in as rane.aman28@gmail.com first.)
6. **Flip explained + metric** — back to the carol tab, click the button:
   the log line "event sent: signup_completed" appears. Then the alice tab
   for contrast.
7. **Results + ship refusal** — run live (both read-only / refusing,
   safe): `python3 agent/results.py 685` (shows "no computed results"
   and the 95% rule), then `python3 agent/ship.py 685 --winner variant_b
   --approve` (refuses: experiment still running). This is the safety
   beat — the agent will not ship without evidence and a human's call.
   Do NOT end 685 to demo a real ship; the flip needs it running.
8. **Rollback + close** — do NOT delete 685 on camera (it is the live
   experiment). Show the proven rollback instead: `grep 663 audit.log`
   (the 19:42 IST rollback of experiment 663) and the experiment-list
   output showing only 397 after the delete. Close on the repo:
   `github.com/amanrane28coder/ExpAgent`.

Add a one-word text label per shot in editing if easy (Flip / Problem /
Plan / Gate / Approve / Live / Results / Rollback); if not, skip — the narration
carries the structure.

## Merge (after recording)

```
ffmpeg -i screen.mp4 -i voice.m4a -c:v copy -c:a aac -shortest expagent-demo.mp4
```

Keep it under 3 minutes (target ~2:25). Upload as unlisted to YouTube;
the submission needs the link before Sun Nov 29, 20:00 IST.

## Do NOT do on camera

- Do not run `agent/apply.py --approve` again (409 — 685 is active).
- Do not DELETE experiment 685 (kills the live flip).
- Do not start experiment 397 (pre-work, never start it).
- Do not present the ~10-minute manual figure as measured — it is the
  brief's estimate for the same four objects by hand.
- Do not claim a winner or show results — the experiment has no traffic
  yet, so there is nothing to report.
