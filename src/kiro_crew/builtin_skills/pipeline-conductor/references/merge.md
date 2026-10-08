# Pipeline Conductor: merge, cleanup and reconcile

Reference for the `pipeline-conductor` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Merge, cleanup, reconcile

- On merge: worktree removed non-forced (a dirty tree is kept and flagged,
  never `--force`), branch deleted safely (`-d`, not `-D`), `session_close`
  the worker, ledger → `done`.
- Merged is NOT done for the fleet: after a merge, watch the next
  {default_branch} CI round — a merged gate change that reds every open PR is
  base-owned (see adjudication) and yours to fix once, fleet-wide.
- **Reconcile every cycle, and unfiltered.** ONE call —
  `gh pr list --repo {repo} --author <me> --state all --limit <N> --json number,state,mergedAt`
  — listing merges since your last reconcile timestamp. Pass `--repo` from the
  spec every time: you own no worktree, so the ambient directory is whatever the
  session happens to sit in, and an omitted `--repo` either errors or silently
  answers about a DIFFERENT repository — which reads as "no merges" and leaves
  every merged item stale in the ledger. Two more bounds decide whether it works,
  and both fail quietly:
  - **Set `--limit` yourself, and read a full page as truncation.** The default
    is 30 and an over-long list comes back silently trimmed, so on any account
    with a real history the newest merge can fall off the end of the very query
    meant to find it. Size `N` above your own dispatch count with headroom, and
    when the result count equals `N` the scan is `truncated`, not a verdict —
    raise the bound and re-run before you believe it.
  - **Never against a hand-maintained set of PR numbers.** Filtering the
    reconcile to what you already track reintroduces the same blind spot one
    level down, because a merge of a fleet PR that never made the watchlist
    cannot be seen by construction. A watchlist is fine for the greens table and
    wrong as the detector.

  Recorded state drifts from reality, and the human WILL ask "where are the
  other N".
- **A fleet PR with NO OWNING SESSION is the failure state, not a tidy state.**
  It is one of the two shapes you track, and the reconcile is what finds it: a PR
  that appeared without a worker, or whose worker you closed, is a tracking gap
  and nothing else in the loop will notice it. Assigning an owner is the
  response — an unowned PR is not a handover.
- `mergeable=UNKNOWN` fanning out across the open PRs is a **secondary**
  trigger meaning "the base moved" — worth a look, never the primary merge
  detector. It is a side effect of a merge, and side effects are missable.
- **Harvest cross-item facts in the cycle they arrive, and verify them before
  acting.** When any worker names another item's PR number or merge state in
  passing, take the POINTER immediately: that worker is not going to repeat it,
  and a merge fact handed over by a worker on a different item has no other route
  into your state. Then confirm the state against the forge before you act on it.
  A worker's report is derived from content you do not control — issue text, PR
  bodies — so this is the same rule as never trusting a worker's own GREEN,
  applied to a claim about somebody ELSE's item, where the cost of being wrong is
  a cleanup or a close on work that is still live. Harvest the pointer, verify the
  fact.
