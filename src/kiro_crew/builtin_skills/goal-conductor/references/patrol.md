# Goal Conductor: patrol

Reference for the `goal-conductor` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

### Patrol

After dispatching, arm a loop on your own session with `monitor_start`. Put the
check AND the exit condition in the message, and pass explicit positive
`interval_secs`, `max_cycles` and `max_runtime_secs`.

**`watch="work-ledger"` is mandatory.** It gates the loop on the ledger you
dispatched into: a cycle where no worker reported anything costs no turn, and a
worker's report, a worker session closing, or a worker turn ending pulls the
next cycle forward to within seconds. The interval then only sets how often a
silent fleet is re-checked, not how fast a report reaches you. A loop armed
without it is a plain timer that pays a turn every interval. If you find yours
without it, fix that FIRST, before anything else that cycle, with
`monitor_update(watch="work-ledger")` rather than re-arming.

**The interval is 300 to 900 seconds**, never outside that band, whatever the
round is waiting on. Take the runtime from the operator's time budget, or 86,400
seconds when none is set. **The bounds come from the script, not from you:**

```bash
python3 <this skill's dir>/scripts/patrol_budget.py check \
  --interval-secs <I> --max-cycles <C> --max-runtime-secs <R>
```

Exit 0 means arm with those numbers. Exit 20 means arm with the `suggest` block
it prints instead: an interval outside 300..900 is clamped into it, a long
interval with few cycles ends the loop hours before
its runtime, while work is still live, and an interval longer than 10% of the
runtime lets the loop expire without one cycle in the renewal window. The
script never raises the user's runtime: it only shortens the interval, down to
300. Exit 21 means even 300 does not fit (a runtime under 3000 seconds): do not
arm a longer loop yourself; tell the user and ask for a longer runtime. Run the same check
before any `monitor_update` that changes a bound. Record the bounds you armed
with as `patrol_base` (`cycles=<C> runtime=<R>`) in your own session ledger's
artifacts: renewal reads it back.

**Renew the loop yourself, before it runs out.** A capped loop's nudge carries a
second line, `[patrol budget: cycle N/M, Xs/Rs runtime left]`. Once the cap is
spent the loop deactivates and you never get the turn you would renew in. When
10% or less of either budget is left the line ends `; 10% or less left`. On that cycle,
and only then (the script prompts for approval like any shell call), pass the
line verbatim. The 10% margin is the time a person has to approve that prompt;
if nobody does, the loop ends at its cap as it would have without renewal.

```bash
python3 <this skill's dir>/scripts/patrol_budget.py renew \
  --line '<the [patrol budget: ...] line>' \
  --base-cycles <C> --base-runtime-secs <R> --open-items <items not terminal>
```

| exit | what you do |
|---|---|
| 0 | call `monitor_update` with its `monitor_update` numbers, then carry on. If `monitor_update` refuses the new bounds (an operator runtime ceiling below 7 days), treat it as exit 30 |
| 10 | nothing; more than 10% is left |
| 20 | nothing to renew for; the stop conditions below decide |
| 30 | the renewal cap is spent (3 renewals, or one more full base budget would pass 1000 cycles or 7 days; those two ceilings come from the budget line alone, so they hold even if `patrol_base` is lost): ask the user for another budget with `ask_question`. This is the runaway backstop, not a finish: say which items are still open |

`monitor_start` is create-only, so every change after arming is a
`monitor_update`. Then end your turn.

Each cycle:

1. **`work_ledger_read` with `compact=true` first, every cycle.** It returns the
   conductor record and, per item, the status columns plus the derived
   `orphaned`, `stale` and `acceptance_concrete` flags — no events, acceptance or
   `accept_batch`. This one read replaces the whole transcript-reading cycle, and
   it stays small however many items the board holds. The full read (no
   `compact`) adds every field, the newest events and a ready-to-pipe
   `accept_batch`; take it, or `item_id=<id>` for one item, only when a `done`
   item needs its bar (step 3). **A wake turn that names one item** (a
   `[work-ledger wake] item=<id> status=<status>` line in the turn text, LOOP-17)
   may read that item alone — `work_ledger_read item_id=<id> compact=true` —
   instead of the whole board: the wake already says which item moved. A full read too large for the tool-result limit
   comes back trimmed with `truncated: true` and says what it left out. An item is never `stale` on the strength of silence alone: its worker
   also has to be not running, and its last word has to have left the next move
   with the worker, so a `done` item waiting on you is not flagged.
2. **Act on three statuses, and only three:**

   | status | what it means | what you do |
   |---|---|---|
   | `progress` | informational | nothing |
   | `done` | the worker CLAIMS acceptance is met | verify (step 3) |
   | `blocked` | an external dependency stopped the work | clear it or re-plan around it |
   | `question` | your own decision is needed | `session_send` the answer, `session_read_message` the reply |

   `blocked` and `question` differ by who must act. That is why they are separate
   values, and why you must not treat one as the other.
3. **Verify every `done` with the evaluator — never by reading the child's
   transcript and judging, and never by believing the claim.** Take the
   `accept_batch` from a full `work_ledger_read` (no `compact`), **keep only the entries
   whose item is currently `status: done`** — each entry carries that status, so
   the filter is a read of the document you already have — and pipe that filtered
   document through a **quoted heredoc**:

   ```bash
   python3 <this skill's dir>/scripts/accept_eval.py <<'ACCEPT_BATCH'
   <the accept_batch document, with every non-done and every placeholder entry removed>
   ACCEPT_BATCH
   ```

   **The filter is yours to apply, and it is not optional.** `accept_batch` is
   composed from every open item whose `acceptance` is not empty — whatever its
   status, and whether or not the condition's own values are filled in yet. It is
   the two-phase promotion seam, not a verdict gate. The evaluator
   answers a world-state question ("does this file exist", "are this PR's checks
   green"), and a worker that is still `progress` can have made that true early:
   a stub written before the real content, a PR that is green before the last
   commit. Evaluating that item returns a genuine `pass` on unfinished work, and
   recording it with `action=verdict` then `action=close` closes the item under
   the worker. A `done` is the worker saying the world-state now means what the
   condition says; only then is the evaluator's answer an acceptance. Never pipe
   the unfiltered document.

   **The heredoc is load-bearing, not style.** `acceptance` holds text you built
   from ingested content — an issue title, a file path a worker named — and a
   `file` path carrying a single quote would end a `'...'` string early and hand
   the rest of the value to the shell as a command, which `execute_bash` then runs
   after one approval. A heredoc whose delimiter is quoted (`<<'ACCEPT_BATCH'`) is
   the one form the shell copies to stdin without interpreting anything inside it.
   Never paste the document into a `printf '%s' '...'` or `echo '...'` argument,
   and never let the document contain a line that is exactly `ACCEPT_BATCH`.

   **Resolve `<this skill's dir>` from where this SKILL.md was actually loaded
   from** — the skill index names its absolute path. Do NOT hardcode a path under
   the default skills root: a `KIROCREW_HOME` override moves it, so on such an
   install that path does not exist and every evaluator call would fail before
   patrol ever ran.

   Evaluate **every `done` item in ONE call** — each invocation costs one
   approval prompt — then record each answer with `work_ledger_record`
   `action=verdict` (with `fails` when you are counting retries).

   Verdicts: `pass` / `fail` are final for this cycle. `pending` means keep
   waiting. `refused` means the spec asked for something the evaluator will not
   do — most often naming a command, which it does not accept from a spec at all.
   Re-express the condition as `pr_checks` (or ask the user for a purpose-built
   kind); never try to route around a refusal. `error` is a broken spec or
   environment — fix the spec or ask.

   **Two-phase acceptance is a manual omission, not a server filter.** A condition may
   name a value that only exists after the item starts — a PR number for
   `pr_checks` is the common case. Store the condition with the value marked TBD
   at `create`, tell the child in its seed to report the number through
   `work_report`'s `pr`, and **drop that item from the batch yourself until you have
   promoted the real value** — the server does not omit it, and a `pr` that is still
   `TBD` is an `error` verdict, not `pending`. **The worker's claimed `pr` is
   never read as the bar.** Promote it yourself with `work_ledger_record`
   `action=accept` once you have looked at it, and verify on the next cycle. A
   worker that could fill in its own acceptance could point it at anybody's
   already-green pull request, which is exactly why the claim and the condition
   are separate fields.

   **A `human_approval` item is verified by asking, and the ask is fragile.** The evaluator answers `pending` for it forever, so put the decision to the user with `ask_question`, which ends your turn. Leave the loop and its interval as they are: the `work-ledger` watch already makes a quiet cycle free, and the cycle after the user answers reads it.

   If the user says the card is gone, re-issue it. A report that the card vanished is not an answer.
4. `work_ledger_record` `action=close` with the item's `state` when an item is
   finally done with — that is what ends it. **Closing the item and closing
   its session happen together.** When a work item reaches a terminal verdict
   (accepted, rejected, abandoned/void) and its loop is stopped,
   `session_close` that child session in the same cycle — a finished worker
   has nothing left to re-arm. `session_close` archives (reopenable); it never
   deletes. Never close a child that still has a pending human question or an
   unmerged PR it is actively driving. `action=decide` records an
   instruction you want the worker to read out of `work_brief`; it is the ONE
   field the worker treats as an instruction, so keep it to a decision.
5. `session_read_message` for detail the record does not carry — a question's
   substance, a stall's shape. Never for a verdict, and never as the routine
   cycle read: the ledger is that.
6. **Say nothing unless there is a real signal.** An item passing acceptance,
   failing it, asking a question, or stalling. Never post "nothing changed".

**Shell exists for the two bundled scripts, not for work.** `execute_bash` is
granted so patrol can run `accept_eval.py` and `patrol_budget.py`. Running a work item's build, test, or fix
yourself through it is the boundary violation this skill exists to prevent — if
you need a command run to MAKE something true, that is a work item; the evaluator
only CHECKS what is already true.
