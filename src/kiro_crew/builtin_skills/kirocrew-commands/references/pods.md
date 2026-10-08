# Kiro Crew CLI Reference: pods (isolated worktree test instances)

Reference for the `kirocrew-commands` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Pods (Isolated Worktree Test Instances)

Ephemeral, full-stack Kiro Crew gateways — one per feature worktree — that run on
their own port + isolated `KIROCREW_HOME` and never touch the live `:5476`
gateway or shared data. Linux `systemd --user` only. `<wt>` is a worktree name
(resolved by directory basename or `feat/<name>` branch convention).

| Command | Description |
|---------|-------------|
| `kirocrew pod install` | Lay down the systemd --user template unit (once per machine) |
| `kirocrew pod provision <wt>` | Build the worktree's venv + SPA dist (the on-ramp) |
| `kirocrew pod up <wt>` | Bring up an isolated pod (auto-builds venv; fails if dist missing) |
| `kirocrew pod up <wt> --provision` | Provision (venv + dist build) then bring up |
| `kirocrew pod up <wt> --json` | Bring up and print `{base_url, token, port}` as JSON |
| `kirocrew pod ls` | List running pods |
| `kirocrew pod status <wt>` | Up/down + health for one pod |
| `kirocrew pod token <wt>` | (Re)mint a dashboard token for a running pod |
| `kirocrew pod url <wt>` | Print the pod's base URL |
| `kirocrew pod logs <wt> -n N` | Tail the pod's journal |
| `kirocrew pod down <wt>` | Evict the pod and delete its isolated HOME |
| `kirocrew pod prune` | Bulk-reclaim orphaned pod HOMEs (`--older-than 3d` by default, `--all`, `--dry-run`, `--json`) |
| `kirocrew pod scenarios` | List the seed scenarios `pod up --seed <scenario>` accepts (`--json`) |
| `kirocrew pod exec <wt> -- <args>` | Run a kirocrew command against a pod, using the pod's own binary and data |
| `kirocrew pod api <wt> <METHOD> <path>` | Call a running pod's HTTP API with its own token; prints `{name, method, path, status, ok, body}` |
| `kirocrew pod api <wt> POST config --data '{…}' --allow-write` | GET and HEAD are permitted by default; every other method needs `--allow-write` |

**Platform:** Linux only. On macOS/Windows every systemd-touching verb refuses
with a one-line message pointing at `./dev-backend.sh` — it does not crash, and
`pod install` writes no unit file. `pod url` works anywhere (pure computation).

Port derivation: `base + (cksum(name) % 199) + 1` (base `7810` → `7811..8009`).
Override with `PORT=` in `~/.kiro/crew/pods/<name>.env`.

`kirocrew pod --help` lists every verb and its flags.
