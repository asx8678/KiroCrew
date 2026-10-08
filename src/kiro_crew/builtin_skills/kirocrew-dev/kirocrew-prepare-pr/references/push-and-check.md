# Prepare PR: Phase 3, push and check

Reference for the `kirocrew-prepare-pr` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

### Phase 3 — Push & check

**Once the PR is open, only five things justify a new push:** a CI red, a review
finding, **a defect in the diff this PR already carries**, **`green_age.py` exit
30**, or a rebase onto a base that now carries the red-main fix or clears a
conflict. Anything else — an improvement, a new surface, an adjacent fix — goes to
a follow-up branch.

**Do not push while the previous head still has runs in flight** — amend into the
pending head. **Once you have decided to change code, cancel the old head's
in-flight runs BEFORE you edit**, after harvesting what you need (the failing log,
each reviewer lane's verdict — a cancelled lane never posts):

```bash
OLD_SHA=$(gh pr view <pr#> --repo <owner>/<repo> --json headRefOid --jq .headRefOid)
gh api "repos/<owner>/<repo>/actions/runs?head_sha=$OLD_SHA&per_page=100" \
  --jq '.workflow_runs[] | select(.status=="queued" or .status=="in_progress") | .id' \
  | while read -r run_id; do gh run cancel "$run_id" --repo <owner>/<repo>; done
```

Leave a reviewer lane running while its verdict still decides *whether* to fix; a
flake gets a rerun after *Before you rerun a red test* below, not a cancel.

**Before you rerun a red test.** A flaky test is a defect someone owns, so a rerun
comes after a record, never instead of one:

- **Prove the red is not yours.** Reproduce the exact node id on a clean
  `origin/<base>` worktree (`kirocrew-worktree-dev`), or show the same node id red on
  `main` or on another head in CI history (a Windows- or macOS-only red cannot be
  reproduced from Linux). A red only your branch shows is yours to fix.
- **Record it in the flake ledger**: the open issues titled `Flaky: <test name> ...`,
  labelled `area: tests`. Search for the test's function name
  (`gh issue list --state open --search "<test name> in:title"`). Comment on the match
  with the run URL, the OS and the failing line, or open one in that form with the same
  three facts.
- **Rerun at most once**, only what failed (`gh run rerun <run-id> --failed`, or
  `--job <job-id>`).
- **A test with two or more ledger reports in 14 days is fixed before more feature work
  lands on top of it**, not rerun again.

1. **Push only the reviewed commit.** Require a clean index/worktree and fail closed unless `[ "$(git rev-parse HEAD)" = "$REVIEWED_SHA" ]`; any intervening mutation returns to Phase 2. Run the post-squash guard (`single_commit` only): `python3 $SKILL_DIR/scripts/push_guard.py --base <base> --require-single-on-base` — **0** safe, **40** do NOT push (read each diff; name yours after `--`), **41** follow the remedy, **2** env.

   **SHA-pinned force-with-lease.** Record `LEASE_SHA=$(git rev-parse origin/<branch>)` at iteration start, BEFORE Phase 1's fetch. **First push (no `origin/<branch>`): skip the clobber check and `git push -u origin <branch>`.** Otherwise check the pre-squash HEAD: `git merge-base --is-ancestor origin/<branch> HEAD` — if it fails, a maintainer commit is on the remote that local history never had; STOP, re-sync, re-include it. Do not re-run that check after the squash. Then `git push --force-with-lease=<branch>:$LEASE_SHA origin <branch>`.

2. **Create/update the PR** from Phase 1 step 5's body. Run `diff_signals.py --check-body` on the finished file **before** `gh` reads it. `<body>` below is the checked file, `$(git rev-parse --absolute-git-dir)/prepare-pr-body.md` — never a second copy. New → `gh pr create --base <base> --head <branch> --title "<CC title>" --body-file <body>`, plus one `--attach <path>` per evidence file (see *Screenshots*). Existing → **regenerate the whole body from the current diff**, then `gh pr edit --body-file <body>` — **BEFORE step 1's push**: the review lanes run on `opened`/`synchronize`, never on `edited`. If `gh` fails (a GraphQL error or rate limit), use the REST fallback in `references/ci.md`. Verify the body landed.

   **Then report the PR's full `https://.../pull/<n>` URL in your chat message.**
   **Prepare-only stops here** after one `pr_status.py` snapshot.

3. **Record dispositions.** For each fixed/rebutted GPT finding, post one comment
   beginning `<!-- ai-review-disposition target=gpt head=<prior-reviewed-sha> -->`.
   The `head=` scopes the ruling to the commit it judged, not the new fix SHA.
   Name its exact printed `span=<id>` on the marker or a `- **...**` title bullet,
   never in quoted evidence. Include outcome and rationale in `> ` lines.
   **One comment covers exactly one lane, and one rationale covers exactly one finding.**
   Design, UX and First Principles use their own `target=` and one comment per item.

   `pr_status.py` and the server's `--disposition-gate` reject multiple spans,
   multiple finding-title bullets, cross-lane or nonexistent spans, or no span
   when that lane has live findings. Edit or delete an invalid comment, not code;
   the readiness self-heal sweep picks it up within about 15 minutes, or run
   `gh workflow run pr-readiness.yml -f pr=<n> -f sha=<head>`.

   A disposition can downgrade repeats but never waives a new defect. Do not instruct
   the next reviewer, and never treat it as the current-SHA-scoped human override.
   Optional `self-added: yes` and `mechanism: <one line>` lines go right after the
   marker, OUTSIDE `> `.

4. **Answer every open concern.** Enumerate what is outstanding, not just what is red:
   ```bash
   gh pr view <pr#> --json comments,reviews \
     --jq '(.comments[]|"COMMENT \(.author.login): \(.body[0:200])"),(.reviews[]|"REVIEW \(.author.login) [\(.state)]: \(.body[0:200])")'
   ```
   plus `pr_findings.py` for unresolved inline threads. Post a disposition for every
   item that is not a PASS and not already answered; resolve what you addressed.

5. **Check the green, then poll.** Each cycle,
   `python3 $SKILL_DIR/scripts/green_age.py --pr <n>` first. **0** → nothing to do.
   **2** → keep polling, and after **three consecutive** 2s report the reason and
   hand the PR over. **30** → re-sync through **Phase 1**, run only the related
   tests for the files the line names, push with the SHA-pinned lease, and post ONE
   comment: `Rebased: main moved in <files> since this head went green (<old-base> -> <new-base>); CI re-running.`
   Exit 30 does NOT override the no-push-while-runs-are-in-flight rule above. After
   three consecutive green-age re-syncs on one PR, hand it to the user.

   Then `python3 $SKILL_DIR/scripts/pr_status.py <pr#> --reviewers <profile reviewer names>`.
   **Always pin the fleet** (Kiro Crew: `--reviewers gpt,opus`): bare `pr_status.py`
   runs discovery mode, where a lane that never posted passes silently.

   - **0** → Phase 4.
   - **20** → run `pr_findings.py` and **TRIAGE before re-pushing**. An `unanswered CONCERNS from <LANE>` reason is cleared by POSTING dispositions, not by pushing. Before reading a red as live, dedupe the check runs to the newest per name: a force-push leaves cancelled twins on old heads. **(a) CI/build/test failure** → read `gh run view <run-id> --log-failed`. If the same check also fails on main, follow *When main is red*. Otherwise, once you decide to fix, cancel the head's in-flight runs, reproduce the **exact failing node ids** locally, and fix the **root cause**; a flake confirmed and recorded per *Before you rerun a red test* gets `gh run rerun <run-id> --failed` (or `--job <job-id>`) once, never a whole-run replay. **(b) Review finding** → whole-design verdicts first, then the three questions. For Kiro Crew Opus-family or GPT 6.1 findings that need code changes, MUST execute [Review repair routing](../SKILL.md#review-repair-routing): delegate the minimal fix and self-review to the model-pinned subagent, then verify in the parent. Otherwise rebut with evidence (never dismiss a CodeQL alert merely to pass), override it per *Overriding a false positive*, or ask a maintainer. **(c) Conflict / behind base** → Phase 1 handles it. Then **loop back to Phase 1** → 2 → 3 carrying those fixes.
   - **10** → still running. In a chat slot, load `kirocrew-core::monitor_start`
     through `tool_search`, request a finite same-session loop, then END THE TURN.
     Slot-less subagent, cron, webhook and task-runner turns cannot arm one: use
     bounded in-turn `wait` + re-poll and disclose that fallback.

     ```
     monitor_start(
       message="Check https://github.com/owner/repo/pull/123 with green_age.py "
               "--pr 123 then pr_status.py --reviewers <profile reviewer names>. "
               "green_age 30: re-sync, run the related tests, push, post one "
               "Rebased comment. pr_status 10: stay silent. Exit 20: read "
               "pr_findings.py and triage; a red main follows When main is red; "
               "Kiro Crew AI repairs MUST follow kirocrew-prepare-pr Review repair routing "
               "with model-pinned subagents, then parent verification and Phases "
               "1 -> 2 -> 3. Push only if authorized. Exit 0: Phase 4, arm "
               "auto-merge, keep watching; on a conflict rebase, push, re-arm. "
               "Merged, terminal state, user stop, blocker or spent budget: "
               "report the outcome, overrides and open findings, then call "
               "autonudge_stop.",
       interval_secs=300, max_cycles=280, max_runtime_secs=86400, gate=False,
       banner="kirocrew-prepare-pr: polling PR #123")
     ```

     Replace the example URL with the real one. Keep `gate=False`: comments and
     advisory findings are outside the typed provider's evidence. Omit `banner` on
     Slack/Discord/Webex. Loop mechanics belong to the `babysit` skill.

     A create-only refusal means a loop may already be active: inspect it on a
     later turn; never start another driver beside it. Missing hosting context
     permits bounded wait/poll. A retained-stop refusal needs the owner, not a
     retry. A manual pause or user stop is preserved; only an authorized budget
     increase revives a budget-paused loop.

     An acknowledgement is only a pending request. END THE TURN so it can apply;
     do not retry because no loop is visible before the turn ends. On a later
     turn verify it with `monitor_inspect()` or
     `python3 $SKILL_DIR/scripts/monitor_armed.py --pr <n>` (**0** armed, **20**
     not, **2** unreadable — not proof of absence), and confirm `cycle_count`
     advances. Keep pending cycles to one status poll.
