# Writing tests: cross-platform, residue diagnosis, speed and memory

Reference for the `writing-tests` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Rule 3 — Cross-platform: macOS, Linux (x86_64 + arm64), Windows

- **Route POSIX calls through `platform_compat`.** See docs/system-specs/common/platform-compat.md. Most
  important: `os.kill(pid, 0)` **TERMINATES** the target on Windows — it is not a
  liveness probe. Use `platform_compat.pid_exists`.
- **Path length is a real constraint.** Windows caps a path at 260 characters unless
  long paths are enabled, and a macOS `AF_UNIX` `sun_path` is capped at ~104 bytes —
  which a macOS `basetemp` alone already exceeds. That is why
  `test/tmpdir_helpers.short_tmp_base()` exists, and why anything that prefixes every
  temp path in the suite has to be measured, not assumed.
- **Case-insensitive filesystems.** macOS and Windows are case-insensitive by default,
  so a test asserting that two paths differing only in case are distinct is broken
  there.
- **Windows clock granularity.** Through Python 3.12 `time.time()` and
  `time.monotonic()` step ~15.6 ms, and so do `Event.wait`, lock and queue timeouts;
  only `time.sleep` has been high-resolution since 3.11 — so a short sleep can put two
  writes on one stamp, and "two writes have different timestamps" is a flake there
  (class 7).
- **Probe, do not guess the platform.** `test/conftest.py::_can_create_symlink` is the
  model: creating a symlink needs `SeCreateSymbolicLinkPrivilege`, which CI runners
  hold and an ordinary shell does not, so a blanket `skipif(IS_WINDOWS)` would drop the
  assertion exactly where it needs to run. Use the `make_escaping_link` /
  `make_dir_link` helpers, which fall back to a junction.
- **arm64 vs x86_64** rarely matters for test logic, but check it when you touch memory
  or page arithmetic (`SC_PAGE_SIZE` is 16K on some arm64 configurations) or a
  dependency with per-architecture wheels.
- Windows gaps are tracked as burn-down lists, not scattered skips:
  `test/windows-collect-ignore.txt` and `test/windows-expected-failures.txt`. Anything
  NOT listed still fails the job. Delete a line when you fix its test.
- **Never `--deselect` a whole file for a missing host capability.** Guard the test that
  needs it — `skipif(not userns_available())` for the OS sandbox — so the report names the
  capability. A deselect is invisible in the output, takes the file's other tests with it,
  and never goes red when its reason expires: eleven such files kept 608 tests off every
  PR, of which 523 would have run on all three platforms, long after the cost that
  justified the exclusion had been fixed.

## Rule 4 — Diagnosing a residue failure

The residue report runs in a session-fixture teardown, so it is attributed to the **last
test the worker ran**, which is almost never the culprit. The guard carries its own
bisector — use it instead of guessing:

```bash
KIROCREW_TMP_PER_TEST=1 pytest src/kiro_crew/apps/builtins/<app>/tests -n0 -q
# AssertionError: 1 temporary entry outlived this run under /tmp/kc-pytest-you-951504:
#     test_provider_listing_never_contains_a_token/tmpw2kvty2z
```

Each residue name becomes `<test id>/<leaked name>`. If the leak survives a `tearDown`
that visibly removes it, suspect Rule 1d: something **re-created** the path after
cleanup. Confirm by wrapping `os.mkdir` in a throwaway `-p` plugin and printing a stack
for paths under the run's temp root — that is how the `sel-writer` thread was found.

For a repository-root residue failure, the cause is almost always Rule 1c: a subprocess
spawned without `cwd=`.

## Rule 5 — Keep the parallel suite fast

At ~56.5k tests, **per-test setup cost dominates any single slow test** — an autouse
fixture is paid ~56,500 times. Profile, never guess; compare candidates back to back on
the same host (run the change, then the base in `git worktree add --detach <dir> <base>`;
never `git stash`: every worktree shares one stash list), because a loaded host makes an
absolute number meaningless.

The recurring wins, in order of leverage:

1. **An autouse fixture that costs more than it protects** — one requesting a
   `tmp_path` it never uses; a repeated `tmp_path_factory.mktemp` (it scans the whole
   basetemp to pick its suffix, so it slows as siblings accumulate); an unconditional
   `mkdir` where the consumer only needs a *path*. Measure the whole chain against a
   file of trivial `assert True` tests.
2. **A production timeout or poll the test never asserts on** — `monkeypatch` the
   interval to `0`; the branch still executes, only the waiting goes.
3. **An expensive immutable thing built per test** — a real `git` repo costs ~1–1.6s in
   subprocesses. Build it once `scope="session"` and `copytree` it per test; copy *from*
   the template rather than yielding it, so nothing one test does can reach another's.
4. **A module-cached tree scan paid once per WORKER** — an `lru_cache`d `rglob` +
   `ast.parse` of `src/` (~30 s) is computed again on every xdist worker the module's
   tests land on; five full runs measured eighteen ratchet modules each re-scanning on
   3–5 workers, ~22 CPU-minutes per run. Add
   `pytestmark = pytest.mark.xdist_group(name="tree_scan_<module>")` — one group per
   file, never one shared group — and the scan runs once per run.

**After any speedup, mutate the production code the test covers and confirm the test
still FAILS.** A test made faster by checking less is a regression. Restore from a copy
of the file you mutated, not from git — `git checkout --` discards unrelated uncommitted
work — and sequence with `;`, not `&&`, or the restore only runs when the mutation
did *not* work.

## Rule 6 — MEMORY is the other budget, and collection is most of it

A worker costs ~2.0 GiB (measured median at `-n 12`; worst observed 2.8 GiB), and
~1,499 MiB of that is paid before your test runs: every xdist worker independently
collects every item in both testpaths (106,491), and 99% of that footprint is private,
so more workers never amortize it. This is why `-n auto` is bounded by available memory
— on an 8–16 GiB laptop the full suite otherwise swaps the machine.

Both halves of that model doubled between audits (from ~57k items / ~750 MiB), so
**re-measure rather than trusting the numbers above** once the suite grows by half
again: `--collect-only -n0` reproduces a worker's collection peak. The reservation in
`xdist_budget.py` is sized on them, and an under-sized reservation is how a laptop
starts swapping.

The consequence for how you write a test:

- **An allocation at module scope is multiplied by every worker, and outlives the
  test.** A big literal inside `@pytest.mark.parametrize` is the worst shape: it is
  built while the module is IMPORTED and the mark keeps it alive on the function
  object for the whole session, so one test's payload is charged to all of them. Two
  such literals — a 64 MiB frame and a 40 MB string — cost 102 MiB per worker until
  they were moved into the test bodies behind a sentinel.
- **A transient peak counts too**, because a worker's high-water mark is what the
  budget must reserve. Building an image as a list of per-pixel tuples cost ~390 MiB
  for a 17 MB PNG; one `frombytes` over a bytes buffer was 10x smaller and
  byte-equivalent.
- **Derive a size from the production constant** rather than restating it. A literal
  `40_000_001` beside a `_MAX_LAYER_B_CHARS` of `40_000_000` hides both the coupling
  and the cost, and goes stale silently.
- Suspect a **module-scope literal** whenever a file's collection RSS is large; measure
  it with `pytest <file> --collect-only` and `/proc/self/status`'s `VmHWM`.
