# ExpAgent demo video — recording script (~2 min)

Record **tomorrow (Fri Nov 27)**. Silent screen capture first (Cmd+Shift+5),
voiceover separately, merge with ffmpeg. Do NOT re-run live writes on camera —
replay the verified runs below. Current live state: experiment **685 running**
(metric 674), flag `checkout-headline`, demo pair **alice (control) + carol
(variant_b)**. Demo server is running detached on port 8000; if the browser
can't reach it, restart with `cd ~/expagent/demo-app && nohup python3 -m
http.server 8000 > /tmp/demo-server.log 2>&1 &`.

## Narration (read over the shots, ~2 min)

**Shot 1 — problem.** "Setting up an A/B test by hand means creating a flag,
its variants, a metric, the split, and the SDK code — four objects, about ten
minutes of dashboard clicks, and one wrong weight silently breaks the test."

**Shot 2 — prompt to plan.** "ExpAgent takes one sentence — A/B test checkout
headline, fifty-fifty, measure sign-ups, two weeks — and turns it into a plan:
the flag, the variants, the metric, the split, and the SDK snippet. The plan
runs in about a tenth of a second."

**Shot 3 — gate blocks.** "Before anything is applied, a deterministic gate
checks the plan. A 99-percent weight split, a zero-percent variant, an unknown
metric — all blocked, with the reason printed. Nothing reaches Flagsmith unless
the plan passes."

**Shot 4 — approve and apply.** "A human reads the plan and approves it. The
agent applies it — metric and experiment created in the cloud — and every
decision is appended to an audit log. The experiment is created, not started,
until a human starts it."

**Shot 5 — live flip.** "Once it's running, the flag drives the page. Alice
sees Buy now. Carol sees Start free trial. One click sends the
signup_completed event the plan measures."

**Shot 6 — rollback.** "And if it's wrong, one command deletes the experiment.
We proved the rollback on the first run, and the audit log shows it."

**Shot 7 — close.** "One prompt, checked, approved, applied. ExpAgent — the
gate, the approval, the audit, and the rollback around every Flagsmith
experiment."

## Shots (what to capture, in order)

1. **Problem** — terminal, one line: the plan command below (shot 2 covers it;
   no separate capture needed, narration over the plan output).
2. **Prompt → plan** — run live (safe, no Flagsmith call):
   `python3 agent/plan.py "A/B test checkout headline, 50/50, measure sign-ups, 2 weeks"`
   Show the plan JSON: flag, variants, metric, split, snippet.
3. **Gate BLOCK** — run live (safe, local only): `python3 gate/probe.py`
   Show the 99-weights plan BLOCKing with its reason.
4. **Approve + apply + status** — do NOT re-apply (experiment 685 is active;
   a re-apply 409s). Show the already-run evidence instead:
   - `python3 agent/status.py 685` (read-only, safe) — shows running.
   - `tail -3 audit.log` — shows the approved apply line.
5. **Live flip** — browser, two tabs (server must be up):
   - `http://localhost:8000/?identity=alice` → button "Buy now", variant control.
   - `http://localhost:8000/?identity=carol` → button "Start free trial",
     variant variant_b. Click it — the log line "event sent:
     signup_completed" appears.
6. **Rollback** — do NOT delete 685 on camera (it is the live experiment).
   Show the proven rollback instead: `grep 663 audit.log` (the 19:42 IST
   rollback of experiment 663) and the experiment-list output showing only
   397 after the delete.
7. **Close** — repo on screen: `github.com/amanrane28coder/ExpAgent`.

## Merge (after recording)

```
ffmpeg -i screen.mp4 -i voice.m4a -c:v copy -c:a aac -shortest expagent-demo.mp4
```

Keep it under 3 minutes (target ~2:15). Upload as unlisted to YouTube;
the submission needs the link before Sun Nov 29, 20:00 IST.

## Do NOT do on camera

- Do not run `agent/apply.py --approve` again (409 — 685 is active).
- Do not DELETE experiment 685 (kills the live flip).
- Do not start experiment 397 (pre-work, never start it).
- Do not present the ~10-minute manual figure as measured — it is the
  brief's estimate for the same four objects by hand.
