# Pipeline Conductor: dispatch mechanics, partitioning and the work-order brief

Reference for the `pipeline-conductor` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

### Dispatch mechanics

- `session_create` MUST pass the worker agent explicitly. An unset agent binds
  the worker to YOUR agent, which has no file-writing tool, and the entire batch
  then refuses the work with a plausible-sounding explanation of why it cannot
  edit files.
- Validate with ONE canary dispatch before a batch. A wrong agent or a broken
  brief costs one session that way, and the whole batch otherwise.
- Respect the session-create rate limit: 20 `session_create` calls per 5-minute
  window per caller (folders: 10). `max_in_flight` defaults to 32, so a
  full-width dispatch round CANNOT complete inside one window — plan two rounds,
  and remember that a create refused by the limiter is a post-claim failure, so
  unclaim per the rule above.
- Worker sessions must be granted **trust mode before seeding** — an unattended
  session stuck on an approval prompt runs zero turns; if you cannot grant it,
  tell the operator instead of seeding sessions that will hang.
- **Check the SHARED CHECKOUT once, before you cut worktrees from it.** One
  `git status --porcelain` there. It is the shared root of every worktree in the
  fleet, so it is exactly the state a conductor is supposed to inspect before
  pointing dozens of workers at it — and it can carry hundreds of staged files
  left by an operation that predates your run. Each worktree has its own index,
  so a worker committing from its own tree is unaffected; the exposure is that
  your seeds send workers to `cd` there for forge calls. Do NOT clean it: a
  reset there is destructive and belongs to whoever left it. Take the
  non-destructive half — the brief's commit ban — and report what you found.
  Staged state there is usually a whole-tree overlay that never moved HEAD
  (`git checkout <ref> -- .`): `<ref>`'s tree plus the files `<ref>` deleted.
  In the shared checkout, test `<remote>/<base>` first, then its reflog entries
  newest first, stopping at the first match or the first one HEAD is not an
  ancestor of. A `<commit>` matches when it is not HEAD, `git merge-base
  --is-ancestor HEAD <commit>` exits 0 and `git diff-index --cached --quiet
  --diff-filter=a <commit>` exits 0; an exit above 1 is unreadable, not a no.
  On a match, report the owner's repair: `git merge --ff-only --no-autostash
  --no-overwrite-ignore <commit>`, or `git reset --keep <commit>`, the same move
  except that it also unstages anything else staged. Both refuse rather than
  overwrite a local edit, and neither is yours to run.
  Re-read the base head rather than caching it, too: the default branch moves
  under a long run, and a worker preflighting against a remembered sha is
  preflighting against the past.
- Before commissioning a fix for a base-wide breakage, search open PRs for one
  that already exists. Fleet-wide breakage is visible to the wider community
  too, and two identical fixes waste a worker and a review lane.
- **RETASK a warmed-up worker rather than closing it and paying for a create.**
  A session that has already read the brief, learned the host's forge
  constraints and absorbed the reporting protocol is cheaper to re-point than to
  rebuild, and creates are rate-limited. One condition: the new item must have
  been through the claim gate first, or retasking just moves a duplicate
  dispatch to a new number.
- **Never hold a session idle waiting for a decision.** A session waiting is not
  a session working. The test for whether a park is legitimate is whether it
  holds context that would be expensive to rebuild — a completed survey already
  written down in its report does not qualify, so retask it and stand a fresh
  worker up if the decision goes the other way.
- **A list whose contents are DERIVABLE must be derived, never typed.** Pending
  is `claimed MINUS sessions MINUS released`, computed each cycle. A
  hand-maintained copy drifts in both directions at once and does so silently:
  items claimed and never dispatched fall out of it and become invisible, while
  items that were never claimed appear in it and get worked with no claim
  protecting them.
- **The issue → session mapping lives on disk because it answers a different
  question from the one you are holding in your head.** It answers *who owns
  this item now*; your memory of the dispatch answers *who sent me this
  message*. A report arriving from a session you already closed is not stale
  because it resembles the other stale ones — it is stale only if the mapping
  says a live worker owns that item. One lookup decides it, and a report that
  looks like the others is exactly where guessing costs a real dispatch.

### Partitioning one change across several workers

When one change is too large for one worker, split it by **exclusive file
ownership**: every file belongs to exactly one worker, and nobody edits outside
their own set — not a one-line mention, not a docstring cross-reference. Two
workers on one file is the merge-conflict version of two sessions on one item.

**Exclusive ownership removes merge conflicts. It does NOT remove a review-order
dependency, and that is the trap.** A premise-level reviewer counts a
mechanism's CONSUMERS in the base, so the PR that BUILDS a mechanism reads as
dead code until the PR that WIRES it has landed: nothing in the base invokes it,
the zero option is behaviorally identical, and the reviewer is right on the
evidence available to it. So a partition ships with a **merge ORDER**: the wiring
PR lands before or with the building PR.

The remedy for a block of that shape is sequencing, and sequencing is YOURS. A
worker must never move another worker's hunk into its own PR to satisfy a
reviewer — that dissolves the ownership split, creates the conflict the split
existed to prevent, and hides a review-order problem as a code change. Land the
wiring PR; the same reviewer's own grep then finds the consumers and the block
dissolves with neither PR changing content.

### The work-order brief (seed message skeleton)

Fill `{...}` from the spec; keep every clause — each one closes a failure mode:

> You own exactly ONE item: {item} on {repo}. Work autonomously; do not wait
> for a human; never ping the human directly — the conductor reports.
> PREFLIGHT (mandatory): view the item; check open PRs and worktrees for
> overlap — if anything already covers it, reply `STANDDOWN: <reason>` and
> stop. Never adopt another session's WIP.
> REPRO ADMISSION (`{verifier.repro_gate}`): expand this clause from the spec.
> In `pod_required` mode, keep the worktree byte-clean and run `kirocrew pod
> scenarios`, choose the closest shipped state, boot the UNMODIFIED worktree in
> that scenario, and drive the externally visible failing behavior through
> `pod api`, pod-e2e/Playwright, or another real product route. Run pod
> status/token/API commands through the worktree's `./.venv/bin/kirocrew` after
> provisioning: the globally installed binary may be sandbox-blind to the pod
> process's sockets and fail closed on ownership proof. If `playwright-cli`
> cannot launch on the host, the repository's own Playwright runner against the
> same live pod is equivalent evidence; record the engine and launch flags, and
> treat a missing REQUIRED engine (for example Safari/WebKit-specific behavior)
> as `missing=<capability>` rather than silently substituting Chromium.
> Record the scenario, exact probe, and failing observable. A unit test, direct
> import, simulated error, or post-fix friction note is NOT admission. No live pod red →
> `STANDDOWN: pod-repro-ineligible — <evidence>; missing=<capability>` and STOP
> with no edit, commit, or PR. Live red admitted → implement, then run the SAME
> pod trace green and tear the pod down to zero residue before `GREEN`.
> CONFIRM the mechanism before fixing: in `best_effort` mode, reproduce where
> cheap; wrong premise →
> `STANDDOWN: premise disproven — <evidence>`. A design decision →
> `PROPOSAL: <link>` (write the proposal on the item; do not build).
> IMPLEMENT in your own worktree (`{worktree_pattern}`, branch
> `{branch_pattern}` from `{default_branch}`): root-cause fix; regression test
> red-on-base and mutation-verified. TESTS: your own changed test files, BY
> PATH, and nothing else. Name the ban rather than implying it — no `make test`,
> no `tox`, no `nox`, no `run-tests`/`local-gate`/"run the gates" wrapper of any
> kind: a wrapper that escalates to the full suite satisfies the letter of a
> targeted-only brief. The ban is on suite wrappers, NOT on the push gate
> below — `preflight.py` and `push_guard.py` spawn only `git`, `gh` and the
> OS tree-kill tool, and run no test at all (`--commit` / `--squash` run the
> repository's own git hooks, as any commit does), so a targeted-test brief
> never licenses an unguarded push. Pass `-n0` **explicitly** on every run: omitting `-n`
> does not mean single process, it inherits whatever the project's pytest
> `addopts` sets, and `-n auto` is a common default. Canonical line —
> `timeout 900 python3 -m pytest -n0 <test file> -x -q </dev/null`. Do not
> substitute a small `-n <N>`: xdist workers contend for scheduling and are what
> starves under fleet load, and an explicit count also bypasses the memory
> budget `auto` is put through.
> HARNESS (this is what makes your evidence mean anything): before you cite any
> test run, check WHICH checkout it imports -- from your worktree, for a Python
> target, `python -c 'import <pkg>; print(<pkg>.__file__)'`. If that path lies
> OUTSIDE your worktree, the target runs one shared environment whose editable
> install points at the MAIN checkout's `src` and your worktree has no
> environment of its own: a bare `pytest` there imports MAIN's module, not the
> file you just edited, so set `PYTHONPATH=<your worktree>/src` on every run
> whose result you will cite. If the path lies INSIDE your worktree (a
> per-worktree environment), set nothing -- an unneeded `PYTHONPATH` can shadow
> the correct import. With the wrong import path a mutation applied to YOUR file
> cannot redden, and "the mutation did not redden" then reads as a weak test when
> the truth is a broken harness. So: **if a mutation does not redden, suspect the
> harness BEFORE you conclude the test is weak.** Beware the two subtler cases --
> where the base ALREADY has the behaviour your test asserts, a green suite can
> look partly correct for entirely the wrong reason.
> NEVER COMMIT FROM THE SHARED CHECKOUT. You may `cd` there for `gh` calls, but
> its index is not yours and may hold hundreds of staged files left by another
> operation, so one `git commit -a` there — or any commit after staging even a
> single file — sweeps that staged work into your PR.
> Each worktree has its own index; commit only from yours. Run nothing there
> that moves its HEAD or writes its index or files (merge, pull, reset, clean,
> checkout, restore, `gh pr checkout`, `gh repo sync`); report its state and
> leave the repair to its owner. Never `git stash` in any worktree: every
> worktree shares one stash list.
> REMOTES: `export GIT_TERMINAL_PROMPT=0` and confirm `gh auth setup-git` has
> run before any push — a bare https push does not use the CLI's token and hangs
> on an interactive prompt indefinitely. If a push exceeds ~2 minutes, time the
> actual pre-push hook over the real payload before naming a cause: process
> liveness cannot distinguish a credential prompt from a slow hook.
> PUSH GATE (mandatory, every push): the scripts live in `<gate>` =
> `<crew-home>/skills/kirocrew-dev/kirocrew-prepare-pr/scripts`, where `<crew-home>` is
> `KIROCREW_HOME` when set and `$HOME/.kiro/crew` otherwise. Invoke them through
> Kiro Crew's runtime interpreter the way Startup invokes `spec_check.py`, and
> quote the resolved path: on POSIX `"$KIROCREW_RUNTIME_PYTHON" -B
> "<gate>/preflight.py"`, on PowerShell `& $env:KIROCREW_RUNTIME_PYTHON -B
> "<gate>/preflight.py"`. `-B` preserves the desktop bundle's no-bytecode-write
> rule. Do NOT add `-I` here even though Startup passes it: `preflight.py` imports
> its sibling `push_guard`, and isolated mode drops the script's own directory
> from the import path, so `-I` turns the gate into a `ModuleNotFoundError` on
> every push.
> Run `preflight.py` before the first commit. Then before EVERY push confirm
> `git status --porcelain` is empty and run `<gate>/push_guard.py
> --base {default_branch} --max-ahead {max_commits}`, which refuses a stale base,
> a replayed upstream commit, anything staged that HEAD lacks, or a changed path
> the gate did not commit on this branch and no other author's pushed commit
> carries (a commit made by hand). When it lists such paths, read each diff: name
> the ones that are yours after `--` on the same command (that vouch rewrites
> nothing), and report any that are not.
> Pass `--max-ahead` explicitly and fill it from
> the spec, never from memory: the script defaults to 5, which is looser than
> most repositories' own PR commit-count gate, so omitting it lets a branch read
> `SAFE TO PUSH` and then fail that gate. Add `--require-single-on-base` only
> when you actually squashed to one commit with the gate's `--squash`; it
> asserts HEAD's only parent is `origin/<base>` and that the gate committed
> every path HEAD changes, and refuses a legitimate multi-commit branch or a
> squash made by hand.
> Commit and amend through the gate, by name: `<gate>/push_guard.py --commit -m
> "<subject>" -- <path>...` (or `-F <message file>`) and `--amend -- <path>...`,
> never `git commit -a` or a bare `git commit`: the index can hold paths you
> did not stage. Squash, if you squash, with `<gate>/push_guard.py --base {default_branch} --squash`
> after writing the message to `<git-dir>/prepare-pr-commit-msg-<branch>.txt`
> (`/` as `-`): it runs the checks above on the commits BEFORE squashing them
> (so never `--max-ahead {max_commits}` there), then commits your branch's tree,
> never an index, and `--require-single-on-base` then enforces the one commit.
> A `40` whose text is the commits-ahead refusal, on commits you authored, is
> answered once with the `--max-ahead N` it prints. Before any `git rebase
> --continue`, `<gate>/push_guard.py --check-index` must exit `0`.
> Read the exit code, do not just test for zero: `0` proceed; `30`/`40`/`41` the gate
> REFUSED, so do not push and report the code with the branch state and stderr (a
> git or network failure inside the gate is a `40` whose stderr names the failed
> command; a `41` prints the staged paths and their remedy — follow it); `64` is
> your own command-line mistake — fix it and retry; `2` the gate could not RUN —
> not a git repository, no git, or a missing script — so do not push and report
> `BLOCKED: push gate inoperative` with the code and stderr, because a worker
> whose sandbox cannot reach the scripts has to surface that once instead of
> stalling every item silently. A non-empty `git status --porcelain` is also a
> stop.
> Unstaged work and a stale base are what otherwise reach the
> PR and cost a review round to find what a git-only check catches in a second.
> PR: English body (What/Why/How/Tests/Other), `Closes #{n}`, full URL in
> your reply. Babysit to green (`monitor_start` ~300s, staggered off a round
> number so a dozen loops do not poll in lockstep, preferring REST over
> GraphQL/search — the whole fleet shares one account's rate limit). Fix every
> Critical/High; disposition every advisory explicitly; read reviewer JOB
> LOGS for the current head, not check conclusions; rebut with measurement,
> never assertion; before re-running any CI job ask what the re-run REPLACES,
> not what it retries; NEVER `/ai-review override` without the conductor's
> sign-off — a blocking finding you dispute is `BLOCKED: <evidence + 2-4
> options>`.
> ENUMERATE, do not spot-fix. For any guard, matcher, allow-list or detector you
> touch, list EVERY signature it is meant to cover and give a per-item verdict on
> which your change catches. The reported case is by construction the one the
> reporter already understood; the set is where the hazard lives, and a fix that
> merely makes the reported case work can make a worse one reachable. Where a
> second site LOOKS like a copy of the defect and is not, say so and say why —
> that is worth as much as finding one, and it is what stops a follow-up issue
> being filed against healthy code.
> MEASURE, do not read. Adding a candidate FORM and relaxing an acceptance
> PREDICATE are different changes and only one of them weakens a gate; you cannot
> tell them apart from the diff, so RUN it. Compare identifiers on BYTES against
> ground truth, never by eye — that is the only comparison that catches a
> homoglyph, a hyphen for an underscore, or trailing whitespace, and a silent
> authentication-false class deserves it precisely because nothing downstream
> will complain.
> PIN every residual you leave behind with a test asserting the CURRENT wrong
> answer, so it cannot be quietly forgotten and whoever closes it knows what
> success looks like. And prove your tests exercise YOUR change rather than
> something already true: stub the new path out and show the matrix reverts.
> A GATE OR POLICY BLOCK IS A VERDICT, NOT AN OBSTACLE. When a publication gate
> refuses your text, ask what the blocked token was FOR before reaching for an
> override — a gate firing on a detail nobody needs is a prompt to write more
> clearly, and satisfying it by rewriting beats bypassing it. When a tool policy
> refuses a path to evidence, DROP that line of evidence and cite the weaker
> source with its weakness labelled; never find a route that technically
> succeeds.
> REPORT with exactly one of six prefixes — `WORKING: / PR: / GREEN: /
> BLOCKED: / STANDDOWN: / PROPOSAL:` — and RE-STATE the prefix on EVERY later
> turn while this assignment is open (an unprefixed turn reads as "no status").
> Write it as BARE leading text: no bold, no italics, no list marker, no
> blockquote ahead of it. The tag is matched at the START of the message, so
> `**BLOCKED:**` can read as no status — and it does so on the one message you
> most need heard.
> Once you report `BLOCKED:`, KEEP that prefix on every turn until the ruling
> reaches you — do NOT switch back to `WORKING:` while you are on an escalation
> hold. The conductor samples your newest protocol message, so a `WORKING:` line
> posted after a `BLOCKED:` can overwrite the escalation before it is ever seen,
> and the ruling you are waiting for is then owed by nobody.
> GREEN must carry the PR URL, head SHA, and a 3-6 step plain-language summary.

### Sending an order to a worker

Every rule here closes a way an order fails SILENTLY, and silence is the failure
mode you are least able to detect.

**Before composing an order, re-read the last order you SENT that worker AND
confirm no newer report from it is pending.** Both halves are needed and they
fail differently. A new order that contradicts a standing one produces NOTHING:
a worker facing two of your instructions resolves toward the restrictive
reading, does not act, and tells you it did not act — which is correct behaviour
and your fault. And a ruling composed from a report that a newer report has
already superseded orders the worker to publish facts it has since disproved.
Re-reading your own last message catches the first; only checking the worker's
queue catches the second.

**A reversal must NAME what it reverses.** "Post the finding" does not obviously
outrank "do not comment on that PR" when both came from you and the earlier one
was framed as a rule. Revoke by name, in both directions — including when new
content flips a refusal you made on content grounds, which is the rule working
rather than you being inconsistent.

**Never state a ranked option as a PREFERENCE. State it as a CONDITION.** You do
not read the code, so your preferences are hypotheses and must be written as
hypotheses: *do X PROVIDED Y holds, and Y is checkable like this*. A preference
gets complied with; a condition gets tested — and a condition attached to your
own preference is repeatedly what catches the preference being wrong. Several
checkable conditions also beat one instruction: a single condition can only ever
produce a correct BLOCKED report, while a pair can find the narrower door that
lands the fix.

**Any instruction to publish evidence says: the reproduction must be
self-contained; never cite a local path.** A path on this host leaks an internal
directory into a permanent public thread and is useless to every reader who
cannot open it. Reproducibility is the intent; naming the path is a flawed
implementation of it, and the leak is worse than the shell-command case because
it is permanent and public.
