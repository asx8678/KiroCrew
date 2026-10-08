# Writing tests: child processes and the working directory

Reference for the `writing-tests` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

### 1c. A child process inherits pytest's CWD, which is the repo root

This is the leak a reviewer cannot see: no line says `open(..., "w")`, the write
happens in a grandchild, and the test can assert against `tmp_path` and pass while the
artifact sits in the checkout. An empty file produced this way has been committed and
shipped from this repo.

- Pass `cwd=<a directory under tmp_path>` to any child that MAY create a file, and to
  every helper that spawns one.
- Scope the assertion to where that child's CWD actually **is**. An assertion over
  `tmp_path` proves nothing about a child that ran somewhere else — and a security test
  whose payload escapes its own assertion is worse than no test, because it reports a
  guarantee it never checked.
- A read-only query is exempt, and sometimes must be: `git check-ignore` against the
  checkout is asking a question *about the checkout*.

The rootdir conftest fails the run on new non-ignored entries at the repository root,
which is how this announces itself.

Two more things a spawning test owes, both learned from processes that outlived the run
by **six days**, spinning at 1464% CPU between them:

- **`HOME` and `PATH` are a PAIR.** If you hand a child a fabricated environment,
  substituting one while inheriting the other is the defect. An inherited `PATH` on a
  developer host routinely leads with a version-manager shim directory (mise, asdf,
  pyenv, volta, nodenv), and a shim resolves its tool set from `HOME` — so with a
  substituted `HOME` the bare name `python3` or `node` reaches the MANAGER, which finds
  no tool state and never execs anything. It spins, forever. Pin the real interpreter's
  own directory first on `PATH`, or resolve the tool to its real executable before
  building the env. Do not drop the `HOME` substitution instead: it is usually a
  blast-radius bound somebody chose on purpose.
- **Reap the process GROUP, on every exit path.** A child is routinely a wrapper that
  forks, so `Popen.kill()` reaps the wrapper and leaves the real work running — and a
  bounded `wait()` ending in a bare `pass` then reports success. Use
  `start_new_session=True` + `os.killpg` on POSIX and `CREATE_NEW_PROCESS_GROUP` +
  `taskkill /T /F` on Windows; `test/installer_test_helpers.run_bounded` is the
  reference. A `try/finally` around the whole post-spawn body, not just the timeout
  branch, is what makes "every exit path" true.

### 1e. Never leave the process working directory somewhere else

The CWD is per-PROCESS, so under xdist one test's `os.chdir` becomes every later test's
starting directory on that worker. Use `monkeypatch.chdir`, which reverts itself.

This is the leak with the widest blast radius measured here. Because a passing test's
`tmp_path` is removed at its own teardown, a test that chdirs into `tmp_path` and does
not come back leaves the worker in a **deleted** directory, and `Path.cwd()` then raises
`FileNotFoundError` for every later test that reaches it — including from inside
production code. The measured instance is in
[testing-conventions.md](../../../../../../docs/system-specs/common/testing-conventions.md)
§ Rules; the shape to recognise is that it reads as "the suite is flaky" — many files,
each passing in isolation. The rootdir conftest restores the CWD before any fixture
teardown, but write the test so it would not need to.
