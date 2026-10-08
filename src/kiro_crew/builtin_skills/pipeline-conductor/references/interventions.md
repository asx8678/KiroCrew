# Pipeline Conductor: the intervention ladder and credit budgets

Reference for the `pipeline-conductor` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Intervention ladder (looping / self-doubt / wasted time)

Signals: `IDLE` twice in a row; same tag across ~5 cycles with rising turn
count; credit burn with no ledger transition; ERR recurring after resume.

1. **Nudge** (`session_send`): restate the next step + protocol requirement.
2. **Inspect** — `spawn_run` ONE bounded inspector and bound it in the TASK
   TEXT. `spawn_run` takes no `allowed_tools` parameter, and the underlying
   field is ignored for ACP-backed agents, so read-only cannot be a property of
   the spawn. Pin a read-only AGENT instead (`agent=` a spec with no write
   tool), say "read only; modify nothing" explicitly, and treat any write the
   inspector reports as an escaped constraint to raise with the operator. Task:
   *"Read the tail of session {key}
   (session_read_message) and the state of PR #{n} on {repo} (web_fetch the PR
   page). Return one verdict — healthy-slow | looping | blocked-misclassified
   | premise-wrong — plus two sentences of evidence. Do not modify anything."*
3. **Rule** on the verdict:
   - `healthy-slow` → extend; note the expected completion signal.
   - `looping` → `session_stop`, re-dispatch a FRESH session with a sharpened
     brief naming the loop (context poisoning rarely self-heals).
   - `blocked-misclassified` → adjudicate it yourself as if BLOCKED.
   - `premise-wrong` → **open-issue mode**: the worker files an issue with the
     evidence and partial diff, descopes the PR to what is defensibly green,
     dispositions the rest as deferred-with-cross-reference, drives the
     narrowed PR green. A decorative fix is worse than no fix.
4. **Reclaim** on SLA breach (no event for 3× `idle_alert_secs`): mark
   reclaimed, re-queue or skip with evidence, close the session.

Two sessions on one item: **decide ownership once** — adopt one, fully stand
down the other. Two owners politely yielding to each other is a deadlock.

## Credit budgets

Roughly every 5 cycles, for items with open sessions:
`credit_spend.py --slots <current,previous...> --budget
{credit_budget_per_item}`.

- `within` → nothing to do.
- `exhausted` → **burn review**, recorded like an adjudication:
  - *Progressing* (PR open, review converging, ledger transitions happening) →
    top-up with a stated size + rationale.
  - *Thrashing* (no transitions, looping signals) → NO top-up — stop, then
    sharpened re-dispatch, open-issue mode, or skip with evidence. Exhaustion
    on a non-moving item is a defect signal, not a billing event.
  - *Blocked on external* → park the item with the dependency recorded and the
    claim released (parked time burns nothing).
  - More than `topup_ceiling` top-ups → escalate to the human with the burn
    history.
- `unmetered` → treat spend as UNKNOWN, not zero — say so in the ledger and
  lean on the time-based signals instead.
- `truncated` → the under-budget answer is incomplete: older shards were skipped
  (`--max-shards`), a shard was unreadable, or a matched row was corrupt. Re-run
  without the bound; if it persists without one, the usage data itself is
  damaged — treat spend as UNKNOWN like `unmetered`, and do not read it as within
  budget.
