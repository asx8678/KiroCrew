# Kiro Crew CLI Reference: profiling, cloud, tailnet, snapshot and restore

Reference for the `kirocrew-commands` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Profiling (debug-only)

Off unless `KIROCREW_DEBUG=1` is set; the CLI is the only entry point. Emits folded
stacks (open in speedscope / flamegraph.pl). See `docs/architecture/design-notes/profiling.md`.

| Command | Description |
|---------|-------------|
| `KIROCREW_DEBUG=1 kirocrew perf sample --call mod:fn` | Profile that callable in-process (no extra dependency) |
| `KIROCREW_DEBUG=1 kirocrew perf sample` | Attach to the running gateway (needs `pip install "kirocrew[perf]"`) |
| `KIROCREW_DEBUG=1 kirocrew perf sample --pid 1234 --seconds 30` | Attach to a specific PID for N seconds (1-300) |
| `... --interval 0.002` | Seconds between samples (0.001-1.0, default 0.005) |
| `... --output /tmp/p.folded` | Where to write the profile (default `./kirocrew-profile.folded`) |
| `KIROCREW_DEBUG=1 kirocrew desktop metrics` | Per-process CPU/memory of the **Electron** app (`--json`, `--top N`, `--path`) |

On macOS the attach path additionally needs elevated privileges (the OS denies
`task_for_pid`), so it may require sudo; `--call` needs neither py-spy nor sudo.

`desktop metrics` reads a recording rather than querying the app: `getAppMetrics()`
is Electron-main-only, so the app samples itself into an artifact when **started**
with `KIROCREW_DEBUG` set. Setting the variable only for the CLI does not make an
already-running app record -- restart it.

## Cloud (Bring-Your-Own AWS)

Runs Kiro Crew on an EC2 instance in **your own** AWS account; credentials are
resolved by the `aws` CLI and never stored by Kiro Crew. All verbs accept
`--profile` / `--region`; the single-instance verbs also accept `--tag`
(defaults to the last launched instance).

| Command | Description |
|---------|-------------|
| `kirocrew cloud doctor` | Check cloud prerequisites + AWS reachability |
| `kirocrew cloud launch` | Provision + configure an instance (interactive) |
| `kirocrew cloud launch --size TIER -y` | Non-interactive launch at a size tier |
| `kirocrew cloud launch --new` | Create a separate new instance instead of resuming the saved one |
| `kirocrew cloud launch --keep-on-failure` | On bootstrap failure keep the instance for inspection |
| `kirocrew cloud list` | List your Kiro Crew cloud instances |
| `kirocrew cloud status` | Show one instance's state |
| `kirocrew cloud connect` | Open the dashboard over an SSM tunnel |
| `kirocrew cloud tunnel` | Open the dashboard SSM tunnel (standalone alias of connect) |
| `kirocrew cloud connect --local-port N --no-browser` | Forward to a specific local port, no browser |
| `kirocrew cloud login` | Sign kiro-cli in on the instance (fixes "not logged in" chat errors) |
| `kirocrew cloud logout` | Sign kiro-cli out on the instance, to switch Kiro account |
| `kirocrew cloud stop` | Stop the instance (pause billing) |
| `kirocrew cloud start` | Start a stopped instance |
| `kirocrew cloud destroy` | Remove the instance and ALL its AWS resources |
| `kirocrew cloud destroy --dry-run` | Show the delete command without running it |
| `kirocrew cloud iam-policy` | Print the least-privilege IAM policy to apply |
| `kirocrew cloud iam-boundary` | Pre-create the immutable permissions boundary (admin, one-time) |

## Tailnet (Tailscale)

Publishes this dashboard on your tailnet and trusts its origin, so a device on the
tailnet reaches it without a public tunnel.

| Command | Description |
|---------|-------------|
| `kirocrew tailnet status` | Show whether the dashboard is published and trusted on your tailnet |
| `kirocrew tailnet up` | Publish the dashboard on your tailnet and trust its origin |
| `kirocrew tailnet down` | Stop publishing the dashboard on your tailnet |
| `... --port N` | Name the dashboard port; `up` needs it whenever discovery has no verified port |

`up` publishes only a port it has evidence for: an explicit `--port`, `KIROCREW_PORT`,
or the running gateway's run marker. With none of those — the gateway is down, the
marker is unreadable, or several gateways are up, where the marker deliberately
refuses — `up` refuses rather than falling back to the configured `dashboard.url`
port, because nothing is verified to answer there and `tailscale serve` would expose
whatever does. Start the gateway and re-run, or name the port yourself. `status` and
`down` do accept the configured port: one only reports, and the other checks mount
ownership before removing anything.

## Snapshot & Restore

| Command | Description |
|---------|-------------|
| `kirocrew snapshot` | Create a portable backup of Kiro Crew state |
| `kirocrew snapshot /path/to/dir` | Snapshot to specific output directory |
| `kirocrew snapshot --keep 7` | Keep N most recent snapshots (default: 7) |
| `kirocrew snapshot --list` | List existing snapshots |
| `kirocrew restore` | Restore from most recent snapshot |
| `kirocrew restore /path/to/snap.tar.gz` | Restore from specific snapshot |
| `kirocrew restore --mode replace` | Replace mode (default) |
| `kirocrew restore --mode merge` | Merge mode |
| `kirocrew restore --dry-run` | Preview without applying |
| `kirocrew restore --components memory,crons` | Restore specific components only |
| `kirocrew restore --list-components` | List restorable components |
| `kirocrew restore --force` | Restore even if gateway is running |
