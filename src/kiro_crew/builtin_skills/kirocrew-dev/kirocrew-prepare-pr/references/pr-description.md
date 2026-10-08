# Prepare PR: the PR description contract

Reference for the `kirocrew-prepare-pr` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## PR description contract

Use these sections only when `.github/PULL_REQUEST_TEMPLATE.md` is absent. Phase 1.5
checks them against the diff.

1. **Problem / Motivation** — `**Goal:** <one sentence>`, then the symptom, or the gap for a feature.
2. **Why it matters** — impact if left unfixed. Then **Not a goal**, one bullet per scope.
3. **What changed (motivation → approach → change)** — symptom → root cause → the specific change. Three short paragraphs at most — one per arrow, well under 500 words of prose; `--check-body` stops past that (exit 21, `WORD_LIMIT`). It describes the **whole diff on this head**, never one round's fix. Write it in the register below.
4. **Backwards compatibility** — `Compatible:` or `Breaking:`. A diff that tightens a contract (new required field, a validator that raises on input the base accepts, narrowed type, removed kind, renamed key) cannot be `Compatible:`; it takes `Breaking:` plus a writer sweep re-run on FRESH `origin/<base>` before the final push, with that sha in the body. A diff that takes something away lists its readers as `Reader:` lines.
5. **Tests** — what was added/updated and what each locks in.
6. **Manual verification** — steps done/needed, or "N/A — unit coverage sufficient" with a one-line why.
7. **Screenshots / video — MANDATORY for any user-visible UI change**, uploaded as GitHub attachments with `gh ... --attach`, never committed, bar one scoped exception. See below.
8. **Issue link** — `Closes #<n>` on a line of its own. See *Issue first*.
9. **Pattern harvest** — `fix`/`revert` PRs only: `Rule candidate:` + `Pattern:`, or `Not generalizable:`.

Omit a section only when truly not applicable, and say so.

### Two checks, two strengths

`diff_signals.py --check-body` applies two rules, plus CI's template check, to the
finished body. Both stop the loop (why: `references/rationale.md`):

| check | what it measures | on breach |
|---|---|---|
| Accounting | every changed area (the `pr-scope.yml` unit: a module directory under `src/kiro_crew/` or `website/src/`, the top-level component elsewhere) is named in the body — by the area, a changed path or its `dir/file` tail, or a unique non-generic file name | **exit 20** — stop, fix the body or the diff |
| Length | words of prose in `What changed` (fenced blocks, table rows, image lines excluded) against the 500 of section 3's paragraph rule | **exit 21** — stop, compress the prose |

The template check runs CI's `.github/scripts/pr-description-check.sh` from
`origin/<base>`: a missing section is **exit 22**. The lowest code wins.

### Snapshot, not changelog

Rewrite the body from `git diff origin/<base>...HEAD` every round, then compare
it with the published body and remove unsupported claims. Copy the frozen goal
verbatim every round. Describe current code, never one round's fix; history
belongs in disposition comments. No history words: `also`, `additionally`,
`now also`, `after review`, `round N`, `follow-up fix`, `per reviewer`,
`addressed`, `updated to`.

### Writing register: Age 5

Use the Age 5 row of the `explain-for` skill. Age 5 is the *register*, never the
depth or the reader; the facts stay complete and technically exact.

- **Punch line first, in every section.** State the effect in user words, then
  only the facts needed to support it. One idea per sentence.
- **Do not recite the diff.** No per-file walkthrough or nested bullets; evidence
  and history belong in the review thread. One general sentence may cover a whole
  area ("docs updated to name the nightly").
- **Lead with a table when the change has more than one moving part.**
- Keep identifiers, paths, errors and flags verbatim. Cut decorative jargon.
- Draw ONE picture at most, only when it explains a changed shape faster than
  prose; how is in `references/body-picture.md`.

Before publishing, reread section 3 and rewrite any sentence that needs a second read.

### Screenshots

Capture each affected surface in its meaningful variants **by looking at the change
yourself** with the `web-verify` skill. A video renders as a player only alone in
its own paragraph.

- **Attach, never commit.** Capture into `$KIROCREW_SCRATCH/evidence/` or the
  gitignored `temp-screenshots/<feature>/`. Write ordinary local paths in the body
  file and pass the same files to `gh` (gh >= 2.99), BEFORE the push that needs
  judging:

  ```bash
  gh pr create --base <base> --head <branch> --title "<CC title>" --body-file <body> \
    --attach ./evidence/after.png --attach ./evidence/demo.mp4
  gh pr edit <n> --body-file <body> --attach ./evidence/after-v2.png
  ```

  Each path becomes a permanent `https://github.com/user-attachments/assets/<uuid>`
  URL that survives force-pushes and merge; carry those URLs over verbatim when a
  later round regenerates the body.
- **Verify:** `gh api repos/<owner>/<repo>/pulls/<n> --jq .body | grep -c user-attachments` MUST print the number of files attached.
- **Limits and formats:** PNG, JPEG, GIF, WebP, MP4, MOV, WebM; 10 MB per image or GIF, 100 MB per video. **No SVG**, attached or committed: the reviewer's Read tool opens it as markup, not pixels, so both lanes skip it; export a PNG.
- **Push access is required; without it, commit the evidence.** `--attach` uploads through an endpoint that 404s on read permission (cli/cli#14302), so a fork contributor has none. They either drag it into the description in the web UI (same `user-attachments` URL, same limits) or `git add -f temp-screenshots/<topic>/after.png` and reference the path; the fork lanes read the committed bytes, 10 MB per file, video included, same formats (another format is skipped with a warning naming it); a bigger recording is dragged in, not committed. Once merged, the blob is in `main`'s history for good; why: `references/rationale.md`.
- Non-media evidence (a JSON dump, a perf baseline) goes in a fenced block in a PR
  comment; link its permalink.

**No-visual-delta waiver** — when the diff touches watched frontend paths but
changes no pixel, both lines together:

```markdown
<!-- no-visual-delta -->
**Why no screenshot:** <one-line reason>
```

Use it for comment-only, type-only or identical-output refactors and non-rendering
attributes. Screenshot instead for any component, layout, theme, spacing or
user-visible string change. **When in doubt, screenshot.**

### Issue link

`gh pr view <n> --json closingIssuesReferences` reads the closure back; an empty list
with an issue named in the body means the keyword is missing or malformed, and
`pr_status.py` prints a `NOTICE:`. Only `Fixes|Closes|Resolves #<n>` as a whole
line closes the issue; `Part of #<n>` and `Refs #<n>` satisfy `Issue Gate` but
close nothing.
