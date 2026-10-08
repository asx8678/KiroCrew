# Prepare PR: dispositions

Reference for the `kirocrew-prepare-pr` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Dispositions — every concern gets exactly one

Answering is prose work. It never needs a push and never widens the diff.

| Disposition | Use when | Must contain |
|---|---|---|
| `fixed` | you changed the code | the change and the SHA |
| `rebutted` | the code stays correct as-is | the evidence it does not hold, **or** the reasoning it is disproportional |
| `accepted-and-deferred` | the work is already decided, just out of scope here — unlike `needs-a-decision`, nothing is being asked | why, plus an issue whose body names a task someone can pick up. The issue MUST carry the `deferred-finding` label, an assignee (the owner) and a `Due: YYYY-MM-DD` line in its body — the Disposition Deferral Check replies to a disposition whose issue lacks any of the three. The Captain tiers it like any other issue. Note the server-side asymmetry: the GPT lane's convergence rules do not accept a deferral as a ruling on a security / data-loss / corruption finding, so a deferred one of those is re-raised every round until fixed, rebutted as not-a-defect, or overridden |
| `needs-a-decision` | the outcome depends on a maintainer ruling | the question, put to the maintainer directly — do **not** file an issue for it |

**What must be answered:**

- Non-PASS verdicts from the **whole-design lanes** — Design, First Principles, UX: every Watch item, Suggestion and Subtraction, each with its own `target=design` / `target=first-principles` / `target=ux` comment naming its `span=`. Their **BLOCK** verdict blocks readiness, and so does an **unanswered CONCERNS** for the current head: `pr_status.py` exits `20` on it even when the rollup is green, and it clears the moment one `target=<lane> head=<current sha>` disposition exists. PASS is advisory and must still be answered. `pr_findings.py` prints each item with its `span=` and `Clears when:` line, above the line-level findings.
- Non-blocking observations in the GPT / Opus bodies.
- One-way-door concerns from Design Review — fix or justify in writing.
- Human review comments and inline threads.

**Whole-design lanes outrank line-level lanes in triage order.** Every round,
read the Design / First Principles / UX verdicts first, decide the shape the round
ends with, then triage GPT/Opus findings against that shape.

**Per concern, individually.** Never one blanket line for a batch. Reply in the
thread when it is a thread, as a PR comment when it is a top-level bot verdict, and
resolve what you addressed.

### Write the disposition for the ledger, not for a reader

The remote GPT lane's **ADJUDICATION LEDGER** keeps only the marker,
lines beginning `> `, and `- **title**` bullets: **twelve lines at most** per
comment. Put rationale in `> ` lines; other prose is for humans only.

- Rebut the finding's class with code evidence, not just its current location;
  a recorded tradeoff applies wherever that same class moves, not to new defects.
- For a security-class rebuttal, argue *not a defect*, not *disproportional*.
  Security/data-loss/corruption cannot converge by deferral or by accepting a
  real defect as too costly.

### Overriding a false positive or over-engineering

A **writer** of the repository may record a judgment that turns a lane green for
one head:

```
/ai-review override <fable|gpt|design|ux|first-principles|scope|all> <current-head-sha>: <one-sentence reason>
```

`fable` is the Opus lane; `scope` is Security Scope Review. Decide it yourself —
do not ask — when all four hold:

1. You verified against the code that the finding is a false positive, or a
   demand the three questions judge disproportional (over-engineering).
2. The `rebutted` disposition with its evidence is already posted.
3. It is not a security, data-loss or corruption finding (any lane, including
   the ones the GPT lane fences), a crash, or a removed guard. A real one gets
   fixed. One you judge *not a defect* is still a human's call: the override
   record is read as proof a person checked it, so draft the `> ` rationale and
   the one-line reason and ask a writer to verify and post it.
4. Your account has write access. Otherwise (an outside author) post the
   disposition and ask a maintainer, with the one-line reason ready to paste.

Start the reason with `agent:` so a reader can tell it from a person's ruling,
and post it with `gh pr comment <n> --body "<the command>"`, naming one lane
(`all` only when one ruling truly covers every lane). It binds that SHA alone: a
new push needs a fresh judgment. Fork lanes honour it like same-repo ones. **Report
every override afterwards** — lane, span, SHA and reason — in the Phase 4 report.
