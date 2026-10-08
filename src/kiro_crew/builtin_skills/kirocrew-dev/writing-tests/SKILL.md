---
repo_scope: src/kiro_crew
name: writing-tests
description: "Kiro Crew repo only: how to write a backend pytest test with NO side effects that does not flake. Use when adding, editing, reviewing or debugging a test there: which conftest applies, what leaks (temp dirs, data home, ~/.kiro, cron, threads), the seven flake classes, cross-platform traps."
triggers: write a test, add a test, fix a flaky test, test is flaky, test side effect, temp dir residue, tmp residue, kirocrew test, pytest kirocrew, test isolation, conftest, xdist, test leaked
---

# Writing a Kiro Crew test that does not leak and does not flake

> **Scope guard: this skill applies ONLY to the Kiro Crew source repository** (or a
> worktree of it). Its rules are conventions of this repo's suite. In any other
> project, ignore it.
>
> The canonical, longer reference is
> [`docs/system-specs/common/testing-conventions.md`](../../../../../docs/system-specs/common/testing-conventions.md).
> If this skill and that document disagree, the document wins. What this skill adds is
> the decision order — what to check first, and what the failure looks like when you
> get it wrong.

## Where this skill stops, and which sibling takes over

This skill owns **authoring** a test: isolation, determinism, cross-platform
behaviour, diagnosing a residue failure, and suite speed. Three siblings own the
neighbouring steps, and none of them restates what is here:

| You are doing | Skill |
|---|---|
| Writing, fixing, or speeding up a test | **this skill** |
| Running the build gate in a worktree, and deciding whether a red is yours | **kirocrew-worktree-dev** |
| Driving a branch to a review-ready PR and through CI | **kirocrew-prepare-pr** |
| Polling that PR until it is green | **babysit** |

The line that matters most in practice: **kirocrew-worktree-dev** tells you whether a
failure is yours (re-run it on `origin/main`, mine CI for what is genuinely flaky);
once it is yours, the fix is here. Do not fix a flake from a summary of the
determinism classes — pick the class from the symptom, in Rule 2.

## The two properties, and why they are one problem

A test must be **hermetic** (no effect that outlives it, anywhere but its own tmp dir)
and **deterministic** (same verdict every run, on every platform, in any order). They
are the same problem because the suite runs `-n auto --dist loadgroup`: a side effect
is not just untidy, it is *the input to another test on the same worker*, and it
surfaces as a flake in a file you never touched.

## Rule 0 — Know which conftest is under your file BEFORE you isolate anything

`setup.cfg` declares two testpaths and they do **not** get the same fixtures. The
table is in [testing-conventions.md](../../../../../docs/system-specs/common/testing-conventions.md)
§ Which conftest you are standing on — read it rather than a second copy here, which
would drift. The short version: only `test/` gets `test/conftest.py`;
`src/kiro_crew/apps/builtins/**/tests/` (and the one `**/*_tests/` suite) gets the
rootdir `conftest.py` plus that suite's own `conftest.py` where one exists.

The rootdir `conftest.py` is the **host floor** — the guards that protect the
developer's machine, so they hold everywhere. That covers damage (temp dirs, the data
home, services) and *resource exhaustion*: the xdist worker budget is registered there,
from the repo-root `xdist_budget.py`, because a run that spawns one worker per core on a
small laptop takes the machine down and does not care which testpath asked. Everything
else is in `test/conftest.py`.

Getting this wrong is the most expensive mistake in this suite, because it fails
**silently and asymmetrically**: an in-package test that assumes `test/conftest.py`'s
fixtures passes on CI (where the operator's home is empty) and damages a real install
locally. When you add isolation, ask: *could a test in ANY testpath damage the host —
or consume enough memory, cores or disk to take it down — without this?* If yes it
belongs at the rootdir; if no, put it in `test/conftest.py`, where the in-package suites
pay nothing for it.

## Rule 1 — The side-effect floor: what actually leaks

Work down this list. Each item is a real leak that has happened here.

### 1a. Temp directories — register the destruction in the SAME scope

Prefer `tmp_path`. If you need `mkdtemp()`, register cleanup with `addCleanup` on the
**next line** — never an `rmtree` in `tearDown`, because `unittest` skips `tearDown`
entirely when `setUp` raises, so that shape leaks on exactly the failing runs nobody
watches. The before/after example is in testing-conventions.md § Rules.

`ignore_errors=True` also **hides** a cleanup that could not finish, so it is not proof
of anything. The floor helps, but it is not your discipline: the rootdir conftest redirects
`tempfile`'s base per run, removes it, and **reports** residue — as a warning today, fatal
under `KIROCREW_TMP_RESIDUE_STRICT=1`. So a leak of yours will not necessarily turn CI red;
clean up anyway. (Staged rollout and its owner: testing-conventions.md § Rules.)

Why it earns a guard: `/tmp` is often a tmpfs with a fixed **inode** budget
(1,048,576 on the hosts this was measured on) and it returns `ENOSPC` to every other
process on the machine while **90% of the bytes are free**. It is not a tidiness issue,
it is a "your shell stops working" issue.

### 1b. The operator's data home, and `~/.kiro`, which is a DIFFERENT axis

`KIROCREW_HOME` is pinned per test at the rootdir, which is what makes `config_dir()`
safe — and it needs to be, because resolving it is **not a read**: it creates the home
and its marker, and can run the `~/.kirocrew` → `~/.kiro/crew` migration.

Six shapes escape the env var:

- **`monkeypatch.undo()` in the body of a test.** It reverts EVERY record on the shared
  instance — your own patches, every fixture that set something through it (`stores`
  pins `KIROCREW_HOME` that way), and until the floor got its own instance, the floor
  itself. A restore that ran `empty_trash()` after `undo()` emptied the operator's real
  trash. A patch you need to drop before the test ends goes in
  `with pytest.MonkeyPatch.context() as patched:`; `undo()` is not a scoped tool.
- **A detached task.** `asyncio.create_task(...)` at boot with no one awaiting it keeps
  running after the test that started it has returned; when its work resolves a path
  (`peek_next` → `spool_path()` → `data_home()`, on a `to_thread` worker) it resolves
  the OPERATOR's home. Fixed where the task is scheduled: `_start_channel_transports`
  resolves `spool_path()` on the loop and hands it to the task, so the worker reads a
  location fixed at boot. Resolve every environment-derived input of a detached task on
  the calling side before detaching, or give the test a handle it can await.
- **What a closed loop leaves behind.** pytest-asyncio 0.20 ends the loop with a bare
  `loop.close()`. A pending task's coroutine then gets `GeneratorExit` at garbage
  collection, so its `finally` blocks run *then* (`_run_chat`'s queue cycle reaches a
  sync `KiroCrewConfig.load()`); a default-executor job (`to_thread`) is abandoned
  mid-flight. Both resolved `config_dir()` after the unpin and grew a fresh fake
  `HOME`'s `~/.kiro/crew`. The floor's `tryfirst` teardown hook now cancels pending
  tasks, runs them to completion and joins the executor while the pins hold, the way
  `asyncio.run` shuts down. A `coroutine ... was never awaited` warning pointing at that
  hook means your test left an unstarted turn behind: await it, or keep the patch that
  was meant to catch it in force until it has run.
- **A default that bypasses the pin on purpose.** `PodConfig.load()` derives the pod
  plane from `_default_home()` / `Path.home()` so a pod cannot redirect the host's
  registry; the floor pins `KIROCREW_POD_ROOT` / `KIROCREW_POD_ENV_DIR` for that reason.
  A new resolver with its own default gets a floor pin AND a ratchet in
  `test_host_isolation_floor.py` in the same change.
- **Import-time from `config_dir()`** (`subagent_persistence._SUBAGENTS_DIR`): the var
  is read after the module captured the path. Each has its own autouse pin. A
  **collection-time probe** is the same shape one step earlier: a module-level
  `_can_spawn()` used by a `skipif` runs before any pin and read the REAL
  `config.json`; run such probes under an empty `KIROCREW_HOME`.
- **Import-time from `Path.home()`**: `~/.kiro` is *kiro-cli's* home, machine-wide and
  shared with the real installed agent — `~/.kiro/settings/mcp.json` is the live
  agent's MCP server list. `_isolate_shared_kiro_paths` redirects these from a table,
  and `test/test_host_isolation_floor.py` **fails when `src/` grows a new one**. That
  ratchet covers IMPORT-TIME bindings only. The lazy resolver
  `config.paths.kiro_home()` — and so `kiro_agents_dir()` / `kiro_sessions_dir()` — is
  yours to isolate, with `KIRO_HOME` or by patching `Path.home()`; they are not
  interchangeable, because the env var outranks the other. A test that skipped this
  projected the developer's *installed* agent specs and failed with an
  `AcpRuntimeError` naming a prompt file in an unrelated worktree.

  Some entries are excluded, and for two opposite reasons — already redirected
  elsewhere (the macOS launchd set, moved as one group by `_isolate_launchd_paths`),
  or a **security anchor that must never move**: the file browser's allow-list root
  `file_explorer/server._HOME`. **Stub the reader, never
  move the anchor.** Redirecting a matcher so a test can pass makes it assert against a
  pattern that no longer matches the thing it protects. An anchor that stops being an
  import-time `Path.home()` binding leaves this tripwire's reach entirely —
  `kiro_usage_api`'s kiro-cli sqlite tuples now resolve the home inside
  `identity_stores.sqlite_dbs()`, so their anchor rule is pinned by
  `test_identity_stores.py::TestUsageTuplesAnchorTheRealHome` instead. Read
  `_EXCLUDED` for the live list rather than a copy here.

### 1c. A child process inherits pytest's CWD, which is the repo root

Pass `cwd=<a directory under tmp_path>` to any child that may create a file, and scope every assertion to where that child actually ran: the write happens in a grandchild, so the test can pass while the artifact sits in the checkout. Read [`references/processes-and-cwd.md`](references/processes-and-cwd.md#1c-a-child-process-inherits-pytests-cwd-which-is-the-repo-root) before a test spawns a child process.

### 1d. Background lifecycle — the one that beats every filesystem cleanup

**A singleton with a daemon thread cannot be cleaned up by tidying files.** The worked
example is `sel.py`: `SecurityEventLog` is a process singleton, its writer is a daemon
thread, and `_init_locked` binds the directory **once** from whatever `_default_dir()`
resolved then. So the first test to call `sel()` fixes it for the whole worker, the
thread keeps writing after that test ends, and `_flush_batch` opens with
`mkdir(parents=True, exist_ok=True)` — **re-creating the directory after tearDown
deleted it**. MEASURED: exactly one stray directory per run of the ops-mission-control
suite. Full telling, with the stack it came from:
[testing-conventions.md](../../../../../docs/system-specs/common/testing-conventions.md)
§ Rules.

The fix was not better cleanup. It was giving the singleton a **session-scoped**
directory belonging to no individual test. When you touch a subsystem with a background
worker, ask: *which directory did its thread capture, and does anything delete that
directory underneath it?*

**The same shape, one level up: a stub that replaces a `shutdown`/`close`/`stop` is
not a stop.** Three tests needed to observe *that* the metrics provider's `shutdown`
was called, so they replaced it with a recorder — and the real `shutdown` is what stops
OpenTelemetry's exporter thread. That thread then survived for the life of the worker,
and because the OTel SDK reinstalls it in every fork child via `os.register_at_fork`,
the sandbox's userns probe forked a MULTITHREADED child. `unshare(CLONE_NEWUSER)`
implies `CLONE_THREAD`, which the kernel refuses with EINVAL unless the caller is
single-threaded, and EINVAL is indistinguishable from "no `CONFIG_USER_NS`" — so the
worker cached "this host has no sandbox backend" and every later sandboxed spawn failed
closed. 19 red tests in two app suites, each passing alone, none of them a metrics test.
**Spy and delegate; never replace a lifecycle method.** The rootdir conftest now fails
the test that leaves an exporter thread running.

Two general lessons worth carrying out of that one: a thread whose target is a bound
method keeps its own object alive, so dropping references never collects it; and
anything registered with `os.register_at_fork` runs inside `os.fork()`, before it
returns, so a fork child is not reliably single-threaded.

Related traps in the same family:

- **A `MagicMock` config reads TRUTHY.** Patching `KiroCrewConfig.load` with a bare
  `MagicMock` makes `cfg.telemetry.enabled` truthy, which starts a real recorder and a
  reader thread, and resolves `Path(cfg.local_dir)` to a *relative* path that writes
  into the repo. That is why `KIROCREW_TELEMETRY=0` is forced for the whole suite. Any
  subsystem gated on a truthy attribute read off a config object has this shape.
- **A sleeper child that outlives the test.** The suite spawns
  `python -c "import time; time.sleep(30)"` to simulate a hung process. Put the
  kill/wait in a `finally` or an `addCleanup`, never only on the happy path.
- **A fixed port.** Bind port `0`. A fixed number collides across xdist workers *and*
  with the operator's running gateway.

### 1e. Never leave the process working directory somewhere else

The CWD is per-PROCESS, so under xdist one test's `os.chdir` becomes every later test's starting directory on that worker. Use `monkeypatch.chdir`, which reverts itself. Read [`references/processes-and-cwd.md`](references/processes-and-cwd.md#1e-never-leave-the-process-working-directory-somewhere-else) before a test changes directory.

### 1f. Never register a real cron job, and never touch a real service

The rootdir conftest traps the stdlib spawn funnels and refuses a
`systemctl`/`launchctl` invocation carrying a **mutating verb**. Read-only queries
(`show`, `cat`, `is-active`) are allowed and need no stub. A test reaching the make-live
cutover path must stub **both** `_run_cmd` and `_dropin_path`.

## Rule 2 — Determinism: seven classes, one correct fix each

Never "fix" a flake with a rerun, a longer `sleep`, a weakened assertion, or a skip.
Full detail and examples: testing-conventions § Determinism. Its short form, twelve
MUST rules, is the
[Determinism contract](../../../../../docs/system-specs/common/testing-conventions.md#determinism-contract-read-this-first)
at the top of that document, and the checklist below opens with the same twelve.

The seven classes, the tell that identifies each, and the ONE correct fix for each are in
[testing-conventions.md](../../../../../docs/system-specs/common/testing-conventions.md)
§ Determinism. Read the section matching your symptom before changing anything: most of them
have a fix that looks like the obvious one and is not.

What this skill adds is when to go looking — a test that passes alone and fails in the suite, or
one that splits by Python version rather than by machine load, is one of those seven and not a
mystery. Do not reach for a rerun, a longer `sleep`, a weakened assertion, or a skip.

The seventh class is the one a sleep hides best: **two stamps written back to back can be
EQUAL.** On Windows through Python 3.12 `time.time()` steps ~15.6 ms, like
`time.monotonic()`, and some filesystems store mtimes in whole seconds, so a `sleep`
between two writes makes a tie less likely, never impossible. Set the stamps (`os.utime(ns=...)`, a stepped
clock, an explicit `created_at`), assert strictly, and pin a time-sorted output's
tie-break with an equal-stamp pair.

Prove a test rather than trusting a green run: 20 repeats at `-n0`, 10 at `-n 4` beside
its neighbours, and a shuffled order. A FIX to a flaky test also shows the forced
condition (a coarse clock, a late executor, a delay at the named seam) red on the parent
commit and green on the fix (testing-conventions § Proving a determinism fix).

The sixth class has a tell of its own: **the run ends early, not red.** On Windows a test that
blocks past `--timeout` is not failed, its xdist worker is killed, and with
`--max-worker-restart=0` the run aborts with every uncollected result missing. If a full run
reports a few thousand tests instead of ~60k, look for `worker ... crashed while running` in the
log: the named test is one that can wait forever. Wait on the observable state, not a guessed
`sleep`, and bound the await whose refusal is under test with `asyncio.wait_for` so a missed
refusal fails by name at that line.

An adjacent trap: **a patch target that misses.** Patch the namespace whose
globals the code under test actually reads. It fails in both directions — patching a
package re-export when the caller reads its own defining module, or patching
`pkg.mod.fn` when the caller did `from pkg.mod import fn` and holds its own binding.
Either way the real function runs, the assertion passes for the wrong reason, and the
test pays real time. **Treat an unexpectedly slow "mocked" test as evidence the mock
missed.** The opposite trap is a patch that is too WIDE: a side effect on a module global
also fires for every background worker that calls it (the `eventlog-io` legacy fold calls
`members.read_dm_binding`). Patch the function awaited in the window you mean, and pin
the order of the calls the test relies on.

Another: **the host is an input, and a "surely-unused" number is not a constant.**
`999999` reads as an impossible PID and is not — `pid_max` is 4194304, so on a
long-running host it names a live process. That broke two tests in opposite ways: one
stopped pruning an entry whose owner "must be dead", and one accused a planted `ps` shim
of executing when it had not. If the code *probes* the PID, pin the probe; if the number
must never appear in real output, pick one no OS can allocate (`99999999999`).

The host's free MEMORY is an input too, and the most-used probe of it is
`SubagentManager.spawn`, which queues — registering nothing in `_tasks` — on a
pressured machine. A queued spawn is still a `SubagentInfo`, so the test dies a line
later on a bare `KeyError`, not on the assert that would have named the cause. Any
file driving `spawn` takes `pytestmark = pytest.mark.usefixtures("healthy_host_memory")`,
and `test_subagent_spawn_host_pin.py` fails when a new one does not. The guard it
pins, and why it stays transparent for the parser's own tests, are in
testing-conventions § Determinism 1. Both the fixture and its ratchet are
`test/`-only — `healthy_host_memory` lives in `test/conftest.py`, and
`test_subagent_spawn_host_pin.py` scans `test/*.py` non-recursively. An in-package
app suite cannot request the fixture and is not swept, so a test there that drives
`SubagentManager.spawn` must pin the floor reading itself.

One more, for tests of a **single-flight or coalescing** path ("N concurrent readers
share ONE scan"): the property only holds for readers that arrive WHILE the shared
operation is in flight, so the test has to keep it in flight until they have. An
instant stub does not: `asyncio.gather` starts the leader first, its executor job
finishes on the pool thread before the loop reaches the `await`, and on Python 3.13
the wrapped future is already done — awaiting a done future does not yield. The
leader then completes with a waiter count of one and the next reader assembles again,
once in five loaded runs. Gate the stub on a `threading.Event`, poll until every
reader is registered, then release it. Whatever the count of assemblies asserts, the
test must first establish the concurrency it is asserting about.

## Rule 3 — Cross-platform: macOS, Linux (x86_64 + arm64), Windows

Route POSIX calls through `platform_compat`: `os.kill(pid, 0)` TERMINATES the target on Windows, so use `platform_compat.pid_exists`. Read [`references/platform-and-budgets.md`](references/platform-and-budgets.md#rule-3--cross-platform-macos-linux-x86_64--arm64-windows) before a test touches processes, signals, paths or file locks.

## Rule 4 — Diagnosing a residue failure

The residue report is attributed to the last test the worker ran, which is almost never the culprit; the guard carries its own bisector. Read [`references/platform-and-budgets.md`](references/platform-and-budgets.md#rule-4--diagnosing-a-residue-failure) when a residue failure appears.

## Rule 5 — Keep the parallel suite fast

Per-test setup cost dominates any single slow test: an autouse fixture is paid once per test. Profile, never guess. Read [`references/platform-and-budgets.md`](references/platform-and-budgets.md#rule-5--keep-the-parallel-suite-fast) before adding a fixture or a slow test.

## Rule 6 — MEMORY is the other budget, and collection is most of it

Most of a worker's memory is paid at collection, before your test runs, so `-n auto` is bounded by available memory. Read [`references/platform-and-budgets.md`](references/platform-and-budgets.md#rule-6--memory-is-the-other-budget-and-collection-is-most-of-it) before adding a collection-time import, probe or large module-level object.

## Checklist before you push a test

### Determinism top 12

One box per rule of the [Determinism contract](../../../../../docs/system-specs/common/testing-conventions.md#determinism-contract-read-this-first),
naming the shape; the contract line holds the detail and its numbers. The helpers are in
`kiro_crew.testing` (`clock`, `wait`, `ids`). Every other item below is a specialised trap.

- [ ] D1: wait on the asserted state with a raising, bounded poll (`wait_until`,
      `async_wait_until`, `until_parked`), never a sleep
      ([class 2](../../../../../docs/system-specs/common/testing-conventions.md#2-wall-clock-races))
- [ ] D2: one clock, installed on the module-under-test's own binding
      (`ManualClock.install`, the `manual_clock` fixture)
      ([class 2](../../../../../docs/system-specs/common/testing-conventions.md#2-wall-clock-races))
- [ ] D3: set the timestamps (`ManualClock(tick=...)`, `os.utime(ns=...)`, `seq_ids`),
      assert order strictly, pin the tie-break
      ([class 7](../../../../../docs/system-specs/common/testing-conventions.md#7-data-order-and-timestamp-ties))
- [ ] D4: no asserted order the code does not define
      ([class 7](../../../../../docs/system-specs/common/testing-conventions.md#7-data-order-and-timestamp-ties))
- [ ] D5: the product's zone and a frozen instant, never the host's (`local_tz`)
      ([class 1](../../../../../docs/system-specs/common/testing-conventions.md#1-nondeterministic-input))
- [ ] D6: seeded RNGs and unallocatable fake PIDs (`seeded_rng`, `unallocatable_pids`)
      ([class 1](../../../../../docs/system-specs/common/testing-conventions.md#1-nondeterministic-input))
- [ ] D7: no upper bound on a measured duration beyond what the contract allows
      ([class 5](../../../../../docs/system-specs/common/testing-conventions.md#5-absolute-time-budgets-on-instrumented-runs))
- [ ] D8: every self-unblocked await, join or communicate bounded, failing by name
      ([class 6](../../../../../docs/system-specs/common/testing-conventions.md#6-a-hang-is-a-lost-run-not-a-failed-test))
- [ ] D9: listeners bind port 0 and report the port ([Rules](../../../../../docs/system-specs/common/testing-conventions.md#rules))
- [ ] D10: loopback only, the network stubbed at the product's seam
      ([side effects](../../../../../docs/system-specs/common/testing-conventions.md#side-effects-what-a-full-run-does-to-the-host-and-how-to-see-it))
- [ ] D11: globals through `monkeypatch`, no in-process reload, evictions restored
      ([class 4](../../../../../docs/system-specs/common/testing-conventions.md#4-order-dependence-and-shared-state))
- [ ] D12: proven by repeats, a shuffled order and, for a fix, the forced condition
      ([proving a fix](../../../../../docs/system-specs/common/testing-conventions.md#proving-a-determinism-fix))

### Specialised traps (search when your test touches X)

A long checklist keyed by what the test touches: temp files, background workers, globals and environment variables, clocks, ports, child processes, platform shims and more. Search [`references/specialised-traps.md`](references/specialised-traps.md#specialised-traps-search-when-your-test-touches-x) for the thing your test touches before you push it.

