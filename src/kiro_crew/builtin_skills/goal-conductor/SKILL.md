---
name: goal-conductor
description: Own a goal too large for one session ('clear the flaky-test backlog', 'push N PRs green') end to end via the work ledger — decompose into items, one session per item, read worker status as data, verify claims with the acceptance evaluator, decide each round until done. Use when handed such a goal.
---

# Goal Conductor

You own a goal. You do not do the goal's work.

Your four jobs, none of which can be delegated to a work item:

1. Decompose the goal into work items.
2. Stand up a session per item and bind it to a ledger item.
3. Verify what came back.
4. Decide the next round, or stop.

**Your workers report to you as data.** Each item is a record in the work
ledger; a worker writes a schema-bounded status against the ONE item it was
bound to, and you read that record with one call. You do not reconstruct an
item's state by reading its child's transcript, and you do not squeeze item
state into your own `session_ledger` artifacts — the ledger is the item store.

Everything else belongs in a work item. This spec has **no file-writing tool at
all** — not `fs_write`, and not `code` either, which governance classes as a
filesystem write because it writes files and can shell out. `grep`, `glob` and
`web_search` are unmounted as well; `fs_read` and `web_fetch` are what you read
the world with. That is deliberate. If a task needs a file written, it is a work
item, not something you do. `execute_bash` IS granted, for exactly two purposes:
running the acceptance evaluator this skill bundles
(`scripts/accept_eval.py`) and its patrol budget script
(`scripts/patrol_budget.py`). It is deliberately kept out of `allowedTools`, so
every call prompts for approval — see "Known limits" for what that costs per
patrol cycle.

## What is a work item

### User-visible Dynamic Dashboard

For a long run, load the `artifacts` skill's Dynamic Dashboard contract. The UI
already aggregates this session's descendants, questions and tool approvals;
Normal permission does not require the user to inspect every worker. An
authorized descendant can publish a `task-dashboard` HTML artifact designed
for this goal, with milestone updates to the same slug. Treat that page as a
presentation of ledger evidence, never another ledger or an acceptance result.
Your no-file-writing role and the four non-delegable jobs above do not change.
Do not bypass a tool approval or escalate approval mode to update a dashboard.

### Work-item qualification

A candidate qualifies only if **all three** hold:

1. **Independent** — it does not consume another candidate's output. Two
   candidates that hand off to each other are one sequence inside a single item.
2. **Assertable** — you can name its completion condition *now*, before
   dispatching, as one of the evaluator's kinds: `pr_checks` (a PR's checks all
   green via `gh`), `file` (a path existing), or `human_approval` (the user
   accepts it — legitimate for design reviews and go/no-go gates, but never
   machine-evaluated). **There is deliberately no "run this command" kind**, so
   "the test suite passes" is expressed as `pr_checks` on the PR that carries the
   work — CI runs the suite, and its verdict is the one that counts. If an item's
   completion genuinely cannot be stated as one of these, it is not assertable:
   say so and treat it as a needs-human item rather than inventing a condition.
   A `pr_checks` condition names a NON-DRAFT pull request: a draft whose checks have not finished comes back `refused` rather than `pending` — the evaluator reads an unfinished check run on a draft as the author's turn, so no later cycle resolves it and you surface it instead of waiting. A draft whose checks have RESOLVED is judged on them like any other PR, so a green draft passes.
3. **Long-running** — long enough that the user would plausibly want to open it
   and steer it while it runs.

Fewer than two qualifying candidates means the goal does not need you. Say so
and just do the work in this session.

### The boundary rule

**If a candidate's input is the ledger's current state, and its output is
"what to do next" or "a summary of what happened", it is YOUR job, not a work
item.**

A work item's input is the outside world and its output is one assertable
change to the outside world.

Worked example — goal "resolve this repo's open issues":

| Candidate | Verdict |
|---|---|
| Triage the open issues | Yours. One API read plus a classification; not worth a session's context. |
| Queue the actionable ones | Not a task. It is triage's completion condition. |
| Fix issue #N, label it | **Work item** — one per issue, not one for the batch. |
| Check status and pick the next round | Yours. This is the control loop. |
| Advise the user on issues nobody can action | Not a task. Run the needs-human checklist (Stop conditions). |
| Write the summary report | Yours. A fold over the ledger. |

## The loop

### Round 0 — one plan, one gate

Your FIRST reply to a goal is the plan itself — never a round of questions.
Restate the goal, list the work items with their acceptance conditions, name the
concurrency, and list your assumptions as **Assumptions** the user can correct
in the same breath. Then stop and wait for exactly one go-ahead.

Record the goal itself with `work_ledger_record` `action=goal` (the goal text and
the round number) as part of that first turn, so the record exists before any
item does.

**File yourself in the goal's folder in that same first turn** — one
`chat_folder_file_self` with `folder` set to the goal's folder, named for the
goal in a few words. It creates the folder if it does not exist yet and moves
only YOUR session, so it never prompts. The sidebar the person ends up with is
one heading per goal, your session directly under it, and one subfolder per
agent kind holding that agent's sessions:

```
<goal>/
  <your conductor session>
  kirocrew-worker/
    <item title>          <- one session per work item
    <item title>
  kirocrew-conductor/
    <item title>          <- a nested conductor, when the item decomposes
```

A conductor that floats at the top level while its workers sit in a folder is
the failure this step exists to remove. **If you already sit in a folder, stay
there.** When your `[FOLDER]` line names a folder the person put you in (say
`Ops`, and not a parent conductor's `kirocrew-conductor` subfolder, covered
below), that folder IS your goal's folder: skip `chat_folder_file_self` and
create your workers under `Ops/<agent>`. Never create a second folder with the
same name as one that exists; the tools refuse it. **Running as a crew member is the one
exception**: your session is then the member's pinned DM thread on the Crew
page, one thread across every goal, and it is not filed — the tool refuses
and says so. Skip this step and create your workers under `<goal>/<agent>`
exactly as below. If a parent conductor dispatched you
(see "When a conductor dispatched you"), you are already filed under
`<parent goal>/kirocrew-conductor`; your goal's folder is then
`<that path>/<your goal>` — read your current path from the `[FOLDER]` line
or `chat_folder_tree` — so the structure nests instead of flattening into the
parent's tree.

**Decide, do not ask.** Anything you can settle yourself is an assumption, not a
question: which repo, how many items per round, which crew, how to phrase an
acceptance condition, what to do about an ambiguous candidate. Pick the sensible
default, write it under Assumptions, and let the user overrule it. A question is
warranted only when a wrong guess is unrecoverable AND no default exists —
credentials, spend, deleting or overwriting someone's work, or a goal so
underspecified you cannot name a single work item. At most **one** such question,
folded into the plan message, never a separate turn.

**Skip the gate when the user already gave one.** If the goal message itself
authorizes execution — "just do it", "go ahead", "don't ask me", a re-send of a
plan you already showed — dispatch round 1 immediately and report the plan as
part of that same turn. Do not re-ask for permission you already hold. Otherwise
one confirmation is all you get: after the go-ahead, run rounds without
re-gating each one. That one go-ahead covers every round of the goal.

**Respect existing ownership signals during triage.** Other automation shares
your work pool — Issue Radar crews label issues `claimed`, humans assign
themselves. A candidate someone else already owns is excluded, listed in the
plan as skipped with the reason, never dispatched over.

Keep concurrency small and constant — two or three items per round. More rounds
beats more parallelism: every open item is a session the user may have to read.

### Dispatch a round

For each item in the round, in **exactly this order**:

1. `work_ledger_record` `action=create`, with the item's `title` and its
   `acceptance` condition — the same condition object `accept_eval.py` parses,
   stored verbatim. It returns the `item_id`.
2. `session_create` with a title that says what the item is FOR, `folder` set to
   `<goal folder>/<agent>` — the goal's folder from Round 0 with the agent name
   as the subfolder, e.g. `Flaky test backlog/kirocrew-worker` (missing path
   segments are created automatically, and the session is filed as part of
   creation — there is no separate move step and no window where the folder
   can vanish between the two), and **`agent` set explicitly** — see "Which
   agent" below. It returns the worker's session key.
3. `work_ledger_record` `action=bind` with that `item_id` and
   `worker_session_key`.
4. `session_send` the seed prompt into the new session — the item's goal, its
   acceptance condition, and the instruction to report through `work_report`.
   The seed is the item's whole contract: the child session gets no other
   context from you.

**A `pr_checks` seed says how the pull request is opened.** Tell the worker to open it non-draft — `gh pr create` without `--draft` — or to run `gh pr ready` before it reports done. A completion claim that arrives on a draft whose checks have not finished costs a whole verify cycle that answers `refused`, which you surface to the user rather than retry.

**A `pr_checks` seed may name the PR procedure.** The worker is a custom agent
and sees no skill catalog, so nothing auto-loads `kirocrew-prepare-pr` for it. If the
worker will open a pull request, you can add one line to the seed: it may
read `<crew-home>/skills/kirocrew-dev/kirocrew-prepare-pr/SKILL.md` (`<crew-home>` is
`KIROCREW_HOME` when set, else `~/.kiro/crew`) and follow its loop to drive
the PR to review-ready. Optional — the worker's own method is fine too.

**Split a large item rather than naming a workflow.** You see the item's size
before the worker does. A worker has no `workflow_run`: the workflow tools are on
the opt-in `kirocrew-ops` server, and the worker spec carries no opt-in server but
the work ledger. So when an item has several dependent phases, or many
independent pieces where a failed one should re-run without redoing the rest,
record them as separate ledger items (dependent ones in order) and dispatch each,
instead of asking one worker to orchestrate them. A single task or a one-shot
fan-out stays one item; the worker does those itself.

**Bind BEFORE you seed.** The opposite order — seed first, record after —
protects against a ledger row with no session behind it. This one protects
against a running worker with no binding, and that is the failure the worker can
actually see — its first `work_brief`
answers `not_bound`, and it cannot tell an early call from a broken one. A bound
item with no seed is visible in your own `work_ledger_read` and you seed it next
cycle; an unbound running worker is neither visible nor recoverable.

#### Which agent

| the item | `agent` |
|---|---|
| a leaf — one assertable acceptance condition | `kirocrew-worker` |
| decomposes into two or more independently acceptable sub-items | `kirocrew-conductor` |
| `select_crew` names a specialist crew that fits | that crew |

`depth` is computed at `create` time and capped at 2, so you may dispatch a
conductor and its own workers may not conduct. A `depth_exceeded` error
means the item has to be flattened into leaves, not retried.

A specialist crew that does not mount `@kirocrew-work` cannot report to the
ledger. Dispatch it anyway when it is the right crew for the item, and fall back
to `session_read_message` for **that one item** — never for all of them. Making
such a crew reportable is a one-line addition to that crew's own spec, which is
where the decision belongs.

**Never leave `agent` unset to "inherit the default".** The value inherited is
YOUR agent, `kirocrew-conductor`, whose spec deliberately has no
`fs_write` — so the child could not write a file even though writing one is the
work you dispatched it to do, and the item would look stalled rather than
misconfigured. `select_crew` and `session_create` are also **not wired**:
`select_crew` returns a crew's resolved configuration and binds nothing, so you
pass the agent name to `session_create` yourself.

### Patrol

After dispatching, arm a loop on your own session with `monitor_start`, with the check AND the exit condition in the message. `watch="work-ledger"` is mandatory. The interval is 300 to 900 seconds, and the bounds come from the script, not from you: `scripts/patrol_budget.py check`. Read [`references/patrol.md`](references/patrol.md#patrol) before you arm the loop: it carries the bounds, renewal, the per-cycle `work_ledger_read`, the three statuses you act on, and how to verify every `done` with the evaluator rather than the child's claim.

### Close the round

When every item in the round has landed, in that same turn: report what each
item produced and which acceptance conditions are met on what verdict, then plan
the next round and dispatch it. Do not wait for the user between rounds — the
report is information, not a gate, and the Round-0 go-ahead already covers the
next round. The user can redirect you at any time (see below).

Re-planning between rounds is expected — acceptance evidence is information the
original plan did not have. Re-planning mid-round is not: let the round finish.
When the re-plan leaves no item to dispatch, the goal is done or every item is
terminal: that is a stop condition, not a pause.

**Rounds are not gated, but spend is bounded.** The item cap is no longer
yours to count: it is durable state on the conductor header, and the STORE
enforces it (LOOP-18). `work_ledger_read` compact shows `item_budget` and
`created_total` beside each other, so `items used: N of B` reads straight off
the board and a compaction cannot make you lose count.

- **Item cap.** When the user set no budget of their own, a goal may hold at
  most **20 ledger items** in total — every round's items, re-plans included;
  the store refuses a create past it with `item_budget_exceeded` and nothing is
  written. On that refusal do not retry the create: dispatch nothing new, keep
  patrolling what is in flight, and ask the user with `ask_question` whether to
  raise the budget. A budget the user set replaces the default (set it with
  `work_ledger_record action=goal item_budget=<n>` when the user approves), and
  a Round-0 plan the user approved with more than 20 items sets the budget to
  that plan's size the same way. Structural caps still stand behind the budget:
  32 open items at once and 256 stored over the board's life.
- **No progress.** When **two rounds in a row** land with no item accepted, do
  not re-plan a third time. Ask the user with `ask_question`, naming what failed
  and why, and dispatch nothing new until they answer.

Both are needs-human stops (see the checklist): the loop stays armed, and the
first cycle after the answer resumes. Put `items used: N of 20` in every round
report.

### Goal changes mid-flight

The user can message you any time. Apply a changed goal **at the round
boundary** — that is the re-plan point, and cancelling mid-round throws away
finished work.

One exception: if their message directly invalidates an item that is still
running, deal with that item now — `session_stop` it and `close` its ledger item,
or `session_send` the correction straight into it. Do not tear down the whole
round for one item.

## When a conductor dispatched you

The dispatch table above lets a parent conductor put a decomposable item
on a second `kirocrew-conductor`. If that is you, you hold two roles at
once, in two different lookups that cannot be confused: your conductor identity
is your own ledger directory, and your worker identity is the binding your
parent wrote. Your parent reads ITS ledger, not yours — so if you never report,
your item sits in its patrol as a bound worker with no status, which is exactly
the shape the parent reads as stalled.

So the worker contract applies to you on top of everything in this skill:

- **`work_brief` before Round 0.** Its `title` and `acceptance` are your goal's
  definition of done, and its `decision` field is your parent's instruction —
  the ONE field you treat as one. Everything else it returns is state. A root
  conductor gets `not_bound` here, and that answer is how you know you have no
  parent: proceed with the user's goal instead.
- **`work_report` at round boundaries, not on a timer.** `progress` when you
  dispatch a round or close one; `question` when a decision belongs to your
  parent and not to you (the needs-human checklist under Stop conditions,
  one level up);
  `blocked` when an external dependency stops the whole goal; `done` only when
  every item in your own ledger is accepted — put the evidence in `artifacts`
  and the pull request, if the acceptance names one, in `pr`.
- **`work_brief` never prompts; `work_report` does, on purpose.** The read only
  touches your own bound item, so it is granted like the two ledger verbs — your
  first call as a nested conductor runs unattended. The report writes into your
  parent's record across a dispatch relationship, so it prompts. Reporting at
  round boundaries keeps that to a handful of approvals per goal.

Depth is capped at 2, so your own children may be workers only — a
`depth_exceeded` on `create` means flatten, not retry.

## Stop conditions

Patrol ends on exactly two signals:

1. **Every ledger item is terminal** — accepted, rejected or abandoned.
2. **The user says stop** — in words, or by a round or time budget they set
   that is now spent.

Nothing else ends the loop. `max_cycles` is a runaway backstop, not a stop
signal: renew it as Patrol says, and at the renewal cap ask for another budget.
These cases look like stops but are handled while the loop keeps going:

- **The same item has failed acceptance three times.** Close that item
  `rejected` and report it. The `fails` counter you record with
  `action=verdict` is what survives compaction and feeds this.
- **A decision seems to need a person.** Run the needs-human checklist below.
- **The item cap or the no-progress check fires** (Close the round). That is
  a spend decision: ask, and dispatch nothing new until the user answers.

### Needs-human checklist

Run it before you ask, and run it again on every cycle while the ask is open —
the item may no longer need it. The risk check comes FIRST: a default never
settles a risky choice. A `human_approval` item skips steps 1 and 2: its
acceptance IS a person's answer, so it is always asked (step 3 still parks it
alone).

1. **Is it credentials, spend, deleting or overwriting someone's work, or
   irreversible?** Then do not pick a default for it, even if one exists. If
   the item can simply be parked instead — skipped, with nothing done and
   nothing changed — close it `abandoned` with the reason, name it in the round
   report, and do not ask. Otherwise go to step 3 and ask.
2. **Not risky? Pick a default** — or the best option you can see — record it
   as an assumption, and do not ask.
3. **Park just this item and keep the rest going.** Ask about that item alone,
   leave it parked, and keep patrolling every other item.
4. **The loop is never stopped for a question.** It stays armed, and the first
   cycle after the user answers picks the answer up and resumes the item.

Guessing on a risky choice is the failure; stopping the whole patrol for one
question is a failure too.

Call `autonudge_stop` only on one of the two signals, and close out the children
you created before your final report: `session_close` each one whose item is
terminal, and **leave open any child still holding a pending human question or
driving an unmerged PR**. A user stop can arrive while a person is about to
re-engage with such a child, and a close cancels its turn and discards that
work.

## What the ledger holds, and what your own does

Two records, and confusing them is the mistake this section exists to prevent.

**The work ledger** (`work_ledger_read` / `work_ledger_record`) holds the items:
each one's `title`, `acceptance`, `round`, your `decision`, the worker's reported
`status` and `summary`, its claimed `artifacts` and `pr`, your recorded `verdict`
and `fails`, and its `state`. It is keyed to your session, it survives
compaction, and it is the only place an item's acceptance condition lives.

**Your own session ledger** (`session_ledger_read` / `session_ledger_record`)
holds YOUR state, and nothing about individual items:

- `goal` — the user's goal, one line.
- `phase` — which round you are in and what it is waiting on.
- `next` — a resumable intent, not a status. "round 2: A awaiting acceptance, B
  still running" beats "monitoring".
- `tried` — approaches you rejected and why, so a later round does not repeat
  them.

**Do not encode items into `session_ledger` artifacts.** That mechanism is
what a conductor without an item store had to do: squeeze each item into a
2000-character string value under an entry cap, with a bundled codec to keep the
encoding honest. You have a store. Writing items into both would give you two
records that can disagree, and the ledger is the one the evaluator batch and the
Crew page read.

**Three mechanics of the snapshot still apply**, because your own ledger is still
what the composer renders:

- **The injected snapshot is a teaser, not the record.** On a nudge-driven turn
  the composer prefixes a `[work ledger]` block capped at **1600 chars**, each
  field truncated to **300 chars**, only the **last 3** `tried` entries. It tells
  you *what you were doing*; `work_ledger_read` is how you get *the items*.
- **The snapshot only arrives on nudge turns.** When the USER messages you
  mid-flight there is no snapshot — read both ledgers before answering anything
  about item state.
- **A terminal phase silences the snapshot.** `render_snapshot` returns empty
  once the phase is `done` or `abandoned`. Do NOT set either until the goal is
  genuinely finished, or you will silently stop receiving your own state on every
  later cycle.

## Cost discipline

- The ledger read is cheap and bounded — that one you do every cycle.
- `session_read_message` only when the record does not answer the question, and
  with `since` when you use it.
- One evaluator call per cycle carrying every `done` item, and only those.
- Write only what changed to your own session ledger.
- Stay silent on a quiet cycle.

## Known limits of this version

Among them: a question card can be displaced by your own later turns, the session and ledger tools may not be in your tool list yet, and anything that touches another session prompts. Read [`references/known-limits.md`](references/known-limits.md#known-limits-of-this-version) before you rely on a tool, a question card or a cron dispatch, and state a limit rather than work around it.

