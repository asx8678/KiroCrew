# Kiro Crew CLI Reference: browsing

Reference for the `kirocrew-commands` skill, read on demand: `SKILL.md` points here at the step that needs it. Script and file paths in code (`scripts/...`) are relative to the skill directory, as in `SKILL.md`.

## Browsing (`browser` MCP tool, then `playwright-cli`)

Browsing is not a `kirocrew` subcommand. Start with the **`browser` MCP tool**
in the native Browser panel:
`op=navigate|snapshot|click|type|press_key|hover|select_option|screenshot|wait_for|back|console`.
It refuses loopback, private, and link-local targets. Use `playwright-cli` when
no native panel serves the session, or for attached logins, saved storage state,
and the full operate verbs. It must be on `PATH`; install via **Settings → Browser**
(also holds the attach token) or `npm install -g @playwright/cli@latest`
(Node.js 20 or newer).

| Command | Description |
|---------|-------------|
| `playwright-cli open <url>` | Open a page (prints URL, title, and a snapshot path) |
| `playwright-cli snapshot` | Write the accessibility tree to a YAML file, print its path |
| `playwright-cli click <ref>` / `fill <ref> <text>` | Act on an element from a snapshot |
| `playwright-cli screenshot [ref]` | Write a PNG, print its path. `[ref]` is an ELEMENT, not a path; do not pass `--filename` (it resolves against the CWD and is not auto-approved) |
| `playwright-cli state-save` / `state-load <file>` | Save or restore a logged-in session. Bare `state-save` writes into the service's own directory; both a name and `state-load` prompt for approval, because each names a local path |
| `playwright-cli attach --extension` | Drive the user's own running Chrome, with their logins |
| `playwright-cli show --port <n> --host 127.0.0.1` | Serve the CLI's dashboard for the Browser panel |

**Browsing workflow:** load the `web-browse`, `web-verify`, or `browser-auth`
skill for the task. On the CLI path:
1. Check `command -v playwright-cli`; if absent, use `web_fetch` and give the
   install command above.
2. Run `playwright-cli open <url>`; read its snapshot YAML only when the tree is
   needed, such as before clicking. Refs (`[ref=e5]`) expire on page changes:
   re-snapshot before the next action.
3. On a login redirect, `state-load` a saved session or let the user sign in in
   the Browser panel, then `state-save`.

**No npm access (internal registry, air-gapped host):** detection is **PATH-based**
-- `playwright-cli` on `PATH` is all that matters, so ANY install route works and the
Settings button is a convenience, not the only one. In order of likelihood:

1. Most internal registries proxy npmjs, so the plain install already works.
2. Force the public registry for this one package:
   `npm install -g @playwright/cli --registry=https://registry.npmjs.org`.
3. **An internal registry that requires a login the user does not have** (the
   common Amazon-internal / corporate case). Install into a user-owned prefix
   against the public registry, ignoring the corporate `.npmrc` for this one
   command, then put the binary on `PATH`:

   ```bash
   NPM_CONFIG_USERCONFIG=/dev/null \
     npm install --prefix ~/.local/share/playwright-cli \
     --registry=https://registry.npmjs.org @playwright/cli@0.1.18
   mkdir -p ~/.local/bin
   ln -sf ~/.local/share/playwright-cli/node_modules/.bin/playwright-cli \
     ~/.local/bin/playwright-cli
   ```

   Two caveats worth stating to the user rather than burying: `~/.local/bin` has
   to be **on `PATH`** or Kiro Crew still reports "not installed" (detection is
   `PATH` + the Node bin dirs, nothing else); and `NPM_CONFIG_USERCONFIG=/dev/null`
   deliberately ignores their employer's registry configuration, which is their
   call to make, not ours to assume.
4. Air-gapped: `npm pack @playwright/cli` on a connected machine, copy the
   `.tgz` over, then `npm install -g ./playwright-cli-<version>.tgz`. Note the
   tarball alone is not runnable -- it needs its `playwright` /
   `playwright-core` dependencies resolved too.

What does **not** substitute for it: `pip install playwright` and
`dotnet tool install Microsoft.Playwright.CLI` install a DIFFERENT tool -- the
`playwright` browser-installer/codegen CLI, not `@playwright/cli` (binary
`playwright-cli`, its own 0.x line, which depends on `playwright@1.63.0-alpha`).
Switching to yarn, pnpm or bun hits the same registry, so it only helps when the
`npm` client itself is missing. And there is **no standalone binary**: the
upstream GitHub release carries no build assets and `playwright-cli.js` starts
with `#!/usr/bin/env node`, so Node.js 18+ is required no matter how it is
fetched.

**Approval:** page-scoped verbs run without prompting the user, because installing
the CLI is itself the consent. Verbs that reach the local machine still prompt on
purpose -- `eval`, `run-code`, `upload`, `state-load`, a named `state-save`, and the
installers. Let the user approve those rather than rewriting the command to dodge
the prompt.

The full verb list is in the skill `playwright-cli install --skills agents --global`
writes.
