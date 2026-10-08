# Prepare PR: Phase 4, landing, and a red main

Reference for the `kirocrew-prepare-pr` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

### Phase 4 — Land it

**Converged** — `pr_status.py` = 0 **and** no unanswered concern remains (re-check
step 4). Unless the user put a hold on the merge, arm auto-merge:
`python3 $SKILL_DIR/scripts/enable_automerge.py <pr#>` — idempotent; exit **20**
(no permission, repo setting, method not allowed) is a note, not a blocker; an
outside author asks a maintainer to merge.

**Keep the same loop running until the PR merges** (24-hour budget):

- **Conflict or behind base** → Phase 1 rebase, related tests, push with the
  lease, then re-arm auto-merge — a push clears it and may dismiss approvals;
  check the approval names the new head and say so when it does not.
- **A new red** after the base moved → triage it as Phase 3 exit 20.
- **Merge queue ejection** (once the queue is on) → read the group's failing run,
  fix it, or rerun it once after *Before you rerun a red test*, then re-arm.
- **Merged** → report and call `autonudge_stop`.

**Report:** the full PR URL, one-line status, commit SHA, whether auto-merge armed
or why not, **every override** (lane, span, SHA, reason), every red-main fix PR
opened, and any Low/nit left on purpose **plus how each was answered**.

**Escalate** only on the four pause reasons under "Iteration budget", or on a spent
budget with the PR still open. Hand over: what is red and why, unresolved
Critical/High, the `pr_status.py` and `--rounds` output, and the PR's full URL.

A recurring-span retrospective's disposition names the span and its hit count
in a `> ` line.

- **A fix that narrows one branch of a fallback or resolution chain must come with a table of every branch** and why each is now correct.
- **Never decline a reviewer's wider scope without a failing test proving the narrower scope is sufficient.**
- **Widening a fix is itself a code change** — re-check the widened sites for the OPPOSITE failure mode.

### When main is red

A red check is main's, not yours, when the same check fails on `origin/<base>`:
read `gh run list --branch <base> --event push --workflow <file> --limit 5`, open
the newest red run's failing log, or reproduce the node id on a clean
`origin/<base>` worktree. A `cancelled` run is not a red; a runner or network
outage is a rerun later, not a code fix.

1. **Is someone fixing it?** Search open PRs and issues for the test id, file or
   error line (`gh pr list --state open --search "<term>"`, the same for
   `gh issue list`, plus the open `ratchet-audit` issue). Found one → link it from
   a PR comment and wait for it inside the loop.
2. **Nobody is** → open a `tier:T1` issue (symptom, failing node id, first bad main
   commit if known, log excerpt; label it `tier:T1` if you can, else the Captain
   tiers it). Fix it on a new branch cut from fresh `origin/<base>`, smallest fix,
   and run this skill's full loop on that PR — it is a `fix` PR, so it carries
   `## Pattern harvest`.
3. **The original PR waits.** No push and no override for that red. When the fix
   merges, Phase 1 rebases onto it; push, re-run, continue to green, then Phase 4.
4. A fix that needs a design decision is a pause reason, not a T1.
