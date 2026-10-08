# Pipeline Conductor: known limits

Reference for the `pipeline-conductor` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Known limits (state them, don't hide them)

- **The probe answers COLUMN 1 only.** It is a transcript classifier that makes
  no subprocess call and no network call — deliberately, and that invariant is
  load-bearing — so it knows nothing about a PR's or an item's real state. Its
  `GREEN` / `PR` / `TERMINAL` tags are the WORKER'S CLAIM, not a forge reading,
  which is exactly why independent green verification is a separate step. Column
  2 therefore costs one forge query per item you actually need it for, and the
  probe cannot batch it for you. Do not read a tag as a verdict about the PR.
- The probe also prints only FIRING lines, so a session that is quietly working
  produces no line at all. Absence of a line is not a row you have checked —
  reconcile the watched set against the ledger's open sessions rather than
  reading silence as health.

- `execute_bash` (which is EVERY script call — preflight, probe, credit rollup),
  `session_send`, `session_stop`, `session_close` and `spawn_run` (the inspector)
  are mounted but never auto-approved: `allowedTools` cannot match arguments, so
  trusting the bundled scripts would mean trusting arbitrary shell. Unattended
  operation therefore requires the operator to arm THIS session in trust mode
  (same "trust before seed" rule as the workers) — without it the patrol stalls
  on its first probe, not on its first intervention.
- Credit metering covers dashboard-session turns; `spawn_run` inspector turns
  and non-chat sessions burn invisibly (`unmetered` verdict exists for a
  reason).
- You cannot detect your own patrol loop's death from the inside — see outage
  recovery for what the procedure bounds and what it cannot close.
- One spec = one repo (M0). Multi-repo is a per-repo spec each, per the design
  doc's template seams.
- GitHub labels/assignees remain the cross-operator lock; your ledger is a
  cache, never the authority, on anything another operator can also touch.
