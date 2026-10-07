# Kiro Crew validated findings: Tools, agent specs, skills, attachments and prompt rules

Part of the validated findings set; start at [00-index.md](00-index.md). Code verified at commit `397f4be`; sources snapshot 2026-10-07 09:53:08 UTC.

## 5. Tools, agent specs, skills, attachments and prompt rules

### TOOL-12 [65, armed, effort S] session_ledger_read sends each event twice — CONFIRMED
- **Verified claim:** GET /api/session-ledger returns the whole state_record (which itself carries `events`, up to _MAX_EVENTS=100) next to a separate 20-event tail, and the MCP tool session_ledger_read dumps both with json.dumps(indent=2). In steady state (>100 updates recorded) one read carries 120 event objects, 32,878 B / 9,983 o200k tokens, versus the 20 events pipeline-conductor/SKILL.md documents. Dropping state.events plus compact UTF-8 JSON measured -68.2% (3,170 tokens).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/session_ledger.py:94-100 — state_record = read_state(...); events = state_record.get("events", [])[-_MAX_EVENT_TAIL:]; return json_response({"state": state_record, "events": events})
  - src/kiro_crew/session_ledger.py:176-177 — _MAX_EVENTS = 100; _MAX_EVENT_TAIL = 20
  - src/kiro_crew/mcp_tools/ledger.py:179 — return json.dumps({"state": state, "events": events}, indent=2)
  - src/kiro_crew/builtin_skills/pipeline-conductor/SKILL.md:114-116 — 'keeps _MAX_EVENTS = 100 events on disk and session_ledger_read returns only the newest _MAX_EVENT_TAIL = 20'
  - measured (harness, 130 recorded updates): state_events=100, tail=20, event objects in output=120; actual 32,878 B / 9,983 tok; compact -21.1% tok; dedup+compact -68.2% (3,170 tok)
  - src/kiro_crew/dashboard/server_runtime/mcp_routes.py:144 — only registration of the GET route; no website/src consumer (grep), the MCP tool is the only reader
- **Checked by:** ran-new-script (copy of fmt/harness.py, home redirected) + ran-existing-script + read
- **Required outcome:** session_ledger_read returns what its contract says: the state record WITHOUT `events`, plus the 20-event tail, as compact JSON (about -68%, ~6.8k tokens saved per read at steady state; conductors read it every patrol cycle).
- **Solution:**
  1. In dashboard/handlers/session_ledger.py:94-100 build the response from a shallow copy of state_record with `events` popped (`st = dict(state_record); tail = st.pop('events', [])[-_MAX_EVENT_TAIL:]`); the MCP tool is the only consumer of GET /api/session-ledger (mcp_routes.py:144; re-grep src/ and website/src before landing).
  2. In mcp_tools/ledger.py:179 render with the shared compact helper from TOOL-13 (`tool_json`) instead of indent=2; keep ensure_ascii semantics per TOOL-13 step 6.
  3. Update the ledger doc where the read shape is described (memory-skills-hooks / pipeline-conductor SKILL.md:114-116 already state 20).
- **Done when:** A test records 130 updates on a throwaway crew home, calls api_session_ledger_get and session_ledger_read: `'events' not in json.loads(out)['state']`, `len(json.loads(out)['events']) == session_ledger._MAX_EVENT_TAIL`, and the output contains no '\n ' indentation; test_session_ledger.py::test_route_record_and_get_roundtrip stays green.
- **Changed from the source claim:** none — route, tool and the 9,983-token / 120-event / -68% figures reproduce exactly at HEAD.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:TOOL-12, REVIEW_FINDINGS:F1

### TOOL-1 [60, default, effort M] Tool results: first-party cut at 100k chars with no spill; third-party servers uncapped — PARTLY
- **Original claim:** No size cap on tool results unless the (default-off) broker is enabled (corrected below)
- **Verified claim:** First-party results are NOT unbounded: every Kiro Crew stdio MCP server (the 8 managed servers and mochi, all via mcp_shared.run_mcp_stdio_loop) frames results through validation.build_tool_response -> sanitize_response, which cuts at MAX_RESPONSE_LEN = 100,000 chars (~25k tokens) head-only with '…[response truncated]' and no spill path, so a 1 MiB result reaches the model as 100,022 chars and anything tail-anchored is lost. The auto_improvement app's MCP server builds its own frame and bypasses build_tool_response, but it caps its own payload at _MAX_RESULT_CHARS = 60,000 chars (redact first, then a head-only slice with no truncation marker; mcp_server.py:42, :376). Third-party MCP servers are launched by kiro-cli directly and get no Kiro Crew cap at all: the broker (spill + image_budget) runs only for servers in mcp_gateway.stub_servers, which is empty by default ('an empty list means no broker runs at all'); mcp_gateway.enabled=False only controls sharing. With a stub, frames over 256 KiB spill to a 16 KiB inline prefix: measured 250 KiB -> 256,000 chars inline, 260 KiB -> 16,638 chars (the cliff holds). Whether kiro-cli caps tool results itself is unknown here.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/validation.py:166 — MAX_RESPONSE_LEN = 100_000 # truncate tool responses
  - src/kiro_crew/validation.py:1202-1216 — sanitize_response: text = text[:max_len] + '\n…[response truncated]'
  - src/kiro_crew/validation.py:4538-4553 — build_tool_response(text, max_len=MAX_RESPONSE_LEN) 'single exit point for all tool responses'
  - src/kiro_crew/mcp_shared.py:1644-1647 — _tool_response -> build_tool_response(text, is_error=flagged); used at :1701, :2099, :2111, :2135
  - src/kiro_crew/apps/builtins/auto_improvement/backend/mcp_server.py:42, :376-377 — _MAX_RESULT_CHARS = 60_000; text = _redact_result(json.dumps(payload, default=str))[:_MAX_RESULT_CHARS] (own head-only cap, no marker; not routed through build_tool_response)
  - src/kiro_crew/config/integration_sections.py:318-329 — McpGatewayConfig.enabled default=False ('Default False — opt-in')
  - src/kiro_crew/config/integration_sections.py:520-533 — stub_servers default_factory=list: 'Empty by default ... an empty list means no broker runs at all'
  - src/kiro_crew/mcp_gateway/backend.py:2341-2356 — spill only in the broker backend: if len(line) > RESPONSE_SPILL_THRESHOLD_BYTES: maybe_spill_response
  - …and 4 more in the verification results.
- **Checked by:** read + ran-new-script
- **Required outcome:** No tool result enters context above ~32-48 KiB on any path (first-party, app servers, and, where Kiro Crew can reach them, third-party). Oversized results keep head and tail plus a readable path to the full copy; images are downscaled on the same path; there is no 256 KiB cliff.
- **Solution:**
  1. Extract a shared cap_tool_result(text) (head+tail, spill file under the session-scoped spill dir, path in the marker) from mcp_gateway/spill.py:91 maybe_spill_response.
  2. Call it from validation.build_tool_response (validation.py:4538) instead of the head-only cut in sanitize_response (:1210-1215), so every run_mcp_stdio_loop server gets it; keep a tail-anchored session-directive marker intact (the warning at :1207 names the risk).
  3. Route auto_improvement/backend/mcp_server.py:377 through build_tool_response.
  4. Third-party servers: Kiro Crew only sees their results through a stub (backend.py:2341). Whether to stub third-party servers by default is a maintainer decision (it changes process topology; integration_sections.py:520 calls the empty roster deliberate) — raise it, do not flip it in this item.
  5. Set _DEFAULT_SPILL_THRESHOLD (pool.py:154) equal to the inline budget so the broker has no cliff, and run image_budget on the same path (also covers TOOL-9).
  6. Delete the false comment at mcp_tools/workflows.py:393. Update docs/architecture/mcp.md in the same commit. MCP tools stay stateless: the spill path derives from the call, never a module global.
- **Done when:** A unit test calls build_tool_response with a 1 MiB text and asserts the inline text is <= the cap, contains head and tail, and names a spill path that exists and holds the full text; a 250 KiB text is capped too; a text ending in a session-directive marker keeps the marker; a broker frame of 250 KiB and one of 260 KiB both come back <= the cap (maybe_spill_response with a threshold equal to the cap). No clock or network involved.
- **Changed from the source claim:** Overstated for first-party servers: the claim says first-party results are unbounded, but sanitize_response caps them at 100,000 chars (head-only, no path). The gap is the size of that cap, the lost tail and the missing spill path, plus auto_improvement and every third-party server. Broker is off because stub_servers is empty by default, not only because mcp_gateway.enabled=False. Severity 70 -> 60. Cliff numbers re-measured and hold. X8's 4.7M-char result came from the reviewer's own runtime, not kiro-cli; discarded as evidence. Unknown (a), whether kiro-cli caps results itself, is still open.
- **Second reader:** top-20 check: corrected. verified_claim and evidence say the auto_improvement MCP server emits with no cap; it has its own 60,000-char head-only cap (_MAX_RESULT_CHARS, mcp_server.py:42, :376). Rest holds; solution step 3 (route through the shared helper for head+tail+spill) still stands.
- **Sources:** FIX_PLAN:TOOL-1, REVIEW_FINDINGS:U1, verify_needed:X8

### TOOL-8 [60, armed, effort M] Computer-use actions return the full accessibility tree; screenshot is only a file path — PARTLY
- **Original claim:** Computer use returns the whole accessibility tree after every action (corrected below)
- **Verified claim:** Tree half confirmed: every mutating computer-use verb re-walks the window (POST_ACTION_SETTLE then svc.snapshot) and returns the whole rendered accessibility tree at the cached walk budget (default DEFAULT_MAX_TREE_NODES = 1200, schema ceiling 5000), and render_tree has only a per-field text_limit (default 500), no total budget — the only bound is the 100,000-char transport cut, which drops the trailer/notes first. computer_get_state's description says 'CALL THIS FIRST every turn'. Screenshot half overstated: results are text-only; get_state attaches a screenshot PATH by default (DEFAULT_ATTACH_SCREENSHOT = True, 1280 px / q55, ~8.3k tokens only if the model reads the file), and mutating verbs force want_image=False. Armed: the kirocrew-computer server is spec-gated on the keystone opt-in and served 0 tools in a throwaway home.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/computer_use/types.py:364-365 — DEFAULT_MAX_TREE_NODES = 1200; MAX_TREE_NODES_LIMIT = 5000
  - src/kiro_crew/computer_use/types.py:368 — DEFAULT_TEXT_LIMIT = 500 (per field)
  - src/kiro_crew/computer_use/tools.py:693-705 — 'A refreshed structural view follows every action, but WITHOUT pixels'; req = replace(..., want_image=False)
  - src/kiro_crew/computer_use/tools.py:731-758 — time.sleep(POST_ACTION_SETTLE_SECS); snap = svc.snapshot(...); body = _render_snapshot(...); returns header + full body
  - src/kiro_crew/computer_use/render.py:78-113 — render_tree: per-field text_limit, one line per element, no total cap
  - src/kiro_crew/mcp_computer.py:363-372 — get_state description: '...the path to a compressed screenshot. CALL THIS FIRST every turn before any click/type/scroll'
  - src/kiro_crew/mcp_computer.py:398-403 — screenshot: 'Attach a screenshot path. Defaults to the user's setting'
  - src/kiro_crew/computer_use/types.py:392-399 — DEFAULT_ATTACH_SCREENSHOT = True; 1280 px / q55 '~8.3k tokens vs ~41k for a raw PNG'
  - …and 2 more in the verification results.
- **Checked by:** read + ran-existing-script
- **Required outcome:** After an action, return only the changed rows or the neighbourhood of the element acted on (<= ~8k chars); computer_get_state keeps a total budget of ~24k chars with an explicit truncation note; ~400 nodes by default; the description stops mandating a get_state call every turn; screenshot stays a path (no change needed beyond keeping it <= 1024 px if desired).
- **Solution:**
  1. In computer_use/tools.py:731-758 diff the post-action snapshot against the cached one (per session, held in the ComputerUseService session cache — never a module global) and render changed rows plus the acted element's neighbourhood; the full tree stays one computer_get_state call away.
  2. Add a total character budget to render_tree (computer_use/render.py:78) that keeps the header, the TRUNCATED/DEPTH notes and the screenshot note, and says how many elements were left out.
  3. Lower DEFAULT_MAX_TREE_NODES (types.py:364) to ~400 — check the Chrome note at types.py:571-578 (a truncated walk suppresses the screenshot) and the drift-check comment at :1159 before changing it; this is a take-away change.
  4. Reword mcp_computer.py:368 from 'CALL THIS FIRST every turn' to 'call before the first action and after the UI changed outside your actions'.
  5. Rules: add no computer_use.* governance scopes, do not touch click_method, keep refusals in band on tools._dispatch. Update docs/system-specs/modules/computer-use.md.
- **Done when:** Driver-faked tests (the existing fake ComputerUseService): a click on a 1,200-node snapshot returns <= 8,000 chars containing the acted element's row; computer_get_state on a 5,000-node fake returns <= 24,000 chars with the truncation note and the screenshot note intact; the per-session cache is keyed by session_key (two sessions do not share diffs).
- **Changed from the source claim:** Screenshot sub-claim corrected: it is a path in a text result (the model pays ~8.3k tokens only if it reads the file), and mutating verbs never capture one. The tree sub-claim holds; 'no total cap' is true except for the 100k transport cut. Severity 70 -> 60 (when used).
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:TOOL-8, REVIEW_FINDINGS:U3, REVIEW_FINDINGS:U8

### TOOL-2 [55, default, effort L] ~30k tokens of always-on tool definitions resent on every request — CONFIRMED
- **Verified claim:** The default kirocrew spec mounts @kirocrew-core (83 tools; tools/list reply 110,309 B, 107,043 B as compact per-tool JSON) and @kirocrew-cron (9 tools; 13,650 B reply, 13,181 B compact) unconditionally: 120,224 B compact (~30k tokens at chars/4) of schemas per request. The default spec grants tool_search, but kiro-cli defers only when the specs exceed EITHER tool_search_min_pct (5% of the window) or tool_search_min_tokens (50,000) — i.e. above min(5% x window, 50k) — and only on kiro-cli >= 2.27.0 (older/unknown versions pin every Crew server resident via ASBX_KIRO_MANDATORY_MCPS). On a 1M window the bar is 50k tokens, so the ~30k never defers unless the user's own MCP servers push the total over it; on a 200k window the bar is 10k and they would defer. The always-on set includes the 8 app/dev tools (issue_radar_*, ops_mission_control_api, pod_*; 13,682 B) with no enablement gate in schemas(), and 19 artifact tools (13,236 B). The heartbeat spec mounts @kirocrew-core with no tool_search (107,043 B never deferrable); worker and research specs also mount @kirocrew-cron. The 1M window for 'auto' is stated by the code, not measured live.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/defaults.json:17-19 — "tool_search", "@kirocrew-cron", "@kirocrew-core" in the default tools
  - src/kiro_crew/agent.py:1082-1083 — _MANAGED_MCP_SERVERS: kirocrew-cron / kirocrew-core with no opt_in and no spec_gate ('Absent = always emitted')
  - src/kiro_crew/mcp_tools/__init__.py:24-56 — DOMAIN_MODULES incl. 'artifacts', 'apps'; build_tool_list concatenates every module's schemas() with no enablement gate
  - src/kiro_crew/mcp_tools/apps.py:36-38 — def schemas(): return [ ...issue_radar_*, ops_mission_control_api, pod_* ] (no app-enabled check)
  - src/kiro_crew/config/sections.py:1186-1205 — tool_search_min_pct default=5, tool_search_min_tokens default=50000, 'whichever is crossed first wins'
  - src/kiro_crew/providers/acp.py:229-233 — 'kiro-cli activates deferral when EITHER is exceeded'
  - src/kiro_crew/acp/harness/_common.py:76-101 — pin_mandatory_mcps_env: Crew servers defer only on the pinned kiro-cli >= 2.27.0, else all crew_owned_mcp_servers() kept resident
  - src/kiro_crew/context_assembly/budget.py:166-168 — 'the default deployment runs provider=acp + model="auto" ... ACP auto actually runs a 1M-window model'
  - …and 3 more in the verification results.
- **Checked by:** read + ran-existing-script + ran-existing-script + ran-new-script
- **Required outcome:** A default session loads <= 10k tokens (~40 KB) of Kiro Crew tool definitions, whatever the window or backend.
- **Solution:**
  1. Split kirocrew-core into an always-on base of ~25 verbs (spawn, skills, learn, ask_question, send_message, memory, wait) plus opt-in servers, using the existing opt_in: True pattern in _MANAGED_MCP_SERVERS (agent.py:1116-1191).
  2. Make kirocrew-cron opt-in or keep only cron_list resident (agent.py:1081).
  3. Move mcp_tools/apps.py (13,682 B) into kirocrew-apps / kirocrew-dev, granted by the app or developer agent spec, or gate schemas() on the app being installed and enabled (#16099).
  4. Collapse the 19 artifact tools (mcp_tools/artifacts.py:52) into artifact, artifact_folder and artifact_comment with an action enum.
  5. Optionally lower tool_search_min_tokens (config/sections.py:1197) so Crew servers defer on 1M windows — only after validating #16059 (Tool Search resume delivers an empty context) and reviewing the closed deferral PRs #17250/#17271. Steps 1-4 do not depend on Tool Search; do them first.
  6. Every split is a take-away change for an agent that used a moved verb: read docs/system-specs/common/take-away-changes.md, grep docs/decisions/, grant the moved servers to the conductor/worker/app specs that name their verbs. Harness identity stays positive (is_kiro_backend); run scripts/check_harness_parity.py. Update docs/architecture/mcp.md (Managed servers).
- **Done when:** A CI test builds the default spec in a throwaway home (agent.build_agent_config()), resolves each @server/@server/tool ref against that server's _list_tools(), and asserts the compact-JSON byte sum <= 40,000; a second assertion pins that every verb named in prompt.md / the default skills is still mounted. scripts/check_harness_parity.py passes.
- **Upstream:** #13232 #15899 #16099 #4749 #16059 PR #17250 PR #17271 PR #16127 PR #15367 (state unverified)
- **Changed from the source claim:** Numbers re-measured at HEAD and hold (core 83 tools / 110,309 B; cron 9 / 13,650 B; 181,625 B for all 8). The three payload figures reconcile: 123.6 KB (Y2) = core+cron with default json.dumps separators (123,673 B); 120.6 KB = compact per-tool JSON (120,224 B here; 120,672 B in the prior home, the 448 B gap is spawn_run/spawn_sub_agents descriptions that embed the live agent roster); 181.6 KB = all 8 servers' tools/list replies. verification_needed sink #5's '~22+ tools' is wrong (92 tools always mounted). X9 ('tools.search returns []') probed the reviewer's own runtime, not Kiro Crew's tool_search; discarded. Severity stays 65 only on the 1M premise; on a 200k window with kiro-cli >= 2.27 it drops to ~40. Spot-check: the headline severity rests on CTX-20's unverified auto=1M premise (CTX-20 is CANT_VERIFY_HERE; the registry row and the meter backfill say auto=200k), the same premise that moved CTX-1 from 75/default to 45/pinned. Set 55 pending CTX-20 (the resident cost is certain on kiro-cli < 2.27, on the heartbeat spec with no tool_search and on harnesses without deferral); back to 65 if CTX-20 confirms 1M, ~40 if it shows 200k.
- **Second reader:** top-20 check: corrected. Claim and evidence hold (measured numbers check out), but severity 65/default rests on CTX-20's unresolved auto=1M premise, which the record itself concedes ('drops to ~40' at 200k). CTX-1 was lowered 75->45 for the same premise, so the two are ranked inconsistently.
- **Sources:** FIX_PLAN:TOOL-2, REVIEW_FINDINGS:H-P1, REVIEW_FINDINGS:H-P3, REVIEW_FINDINGS:H-P5, REVIEW_FINDINGS:Part5b§5.1, REVIEW_FINDINGS:§6.1, verify_needed:O5, verify_needed:X9, verify_needed:X14, verify_needed:G16(#15899), verify_needed:G18(#16099), verify_needed:G25(#13232), verify_needed:G31(#4749), verify_needed:G36, verify_needed:G38, verify_needed:G41, verify_needed:G42, verify_needed:G43, verify_needed:G44, verification_needed:sink#5, verification_needed:opt(lazy-schemas), verification_needed:refactor(lazy-schemas)

### TOOL-4 [55, default, effort S] spawn_status default: full transcript, cut at 100k chars, so the closing answer is lost — PARTLY
- **Original claim:** spawn_status returns the full transcript by default (corrected below)
- **Verified claim:** With no offset/limit/grep, spawn_status asks /api/spawn/<id> for the full retained transcript (the schema says 'Omit for the full transcript'; _apply_result_view returns the text unchanged 'so the default spawn_status contract (full transcript) is preserved'), and the route reads the whole result.txt (capped on disk at RESULT_FILE_MAX_BYTES = 512,000 B). The model does not receive 512 KB, though: the MCP transport (build_tool_response -> sanitize_response) cuts every first-party result to 100,000 chars head-only, so a no-argument read of a large transcript costs ~25k tokens AND drops the tail, which is where the subagent's closing answer is (measured: a 438 KB transcript -> 100,022 chars, closing line lost). Separately, spawn_sub_agents compares against the constant COMPLETION_KEEP_DEFAULT_CHARS (3000) instead of the configured agent.completion_keep_chars.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/spawn.py:525-556 — spawn_status schema: limit 'Max lines to return (1-2000). Omit for the full transcript'
  - src/kiro_crew/mcp_tools/spawn.py:1179-1196 — spawn_status sends offset/limit/grep only when the caller passed them
  - src/kiro_crew/dashboard/messaging_api/run_views.py:87-106 — _apply_result_view: no params -> 'return text, {}' ('full transcript' contract)
  - src/kiro_crew/dashboard/messaging_api/run_views.py:198-212 — 'Read full result from disk (info.result is truncated to 3000 chars)'
  - src/kiro_crew/context_management.py:24 — RESULT_FILE_MAX_BYTES = 512_000
  - src/kiro_crew/validation.py:166 — MAX_RESPONSE_LEN = 100_000 (head-only cut applied to every first-party tool result)
  - src/kiro_crew/mcp_tools/spawn.py:1544 — if len(result_text) > COMPLETION_KEEP_DEFAULT_CHARS: (constant, not agent.completion_keep_chars)
  - src/kiro_crew/config/sections.py:1769 — completion_keep_chars: int = field(...) (the configured value spawn_sub_agents ignores)
  - …and 1 more in the verification results.
- **Checked by:** read + ran-new-script
- **Required outcome:** With no arguments spawn_status returns <= 12k chars: the TAIL of a finished transcript (last ~200 lines, where the answer is) with a has_more/offset header; while running, the tail since a cursor; spawn_sub_agents uses the configured agent.completion_keep_chars.
- **Solution:**
  1. Keep GET /api/spawn/{id}'s no-parameter contract (full transcript): the dashboard ActivityViewer (website/src/pages/chat/ActivityViewer.tsx:86 renders d.result), the CLI poll (cli_commands.py:442) and spawn_sub_agents (mcp_tools/spawn.py:1462/:1500 -> :1544, which summarizes the FULL text — the property EVT-2 relies on) all read it with no params.
  2. Apply the bounded default in the MCP tool only: in mcp_tools/spawn.py:1179-1196, when the caller passes no offset/limit/grep, request an explicit tail view (e.g. a new `tail` query param handled by _apply_result_view in dashboard/messaging_api/run_views.py:87-106) capped at <= 12k chars with meta {total_lines, offset, has_more}; explicit offset/limit/grep behave as today.
  3. Update the spawn_status schema text at mcp_tools/spawn.py:553 ('Omit for the full transcript') to describe the tail default and how to page from the start. This changes the tool's documented default, so treat it as a take-away change (take-away-changes.md; Reader: config/prompt.md:30 'spawn_status reads the finished transcript' and skills that call spawn_status).
  4. In mcp_tools/spawn.py:1544 read the configured keep (agent.completion_keep_chars, honouring 0 = no truncation) the way subagent_manager/run.py:626-638 does, resolving it per call (no module global; MCP tools stay stateless).
  5. Update docs/system-specs/modules/subagent.md for the new default.
- **Done when:** A handler test stubs mcp_core._get / the result file with a 400 KB transcript: a no-arg spawn_status returns <= 12,000 chars, includes the last line, and states has_more with the next offset; paging with the returned offsets reaches line 0 and the end; a spawn_sub_agents test with completion_keep_chars=500 summarizes a 1,000-char result and with 0 does not. A route test asserts a no-parameter GET /api/spawn/{id} still returns the full transcript (dashboard ActivityViewer, CLI poll and spawn_sub_agents unchanged).
- **Changed from the source claim:** Overstated cost: the transport's 100,000-char cap means one no-arg call is ~25k tokens, not up to ~128k; but the same cap makes the default worse in a way the claim missed — it keeps the head and drops the closing answer. Both sub-claims confirmed. Severity 60 -> 55.
- **Second reader:** top-20 check: corrected. Solution step 1 changes the shared route's no-parameter contract in run_views._apply_result_view to a tail. Three other readers depend on it returning the full transcript: the dashboard ActivityViewer (ActivityViewer.tsx:86 renders d.result), the CLI poll (cli_commands.py:442) and spawn_sub_agents (spawn.py:1462/:1500 -> :1544, whose full-text preview EVT-2 relies on). The bounded default must be applied in the MCP tool, not at the route. The tool-default change is also an unnamed take-away change. Claim, evidence and severity hold.
- **Sources:** FIX_PLAN:TOOL-4, REVIEW_FINDINGS:U4

### ATT-1 [50, default, effort M] Channel attachments: no per-message inline total — CONFIRMED
- **Verified claim:** messaging/attachments.py IngestLimits caps each attachment (text/document text inlined up to max_text_inject = 50 KiB chars, max 10 attachments) but has no per-message inline total; text is truncated head-only ('[... truncated]'), and the downloaded text/document file is deleted in the `finally` of the same loop, so the agent gets no path to the rest. A text file over max_text_bytes (512 KiB) is rejected with only a size note — no content, no path. Every channel that ingests attachments shares this code (slack, discord, telegram, teams, whatsapp, wecom, weixin, webex). Re-measured through the real ingest_attachments with Slack's limits: 400 KB log -> 51,244 chars head only, file not kept; 20-page PDF -> 51,255 chars; 10 x 300 KB CSV in one message -> 512,501 chars (~128k tokens) inline; 2 MB log -> an 87 B rejection note.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/messaging/attachments.py:91-103 — `class IngestLimits`: `max_text_inject: int = 50 * 1024`, `max_attachments: int = 10`; no per-message total field
  - src/kiro_crew/messaging/attachments.py:293-295 — `_truncate`: `return content[:limit], "\n[… truncated]"` (head only)
  - src/kiro_crew/messaging/attachments.py:495-496 — TEXT: `_read_text_file(dest, lim.max_text_inject)` appended as `[File: ...]` block
  - src/kiro_crew/messaging/attachments.py:526-527 — DOCUMENT: `_clean_text(raw, lim.max_text_inject)` appended as `[Document: ...]` block
  - src/kiro_crew/messaging/attachments.py:533-539 — `finally: # Anything not handed to the caller is deleted here. ... os.unlink(dest)`
  - src/kiro_crew/messaging/attachments.py:366 — oversize: `[Attachment {name} ({size} bytes) — too large, limit {cap}]` (no path kept)
  - src/kiro_crew/slack/files.py:75-81 — Slack `_LIMITS = IngestLimits(max_text_bytes=512*1024, ..., max_text_inject=50*1024)`; ingest_attachments( is called from discord/, teams/, telegram/, webex/, wecom/, weixin/, whatsapp/ attachments.py too
  - measured (att_run.py, real ingest_attachments + append_attachment_context): mid.log 409,661 B -> 51,244 chars, file_kept=False; report.pdf -> 51,255; 10 x data.csv (307,276 B) -> 512,501 chars; big.log 2,097,185 B -> 87 B rejection (all identical to prior)
- **Checked by:** ran-existing-script + read
- **Required outcome:** Per file: a head+tail preview of <= 8 KB plus the full file kept by a path the agent can read; per message: <= 32-48 KB of attachment text inline; oversized files kept by path with a preview, not rejected; the stored files are removed with the session.
- **Solution:**
  1. Add `max_inline_total` to IngestLimits (messaging/attachments.py:91-103) and enforce it in ingest_attachments across the loop (:340), switching later files to preview-only once the total is spent.
  2. Replace head-only `_truncate` (:293) with a head+tail preview for attachments, still redacting first (`_clean_text` order).
  3. Keep the full file (and oversized text files, instead of the :366/:407 rejection) in a session-scoped store with the same deletion contract as chat_attachments.py (copied beside the session, removed with it via stage_attachments_removal/purge), never a bare system-temp path; the bytes are decrypted on encrypting channels (note at attachments.py:331-338), so the store must sit under a crew-home location that is not agent-shared across sessions.
  4. The path handed to the agent must be readable by the session's sandboxed file tools WITHOUT widening any sandbox fence (AGENTS.md: sandbox scope is the operator's) — e.g. place the copy in the session tree's own scratch/work window that sandbox.py already exposes to that session (`$KIROCREW_SCRATCH`, the extra_private_dirs window), and verify against upstream #15145 (ATT-3).
  5. TAKE-AWAY CHANGE: less text is inlined than today; keep a preview and say the full text is at the path. Read docs/system-specs/common/take-away-changes.md; update docs/system-specs/modules/messaging.md (attachment ingestion) in the same commit.
- **Done when:** ingest_attachments tests with a fake download (copyfile) and Slack limits in a tmp KIROCREW_HOME: 10 x 300 KB CSVs -> <= 48 KB inline total plus 10 readable stored paths; a 2 MB log -> a stored path plus a head+tail preview (no rejection); a 400 KB log preview contains its last line; removing the session removes the stored files; the redaction tests still pass.
- **Upstream:** #15145 (state unverified)
- **Changed from the source claim:** none; every measured number re-ran identical (51,244 / 51,255 / 512,501 chars, 87 B rejection).
- **Sources:** FIX_PLAN:ATT-1, REVIEW_FINDINGS:Part6/ATT-1, verify_needed:Y1, verify_needed:G44

### OUT-1 [50, default, effort M] Empty-reply auto-continue fires after intentionally final tool calls — CONFIRMED
- **Verified claim:** With session.empty_response_auto_continue on by default and only ask_question exempt (_record_terminal_question), a depth-0 dashboard turn that ends text -> suggest_followup / monitor_start / spawn_run -> end_turn with no closing text queues _ACTIVITY_NO_REPLY_CONTINUE_MSG (rung=continue), although those tools' receipts say 'End your turn now' / 'END YOUR TURN'. Reproduced with the real _run_chat and scripted ACP events: continuation queued for suggest_followup, monitor_start and spawn_run; none for ask_question or when one closing line follows. Each hit is one more full-context request (a fresh dashboard first message is 52,305 chars plus ~124 KB of core+cron schemas). Structured monitor turns run at _prompt_depth=1 and are exempt; unstructured prompt-loop turns run at depth 0 and get the same ladder. Frequency is unmeasured.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/sections.py:1880-1881 — empty_response_auto_continue: bool = field(default=True, ...)
  - src/kiro_crew/dashboard/chat_runner.py:9067-9070 — _record_terminal_question: only kind == 'ask_question' and outcome.startswith(QUESTION_CARD_SHOWN_PREFIX) sets _terminal_question_posted
  - src/kiro_crew/dashboard/chat_runner.py:15963-15967 — empty verdict guarded by `not _terminal_question_posted`; :16032-16035 continue rung requires _prompt_depth == 0 and _empty_auto_continue_enabled(); :16090 _empty_continue_msg = _ACTIVITY_NO_REPLY_CONTINUE_MSG
  - src/kiro_crew/mcp_tools/control.py:2176 — suggest_followup receipt '... must not be lost. End your turn now.'; :1678 monitor_start and :1850 monitor_watch 'End your turn now.'
  - src/kiro_crew/mcp_tools/spawn.py:991-997 — 'END YOUR TURN now'; :1033 spawn_continue 'END YOUR TURN and wait for it.'
  - src/kiro_crew/dashboard/session_directive_apply.py:1896 — 'Follow-up card shown below the composer.'; :734 autonudge 'End your turn now — the loop wakes you.'
  - src/kiro_crew/config/prompt.md:43 — 'Report the dispatch and **END YOUR TURN**'; :58 suggest_followup 'at END of a large finished task'
  - src/kiro_crew/slack/gateway.py:7415-7420 — structured monitor turns pass _prompt_depth = 1 ('Nested depth disables dashboard recovery paths')
  - …and 2 more in the verification results.
- **Checked by:** ran-existing-script (copy, homes redirected) + ran-existing-script (copy) + read
- **Required outcome:** A turn whose last call is an intentionally final tool (suggest_followup, monitor_start/monitor_watch, autonudge arm, spawn_run/spawn_continue with at least one dispatched run) gets no synthetic continuation turn.
- **Solution:**
  1. Generalize _record_terminal_question (dashboard/chat_runner.py:9067) into a positive table keyed on canonical tool identity and its applied outcome: ask_question -> QUESTION_CARD_SHOWN_PREFIX; suggest_followup -> 'Follow-up card shown below the composer.' (session_directive_apply.py:1896); monitor_start/monitor_watch/autonudge -> their 'armed' outcome.
  2. For spawn_run/spawn_continue either exempt them when the receipt names at least one dispatched run, or reword the receipt (spawn.py:991-997, :1033) to ask for a one-line closing reply.
  3. Keep the guard positive (a named set of terminal tools), never 'any tool'.
  4. Update docs/system-specs/modules/session.md (empty-reply ladder section, ~:653-725).
- **Done when:** In test_dashboard_chat.py TestProductiveTurnNeverReplaysVerbatim (:20676), parametrized text -> {suggest_followup, monitor_start, monitor_watch, spawn_run} -> end_turn runs with scripted ACP events never queue _ACTIVITY_NO_REPLY_CONTINUE_MSG; test_text_then_tool_turn_continues_instead_of_replaying (:20773) stays green for a non-terminal tool. On an install: grep the gateway log for 'Empty model response for slot … rung=continue' (chat_runner.py:16151).
- **Changed from the source claim:** Reproduced exactly. Refinement: structured monitor turns run at _prompt_depth=1 (slack/gateway.py:7420) and are already exempt from the continue rung; the 'prompt loops multiply LOOP-2/3' note holds only for unstructured prompt-loop turns at depth 0. The slack/gateway.py:7525 pointer has drifted. '>=38k tokens per hit' is consistent with the re-measured 52,305-char first message + 123,959 B core+cron schemas.
- **Sources:** FIX_PLAN:OUT-1, REVIEW_FINDINGS:Part6/OUT-1

### TOOL-5 [50, default, effort S] get_chat_session has no per-message or total cap — CONFIRMED
- **Verified claim:** get_chat_session renders up to max_messages (default 50, max 200) messages with each message's full content and no per-message or total cap in the handler or the projection. The only bound is the MCP transport's 100,000-char head-only cut (sanitize_response), which, because the messages are the newest N in chronological order, drops the MOST RECENT messages of a long transcript instead of the oldest. Replay elsewhere caps user 8,000 / assistant 4,000 chars per message.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/sessions.py:279 — max_messages = args.get('max_messages', 50)
  - src/kiro_crew/validation.py:3613-3618 — GET_CHAT_SESSION_SCHEMA max_messages min 1 max 200 default 50
  - src/kiro_crew/mcp_tools/sessions.py:352 — messages = cl.derive_recent(key, max_messages=max_messages, roles=RECALL_ROLES)
  - src/kiro_crew/mcp_tools/sessions.py:381-387 — lines.append(f"**{role}:** {m.get('content', '')}") for every message, no truncation
  - src/kiro_crew/history_projection.py:183-204 — recent(): returns {'role','content'} for messages[-max_messages:] unmodified
  - src/kiro_crew/validation.py:166, :1210-1215 — transport cut text[:100_000] + '…[response truncated]' (keeps the oldest of the returned messages)
  - src/kiro_crew/context_assembly/replay.py:107-108 — replay caps user_cap 8000, assist_cap 4000 (not applied here)
- **Checked by:** read
- **Required outcome:** Each message is capped the same way as replay (user 8k, assistant 4k, tool 2k, head+tail with a marker); the total is <= ~40k chars with a next/before cursor, and when the budget binds the newest messages are the ones kept.
- **Solution:**
  1. In mcp_tools/sessions.py:381-387 cap each message's content with the replay caps (reuse the helper behind context_assembly/replay.py:107-108 rather than new literals).
  2. Fill a total budget (~40,000 chars) from the newest message backwards and add a header line with a 'before' cursor (message index) when older messages were left out; accept an optional 'before' arg in GET_CHAT_SESSION_SCHEMA (validation.py:3613).
  3. Put the limits in mcp_tools/_limits.py and quote them in the description from there. Keep the tool stateless (the cursor is an index the caller passes back).
- **Done when:** A handler test with a ConversationLog fixture of 60 messages of 20,000 chars each: output <= 40,000 chars plus header, contains the newest message, every message body is <= its role cap with a truncation marker, and calling again with the returned 'before' cursor returns the next older page.
- **Changed from the source claim:** Holds as stated; adds that the transport's 100k head-only cut is the de-facto total and it drops the newest messages, the opposite of what a reader wants.
- **Second reader:** random-sample check: agreed.
- **Sources:** FIX_PLAN:TOOL-5, REVIEW_FINDINGS:U5

### ATT-2 [45, default, effort M] Large dashboard pastes go inline into the prompt — CONFIRMED
- **Verified claim:** The dashboard composer collapses a large paste into a chip only for display; on send, buildOutgoingTurn expands every chip back to its full content into the wire text, and api_chat reads the body with read_bounded_json(max_bytes=None) (bounded only by the app-wide 60 MiB client_max_size) and applies no per-message cap. The composer only warns at 0.9 of the model window. So a 200 KB paste reaches the prompt whole: measured 204,871 B of prompt text for the user message, while the same content attached as a file costs a ~100 B `[attached_file N] /path` line (four files incl. a 2 MB log: 523 B). On the kiro-cli path that user message stays in native history and is replayed on later turns (FIX_PLAN 0.2; not measured here).
- **Evidence (at `397f4be`):**
  - website/src/chat-core/composer/outgoingTurn.ts:157 — `let wire = pastes.length ? expandAll(linked, pastes) : linked`
  - website/src/utils/pasteTokens.ts:202-211 — expandAll substitutes each token with `r.block.content` (full paste)
  - website/src/components/composerPromptLength.ts:5-8 — '`POST /api/chat` (`api_chat` ...) applies no per-message cap of its own'; :24 `PROMPT_LENGTH_WARN_RATIO = 0.9`
  - src/kiro_crew/dashboard/chat_handlers.py:687 — `body, body_err = await read_bounded_json(request, max_bytes=None)`
  - src/kiro_crew/dashboard/server.py:1605 — `client_max_size=60 * 1024 * 1024` (the only bound)
  - measured (att_run.py via build_prompt_blocks): 200 KB paste -> 204,871 B prompt text (prior 204,871 B); [attached_file] x4 (2 MB log, pdf, csv, png) -> 523 B text
- **Checked by:** ran-existing-script + read
- **Required outcome:** A paste over ~32 KB is stored as a session file and sent as `[attached_file N] /path` plus a head/tail preview, not inlined whole.
- **Solution:**
  1. Server side (preferred, covers every client): in api_chat (dashboard/chat_handlers.py:687 onward), when `meta.pastes` blocks expand to more than ~32 KB, write each large block to the session's attachment store (same deletion contract as chat_attachments.py; path readable by the session's sandboxed file tools without widening any fence, see ATT-1/ATT-3) and replace it in the message with `[attached_file N] /path` + a head/tail preview of <= 4 KB.
  2. Or client side: in buildOutgoingTurn (outgoingTurn.ts:157) upload chips above the threshold through the existing file-attach route and send the path instead of expandAll content; follow website/AGENTS.md and update composerPromptLength.ts's header comment.
  3. TAKE-AWAY CHANGE: the model no longer sees the whole paste inline; keep a preview and say where the full text is (docs/system-specs/common/take-away-changes.md; grep docs/decisions/ — no entry found at HEAD). Update docs/architecture/context-management.md / the dashboard chat spec in the same commit.
- **Done when:** A backend test posts a 200 KB paste through api_chat's message-preparation path with a fake slot and tmp KIROCREW_HOME and asserts the text reaching ContextBuilder.build_message is <= 4 KB plus an `[attached_file N] <path>` line whose file holds the full paste; a frontend vitest asserts buildOutgoingTurn leaves small pastes inline (unchanged behaviour below the threshold).
- **Changed from the source claim:** none; 204,871 B re-measured identical. Replay-on-every-later-turn is kiro-cli native history behaviour (not measured here).
- **Sources:** FIX_PLAN:ATT-2, REVIEW_FINDINGS:Part6/ATT-2

### OUT-2 [45, default, effort M] Subagent, workflow, task, cron turns get the hard diff rule; [OPTIONS:] only on choices — PARTLY
- **Original claim:** Subagent/workflow/task-runner turns are told to emit diff blocks and [OPTIONS:] (corrected below)
- **Verified claim:** _critical_rules_for gives every non-dashboard runtime source the hard _DIFF_RULE_CHANNEL ('No exceptions — even single-line changes MUST get a diff block'), and prompt.md's Output Format repeats it unconditionally; measured on the first message of subagent, workflow-step, taskrunner, cron (minimal_context=False), Slack and CLI sessions (all hard_rule=True), while only cron minimal_context=True escapes the hard rule. The per-turn re-assertion (71 tokens) is added only on follow-ups whose caller passes runtime_source (workflow steps, channels) — subagent follow-ups do not pass it and were not re-asserted. The [OPTIONS:] rule is conditional ('when presenting choices') rather than always required, but subagent results strip it (run.py:3221). Measured output cost of the mandated diff: +646 tokens creating a 40-line file, +4,756 for 400 lines, 88-130 per small edit; 3 diffs filled 3,329 of a 3,836-char subagent transcript, so the closing summary was lost past the 3,000-char completion keep.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:1197-1205 — _DIFF_RULE_CHANNEL '... No exceptions — even single-line changes MUST get a diff block.'
  - src/kiro_crew/context.py:1324-1336 — _critical_rules_for: return _CRITICAL_RULES if source == 'dashboard' else _CRITICAL_RULES_CHANNEL
  - src/kiro_crew/context.py:1214-1216 — 'When presenting choices or options to the user, you MUST end your response with [OPTIONS: ...]' (conditional)
  - src/kiro_crew/context_assembly/sections.py:69-72 — subagent: and taskrunner keys resolve to non-dashboard sources; :471-478 per-turn 'For THIS turn ... you MUST include a ```diff' block
  - src/kiro_crew/context.py:3394-3395 — per-turn refresh only `if not is_new_session and runtime_source`
  - src/kiro_crew/workflow_memory.py:388 — runtime_source='workflow'
  - src/kiro_crew/config/prompt.md:5 — 'After ANY file change ... show a ```diff block unless the latest injected critical rule or [RUNTIME] surface note relaxes it'
  - src/kiro_crew/subagent_manager/run.py:3220-3221 — Strip [OPTIONS: ...] tags from subagent results
  - …and 3 more in the verification results.
- **Checked by:** ran-existing-script (copy) + ran-existing-script (copy) + read
- **Required outcome:** Subagent, workflow-step and task-runner turns (model-read surfaces) emit no diff blocks and no [OPTIONS:] line; dashboard and channel rule constants stay byte-identical (Slack/cron is OUT-2b).
- **Solution:**
  1. Add a third fixed rule variant in context.py (next to _DIFF_RULE_DASHBOARD/_DIFF_RULE_CHANNEL, :1176/:1197): no diff blocks, no [OPTIONS:], end with one line per changed file (path, +N/-M).
  2. Select it in _critical_rules_for (:1324) by a POSITIVE set of sources {subagent, workflow, taskrunner} (optionally heartbeat/background) — one new row, consistent with CTX-8's single authoritative rule.
  3. Skip the per-turn diff re-assertion for that set in sections.runtime_refresh_blocks (:471-478) and make prompt.md:5 defer to the injected rule.
  4. Do NOT change Slack, cron or CLI here (OUT-2b, a take-away change).
  5. Take-away flag: the subagent card's Output panel (website/src/pages/chat/ActivityViewer.tsx:339-350) shows only subagent text, so a parent/user loses the inline diff there; read docs/system-specs/common/take-away-changes.md.
- **Done when:** Unit tests: _critical_rules_for('subagent:a', None), _critical_rules_for('w', 'workflow') and _critical_rules_for('taskrunner:t', None) contain neither 'No exceptions' nor '[OPTIONS:'; _CRITICAL_RULES and _CRITICAL_RULES_CHANNEL are byte-identical to a pinned copy; runtime_refresh_blocks('w', 'workflow') carries no ```diff mandate.
- **Changed from the source claim:** Diff-mandate numbers reproduce exactly. Overstatements corrected: [OPTIONS:] is required only 'when presenting choices' (context.py:1214), and the per-turn re-assertion reaches only follow-ups that pass runtime_source (workflow, channels), not subagent follow-ups (context.py:3394). Hence PARTLY; the fix is unchanged.
- **Sources:** FIX_PLAN:OUT-2, REVIEW_FINDINGS:Part6/OUT-2, verify_needed:B1

### TOOL-13 [45, default, effort M] Model-facing JSON pretty-printed and ASCII-escaped at ~20 sites — CONFIRMED
- **Verified claim:** Model-facing MCP results are pretty-printed and/or ASCII-escaped at 29 sites at HEAD: 20 model-facing json.dumps(indent=2) sites (24 indent sites in mcp_tools/, mcp_*.py and app mcp_server.py, minus 4 file writers) of which 9 keep the default ensure_ascii=True and 11 already pass ensure_ascii=False, plus 9 compact-but-ASCII-escaped sites (spawn.py x6, apps.py:583, browser.py:308, auto_improvement mcp_server.py:376). Compact UTF-8 measured -21.1% (ledger), -24.3% (workflow_result), -30.8% (cron_list json), -32.7% (crew_log_read), -23.2% (work_brief) of o200k tokens in English; -50.5% (workflow_result CJK) and -64.4% (spawn_sub_agents records CJK). Byte budgets in mcp_cron._render_cron_list_json and mcp_crew_log/_debug/_work renderers are measured on indented text. Safety gap: mochi goes through mcp_shared.build_tool_response -> sanitize_response, but the auto_improvement backend server has its own stdio loop that only redacts and slices to 60,000 chars (no sanitize_response), so ensure_ascii=False there would let bidi/zero-width chars through.
- **Evidence (at `397f4be`):**
  - AST survey a throwaway script 58 json.dumps calls in MCP modules, 24 with indent=2 (4 are file writers: mochi/mcp_server.py:680, mcp_cleanup.py:446, mcp_quarantine.py:350, mcp_tools/control.py:1328), 36 with default ensure_ascii
  - src/kiro_crew/mcp_tools/workflows.py:409, mcp_tools/ledger.py:179, mcp_tools/messaging.py:885, apps/builtins/mochi/mcp_server.py:103, mcp_cron.py:2530/:2547, mcp_crew_log.py:303, mcp_debug.py:387/:482 — indent=2 with default ensure_ascii
  - src/kiro_crew/mcp_crew_log.py:455,491,499,538,594, mcp_debug.py:453,466,474, mcp_work.py:171,623, mcp_tools/apps.py:684 — indent=2 with ensure_ascii=False already
  - src/kiro_crew/mcp_tools/spawn.py:1528,1552,1562,1583,1597,1599; mcp_tools/apps.py:583; mcp_tools/browser.py:308; apps/builtins/auto_improvement/backend/mcp_server.py:376 — default ensure_ascii
  - src/kiro_crew/mcp_cron.py:2530 — cost = len(json.dumps(record, indent=2, sort_keys=True)) + 4 (budget measured on indented text)
  - src/kiro_crew/validation.py:4551 — build_tool_response calls sanitize_response (mcp_shared.py:1647); mochi uses run_mcp_stdio_loop (mochi/mcp_server.py:48,756)
  - src/kiro_crew/apps/builtins/auto_improvement/backend/mcp_server.py:376 — text = _redact_result(json.dumps(payload, default=str))[:_MAX_RESULT_CHARS]; own loop at :381-411, no sanitize_response
  - measured o200k (fmt harness): workflow_result 4,177 -> 3,164 tok compact (-24.3%); workflow_result_cjk 6,286 -> 3,112 (-50.5%); spawn_sub_agents_records_cjk 1,113 -> 396 (-64.4%); crew_log_read 7,885 -> 5,304 (-32.7%); cron_list_json 1,016 -> 703 (-30.8%)
- **Checked by:** ran-new-script + ran-new-script (copy of fmt/harness.py) + ran-existing-script + read
- **Required outcome:** Every model-facing JSON tool result is compact UTF-8, and every byte budget is measured on the exact text that is sent; no bidi/zero-width character reaches the model on any server.
- **Solution:**
  1. Add a leaf helper (e.g. mcp_shared.tool_json(obj, *, drop_empty=False, keep=())) = json.dumps(obj, separators=(',', ':'), ensure_ascii=False, default=str), with drop_empty opt-in per site and a keep-list (cron_list `history: null` means unreadable).
  2. Route the 20 model-facing indent sites and the 9 ASCII-only sites listed in evidence through it.
  3. Route the budget code through the same helper so the measured size is the sent size: mcp_cron._render_cron_list_json (:2486, :2530), mcp_crew_log._fit (:431) / _one_row_within_budget (:468), mcp_debug._fit (:438), mcp_work._render (:163) / _fit_ledger (:202).
  4. Leave file writers alone: mochi/mcp_server.py:680, mcp_cleanup.py:446, mcp_quarantine.py:350, mcp_tools/control.py:1328.
  5. Safety: servers on mcp_shared.run_mcp_stdio_loop (core, cron, crew-log, debug, work, dashboard, panel, computer, mochi) get sanitize_response via build_tool_response (validation.py:4551). The auto_improvement backend server (backend/mcp_server.py:376) does NOT: either keep ensure_ascii=True there or call validation.sanitize_string before slicing.
  6. Add an AST ratchet forbidding json.dumps(indent=...) in mcp_tools/, mcp_*.py and apps/builtins/*/mcp_server.py outside an allow-list of file writers.
- **Done when:** Per site: old and new output json.loads to equal objects; each budgeted size equals len(output); a CJK/emoji payload round-trips; a payload containing U+202E and U+200B comes back without them from every server, auto_improvement included; the AST ratchet test passes; row-count pins in test_mcp_crew_log.py and test_mcp_cron_list_json.py are retuned only where more rows now fit.
- **Changed from the source claim:** Site list and line numbers unchanged at HEAD; measured savings reproduce exactly. Corrections: 'most use ensure_ascii=True' is overstated (9 of 20 indent sites; 11 already ensure_ascii=False); total affected sites is 29, not ~20; the safety note's open question is answered: mochi IS sanitized (mcp_shared), auto_improvement is NOT (own loop, redact + 60k slice only). Severity 50 -> 45: the heaviest sites (ledger, workflow_result, crew_log, cron_list json, work) sit on armed or opt-in servers; on default-mounted tools only spawn_sub_agents, browser and read_slack_profile are affected (TOOL-12 separately owns the ledger).
- **Sources:** FIX_PLAN:TOOL-13, REVIEW_FINDINGS:F2, REVIEW_FINDINGS:F3

### TOOL-3 [45, default, effort M] Tool descriptions are bloated — CONFIRMED
- **Verified claim:** Measured from real tools/list replies at HEAD: across all 8 managed servers (136 tools) tool-level descriptions total 84,582 chars and nested property descriptions 48,607 chars. On the always-mounted default set (core+cron, 92 tools) they are 49,631 + 38,658 = 88,289 chars, about 73% of the 120,224 B compact payload. The '10 largest tools total 48 KB' figure is whole-schema size, not description size: top 10 by compact JSON = 47,774 B (monitor_start 8,484, send_message 6,985, cron_add 6,318, issue_radar_crew_record 5,396, spawn_run 5,069, monitor_update 3,649, learn_add 3,373, work_ledger_read 3,101, chat_tag 2,717, cron_update 2,682). By description text alone (tool + property) the leaders are monitor_start 7,668, send_message 6,016, cron_add 4,963, spawn_run 4,244, issue_radar_crew_record 3,450.
- **Evidence (at `397f4be`):**
  - measured (merge/scripts/C/desc_measure.py over merge/scripts/C/tl/tl_mcp-*.json): all 8 servers n=136 tooldesc=84,582 propdesc=48,607
  - measured: core+cron n=92 tooldesc=49,631 propdesc=38,658 compact=120,224 B, default json.dumps=123,673 B
  - measured: top-10 whole-tool compact bytes = 47,774 B; monitor_start 8,484 B (desc 3,026 + prop 4,642)
  - src/kiro_crew/mcp_tools/control.py:501 — "name": "monitor_start"
  - src/kiro_crew/mcp_cron.py:1893 — "name": "cron_list" (desc 1,029 + prop 820 chars); :1949 cron_add (prop 4,659 chars)
  - src/kiro_crew/mcp_tools/_limits.py:1-6 — 'A limit quoted in a tool description MUST come from here rather than a literal'
- **Checked by:** ran-existing-script + ran-new-script
- **Required outcome:** <= ~600 chars per tool description and <= ~120 chars per property description on the always-mounted servers, with how-to detail moved into skills (babysit, the cron skill) or into refusal text; the gain is counted once with TOOL-2.
- **Solution:**
  1. Add a CI test that loads each managed server's _list_tools() (mcp_core, mcp_cron, mcp_dashboard, mcp_work, ...), measures len(description) per tool and per nested property description, and compares against per-tool and per-server budgets plus a checked-in baseline file that may only shrink.
  2. Trim the largest first: monitor_start (mcp_tools/control.py:501), send_message, cron_add (mcp_cron.py:1949, property docs), spawn_run, issue_radar_crew_record (mcp_tools/apps.py), monitor_update, learn_add, chat_tag, suggest_followup.
  3. Keep limits quoted in descriptions sourced from mcp_tools/_limits.py; keep spawn_run's line format (run card parses it; test_mcp_core_coverage pins it).
  4. Move usage prose into the skill the tool already points at (skill_fetch) — a take-away for models that relied on inline guidance, so spot-check monitor and cron behaviour.
- **Done when:** The budget test passes with the baseline; a deliberately grown description (+1 char over the baseline) fails it; core+cron description text drops from 88,289 chars toward the target, re-measured by the same script.
- **Upstream:** #13232 (state unverified)
- **Changed from the source claim:** Conflict resolved: REVIEW_FINDINGS' 84.6 KB + 48.6 KB is exact but is the all-8-servers total; its '10 largest total 48 KB' and 'monitor_start 8.5 KB' are whole-schema compact bytes. Y2's '~63 KB of 123.6 KB' and its per-tool figures (monitor_start 2,589) came from a static string-literal scan that undercounts (it misses computed/f-string text): the real description text on that 123.6 KB core+cron payload is 88,289 chars and monitor_start's tool-level description alone is 3,026.
- **Sources:** FIX_PLAN:TOOL-3, REVIEW_FINDINGS:H-P4, verify_needed:Y2

### TOOL-6 [45, default, effort M] Artifact edits rewrite the whole content — CONFIRMED
- **Verified claim:** For chat-backed artifacts (widgets; content lives in current.html) the only write path is artifact_update, whose `content` is the whole new content (PATCH /api/artifacts/<slug> with the full body), and artifact_get returns the whole content with no offset/limit; there is no edit/patch verb among the 19 artifact tools. Editing one line of a 40 KB artifact therefore costs the full content in (artifact_get) and out (artifact_update), ~10k + ~10k tokens at chars/4. File-backed artifacts (source_path) read the live file, so a native file edit also works for those. Above 100,000 chars, artifact_get is cut head-only by the MCP transport, so a read-modify-write of a large artifact silently truncates it (a new version is still recorded and revertable).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/artifacts.py:158-185 — artifact_update schema: content 'New content. Each call records a new version'
  - src/kiro_crew/mcp_tools/artifacts.py:806-818 — artifact_update: update_body = {k: v ...}; mcp_core._patch(f'/api/artifacts/{slug}', update_body)
  - src/kiro_crew/mcp_tools/artifacts.py:766-803 — artifact_get: out_body = meta + '--- content ---\n' + content (whole content, no paging args)
  - src/kiro_crew/mcp_tools/artifacts.py:52-583 — schemas(): 19 tools: artifact_save/get/update/revert/list/versions/delete, comments x5, folders x5, artifact_move, deploy_artifact; none edits in place
  - src/kiro_crew/artifacts.py:465-469 — file-backed source_path is metadata; content lives in current.html, 'we never write back to source_path'
  - src/kiro_crew/artifacts.py:714-720 — get(): file-backed current read returns the live file from disk
  - src/kiro_crew/validation.py:166 — MAX_RESPONSE_LEN = 100_000 (artifact_get output cut head-only above this)
- **Checked by:** read
- **Required outcome:** artifact_edit(slug, edits=[{old, new}]) applies exact-match replacements (each `old` must match exactly once) atomically as one new version, and artifact_get pages (offset/limit, with total size) so neither direction moves the whole artifact for a small change.
- **Solution:**
  1. Add an artifact_edit tool in mcp_tools/artifacts.py with an edits array; the gateway handler (dashboard/handlers/artifacts.py) applies all edits under the store lock to the current content, refuses if any `old` matches 0 or > 1 times, and records one version (artifacts.py store write path).
  2. Add offset/limit (chars or lines) to artifact_get (mcp_tools/artifacts.py:766) and ARTIFACT_GET_SCHEMA, returning total_chars and has_more; default to a bounded first page when the content exceeds the transport cap, so a read never silently truncates.
  3. Point the artifacts SKILL.md iterate flow at artifact_edit; keep artifact_update for full rewrites.
  4. Fold this into TOOL-2 step 4 if the artifact tools are collapsed (an `edit` action). Update docs/system-specs/modules/artifacts.md.
- **Done when:** Handler tests: editing one line of a 40 KB artifact sends < 1 KB of arguments and produces version n+1 whose content equals the expected replacement; an `old` matching twice or zero times is refused and creates no version; artifact_get with offset/limit pages a 150,000-char artifact end to end and reports has_more correctly.
- **Changed from the source claim:** Holds for chat-backed artifacts; file-backed ones can already be edited through the file. Added: above 100k chars artifact_get is head-cut by the transport, so a full read-modify-write truncates the artifact.
- **Sources:** FIX_PLAN:TOOL-6, REVIEW_FINDINGS:U6

### SKL-1 [45, armed, effort M] Skill triggers inject bodies on weak (one-word) matches — CONFIRMED
- **Verified claim:** Trigger matching is off by default (skills.max_triggered=0). Once an operator sets it > 0, a phrase's score is the fraction of ITS words present in the message, so a one-word phrase scores 1.0 on any message containing that word and a four-word phrase passes on three of its words (0.75 >= MIN_TRIGGER_OVERLAP 0.7); the whole turn text is scored (attachment text included), not the user's typed span the lessons path uses. Re-measured at HEAD over the same 96-message corpus (cap 3, fresh session per message): 48 messages matched, 26 got at least one false-positive BODY, 854,756 B injected of which 427,975 B (50.1%) were false-positive bodies; one-word phrases won 36/63 matches (57%) and 26/39 false-positive matches (67%). A 400 KB Slack log attachment alone fires artifact-deploy + artifacts (43,853 chars of skills).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/memory_sections.py:553 — `max_triggered: int = field(default=0, ...)` (off by default)
  - src/kiro_crew/trigger_match.py:38 — `MIN_TRIGGER_OVERLAP = 0.7`
  - src/kiro_crew/trigger_match.py:73 — `best = max(best, len(phrase_words & text_words) / len(phrase_words))` (one-word phrase -> 1.0; 3 of 4 words -> 0.75)
  - src/kiro_crew/context.py:3621 — `triggered = self.skills.get_triggered_skills(text, project_dir=project, select=select)` scores the whole turn `text`
  - src/kiro_crew/context.py:3755-3764 — per-turn lessons match `text[user_text_range[0] : user_text_range[1]]` (typed span only)
  - src/kiro_crew/skill_runtime/delivery.py:169 — `inject_on_trigger: false` is the only pointer-only opt-out; default is full body
  - measured (trigger_run.py cap 3): 48/96 matched, 26/96 got a false-positive body, 854,756 B injected, 427,975 B (50.1%) false-positive bodies — prior: 26/96, 854,602 B, 50%
  - measured (fp_stats.py): one-word phrases won 36/63 matches (57%), 26/39 false-positive matches (67%); examples 'page'->ops-mission-control 9,615 B, 'version'->artifacts 23,545 B, 'Word'/'Excel'->computer-use 26,922 B, 'ship'->artifact-deploy 20,392 B
  - …and 1 more in the verification results.
- **Checked by:** ran-existing-script + ran-existing-script + ran-existing-script + read
- **Required outcome:** A skill body is injected only for a confident match; weak (one-word or partial) matches get the existing pointer line at most, and attachment text never fires a trigger.
- **Solution:**
  1. Frontmatter: in the shipped skills (builtin_skills/*/SKILL.md, apps/builtins/*/skills/*/SKILL.md) remove one-word trigger phrases or make them two content words, add `!` negatives where a common word is ambiguous, and fix the literal `missing $` phrase (papyrus-diagnose-compilation).
  2. Add a CI lint (test over every packaged SKILL.md frontmatter) rejecting trigger phrases of fewer than two content words.
  3. Matcher (skills.py get_triggered_skills ~:5402 / context.py:3631 split): inject the BODY only when a phrase of >= 2 content words matches in full (score 1.0); any weaker match goes through the existing trigger_hint pointer (skill_runtime/delivery.py:201). Keep the inject_on_trigger default (delivery.py:169) and the shared trigger_match.py scorer (crew routing uses it too — add a stricter predicate beside rank_triggered, do not change rank_triggered's semantics).
  4. Score only the user's typed span: pass `text[user_text_range[0]:user_text_range[1]]` (or the HOOK_MODIFY text) to get_triggered_skills at context.py:3621, as the lessons path does at :3755-3764.
  5. Update docs/system-specs/modules/memory-skills-hooks.md (trigger section) in the same commit. Simulated earlier: body-only-on-strong-match cuts ~54% of injected bytes with no dropped true match.
- **Done when:** A corpus test (fixture of ordinary messages with hand-labelled relevant skills) runs SkillsLoader.get_triggered_skills + ContextBuilder.build_message with max_triggered=3 in a tmp KIROCREW_HOME and asserts false-positive body bytes <= a pinned ceiling (e.g. <= 10% of injected bytes) and that every labelled true match still gets a body or pointer; the frontmatter lint passes; a test with a 400 KB attachment block appended to a one-line typed message fires no skill.
- **Changed from the source claim:** none on the mechanism; numbers re-measured: 854,756 B injected (was 854,602), FP body share 50.1% (was 50%), 26/96 FP-body messages unchanged. Note: 33/96 messages had a false-positive MATCH (incl. pointer-only kirocrew-commands); 26 had a false-positive BODY.
- **Sources:** FIX_PLAN:SKL-1, REVIEW_FINDINGS:Part6/SKL-1

### SPEC-1 [45, armed, effort M] Conductor specs mount 117 tools whole — CONFIRMED
- **Verified claim:** The goal conductor (kirocrew-conductor, and its kirocrew-ledger-conductor twin) mounts @kirocrew-core, @kirocrew-dashboard and @kirocrew-work whole (117 tools, 149,999 B of compact schemas), the pipeline and security conductors mount core + dashboard whole (112 tools, 141,209 B), while their charters (allowedTools verbs) are 24 / 18 / 19 verbs. Keeping the charter plus every tool their prompt/skill names would mount 61,604 / 50,371 / 51,412 B (~22k tokens saved per request). The code comment justifies the whole-server mounts by Tool Search deferral, which needs > min(5% x window, 50k) tokens and so does not engage on a 1M window (TOOL-2). kirocrew-dashboard-author mounts all of @kirocrew-core (107,043 B) where its charter + prompt use 5 core verbs (6,676 B); kirocrew-research copies the default mounts (core + cron, 120,224 B) although its prompt names no Kiro Crew tool.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/agent_materialization/conductor_agents.py:649-674 — config['tools'] = [..., 'tool_search', '@kirocrew-core', '@kirocrew-dashboard', '@kirocrew-work']
  - src/kiro_crew/agent_materialization/conductor_agents.py:662-666 — 'Load-bearing ... with MCP Tool Search active the session-control specs are deferred'
  - src/kiro_crew/agent_materialization/conductor_agents.py:839-847, :930-938 — pipeline/security conductors: '@kirocrew-core', '@kirocrew-dashboard'
  - src/kiro_crew/agent_materialization/service_agents.py:108-124 — research: config = agent_mod.build_agent_config() (default mounts) + _RESEARCH_SYSTEM_PROMPT
  - test/test_conductor_agent.py:162-165 — pins '@kirocrew-dashboard' and '@kirocrew-core' in data['tools']
  - measured (merge/scripts/C/spec/payload.py): conductor/ledger-conductor 149,999 B; pipeline/security 141,209 B; dashboard-author 107,043 B; research 120,224 B
  - measured (merge/scripts/C/spec/charter.py): keep charter+prompt-named -> 61,604 / 50,371 / 51,412 B; (narrow.py) charter verbs only 24/18/19 -> 46,109 / 37,233 / 38,274 B
  - measured: dashboard-author keep set skill_search, skill_discover, memory_recall, resource_status, learn_add = 6,676 B; _RESEARCH_SYSTEM_PROMPT (4,573 chars) names 0 Crew tools
  - …and 1 more in the verification results.
- **Checked by:** read + ran-existing-script + ran-existing-script + ran-existing-script
- **Required outcome:** Each conductor and service spec mounts only the verbs its charter or prompt uses: each conductor's mounted tools/list bytes <= ~65 KB with every prompt-named tool still mounted; dashboard-author and research mount only what they name.
- **Solution:**
  1. Replace the whole-server refs at conductor_agents.py:667-673, :845-847, :936-938 with explicit @server/verb lists built from the charter frozensets at :101-176 plus the prompt-named verbs (monitor_inspect, session_send/stop/close, spawn_run, spawn_sub_agents, task_run, workflow_run, work_report, panel_publish as applicable).
  2. Same for dashboard-author (5 core verbs; note its prompt names work_report, which lives on the unmounted kirocrew-work) and research (service_agents.py:115: drop @kirocrew-core/@kirocrew-cron or mount only what the Research Lab loop needs).
  3. Update test/test_conductor_agent.py:162-165 to pin the verb lists.
  4. Mirrored harnesses mount whole servers anyway (no regression there); keep identity checks positive. This is a take-away change for any verb a user relied on the conductor having: read take-away-changes.md, list them in the PR's Reader: section. Composes with TOOL-3; do not double count. A Tool Search overlay with min_tokens=0 per session is the fallback, but see TOOL-2 step 5 (#16059).
- **Done when:** A test builds each conductor spec in a throwaway home and resolves its refs against the servers' _list_tools(): mounted compact bytes <= 65,000 and every tool name the prompt (and its skill) mentions is mounted; dashboard-author <= 10,000 B.
- **Changed from the source claim:** Numbers re-measured at HEAD: 448 B lower than the prior run on every row (spawn_run/spawn_sub_agents descriptions embed the installed-agent roster, so they vary by home); otherwise identical. Added kirocrew-ledger-conductor, which carries the same mounts. Spot-check: 'Tool Search does not engage' holds only on CTX-20's unverified auto=1M premise (on a 200k window with kiro-cli >= 2.27 the conductor's ~37k tokens exceed the 10k bar and DO defer, which is what the conductor_agents.py:662-666 comment relies on). Set 45 pending CTX-20, back to 55 if it confirms 1M; consistent with CTX-1 and TOOL-2.
- **Second reader:** top-20 check: corrected. Claim and evidence hold, but the 'Tool Search deferral does not engage' half and the severity rest on CTX-20's unresolved auto=1M premise (on 200k with kiro-cli >= 2.27 the whole-server mounts defer, as the conductor_agents.py:662-666 comment intends). Ranked inconsistently with CTX-1.
- **Sources:** FIX_PLAN:SPEC-1, REVIEW_FINDINGS:Part6/SPEC-1

### SPEC-2 [45, armed, effort M] Custom and conductor agent prompts delivered twice per request — CONFIRMED
- **Verified claim:** For every agent other than the managed default, the spec `prompt` is delivered natively (kiro-cli's launch view keeps it, only re-anchoring a relative file:// path; KAS inlines it as customAgents[].prompt) AND context.py injects the same text as [AGENT SYSTEM PROMPT] at session start and again after every compaction (turn.py post_compaction_parts). Only `kirocrew` escapes, because its spec carries the 224-char _NATIVE_PROMPT_STUB. Re-measured duplicated bytes per request (injected block matches the native prompt 99.6-100%): conductor and ledger-conductor 13,189; dashboard-author 7,062; pipeline conductor 5,756; security conductor 5,657; research 4,573; personal-shopper advisor 3,801; heartbeat 3,014; worker 2,299; app agents 0.8-1.7 KB; 67,825 chars across the 23 specs measured; user personas unbounded.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:2966 — is_custom = bool(agent) and agent != 'kirocrew'; :2983-2986 elif is_custom: agent_prompt = self._load_agent_prompt(...)
  - src/kiro_crew/context.py:3232-3235 — parts.append(f'[AGENT SYSTEM PROMPT]\n{agent_prompt}\n[END AGENT SYSTEM PROMPT]') at session start
  - src/kiro_crew/context_assembly/turn.py:72-82 — post-compaction: _resolve_agent_prompt(..., session_start=False) and the same block appended
  - src/kiro_crew/acp/skill_projection.py:2976-2980 — native view keeps view['prompt'] (only a relative file:// path is re-anchored)
  - src/kiro_crew/acp/kas_agents.py:797-799 — KAS projection: out = {'id': agent_id, 'prompt': prompt, ...}
  - src/kiro_crew/agent.py:2061-2065 — _NATIVE_PROMPT_STUB (the managed default's prompt only)
  - src/kiro_crew/agent.py:5247 — _HEARTBEAT_SYSTEM_PROMPT: measured 3,014 chars (3,030 B), identical to kirocrew-heartbeat.json's prompt
  - measured (merge/scripts/C/spec/dup_prompt.py): native vs injected — conductor 13,189/13,214 (0.998), dashboard-author 7,062/7,062 (1.0), heartbeat 3,014/3,022 (0.997), worker 2,299/2,306; sum 67,825
  - …and 1 more in the verification results.
- **Checked by:** read + ran-existing-script
- **Required outcome:** One copy of an agent's prompt per session on every backend.
- **Solution:**
  1. Before coding, review open PR #17172 ('send a custom agent's prompt once on kiro-cli and KAS'); if it is open, review or re-land it instead of a second fix. Otherwise: (a) preferred — write _NATIVE_PROMPT_STUB as the prompt in the native kiro launch view (acp/skill_projection.py:2976) and in the KAS projection (acp/kas_agents.py:798) for any spec whose prompt context.py injects, so the injected block is the single, resolved source; or (b) skip the injection in context.py:2983 / turn.py:72 when the backend delivers the spec prompt natively, gated on a positive set {ACP_BACKEND_KIRO, ACP_BACKEND_KAS}, never 'not claude'. Mirrored harnesses keep the injection. Option (a) does not cover KIROCREW_NATIVE_SKILL_PROJECTION=0. Update src/kiro_crew/docs/agent-spec-fields.md (Prompt) in the same commit; run scripts/check_harness_parity.py.
- **Done when:** The prepared kiro view for kirocrew-conductor carries the stub and its first-turn message contains the conductor prompt text exactly once; with agent.acp_backend='codex' the injected block is still present; a post-compaction rebuild for the conductor contains it once; agent-spec-fields.md updated.
- **Upstream:** #15822 #13305 PR #17172 PR #15817 PR #13257 (state unverified)
- **Changed from the source claim:** Holds; figures re-measured and unchanged. Heartbeat conflict resolved: _HEARTBEAT_SYSTEM_PROMPT is 3,014 chars / 3,030 B at HEAD (agent.py:5247), equal to the materialized spec prompt, so X14's '~5.9 KB' is wrong and FIX_PLAN's '3 KB charter' is right. Added: ledger-conductor (13,189), security conductor (5,657), research (4,573), personal-shopper (3,801).
- **Second reader:** random-sample check: agreed.
- **Sources:** FIX_PLAN:SPEC-2, REVIEW_FINDINGS:Part6/SPEC-2, verify_needed:G17(#15822), verify_needed:G31(#13305), verify_needed:G36, verify_needed:G37, verify_needed:G43

### TOOL-14 [40, default, effort S] memory_recall result is mostly retrieval diagnostics — CONFIRMED
- **Verified claim:** A memory_recall result is mostly retrieval diagnostics: on the 5-fact/3-episode/1-lesson fixture the model receives 1,041 o200k tokens of which only 352 are the three memory context blocks; the `retrieval` block alone is ~712 tokens (per-row id/key/source, microsecond updated_at/created_at, unrounded cosine/score floats, matched_terms), plus algorithm_version, policy_revision and the *_chars/total_chars counters. On a V2 store `operating_point` is added on top. The model-facing projection _model_recall_payload strips only snippet/text, not the diagnostics.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/memory_recall.py:92-133 — recall_evidence copies reason/algorithm/cosine_floor/.../score/similarity unrounded into each row's `retrieval`
  - src/kiro_crew/memory_recall.py:179-192 — _model_recall_payload: 'compact evidence beside them' but only drops snippet/text and *_preview
  - src/kiro_crew/memory_recall.py:259-262 — result[f'{kind}_chars'] and result['total_chars'] added to the payload
  - src/kiro_crew/vector_memory_runtime/recall.py:181-184 — if store.algorithm_version == 'v2': evidence['operating_point'] = v2_operating_point(...)
  - measured (fmt harness, tiktoken o200k): total 1,041 tok; memory context 352 tok; non-memory 689 tok; sample fact row: updated_at '2026-10-07T10:26:46.248009+00:00', cosine 0.7866976169518948
  - measured terse variant (context blocks + id/reason only): 483 tok (-53.6%)
  - test/test_memv2_audit_b_regressions.py:18 — test_mcp_recall_contains_each_body_once (needs `retrieval` present)
- **Checked by:** ran-new-script (copy) + ran-new-script + read
- **Required outcome:** In the model-facing projection only, each recall row is {id, text/snippet, retrieval: {reason}} with floats rounded to 4 places and no timestamps, counters, policy_revision or operating_point; the HTTP/UI payload is unchanged. Expected saving about -53% per call.
- **Solution:**
  1. Extend _model_recall_payload (memory_recall.py:179) to project each retrieval row to {id, retrieval: {reason}} (keep key for facts), round any kept float to 4 places, and drop updated_at/created_at.
  2. Drop algorithm_version, policy_revision, *_chars, total_chars and retrieval.operating_point from the model copy only (the HTTP route keeps them; bound_recall_payload's budget logic at :259-272 keeps using them internally).
  3. Keep `retrieval` present (shrunk, not deleted) so test_mcp_recall_contains_each_body_once still holds.
  4. Update memory-skills-hooks.md where the recall result shape is described.
- **Done when:** test_memv2_audit_b_regressions.py::test_mcp_recall_contains_each_body_once passes; a new test calls mcp_tools.learn.memory_recall against a stubbed /api/memory/recall payload (v1 and v2 shapes) and asserts every retrieval row's keys are a subset of {id, key, retrieval} with retrieval keys == {reason}, no float has more than 4 decimals, and 'operating_point', 'policy_revision', 'total_chars' are absent from the returned text.
- **Changed from the source claim:** Mechanism and the 1,041-token total reproduce exactly. Non-memory share measured 689 of 1,041 tokens (prior 653; the difference is how JSON scaffolding is attributed). The harness fixture runs the v1 algorithm, so operating_point (V2 only) was not in the measured figure; on a V2 store the share is higher. Line numbers unchanged.
- **Sources:** FIX_PLAN:TOOL-14, REVIEW_FINDINGS:F4

### SKL-2 [40, armed, effort S] Kiro Crew dev skills lack repo_scope — CONFIRMED
- **Verified claim:** kirocrew-prepare-pr, writing-tests, kirocrew-worktree-dev and dashboard-template describe themselves as Kiro Crew repo only, but none sets `repo_scope:` (no shipped SKILL.md under builtin_skills/, deploy/skills/ or apps/builtins/ carries the key), so the loader's mechanical gate never applies. With triggers on (max_triggered > 0) and NO project, 'commit and push these changes' / 'create pr for this branch' inject prepare-pr (53,466 B per turn) and 'add a test ...' injects writing-tests (58,901 B); together the two were 326,647 B of 854,756 B injected over the 96-message corpus (38%).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/skills.py:3712 — `_repo_scope_satisfied(relpath, project_dir)`: fails CLOSED with no project
  - src/kiro_crew/skills.py:5460-5462 — get_triggered_skills: `scope = meta.get("repo_scope", "").strip(); if scope and not self._repo_scope_satisfied(scope, project_dir): continue`
  - grep -rln repo_scope src/kiro_crew/builtin_skills src/kiro_crew/deploy/skills src/kiro_crew/apps/builtins — no matches at HEAD
  - src/kiro_crew/builtin_skills/kirocrew-dev/kirocrew-prepare-pr/SKILL.md:3 — 'LOAD THIS FOR EVERY KIRO CREW PR ... (Kiro Crew repo only; not other repos or CRs)'; :5 triggers include 'commit and push', 'create pr'
  - src/kiro_crew/builtin_skills/kirocrew-dev/writing-tests/SKILL.md:3 — 'Kiro Crew repo only'; :4 triggers include 'add a test', 'write a test'
  - measured (trigger_run.py, project_dir=None, cap 3): #19 'commit and push these changes' -> prepare-pr 53,466 B; #20 'create pr for this branch...' -> 53,466 B; #03 'add a test for the date parsing helper' -> writing-tests 58,901 B
  - measured: prepare-pr + writing-tests = 326,647 B of 854,756 B injected (38.2%) — prior 326 KB of 855 KB
- **Checked by:** ran-existing-script + read
- **Required outcome:** Kiro Crew repo-development skills are mechanically eligible only when the session's active project is the Kiro Crew checkout.
- **Solution:**
  1. Add `repo_scope: src/kiro_crew` to the frontmatter of builtin_skills/kirocrew-dev/kirocrew-prepare-pr/SKILL.md, writing-tests/SKILL.md, kirocrew-worktree-dev/SKILL.md and dashboard-template/SKILL.md (NOT babysit, which is a general skill).
  2. TAKE-AWAY CHANGE (docs/system-specs/common/take-away-changes.md): the gate also hides these skills from skill_search (skills.py:5735) and the catalog (skills.py:5850) outside the repo, so name it in the PR's `Reader:` list; grep docs/decisions/ first (no entry found at HEAD).
  3. Update docs/system-specs/modules/memory-skills-hooks.md where repo-scoped skills are listed, in the same commit.
- **Done when:** With max_triggered=3 in a tmp KIROCREW_HOME: `get_triggered_skills('create pr for this branch', project_dir=None)` returns no kirocrew-dev/* key; with project_dir set to a tmp dir containing `src/kiro_crew/` it includes kirocrew-dev/kirocrew-prepare-pr; a frontmatter test pins repo_scope on the four skills.
- **Changed from the source claim:** none; 53,466 B / 58,901 B re-measured identical; share 38.2% (was 38%).
- **Sources:** FIX_PLAN:SKL-2, REVIEW_FINDINGS:Part6/SKL-2

### SPEC-3 [40, armed, effort M] kirocrew-worker is not a slim agent — CONFIRMED
- **Verified claim:** kirocrew-worker is built as the default spec plus @kirocrew-work (and the kirocrew-work server entry), minus auto-approval of three cron scheduling verbs; @kirocrew-cron stays mounted. Its mounted schemas are 129,014 B (~32k tokens) against 120,224 B for the default: core 107,043 + cron 13,181 + work 8,790, including monitor+autonudge 14,862 B, artifacts 13,236 B, app/dev tools 13,682 B, spawn 13,079 B and workflow 4,232 B. Its injected first turn is small (8,725 chars vs 52,382 for kirocrew), so it is slim in prompt but not in tools.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/agent_materialization/worker_agent.py:681-684 — tools = default tools; if '@kirocrew-work' not in tools: tools.append('@kirocrew-work')
  - src/kiro_crew/agent_materialization/worker_agent.py:697-700 — 'worker = default + @kirocrew-work − cron scheduling' (_apply_worker_exclusions)
  - src/kiro_crew/agent.py:4862-4873 — _WORKER_EXCLUDED_GRANTS withholds AUTO-APPROVE only: '@kirocrew-cron stays in tools exactly as the default has it'
  - measured (merge/scripts/C/spec/payload.py): kirocrew-worker 129,014 B {cron 13,181, core 107,043, work 8,790}; kirocrew 120,224 B
  - measured (merge/scripts/C/desc_measure.py): monitor_*+autonudge_stop 14,862 B; artifacts 13,236; apps 13,682; spawn 13,079; workflow_* 4,232
  - measured (merge/scripts/C/spec/dup_prompt.py): first-turn message kirocrew-worker 8,725 chars vs kirocrew 52,382
  - src/kiro_crew/subagent_manager/run.py:2105 — agent = info.agent or execution.template_id (O4's default-spawn resolution)
- **Checked by:** read + ran-existing-script + ran-existing-script + ran-new-script
- **Required outcome:** Before CTX-7 redirects default spawns, either narrow kirocrew-worker or add a kirocrew-step spec whose mounted schemas are <= ~25 KB with no cron_*, workflow_* or monitor_* tools.
- **Solution:**
  1. Add a kirocrew-step spec (agent_materialization/, next to worker_agent.py) that mounts a ~16-verb base of @kirocrew-core verbs (spawn status/results, skills, learn, memory, ask_question, send_message, wait) as @kirocrew-core/<verb> refs, plus @kirocrew-work only where the work ledger is used; no @kirocrew-cron. Prior measurement: a 16-tool base is 20,463 B.
  2. Or narrow the worker itself: drop @kirocrew-cron from its tools (worker_agent.py:681) — a take-away change for items that schedule today (they currently get an approval prompt), so decide per verb with the maintainer.
  3. Land SPEC-2 first so the step spec's prompt is delivered once. Keep harness identity positive; update src/kiro_crew/docs/agent-spec-fields.md and docs/system-specs/modules/subagent.md.
- **Done when:** A test builds the step (or narrowed worker) spec in a throwaway home and asserts mounted compact schema bytes <= 25,000 and that no mounted tool name starts with cron_, workflow_ or monitor_.
- **Changed from the source claim:** Holds; re-measured 129,014 B (prior 129,462; the 448 B home-dependent roster text). O4 vs CTX-7/SPEC-3 reconciled: O4's measured saving is real for the injected first turn (worker 8,725 vs 52,382 chars), but defaulting spawns to the worker AS IS adds 8,790 B of @kirocrew-work schemas to every request and keeps all ~120 KB of default schemas, so the token fix needs a slim step spec, not the current worker; O4's proposal is refuted as a schema saving, not as a prompt saving.
- **Sources:** FIX_PLAN:SPEC-3, REVIEW_FINDINGS:Part6/SPEC-3

### TOOL-7 [40, armed, effort S] workflow_result returns the full event stream uncapped — CONFIRMED
- **Verified claim:** workflow_result returns {run_id, status, result, error, events, agent_results?, partial_results?, agent_errors?} as json.dumps(indent=2) with the whole event stream and no cap, paging or summary mode (its only argument is run_id). The run registry appends events without a bound. The only limit is the MCP transport's 100,000-char head-only cut, and because agent_results / partial_results / agent_errors are serialized AFTER events, a long event stream pushes exactly the fields the completion message tells the reader to fetch past the cut. The comment claiming the gateway spill handles oversize payloads is wrong on a default install (no broker).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/workflows.py:131-143 — workflow_result schema: 'full result + event stream', properties {run_id} only
  - src/kiro_crew/mcp_tools/workflows.py:393-409 — wf_payload = {run_id, status, result, error, events: _redact_obj(d.get('events', []))}, then agent_results/partial_results/agent_errors; json.dumps(wf_payload, indent=2, default=str)
  - src/kiro_crew/mcp_tools/workflows.py:393 — '# Oversize payloads are handled by the MCP gateway's existing result spill.'
  - src/kiro_crew/workflows/registry.py:427-431 — record_event: h.events.append(event) (no bound)
  - src/kiro_crew/dashboard/handlers/workflows.py:13 — GET /api/workflows/runs/{id} -> {…, events:[…], plan?} (full)
  - src/kiro_crew/validation.py:166, :1210-1215 — transport cut at 100,000 chars, head kept
- **Checked by:** read
- **Required outcome:** By default workflow_result returns compact JSON with status, the final result capped at ~8k chars, the failures/agent_errors, partial results, and the last 20 events; section=events\|agent_results with offset/limit returns the rest.
- **Solution:**
  1. In mcp_tools/workflows.py:368-411 add `section` (summary|events|agent_results) and offset/limit to the schema at :131 and to WORKFLOW_RUN_ID_SCHEMA's successor; default summary puts status, error, agent_errors and partial_results BEFORE any events.
  2. Cap result at ~8k chars with a pointer to section=result paging; last 20 events in summary.
  3. Render with the compact helper from TOOL-13 and drop the repeated envelope (TOOL-16) in the same PR.
  4. Delete the spill comment at :393. Update docs/system-specs/modules/workflows.md.
- **Done when:** A handler test with a stubbed /api/workflows/runs reply of 5,000 events and agent_errors: the default output is <= 12k chars, contains agent_errors and the last 20 events; section=events with offset/limit pages through all 5,000; test_mcp_core_coverage.py's workflow_result cases still pass.
- **Changed from the source claim:** Holds. Added: the 100k transport cut drops the trailing agent_results/partial_results/agent_errors first.
- **Sources:** FIX_PLAN:TOOL-7, REVIEW_FINDINGS:U7

### SKL-4 [35, default, effort M] Several SKILL.md files exceed 32 KiB and prompts force whole reads — CONFIRMED
- **Verified claim:** Five packaged SKILL.md files exceed 32 KiB: pipeline-conductor 105,481 B (also over the 99,000 B SKILL_READ_CAPACITY, so the skill tool pages it), writing-tests 59,478 B, kirocrew-prepare-pr 54,404 B, goal-conductor 40,625 B and kirocrew-commands 33,076 B (39 builtin SKILL.md, 656,473 B total). prompt.md:64 tells the model to load a skill by `cat <path>` (whole file, no Crew-side bound); prompt.md:204 requires reading computer-use (27,530 B) before the first computer-use call; prompt.md:89 requires loading blocked-by-policy (12,277 B) before retrying a refused call; the security-conductor skill requires reading lessons.md (12,459 B) before its first dispatch. Those forced reads are per triggering event (first computer-use call, first refusal, first dispatch), not per session.
- **Evidence (at `397f4be`):**
  - measured (wc -c at HEAD): builtin_skills/pipeline-conductor/SKILL.md 105,481; kirocrew-dev/writing-tests 59,478; kirocrew-dev/kirocrew-prepare-pr 54,404; goal-conductor 40,625; kirocrew-commands 33,076; 39 builtin SKILL.md = 656,473 B (all identical to prior)
  - src/kiro_crew/skills.py:322 — `SKILL_READ_CAPACITY = 99_000` (pipeline-conductor exceeds it)
  - src/kiro_crew/config/prompt.md:64 — 'Load a skill by reading its file (`cat <path>`)'
  - src/kiro_crew/config/prompt.md:204 — 'Read the `computer-use` skill before your first call.' (builtin_skills/computer-use/SKILL.md = 27,530 B)
  - src/kiro_crew/config/prompt.md:89 — 'load the `blocked-by-policy` skill before a second attempt' (builtin_skills/blocked-by-policy/SKILL.md = 12,277 B)
  - src/kiro_crew/builtin_skills/security-conductor/SKILL.md:230-232 — '`lessons.md` beside this file ... Read it before the first dispatch' (lessons.md = 12,459 B)
  - refs_upfront.py: kirocrew-prepare-pr already names 6 on-demand reference docs (69,014 B) — the pattern to copy
- **Checked by:** ran-existing-script + read
- **Required outcome:** Every packaged SKILL.md is <= 32 KiB (a 16-24 KB core), with detail moved into references read on demand; prompt rules point at the section needed rather than forcing whole-file reads.
- **Solution:**
  1. Split pipeline-conductor, writing-tests, kirocrew-prepare-pr, goal-conductor and kirocrew-commands (builtin_skills/...) into a 16-24 KB core SKILL.md plus `references/*.md` read on demand, as kirocrew-prepare-pr already does for part of its content.
  2. Reword prompt.md:64 to read a skill through the skill tool (paged at SKILL_READ_CAPACITY) or by section, not `cat` of the whole file; reword :204 and :89 to name the section to read (e.g. the computer-use 'first call' section) instead of the whole skill; have security-conductor/SKILL.md:230-232 point each worker seed at the lessons.md sections it needs (already half-said) rather than a whole read before dispatch.
  3. Coordinate with LOOP-2 step 3 (pipeline-conductor split). Update docs/system-specs/modules/memory-skills-hooks.md and the pipeline-conductor spec in the same commit.
- **Done when:** A CI test walks every packaged SKILL.md (builtin_skills/, deploy/skills/, apps/builtins/*/skills/) and asserts size <= 32,768 B except a shrink-only allowlist file whose recorded sizes may only decrease; a text test asserts prompt.md no longer instructs `cat` of a whole skill.
- **Changed from the source claim:** none on sizes (all byte-identical). Clarified: the 27.5 KB / 12.3 KB / 12.5 KB reads are forced per triggering event (first computer-use call, first refusal, conductor's first dispatch), not on every session start.
- **Sources:** FIX_PLAN:SKL-4, REVIEW_FINDINGS:Part6/SKL-4

### TOOL-15 [35, default, effort S] List renderers repeat the same instruction on every row — CONFIRMED
- **Verified claim:** List renderers repeat per-row boilerplate: skill_search appends a `load: skill_search(action='read', key=...) or `$key`` line to every row; the kirocrew-dashboard session_status renderer repeats the full 'gone' and 'unknown' explanations on every such row; list_sessions renders each row as an emoji header, a `---` rule, bold title and an italic meta line. A one-header/plain-row rendering measured -32.4% (skill_search, 20 rows), -39.1% (session_status, 12 rows) and -26.6% (list_sessions, 10 rows) of o200k tokens.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/skills.py:312 — else f"load: skill_search(action='read', key='{s['key']}') or `${s['key']}`" (per row)
  - src/kiro_crew/mcp_dashboard.py:2918-2922 — per-row 'gone (the crew log has it, the dashboard does not: closed, archived, or lost)'
  - src/kiro_crew/mcp_dashboard.py:2933-2936 — per-row 'unknown (you created it; neither a live session nor the crew log accounts for it)'
  - src/kiro_crew/mcp_tools/sessions.py:453-466 — '\U0001f5c2 Sessions ...', lines.append('\n---'), '**{title}** · `{key}`', '_{meta}_' per row
  - measured (fmt harness, o200k): skill_search 1,871 -> 1,265 tok; session_status 384 -> 234; list_sessions 489 -> 359
  - test/test_mcp_core_coverage.py:457-461 — spawn_run receipt wording is pinned (leave spawn_run's format alone)
- **Checked by:** ran-new-script (copy) + ran-existing-script + read
- **Required outcome:** List results state each instruction once: one header line (with the load instruction / status legend), a footnote only when a gone/unknown row is present, and plain `key \| title \| agent \| msgs \| created` rows.
- **Solution:**
  1. mcp_tools/skills.py:305-313: emit the load instruction once in the header ('load any: skill_search(action=\'read\', key=KEY) or `$KEY`') and drop the per-row `load:` line (keep the inline-content branch).
  2. mcp_dashboard.py:2914-2939: render gone/unknown rows as `target — gone` / `target (title) — unknown` and append one footnote per status that occurs.
  3. mcp_tools/sessions.py:453-470: one header line naming the columns, then one plain line per session; keep the summary line when present.
  4. Do not touch spawn_run's receipt (run card parses it; test_mcp_core_coverage.py pins it). Update any doc that quotes these outputs.
- **Done when:** Unit tests render 20 skill rows, 12 session_status rows (with 4 gone and 2 unknown) and 10 list_sessions rows from fixed fixtures and assert: the `load:` instruction appears exactly once, each gone/unknown explanation appears at most once, no `---` rule or emoji per row; every key still appears; test_mcp_core_coverage.py stays green.
- **Changed from the source claim:** none — sites re-located (mcp_dashboard lines now 2918/2933 vs 2920/2935) and the -27%..-39% measurements reproduce exactly.
- **Sources:** FIX_PLAN:TOOL-15, REVIEW_FINDINGS:F5

### TOOL-20 [35, default, effort M] repeat_loop catches only identical calls; no per-turn tool-call ceiling — CONFIRMED
- **Verified claim:** RepeatLoopTracker keys a call on sha256(tool_name, tool_input) and its outcome on sha256(status, output); the streak resets whenever the output digest differs, and the notice fires once per signature after REPEAT_LOOP_THRESHOLD = 3 CONSECUTIVE identical outcomes. Any varying byte in the output (a timestamp, a duration, a PID, a test-ordering line) or the input defeats it. Nothing bounds the number of tool calls in a turn: the only per-turn call count (_turn_tool_calls) feeds diagnostics and the risk-badge sampler (decisions/points/tool_risk.py MAX_CALLS_PER_TURN = 20 caps badge classification, not calls). The tracker exists only in dashboard/chat_runner._run_chat; subagent, workflow-step and task-runner turn loops hold none.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/repeat_loop.py:1-10 — 'The signal is narrow on purpose: the SAME tool with the SAME input coming back with the SAME result'
  - src/kiro_crew/repeat_loop.py:20 — REPEAT_LOOP_THRESHOLD = 3; :24 _MAX_TRACKED = 512 (bounds trackers, not calls)
  - src/kiro_crew/repeat_loop.py:74 — self._calls[key] = (_digest(tool_name or '', tool_input), shown)
  - src/kiro_crew/repeat_loop.py:103-112 — outcome = _digest(status, seen or kept); streak += 1 only if equal to the last outcome, else reset to 1
  - src/kiro_crew/dashboard/chat_runner.py:9268 — _repeat_loop = RepeatLoopTracker() (the only instantiation in src/)
  - src/kiro_crew/dashboard/chat_runner.py:9125 — _turn_tool_calls = 0 # tool dispatches this turn (refusal diagnostic)
  - src/kiro_crew/decisions/points/tool_risk.py:201, :388 — MAX_CALLS_PER_TURN = 20 gates risk-badge records only
- **Checked by:** read
- **Required outcome:** A turn that keeps re-running the same tool with near-identical input and output (differences only in volatile tokens) gets the same notify-once in-band notice; every turn loop that runs tools (chat, subagent, workflow step) holds a tracker; and an unusually long turn (e.g. > N tool calls) gets one advisory notice. Nothing is aborted by this item.
- **Solution:**
  1. Add a comparison-only normalizer (mask hex ids, UUIDs, ISO timestamps, durations like '1.23s', PIDs, tmp paths) to repeat_loop.py and compute a second signature/outcome pair from the normalized input/output; fire on either the exact or the normalized streak reaching the threshold. Share the normalizer with the task-runner loop fix (#17186 / WF-6) rather than writing two.
  2. Add an advisory per-turn call budget to the tracker (count note_call ids; one notice past e.g. 60 calls), still notify-once and never an abort — the module docstring forbids aborting because deliberate polling trips the same signal.
  3. Hold a tracker in the subagent turn loop (subagent_manager/run.py) and the workflow agent_exec loop, with the same delivery seam as chat (see TOOL-21).
  4. Document in docs/system-specs/modules/session.md (or the module that owns turn observers).
- **Done when:** Unit tests on RepeatLoopTracker: three results that differ only in an ISO timestamp and a '0.42s' duration produce a notice on the third; three results with different FAILED test names produce none; 61 distinct calls produce exactly one budget notice; the existing repeat_loop tests stay green. No clock or sleep involved.
- **Upstream:** #17186 (state unverified)
- **Changed from the source claim:** Holds; second-reader verification done. Added: only _run_chat holds a tracker, so subagent/workflow/task-runner turns have no repeat detection at all.
- **Sources:** verify_needed:A10

### SKL-6 [35, armed, effort S] Triggered global skills have no byte cap; capped confined skills re-inject on every match — PARTLY
- **Original claim:** Trigger-matched skill bodies have no byte cap; confined skills re-inject every match (corrected below)
- **Verified claim:** With skills.max_triggered > 0, the trigger path loads a matched GLOBAL (operator-installed / built-in / app) skill with `load_skill(name, project)` and no max_bytes, which reads up to the 50 MB file-safety cap: a 153,062 B global body was injected whole in one turn (the `$skill` path would refuse it, capped at SKILL_READ_CAPACITY 99,000). CONFINED project skills, however, ARE capped: every confined body read goes through PROJECT_SKILL_BODY_CAP = 24,750 B (an oversized project SKILL.md is skipped). What holds for confined skills is the dedup exclusion: they are never demotion candidates, so their full body re-injects on every matching turn (19,442 B on each of 3 consecutive matches in one session, vs the global skill demoted to a pointer on its second match). Per-turn total is bounded only by max_triggered x body size.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:3654 — trigger path `content = self.skills.load_skill(name, project)` (no max_bytes)
  - src/kiro_crew/skills.py:3392-3397 — `def load_skill(..., *, max_bytes: int | None = None, ...)`
  - src/kiro_crew/skills.py:3507 — '`max_bytes=None` reads up to the shared file safety cap'; src/kiro_crew/hooks.py:2114 `MAX_FILE_BYTES = 50 * 1024 * 1024`
  - src/kiro_crew/skills.py:312 — `PROJECT_SKILL_BODY_CAP = 24_750` ('an oversized project SKILL.md is skipped rather than loaded whole'); applied at :3471 for confined reads in load_skill
  - src/kiro_crew/skills.py:322 — `SKILL_READ_CAPACITY = 99_000` used by the `$skill` path (read_scoped_skill :5745)
  - src/kiro_crew/context.py:3671-3683 — `confined = self.skills.confined_triggered(...)`; candidates exclude `name in confined` ('they always re-inject their body')
  - src/kiro_crew/skill_runtime/delivery.py:176-198 — confined_triggered: 'They always re-inject the body'
  - measured (confined_reinject.py): global 150 KB skill -> 153,062 B body injected; repeat match -> pointer only. Trusted-project confined skill 19.4 KB -> 19,442 B body on each of 3 consecutive matching turns. A 150 KB confined SKILL.md was refused: 'Skipping oversized confined skill file'
- **Checked by:** ran-new-script + read
- **Required outcome:** Trigger-injected skill bodies have a per-body byte cap equal to the `$skill` path's (SKILL_READ_CAPACITY) and a per-turn total, and a confined project skill's repeat match in the same provider window does not re-send its whole body.
- **Solution:**
  1. Pass `max_bytes=SKILL_READ_CAPACITY` at context.py:3654 (load_skill already refuses rather than truncates, skills.py:3516-3522); for a refused oversized body emit the pointer line (trigger_hint) so the agent can page it through the skill tool.
  2. Add a per-turn total for trigger-injected bodies (e.g. a SkillsConfig field defaulting to SKILL_READ_CAPACITY) beyond which further matches degrade to pointers; this is a new config field, so follow docs/system-specs/modules/config.md.
  3. Confined skills: dedup them by digest like global ones, but on demotion emit a confinement-safe pointer (skill name only, read through skill_search's confined reader — no filesystem path), instead of excluding them from candidates at context.py:3679-3683; do not widen confinement.
  4. Update docs/system-specs/modules/memory-skills-hooks.md (trigger delivery) in the same commit.
- **Done when:** Tests in a tmp KIROCREW_HOME with max_triggered=3: (a) a 150 KB global skill matched by trigger yields a pointer line, not a 150 KB body; (b) three matches whose bodies sum past the per-turn total inject bodies up to the total and pointers after it; (c) a trusted tmp project with a 20 KB confined skill matched on two consecutive turns of one session key yields the body once and a name-only pointer the second time; (d) needs_reinjection re-sends it.
- **Changed from the source claim:** Corrected: confined project skills are NOT uncapped — PROJECT_SKILL_BODY_CAP (24,750 B) bounds every confined body read; the uncapped body is the GLOBAL/built-in/app skill on the trigger path (measured 153,062 B injected). The dedup exclusion for confined skills holds (measured 3x re-injection). Severity 40 -> 35: the re-inject case is bounded at ~24.7 KB per match.
- **Sources:** REVIEW_FINDINGS:X2, verify_needed:X2

### OUT-3 [30, default, effort S] Checklist recovery block orders one call per row — CONFIRMED
- **Verified claim:** After a cold start without provider history, the [Task checklist — automatic recovery] block (dashboard/state.py todo_recovery_prompt) is prepended before the user's request and orders 'one `create` call ... then one `complete` call for every task marked [x]': for a 10-row checklist with 7 done it is 1,638 chars / 508 o200k tokens and mandates 8 tool calls (1 create + 7 complete) before the request. Whether kiro-cli's todo_list `complete` accepts several ids in one call (needed for the batched fix) is not checkable here: no fixture in the repo records the complete command's argument shape and kiro-cli is not installed.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/state.py:4583 — def todo_recovery_prompt; :4605-4610 '... one `create` call with this exact description and these tasks in this order, then one `complete` call for every task marked [x].'
  - src/kiro_crew/dashboard/chat_turn/prompt_assembly.py:136-147 — cold start the provider did not resume gets the whole recovery block, prepended to the message
  - docs/architecture/context-management.md:220 — '[Task checklist — automatic recovery] | todo_recovery_prompt, prepended ... a cold start without provider history'
  - ran a throwaway script rows=10 done=7; recovery block 1,638 ch / 508 tok; mandated calls 8 (1 create + 7 complete)
  - src/kiro_crew/acp/_dispatch.py:2718-2722 — every todo_list command (create / complete / list) echoes the entire list (each extra call returns the full list again)
- **Checked by:** ran-existing-script (copy, homes redirected) + read
- **Required outcome:** The checklist recovery costs at most two tool calls (one create, one batched complete) regardless of how many rows are done, and the block wording says so.
- **Solution:**
  1. Verify on a real install that kiro-cli's todo_list `complete` accepts multiple ids/indices in one call (live check); if it does not, the fix is wording-only to the minimum the tool allows.
  2. Reword state.py:4605-4610 to 'one `create`, then ONE `complete` call listing every [x] task id'.
  3. Optionally rebuild lazily (only when the agent next touches todo_list).
  4. Update docs/architecture/context-management.md:220.
- **Done when:** test_todo_pill_resync.py asserts the recovery block for a 10-row/7-done checklist asks for exactly one `complete` call that lists every done id (string check on todo_recovery_prompt()), and the warm person-edit block test (:81) stays green.
- **Changed from the source claim:** none — 508-token / 8-call figures reproduce exactly; the kiro-cli batched-complete precondition remains unverified here (FIX_PLAN already flagged it).
- **Sources:** FIX_PLAN:OUT-3, REVIEW_FINDINGS:Part6/OUT-3

### SKL-3 [30, armed, effort M] Skill-body dedup resets on native resume — CONFIRMED
- **Verified claim:** The per-session skill-body dedup record is reset whenever build_message runs with `is_new_session or needs_reinjection`. A natively resumed session (FirstTurnState.RESUMED, whose ACP session/load restored the transcript) still derives `is_new=True`, the dashboard passes it through as is_new_session, and `_has_native_history` (which would tell the two apart) is computed only later in build_message; the record itself is an in-memory dict, so a gateway restart also empties it. Re-measured with triggers on (cap 3): fresh -> 13,334 B widgets body; same skill -> 623 B pointer; native resume -> next match re-injects the 13,334 B body; restart + native resume -> 13,334 B again, although session/load already restored the earlier copy.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/context.py:3557-3558 — `if skill_bodies_session and (is_new_session or needs_reinjection): self._dedup_triggered_bodies(..., reset=True, candidates=[])`
  - src/kiro_crew/context.py:3674-3677 — `_dedup_triggered_bodies(..., reset=is_new_session or needs_reinjection, ...)`
  - src/kiro_crew/context.py:3815 — `_has_native_history = bool(session_key and (resumed or not is_new_session))` computed after the reset
  - src/kiro_crew/context.py:1897 — `self._sent_skill_bodies: dict[str, dict[str, str]] = {}` (process memory only)
  - src/kiro_crew/session.py:1018 — `FirstTurnState.is_new` returns True for RESUMED (`self is not FirstTurnState.NOTHING_ARMED`)
  - src/kiro_crew/dashboard/chat_runner.py:10201 — `_context_is_new = is_new or _replay_pending or _first_turn_history_owed`; :10242 `is_new_session=_context_is_new` (with `resumed=resumed`)
  - measured (dedup_check.py): T2 body 13,334 B; T3 pointer 623 B; T4 resume (is_new=True, resumed=True); T5 same skill -> body 13,334 B again; T8 restart + resume -> body 13,334 B (prior: 13,334 B, unchanged)
- **Checked by:** ran-existing-script + read
- **Required outcome:** A native resume (and a restart followed by a native resume) keeps the record of skill bodies the provider session already holds, so a repeat match sends the pointer; only a genuinely fresh window (no native history) or needs_reinjection resets it.
- **Solution:**
  1. In ContextBuilder.build_message, compute `_has_native_history` (context.py:3815) BEFORE the reset at :3557 and reset the record only when there is no native history or `needs_reinjection` is set; apply the same predicate to the `reset=` argument at :3677.
  2. For restarts, rebuild the record lazily from the `[Skill: name]` blocks in the session's conversation log (bounded tail read), or persist the per-session digest map beside the session record under the data home; keep `_SENT_SKILL_BODY_SESSIONS` bounding.
  3. Keep the fail-safe direction documented in `_dedup_triggered_bodies` (a wrong demote yields a pointer, never silence). Note the separate opposite gap — backend self-compaction not arming needs_reinjection (comment at context.py:3545-3555, upstream #17467) — is CTX-12, not this item.
  4. Update docs/system-specs/modules/memory-skills-hooks.md (skill-body dedup) in the same commit.
- **Done when:** A ContextBuilder test with max_triggered=3 in a tmp KIROCREW_HOME: fresh -> body; second match -> pointer; build with is_new_session=True, resumed=True then a match -> pointer; a NEW ContextBuilder + resumed=True then a match -> pointer; needs_reinjection=True then a match -> body. No sleeps or real sessions.
- **Upstream:** #17467 (state unverified)
- **Changed from the source claim:** Dropped upstream #17467 from this item: #17467 ('turn loops ignore a backend compaction') is the opposite under-injection defect (CTX-12). Numbers unchanged (13,334 B re-inject).
- **Sources:** FIX_PLAN:SKL-3, REVIEW_FINDINGS:Part6/SKL-3

### SPEC-7 [30, armed, effort S] App specs ship a `skills` key kiro-cli may reject — CONFIRMED
- **Verified claim:** auto_improvement discovery.json and personal_shopper advisor.json ship a top-level `skills` array; the bridge keeps it as a user 'preference' key, so the materialized ~/.kiro/agents/auto-improvement--auto-improvement-discovery.json and personal-shopper--personal-shopper-advisor.json carry `skills`, and the native kiro launch view is a deepcopy of the spec, so the key reaches kiro-cli. agent-spec-fields.md says kiro-cli validates specs with deny_unknown_fields and falls back to the DEFAULT agent on an unknown key, and that `skills` is never written under that name 'because kiro-cli would reject the unknown field and drop the agent'; test_app_bridges.py treats `skills` as a live, user-pinnable field (agent_discovery reads it). The repo contradicts itself; whether the installed kiro-cli rejects `skills` was not run here. If the doc is right, both agents silently run as kiro-cli's default agent, losing their prompt, tools and containment.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/auto_improvement/agents/discovery.json:6 — "skills": ["ai-discover", "metric-design"]
  - src/kiro_crew/apps/builtins/personal_shopper/agents/advisor.json:7 — "skills": [
  - src/kiro_crew/docs/agent-spec-fields.md:78-82 — 'kiro-cli validates the file with serde deny_unknown_fields and silently falls back to the default agent on any key it does not know'
  - src/kiro_crew/docs/agent-spec-fields.md:730-733 — 'skills is a computed view of resources and is never written back under that name, because kiro-cli would reject the unknown field and drop the agent'
  - test/test_app_bridges.py:3760-3767 — 'skills belongs here too ... it is a live field (agent_discovery.py reads row.get("skills"))'
  - src/kiro_crew/agent_discovery.py:1856 — skills=list(row.get('skills') or [])
  - src/kiro_crew/acp/skill_projection.py:2882 — view = copy.deepcopy(spec) (unknown keys survive into the launch view)
  - measured (merge/scripts/C/spec/build.py --apps): both materialized specs contain key 'skills' (['ai-discover','metric-design'] / ['personal-shopper'])
- **Checked by:** read + ran-existing-script
- **Required outcome:** Materialized app specs use only fields kiro-cli accepts; an app agent's skills are expressed as resources: ["skill://…"] (or stripped before writing), and the test and the doc agree.
- **Solution:**
  1. Run the live check to settle kiro-cli's behaviour.
  2. In apps/bridges.py (_render_shipped_agent / _register_agents) map a template `skills` list to `skill://<name>` entries in `resources` and drop the `skills` key before writing, or move the two templates to resources directly.
  3. Fix test_app_bridges.py:3760-3767 to stop listing `skills` as a preserved spec key, and keep agent_discovery reading skills from resources.
  4. Update src/kiro_crew/docs/agent-spec-fields.md if the live check contradicts it.
- **Done when:** A test over apps/builtins/*/agents/*.json materialized in a throwaway home asserts every top-level key is in the accepted kiro-cli field set (the inventory in agent-spec-fields.md); live: `kiro-cli chat --agent auto-improvement--auto-improvement-discovery` starts that agent (its prompt, not the default).
- **Changed from the source claim:** Repo self-contradiction confirmed and shown to reach the on-disk spec and launch view; kiro-cli's actual behaviour still unrun.
- **Sources:** FIX_PLAN:SPEC-7, REVIEW_FINDINGS:Part6/SPEC-7

### TOOL-10 [30, armed, effort S] Browser snapshot silently cut at 2000 chars — CONFIRMED
- **Verified claim:** mcp_tools/browser.py _result_text returns f'Browser {op}: {rendered[:2000]}' for every non-screenshot op, including op='snapshot' (which the description says to call first for element refs), with no truncation notice, total or remaining count. The tool is always advertised but only works when a native Browser panel serves the session (desktop shell); otherwise it points the model at playwright-cli.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/browser.py:294-311 — _result_text: rendered = ...json.dumps(result); return f"Browser {op}: {rendered[:2000]}"
  - src/kiro_crew/mcp_tools/browser.py:205-216 — description: "Call op='snapshot' first to get element refs"
  - src/kiro_crew/mcp_tools/browser.py:193-202 — 'Always advertised (never gated on _browsing_available())'; works only with a native panel
- **Checked by:** read
- **Required outcome:** A snapshot budget of ~16-24k chars; when cut, the result ends with '[truncated: N more chars/nodes; scope with ref=…]', and op='snapshot' accepts a ref/selector to scope the tree.
- **Solution:**
  1. Replace rendered[:2000] (mcp_tools/browser.py:311) with a named limit in mcp_tools/_limits.py (~20k for snapshot, keep a small limit for other ops) and append an explicit truncation note with the omitted size.
  2. Pass a scoping ref through args for op='snapshot' if the panel command supports it; document it in the description.
  3. Keep the screenshot branch unchanged.
- **Done when:** A unit test feeds _result_text('snapshot', <30k-char tree>) and asserts the output is <= the limit, ends with a truncation note naming the omitted count, and a 1,500-char result is returned whole with no note.
- **Changed from the source claim:** Holds. Scope corrected default -> armed: the tool only returns snapshots when the desktop Browser panel serves the session.
- **Sources:** FIX_PLAN:TOOL-10, REVIEW_FINDINGS:U9

### TOOL-16 [30, armed, effort S] workflow_result events repeat run_id, timestamps and summaries — CONFIRMED
- **Verified claim:** Each workflow_result event is serialized via WorkflowEvent.to_json with its own run_id, seq and a 32-char microsecond ISO timestamp, and each agent_finished event carries result_summary = the first 120 chars of the agent's result, which agent_results then carries in full. On a 6-agent run (28 events) all 28 events repeat run_id; a one-line-per-event rendering (run_id once, HH:MM:SS, empties dropped) measured -52.9% (4,177 -> 1,969 o200k tokens), -67.8% for CJK.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/workflows/__init__.py:221-230 — to_json returns {run_id, seq, ts, type, data} for every event
  - src/kiro_crew/workflows/runner.py:500 — result_summary=("" if result is None else str(result)[:120])
  - src/kiro_crew/workflows/events.py:141-152 — agent_finished(..., result_summary, ok, error='') stored in data
  - src/kiro_crew/mcp_tools/workflows.py:391-411 — wf_payload['events'] = all events; agent_results added in full; json.dumps(wf_payload, indent=2)
  - measured (fmt harness): 28/28 events carry run_id; ts length 32 ('2026-10-07T10:26:43.558220+00:00'); workflow_result 4,177 -> terse 1,969 tok; CJK 6,286 -> 2,026 tok
- **Checked by:** ran-new-script (copy) + ran-existing-script + read
- **Required outcome:** In mcp_tools/workflows.py's model-facing rendering only: run_id appears once, event timestamps are HH:MM:SS, result_summary is dropped when agent_results is present, and empty strings are dropped (about -53%). The stored journal and the dashboard API are unchanged. Land together with TOOL-7.
- **Solution:**
  1. In mcp_tools/workflows.py workflow_result (:368-411), project each event to {seq, t: ts[11:19], type, data-without-empties} and omit the per-event run_id (top-level run_id stays).
  2. When agent_results is present, drop data.result_summary from agent_finished events.
  3. Render with the TOOL-13 compact helper; keep _redact_obj on every projected field.
  4. Do not change WorkflowEvent.to_json (workflows/__init__.py:221) — the journal, resume and the dashboard read it.
- **Done when:** test_mcp_core_coverage.py workflow_result tests (:835-868) still pass; a new test feeds a stubbed /api/workflows/runs/<id> snapshot with 28 events and 6 agent_results and asserts the run_id string occurs exactly once in the output, no event timestamp is longer than 8 chars, and no 'result_summary' key appears.
- **Changed from the source claim:** Minor precision: result_summary repeats a 120-char prefix of each agent result (runner.py:500), not the whole agent_results. Otherwise none; -53% reproduces.
- **Second reader:** random-sample check: agreed.
- **Sources:** FIX_PLAN:TOOL-16, REVIEW_FINDINGS:F6

### OUT-4 [25, default, effort S] Prompt's routine resource_status pre-check is redundant only on full-context sessions — PARTLY
- **Original claim:** Prompt requires a routine resource_status pre-check (corrected below)
- **Verified claim:** prompt.md (:31, :47) and the resource_status tool description tell the model to call resource_status BEFORE full tests, large builds or wide spawn waves. On full-context sessions the per-turn [RESOURCES] line already pushes the same signal and is silent when memory, task ceiling and kernel pressure are all clear, so a routine pre-check returns ~350 chars saying 'Posture: AMPLE' while [RESOURCES] is empty (measured on this host). But the [RESOURCES] line is skipped for minimal_context sessions (which still receive prompt.md), and it does not carry the live sub-agent cap, so 'absent means fine' holds only for full-context sessions; there the pre-check is redundant.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/prompt.md:31 — '`resource_status`: BEFORE full tests, large builds or wide spawn waves, check memory/CPU headroom and live cap.'
  - src/kiro_crew/config/prompt.md:47 — '... check `resource_status` before a wide wave.'
  - src/kiro_crew/mcp_tools/spawn.py:614-620 — resource_status description 'Check current host resource headroom BEFORE starting a heavy step ...'
  - src/kiro_crew/context_assembly/turn.py:324-340 — [RESOURCES] advisory injected ONLY when a ceiling is near ('Silent (zero token cost) when all three are clear'); 'Skipped for minimal contexts' (if not minimal_context)
  - ran a throwaway script resource_status 350 chars ('Posture: AMPLE ... Guidance: ample headroom — heavy work is fine.'); [RESOURCES] context_line 0 chars
- **Checked by:** ran-new-script + read
- **Required outcome:** On full-context sessions the model calls resource_status only when a [RESOURCES] line is present or after a heavy step died; minimal-context sessions (no [RESOURCES] line) keep a conditional pre-check before genuinely heavy work.
- **Solution:**
  1. Reword prompt.md:31 and :47 to 'call resource_status only when a [RESOURCES] line is present, after a heavy step was killed, or in a minimal-context run before a full suite/wide wave'.
  2. Remove 'BEFORE starting a heavy step' as an unconditional instruction from the description at mcp_tools/spawn.py:614-620 (keep what it returns).
  3. Optionally add the live sub-agent cap to the [RESOURCES] line when it is below the configured max, so the line is the complete signal.
- **Done when:** A text test over config/prompt.md and the resource_status tool description finds no unconditional 'BEFORE' pre-check instruction; the existing [RESOURCES] injection test stays green; a build_message test with minimal_context=True still carries a conditional resource_status instruction.
- **Changed from the source claim:** 350-char / empty-[RESOURCES] measurement reproduces. Mis-scope corrected: turn.py skips [RESOURCES] under minimal_context while prompt.md still reaches those sessions, and the line omits the live sub-agent cap, so 'absent means fine' is true only for full-context sessions. Severity 30 -> 25.
- **Sources:** FIX_PLAN:OUT-4, REVIEW_FINDINGS:Part6/OUT-4

### SKL-7 [25, default, effort S] $skill expansion can stack five 99K bodies in one turn — CONFIRMED
- **Verified claim:** Each distinct `$name` token in a dashboard message resolves to a full skill body read with read_scoped_skill's default bound (SKILL_READ_CAPACITY 99,000 B per body; a larger body is refused, not truncated), up to `_MAX_DOLLAR_SKILLS = 5` per message, with no per-turn total, so one message can append up to ~495 KB; all bodies are appended to the user's message and every body is also snapshotted into the transcript row's `meta.skills`. Measured: five tokens ($computer-use $widgets $artifacts $kirocrew-dev/writing-tests $goal-conductor) -> 163,048 B in one turn.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/skills.py:244 — `_MAX_DOLLAR_SKILLS = 5`
  - src/kiro_crew/skills.py:322 — `SKILL_READ_CAPACITY = 99_000`; :5745 read_scoped_skill(`max_bytes: int = SKILL_READ_CAPACITY`) refuses an over-bound body
  - src/kiro_crew/skill_runtime/search.py:264-326 — resolve_dollar_skills: loops tokens, `if len(resolved) >= sk._MAX_DOLLAR_SKILLS: break` (count cap only, no byte total)
  - src/kiro_crew/dashboard/chat_runner.py:4702 — `expanded = message + "\n\n" + "\n\n---\n\n".join(blocks)` (all bodies, no total)
  - src/kiro_crew/dashboard/chat_runner.py:4704-4709 — `slot.append(..., meta={"kind": "skill_load", "skills": snapshots})` (each full body snapshotted into the transcript row)
  - measured (dollar_check.py): 5 bodies, 163,048 B in one message (prior 163,048 B, unchanged)
- **Checked by:** ran-existing-script + read
- **Required outcome:** One message's `$skill` expansion has a per-turn byte total; bodies past it (or already in the session, see SKL-5) are delivered as pointers the agent can page, and the transcript row stores references instead of duplicate full bodies.
- **Solution:**
  1. In skill_runtime/search.py resolve_dollar_skills (or in _expand_dollar_skills, chat_runner.py:4689-4702), keep a running byte total and stop appending bodies once it would exceed a per-turn budget (suggest SKILL_READ_CAPACITY, i.e. one full body's worth); for the remaining resolved skills append a pointer line (name + 'read with the skill tool') and tell the user in the chip which were deferred.
  2. Store `{name, sha256, bytes}` in `meta.skills` instead of the full body, or cap the snapshot; check the dashboard renderer that reads `meta.skills` (website/src, kind 'skill_load') before changing the shape — that is a UI contract, so it is a take-away change for the transcript view.
  3. Compose with SKL-5 (session dedup). Update docs/system-specs/modules/memory-skills-hooks.md ($skill section).
- **Done when:** A unit test resolves five `$` tokens whose bodies sum past the budget (tmp KIROCREW_HOME skills of fixed sizes) and asserts the expanded message carries bodies totalling <= the budget plus pointer lines for the rest, and that the transcript row's meta.skills holds no body larger than the agreed snapshot cap.
- **Changed from the source claim:** none; 163,048 B re-measured identical. Clarified the per-body bound is a refusal (bodies > 99,000 B are not loaded via `$`), so the worst case is 5 x 99,000 B.
- **Sources:** REVIEW_FINDINGS:X4, verify_needed:X4

### SPEC-4 [25, default, effort S] Background agents load the user's global steering files; workspace globs usually find none — PARTLY
- **Original claim:** Internal background agents inherit user steering files (corrected below)
- **Verified claim:** When steering inheritance is on (the default: it is off only if kiro-cli's own disable-inherit setting is true), prepare_native_skill_projection appends file://<kiro_home>/steering/**/*.md, file://.kiro/steering/**/*.md and file://AGENTS.md to EVERY launch view, internal ones included (kirocrew-lite, -knowledge, -heartbeat, -guest): re-measured, all 26 views carry the three globs and 33,973 B in a seed of 8,010 B global steering + 19,973 B AGENTS.md + 5,990 B workspace steering. The cost for internal background agents is overstated by that figure: the lite and knowledge pools run with cwd <data home>/workspace, where the two workspace-relative globs normally match nothing, so a title/summary/consolidation/extraction call pays the user's GLOBAL steering only (8,010 B in this seed; zero with none; G16 reports ~18k tokens on a heavy home). Matches kiro-cli's default; the gap is the missing opt-out.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/acp/skill_projection.py:3023-3031 — if inherited: for view in specs.values(): append f'file://{kiro_home()}/steering/**/*.md', 'file://.kiro/steering/**/*.md', 'file://AGENTS.md'
  - src/kiro_crew/acp/skill_projection.py:1481-1487 — _inheritance_preference: inherit unless the native disable setting is True
  - src/kiro_crew/acp/client.py:3581-3584 — default work dir: config_dir() / 'workspace'
  - src/kiro_crew/knowledge/llm_pool.py:446-447 — knowledge pool cwd is 'a workspace under the data home'
  - src/kiro_crew/config/sections.py:277 — BACKGROUND_WORKER_AGENTS = ('kirocrew-lite', 'kirocrew-heartbeat')
  - measured (merge/scripts/C/spec/views.py): 26 views, n_res>=3, res_bytes 33,973 each (global 8,010 + AGENTS.md 19,973 + ws steering 5,990)
- **Checked by:** read + ran-existing-script
- **Required outcome:** Internal background agents (BACKGROUND_WORKER_AGENTS plus kirocrew-knowledge) skip the inherited steering globs; kirocrew and user custom agents keep kiro-cli's default inheritance.
- **Solution:**
  1. In acp/skill_projection.py:3023-3031 skip the three appends for a Crew-side set of internal agents (BACKGROUND_WORKER_AGENTS + kirocrew-knowledge); it cannot be a spec key because kiro-cli validates specs with deny_unknown_fields.
  2. Keep _project_steering_delivered / inherits_default_resources (:1490) consistent with the new rule, and mind _is_legacy_projected_view (:1700-1727) so old views are not misclassified.
  3. Take-away change (a user who relied on global steering reaching titles/summaries loses it): read take-away-changes.md and list it.
  4. Do NOT change the guest agent's inheritance under this item — the operator's global steering reaching the non-operator guest is a security question for the owner (SEC-22).
- **Done when:** With a seeded ~/.kiro/steering file in a throwaway home, prepare_native_skill_projection's views for kirocrew-lite and kirocrew-knowledge carry no inherited globs, while kirocrew and a user custom agent still carry all three.
- **Changed from the source claim:** Mechanism and the 26/26, 33,973 B measurement hold, but 33,973 B is global + workspace files; background pools run in the data-home workspace and normally pay only the user's global steering (8,010 B of that seed). Severity 30 -> 25.
- **Sources:** FIX_PLAN:SPEC-4, REVIEW_FINDINGS:Part6/SPEC-4

### TOOL-11 [25, default, effort S] kiro_cli_logs returns up to 80k chars — CONFIRMED
- **Verified claim:** kiro_cli_logs is bounded but large by default: tail defaults to 200 lines per source, each source is byte-capped at min(64 KiB, 80,000 / number_of_sources), and the whole response at _MAX_LOG_RESPONSE_CHARS = 80,000 chars, trimmed from the front so the newest lines survive. With protocol-log-length lines a default call returns 65,562 chars from one 1 MB source and 79,544 chars from three (measured) — ~16-20k tokens per call; even tail=50 returns 79,543 chars with three sources, because the line count is per source.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/diagnostics.py:327 — _MAX_LOG_READ_BYTES = 64 * 1024 (per source)
  - src/kiro_crew/diagnostics.py:347 — _MAX_LOG_RESPONSE_CHARS = 80_000 (whole response; front-trimmed, test-pinned under the transport ceiling)
  - src/kiro_crew/diagnostics.py:714 — max_bytes = min(_MAX_LOG_READ_BYTES, _MAX_LOG_RESPONSE_CHARS // max(len(sources), 1))
  - src/kiro_crew/mcp_tools/logs.py:59-75 — '`tail` bounds the number of lines (default 200)' per source
  - measured (merge/scripts/C/tool11_logs.py, stubbed source list, ~600-char lines): 1 source default -> 65,562 chars; 3 sources default -> 79,544; 3 sources tail=50 -> 79,543
- **Checked by:** read + ran-new-script
- **Required outcome:** A default call returns ~20k chars / ~50 newest lines in total (not per source) unless the caller asks for more; the existing 80k ceiling stays as the hard limit.
- **Solution:**
  1. Add a default response budget (~20,000 chars) in diagnostics.read_kiro_cli_logs (diagnostics.py:647) used when the caller passes no tail, keeping _MAX_LOG_RESPONSE_CHARS as the ceiling for explicit requests.
  2. Make the default tail apply to the merged newest lines across sources (or divide it among sources), and say in the header how many older lines were left out and how to ask for more.
  3. Update the description at mcp_tools/logs.py:59 and keep test_response_budget_stays_under_the_transport_ceiling green.
- **Done when:** With three fake 1 MB logs (stubbed _kiro_cli_extra_logs), read_kiro_cli_logs() returns <= 20,000 chars containing the newest line of each source; read_kiro_cli_logs(tail=2000) still returns <= 80,000 chars; existing diagnostics tests pass.
- **Changed from the source claim:** Holds as a ceiling and as the typical default size with real-length lines. Consistent with the fifth-pass note that the tool is bounded (tail 200, per-source and whole caps): both are true. The U10 SKILL.md half lives in LOOP-2/SKL-4.
- **Sources:** FIX_PLAN:TOOL-11, REVIEW_FINDINGS:U10

### TOOL-17 [25, default, effort S] Repeated policy denials sent in full — CONFIRMED
- **Verified claim:** Every in-band deny notice is built in full by build_refusal_steer_notice, carrying 401 chars of invariant wording (excluding title, reason and cause text); _steer_policy_notice builds and steers a fresh full notice for every denial, with no shorter form for a 2nd+ denial in the same turn; the git-publish reason embeds the raw rule regex '(rule pattern: ...)' plus a 'Refusal diagnostic' line. Measured: git force-push notice 1,177 chars / 310 o200k tokens; a short repeat form is 198 tokens (-36.1%).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/deny_notice.py:196-204 — '[Kiro Crew host notice] The tool call you just made {clause}. This was NOT a user action — ... Decide and continue in this same turn: {guidance}{tail}'
  - src/kiro_crew/dashboard/chat_runner.py:1106-1150 — _steer_policy_notice: notice = build_refusal_steer_notice(title, reason, ...) on every call; no per-turn repeat check
  - src/kiro_crew/security/__init__.py:1383 — f"{note} (rule pattern: {gated_pattern})"
  - measured a throwaway script invariant boilerplate per notice 401 chars; notice by cause 686-884 chars; git_force_push 1,177 chars (rule regex included); IMDS 2,210 chars (1,411 remediation)
  - measured o200k: deny_git_force_push actual 310 tok -> repeat form 198 tok (-36.1%)
- **Checked by:** ran-new-script (copy of fmt/refusals.py, home redirected, hook probe removed) + ran-existing-script + read
- **Required outcome:** The second and later policy denials in one turn use a short form ('Blocked again by host policy (not the user): <title>: <reason>; same guidance as above'), measured -36%; optionally the raw '(rule pattern: …)' regex is dropped from the model's copy. Remediation text (e.g. IMDS 1,411 chars) is not shortened without the security owner.
- **Solution:**
  1. Read docs/system-specs/modules/security.md and sel.md first.
  2. In dashboard/chat_runner.py _steer_policy_notice (:1106), detect a prior notice already in the turn's `notices` list (the per-turn pending list it appends to) and, for cause == DENY_CAUSE_POLICY, build a short repeat form via a new deny_notice.build_repeat_refusal_notice(title, reason); keep the first notice byte-identical.
  3. Consider dropping '(rule pattern: …)' from the model copy only (security/__init__.py:1383) while keeping it in the SEL audit record.
  4. Do not touch remediation_for / IMDS text.
- **Done when:** A test drives two policy denials through _steer_policy_notice with a fake client (supports_refusal_steer=True, steer() returns True) and the same `notices` list: the first notice equals build_refusal_steer_notice(...) exactly, the second starts with 'Blocked again by host policy' and is shorter than the first; test_deny_guidance.py stays green.
- **Changed from the source claim:** none — 401-char invariant wording and the -36% repeat-form figure reproduce exactly; builder now at deny_notice.py:196-204.
- **Sources:** FIX_PLAN:TOOL-17, REVIEW_FINDINGS:F7

### TOOL-21 [25, default, effort S] Repeat-loop steering is best-effort only — CONFIRMED
- **Verified claim:** When the tracker fires, _run_chat calls _steer_repeat_loop_notice and ignores its result. The steer is attempted only when client.supports_refusal_steer is true — ACP_BACKENDS_STEER = {kiro, KAS} — so on every other selectable harness the notice is never delivered at all; on kiro/KAS a failed or timed-out steer is logged at debug and dropped. There is no fallback (next-prompt injection) and no Crew-side turn cancel, so a loop the model does not break by itself runs until the turn ends or the transport timeout.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:1089-1103 — _steer_repeat_loop_notice: if not getattr(client, 'supports_refusal_steer', False): return False; except: logger.debug('repeat-loop steer failed'); return False
  - src/kiro_crew/dashboard/chat_runner.py:12303-12304 — if _loop_notice: await _steer_repeat_loop_notice(client, ...) (return value unused)
  - src/kiro_crew/agent_sdk/backends.py:1089 — ACP_BACKENDS_STEER = frozenset({ACP_BACKEND_KIRO, ACP_BACKEND_KAS})
  - src/kiro_crew/repeat_loop.py:7-9 — 'Nothing is aborted: a call that is deliberately polled for an outside change trips the same signal'
- **Checked by:** read
- **Required outcome:** A repeat-loop notice that could not be steered into the running turn is still delivered: on the next model boundary Kiro Crew controls (the next prompt of this session), and on non-steer harnesses always that way. Whether a Crew-side cancel should follow N further identical repeats after the notice is a maintainer decision, because the module's contract deliberately never aborts (polling trips the same signal).
- **Solution:**
  1. In dashboard/chat_runner.py:12303 keep the boolean from _steer_repeat_loop_notice; when False, park the notice on the slot (per-session state in the gateway, not a module global) and prepend it once to the next user/continuation prompt built for that session.
  2. Select the delivery path by positive capability (supports_refusal_steer), never by harness identity, matching _steer_policy_notice's rule.
  3. Raise the cancel option with the maintainer; if accepted, add it as a separate item with its own threshold (e.g. 3 further identical outcomes after the notice) and a take-away entry, since it changes what a polling turn does today.
- **Done when:** A _run_chat test with a fake client whose supports_refusal_steer is False and a scripted 3x identical tool result: the next prompt sent for that session contains the notice exactly once; with a fake client whose steer raises, same result; with a steering client that accepts, the notice is not repeated on the next prompt.
- **Changed from the source claim:** Holds and is wider than stated: on harnesses outside {kiro, KAS} the notice is never even attempted. Severity assigned 25 (claim had none).
- **Sources:** verification_needed:P1-5, verification_needed:refactor(repeat-loop-steer)

### SPEC-5 [25, armed, effort S] pptx composer preloads 50 KB of docs as resources — CONFIRMED
- **Verified claim:** pptx-maker-composer.json declares 4 file:// resources (create-new-2-compose.md, create-new-3-review.md, slide-json-spec.md, guides/grid.md) that kiro-cli loads into every request, while its tools already include @sdpm/read_workflows and @sdpm/read_guides. In the engine files fetched for the earlier measurement (pinned engine v0.3.8 / b7c7fcf) they total 50,710 B (~12.7k tokens): 10,493 + 2,705 + 24,075 + 13,437. The engine is not downloadable here, so the byte counts come from that snapshot; the template's four declarations are verified at HEAD.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/pptx_maker/agents/pptx-maker-composer.json:13-18 — resources: 4 x file://{ENGINE_ROOT}/skill/references/...
  - src/kiro_crew/apps/builtins/pptx_maker/agents/pptx-maker-composer.json:19-27 — tools include '@sdpm/read_guides', '@sdpm/read_workflows'
  - src/kiro_crew/apps/builtins/pptx_maker/backend/engine_source.py:60-74 — ENGINE_TAG = 'v0.3.8'; ENGINE_COMMIT = 'b7c7fcf0…'
  - measured (wc -c over SCRATCHPAD/spec/dl_pptx, the prior session's fetch): 10,493 + 2,705 + 24,075 + 13,437 = 50,710 B
  - measured (merge/scripts/C/spec/build.py --apps): materialized composer spec carries the 4 resources (plus 3 inherited steering globs, SPEC-4)
- **Checked by:** read + ran-existing-script
- **Required outcome:** The composer preloads only what every turn needs (probably slide-json-spec) and reads the rest through its read tools.
- **Solution:**
  1. In apps/builtins/pptx_maker/agents/pptx-maker-composer.json keep slide-json-spec.md (or none) as a resource and drop the compose/review workflows and grid guide;
  2. point the composer prompt at @sdpm/read_workflows / read_guides for them (the prompt is the engine's composer.md, so add a Crew-side note in the template or ask upstream);
  3. apply the same review to vibe (19,198 B), style (15,571 B) and spec (8,782 B) per the prior measurement;
  4. spot-check slide quality on a real deck (take-away risk: the model may skip a reading it used to get for free).
- **Done when:** A template test resolves each pptx-maker agent's resources against a fixture engine tree and asserts the declared bytes are under a per-agent budget (e.g. <= 25 KB for the composer); a manual deck run produces a valid .pptx.
- **Changed from the source claim:** Holds; byte counts are from the prior fetched snapshot of the pinned engine, not re-downloaded (no network here).
- **Sources:** FIX_PLAN:SPEC-5, REVIEW_FINDINGS:Part6/SPEC-5

### SES-4 [20, default, effort S] kirocrew-lite spec does not set includeMcpJson: false — CONFIRMED
- **Verified claim:** _install_lite_agent_fallback (the only writer of kirocrew-lite.json, called from rebuild and the prerequisite repair) writes {name, model, tools: [], mcpServers: {}, prompt: ''} with no includeMcpJson, and the materialized file in a throwaway home has the key ABSENT (100 B), while the sibling guest and knowledge specs pin it false. kiro-cli reads an absent key as true, so every kirocrew-lite session (session.BACKGROUND_AGENT: titles, summaries, consolidation, the decision judge, meetings translation, workflow helpers) merges the user's global ~/.kiro/settings/mcp.json servers. tools is [], so no schemas reach the model; the cost is server processes/startup (and any slow or OAuth-prompting user server) on background calls. The repo's own comments disagree on whether kiro-cli spawns a merged server nobody references (service_agents.py:40-41 says it does).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/agent_materialization/service_agents.py:48-57 — lite_config = {name: 'kirocrew-lite', model, tools: [], mcpServers: {}, prompt: ''} (no includeMcpJson)
  - src/kiro_crew/agent_materialization/service_agents.py:37-41 — guest: "includeMcpJson": False with 'kiro-cli defaults this to True and would spawn every server in the user-level mcp.json'
  - src/kiro_crew/agent_materialization/service_agents.py:98 — knowledge: "includeMcpJson": False
  - src/kiro_crew/docs/agent-spec-fields.md (MCP servers table) — 'Absent, kiro-cli reads it as true'
  - src/kiro_crew/session.py:580 — BACKGROUND_AGENT = "kirocrew-lite"; also decisions/impl_llm.py:65, history_consolidation.py:2931/2968/3598, meetings translate.py:114, workflows/service.py:909
  - src/kiro_crew/agent.py:1104-1106 — contrary comment: 'kiro-cli loads a server only when something references it'
  - measured (merge/scripts/C/spec/build.py --apps): kirocrew-lite.json 100 B, includeMcpJson=ABSENT; kirocrew-guest / kirocrew-knowledge False
- **Checked by:** read + ran-existing-script
- **Required outcome:** Every Kiro Crew-written background spec, kirocrew-lite included, states includeMcpJson: false.
- **Solution:**
  1. Add "includeMcpJson": False to lite_config in agent_materialization/service_agents.py:50-56 (with the same comment the guest spec carries).
  2. Because the lite spec is only rewritten when missing (kiro_prerequisite.py:3178-3181) or on rebuild, make sure rebuild_agent_config rewrites it so existing installs pick the key up.
  3. Add a test that every spec in BACKGROUND_WORKER_AGENTS (config/sections.py:277) plus kirocrew-knowledge and kirocrew-guest pins includeMcpJson False.
  4. Update docs/architecture/mcp.md ('Kiro Crew forces false on every agent it manages').
- **Done when:** In a throwaway home, agent.rebuild_agent_config(clean=True) writes kirocrew-lite.json with includeMcpJson == False; the new parametrized test over the Crew-written service specs passes.
- **Changed from the source claim:** Plausible -> confirmed on disk. Cost corrected to processes/startup, not tokens (tools is empty). Severity 25 -> 20.
- **Sources:** FIX_PLAN:SES-4, REVIEW_FINDINGS:H-P7

### SKL-5 [20, default, effort S] $skill expansion bypasses the per-session dedup — CONFIRMED
- **Verified claim:** `_expand_dollar_skills` (dashboard chat runner, its only caller at chat_runner.py:10595) appends the full redacted body of every resolved `$skill` to the user's message on every turn and never consults ContextBuilder's per-session record (`_dedup_triggered_bodies`) nor records the body there, so `$babysit` on two consecutive turns sends the 21,543 B body twice, and a body later matched by the trigger path is re-sent again.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:4633 — `def _expand_dollar_skills(message, state, slot, session_key)`
  - src/kiro_crew/dashboard/chat_runner.py:4663 — `resolved = skills.resolve_dollar_skills(message, slot.project or None, only=only)`
  - src/kiro_crew/dashboard/chat_runner.py:4702 — `expanded = message + "\n\n" + "\n\n---\n\n".join(blocks)` (no dedup lookup in 4633-4711)
  - src/kiro_crew/dashboard/chat_runner.py:10595 — sole call site
  - src/kiro_crew/context.py:1976 — `_dedup_triggered_bodies` is only called from the trigger path (context.py:3558, :3674)
  - measured (dollar_check.py): turn 1 '$babysit ...' 21,543 B; turn 2 '$babysit ...' 21,543 B again (prior 21,543 B, unchanged)
- **Checked by:** ran-existing-script + read
- **Required outcome:** A `$skill` body already sent in this provider session is replaced by its pointer line on later turns, and a `$`-loaded body is recorded so the trigger path also demotes it.
- **Solution:**
  1. Route `$` bodies through the same per-session digest record: before appending at chat_runner.py:4697-4702, ask ContextBuilder (via a small public method wrapping `_dedup_triggered_bodies`, context.py:1976) which (name, sha256(body)) pairs this session already holds; emit the pointer form (skill_runtime/delivery.py:201 trigger_hint) for those.
  2. Record the delivered `$` bodies in the same record so a later trigger match demotes; respect the same reset rule (fresh window / needs_reinjection; see SKL-3).
  3. Keep the transcript chip and `meta.skills` snapshot (chat_runner.py:4704-4709) so the UI still shows what was requested; note pointer vs body there.
  4. Update docs/system-specs/modules/memory-skills-hooks.md ($skill section).
- **Done when:** A chat-runner unit test (fake state/slot, tmp KIROCREW_HOME) sends '$babysit watch PR 1' then '$babysit the next one' on one session key: the first expanded message contains the babysit body, the second contains only the pointer line; a needs_reinjection turn re-sends the body.
- **Changed from the source claim:** none; 21,543 B per turn re-measured identical.
- **Sources:** FIX_PLAN:SKL-5, REVIEW_FINDINGS:Part6/SKL-5

### OUT-6 [15, default, effort M] Prompt's recall-miss path costs 2 tool calls (memory_recall, search_chat_history), not 3 — PARTLY
- **Original claim:** Prompt tells the model to check all three recall sources on a miss (corrected below)
- **Verified claim:** prompt.md:83 tells the model, on a question about past work, to check the injected memory block, then call memory_recall, 'only then fall back to search_chat_history (then get_chat_session ...)', and 'Never say "I don't have that information" without checking all three.' The 'three' are the injected block (no call), memory_recall and search_chat_history; a full miss therefore costs memory_recall + search_chat_history (+ an optional get_chat_session for exact words). A server-side chat-history fallback inside memory_recall would save one call per miss (the separate search), not two; get_chat_session may still be needed for verbatim text. Frequency is unmeasured.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/prompt.md:83 — '... call `memory_recall` with a specific question; only then fall back to `search_chat_history` (then `get_chat_session` on a promising `session_key`) ... Never say "I don't have that information" without checking all three.'
  - src/kiro_crew/mcp_tools/sessions.py:38-50 — search_chat_history description: 'For what was decided or learned, call memory_recall first'; returns snippets, 'call get_chat_session ... to read the full'
  - src/kiro_crew/mcp_tools/learn.py:234-252 — memory_recall queries /api/memory/recall only; no chat-history fallback
  - src/kiro_crew/mcp_tools/learn.py:58-67 — memory_recall description: store bound to this session (Global V1 or the member's private V2)
- **Checked by:** read
- **Required outcome:** On a memory miss one recall call suffices: when no memory hit clears its threshold, memory_recall returns up to N keyword snippet hits from the caller's own chat history (with session keys), and prompt.md:83 no longer asks for a separate search_chat_history call.
- **Solution:**
  1. In the /api/memory/recall handler (or mcp_tools/learn.py:234 memory_recall), when no fact/episode/lesson clears its threshold, run the same keyword search search_chat_history uses, scoped exactly as search_chat_history scopes the caller (same session/workspace and member-privacy rules; a private V2 member must not see Global or sibling transcripts), and return <= N snippet hits with session_key under a separate 'chat_history' key.
  2. Reword prompt.md:83 to 'memory_recall (it falls back to chat history on a miss); get_chat_session for exact words'.
  3. Keep search_chat_history as a tool for explicit keyword search. Update memory-skills-hooks.md.
- **Done when:** With an empty memory store and a seeded transcript containing the query keyword, memory_recall returns that snippet with its session_key; with a matching fact it returns no chat_history block; a private-member session never receives another member's transcript; prompt.md no longer contains 'all three'.
- **Changed from the source claim:** Mechanism confirmed, but the cost is overstated: 'all three' counts the injected block (no tool call), so the miss path is 2 calls (+1 optional get_chat_session) and the fallback saves one call. Added constraint: the fallback must keep search_chat_history's scoping and V2 member privacy. Severity 20 -> 15.
- **Sources:** FIX_PLAN:OUT-6, REVIEW_FINDINGS:Part6/OUT-6

### TOOL-18 [15, default, effort S] Non-JSON HTTP error bodies pass through uncapped — CONFIRMED
- **Verified claim:** mcp_core._http_error_body reads the whole HTTPError body with no size limit and, when it is not a JSON {error} object, uses it verbatim as the error message (`message = raw or str(exc)`). A 20,789-byte HTML body came back as a 20,789-char error; a 311,552-byte body as 311,552 chars. The only bound before the model is sanitize_response's MAX_RESPONSE_LEN = 100,000 chars at the MCP exit (build_tool_response), so up to 100k chars of HTML can reach the tool result.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_core.py:1620-1624 — raw = exc.read().decode('utf-8', 'replace').strip(); message = raw or str(exc)
  - src/kiro_crew/validation.py:166 — MAX_RESPONSE_LEN = 100_000 # truncate tool responses (applied by sanitize_response via build_tool_response, validation.py:4551)
  - measured a throwaway script 20,789 B HTML body -> 20,789-char error; 311,552 B -> 311,552-char error (no cap in _http_error_body)
- **Checked by:** ran-new-script + read
- **Required outcome:** A non-JSON HTTP error body is capped at about 300 chars with '… (N bytes)' appended before it can reach a tool result; JSON {error} bodies and their code/counted markers are unchanged.
- **Solution:**
  1. In mcp_core._http_error_body (:1606), keep `raw` intact for the JSON parse, but when falling back to the raw text set message = raw[:300] + f'… ({len(raw)} bytes)' if len(raw) > 300.
  2. Optionally bound exc.read() (e.g. read(64 KiB)) so a huge body is never fully buffered.
  3. Keep redaction (redact_exfiltration_urls + redact_credentials) after the cut.
- **Done when:** A unit test builds urllib.error.HTTPError with a 20 KB io.BytesIO HTML body and asserts len(mcp_core._http_error_body(exc)['error']) <= 400 and that it ends with '(20… bytes)'; a JSON body {'error': 'unknown session', 'code': 'unknown_session', 'counted': true} still round-trips unchanged.
- **Changed from the source claim:** none — line unchanged at HEAD; measured passthrough; the 'up to 100k' ceiling is validation.MAX_RESPONSE_LEN at the MCP exit.
- **Sources:** FIX_PLAN:TOOL-18, REVIEW_FINDINGS:F8

### ATT-4 [15, armed, effort S] Slack voice-memo transcripts join turn text uncapped; bound is 1 h per memo, none on AWS — PARTLY
- **Original claim:** Voice-memo transcripts joined into turn text without a cap (corrected below)
- **Verified claim:** slack/events.py joins every voice-memo transcript of a message into the turn text with no length cap (redaction only). The stated de-facto bound is wrong: the transcription path (_transcribe_files) does not apply IngestLimits.max_audio_bytes (25 MiB) and the Slack download has no byte cap; the only bound is a per-memo DURATION cap of 3,600 s on the local/Apple providers (batch_duration_cap_secs), and none at all on AWS Transcribe (returns None). Memos are not counted against max_attachments (a separate loop over all files). So one memo can add roughly an hour of speech (~9k words, ~50 KB, ~12k tokens) and N memos N times that, all in one turn.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/slack/events.py:2732-2736 — `raw = "\n".join(transcripts)` -> redaction -> `[Voice memo transcription]\n{raw}` prepended to text (no length cap)
  - src/kiro_crew/slack/events.py:1803-1883 — _transcribe_files loops every voice-memo file; only check is `audio_exceeds_secs(dest, duration_cap)`; no byte or count cap
  - src/kiro_crew/transcribe.py:1969 — `_MAX_AUDIO_SECS = 3600`; :1989-1991 `if stt_config.provider == "transcribe": return None` else `_MAX_AUDIO_SECS`
  - src/kiro_crew/slack/client.py:1025 — download_file: no size limit in the download path
  - src/kiro_crew/messaging/attachments.py:96 — `max_audio_bytes = 25 MiB` is applied only inside ingest_attachments, which Slack calls with handle_audio=False (slack/files.py:437)
- **Checked by:** read
- **Required outcome:** Voice-memo transcripts injected into one turn have an explicit per-message character cap, with the remainder kept by path or summarized, consistent with ATT-1's per-message inline budget.
- **Solution:**
  1. In slack/events.py after the join (:2732), cap the joined transcript at the same per-message inline budget ATT-1 introduces (IngestLimits.max_inline_total), using a head+tail preview and a note '[transcript truncated: N chars; full text at <path>]'.
  2. Store the full transcript in the same session-scoped attachment store as ATT-1 (readable by the session's sandboxed tools without widening any fence).
  3. Optionally apply IngestLimits.max_audio_bytes before transcription in _transcribe_files (:1813) so the AWS Transcribe path has a bound too.
  4. Update docs/system-specs/modules/slack-gateway.md (voice memos) and stt-streaming.md if the cap is shared.
- **Done when:** A slack-events unit test with a fake transcriber returning two 60,000-char transcripts asserts the turn text carries <= the configured inline cap of transcript plus a truncation note naming a stored path; a short memo passes through unchanged.
- **Changed from the source claim:** Corrected the bound: not the 25 MB audio cap (not applied on the transcription path) but a 3,600 s per-memo duration cap on local/Apple STT and none on AWS Transcribe; memo count is not capped by Crew. Assigned severity 15 (claimed None).
- **Sources:** verify_needed:voice-memo-note

### SPEC-6 [15, armed, effort S] App agent templates omit includeMcpJson: false — CONFIRMED
- **Verified claim:** Six shipped app agent templates — the four pptx-maker agents and auto-improvement discovery.json and pr-author.json — omit includeMcpJson; the bridge treats includeMcpJson as a framework-owned key copied from the template (it does not force false), so the materialized ~/.kiro/agents/<app>--<agent>.json files also lack it and kiro-cli reads them as true, merging every global mcp.json server into those sessions. The other app templates (engineer, scout, meetings x3, mochi x2, personal-shopper) state false. Their tool lists are closed (@sdpm/... or the app's own server), so the cost is processes/startup, not schemas. docs/architecture/mcp.md's 'Kiro Crew forces false on every agent it manages (the primary agent and every app agent)' is therefore inaccurate.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/pptx_maker/agents/pptx-maker-{composer,spec,style,vibe}.json — no includeMcpJson key (grep count 0)
  - src/kiro_crew/apps/builtins/auto_improvement/agents/discovery.json, pr-author.json — no includeMcpJson (grep count 0); engineer.json:8 / scout.json:8 state false
  - src/kiro_crew/apps/bridges.py:734 — _FRAMEWORK_OWNED_AGENT_KEYS includes 'includeMcpJson' (refreshed from the template, not forced)
  - src/kiro_crew/apps/bridges.py:709 — if agent_data.get('includeMcpJson') is False: ... (absent is treated as not opted out)
  - measured (merge/scripts/C/spec/build.py --apps): includeMcpJson ABSENT on pptx-maker-composer/spec/style/vibe and auto-improvement-discovery/pr-author; False on the other 8 app agents
  - docs/architecture/mcp.md:138-139 — 'Kiro Crew forces false on every agent it manages (the primary agent and every app agent)'
- **Checked by:** read + ran-existing-script
- **Required outcome:** Every shipped app agent template states includeMcpJson: false (no blanket default for third-party apps).
- **Solution:**
  1. Add "includeMcpJson": false to the four pptx-maker templates and auto_improvement discovery.json / pr-author.json.
  2. Add a test over apps/builtins/*/agents/*.json asserting the key is present and false.
  3. Do not force it in bridges.py for third-party apps (they may rely on ambient servers).
  4. Correct docs/architecture/mcp.md:138-139 to say shipped app templates pin it and the bridge preserves the template's value.
- **Done when:** The new template test passes; build.py-style materialization in a throwaway home shows includeMcpJson == False on all 14 shipped app agents.
- **Changed from the source claim:** Holds on disk as well as in the templates (the bridge does not add the key). Added: mcp.md's 'every app agent' statement is wrong.
- **Sources:** FIX_PLAN:SPEC-6, REVIEW_FINDINGS:Part6/SPEC-6

### TOOL-23 [10, default, effort S] mcp_shared tools/call ignores keys outside 'arguments'; only non-conforming clients hit it — PARTLY
- **Original claim:** External harness: tools.call envelope ignores unknown argument keys (corrected below)
- **Verified claim:** The X10 probe exercised the reviewing session's own tool runtime (tools.call / pi.ls), not Kiro Crew, so it is no evidence about Kiro Crew. Kiro Crew does have a narrow counterpart at the MCP envelope: mcp_shared's tools/call handler takes params.get('arguments', {}) and replaces a missing or non-object 'arguments' with {}, ignoring sibling keys such as 'input' or 'args', so a tool whose fields are all optional (e.g. list_sessions, spawn_list, kiro_cli_logs) runs with its defaults instead of refusing the call; a tool with a required field fails loudly. Inside 'arguments', unknown keys are refused by validate_tool_args ('unknown field for tool ...'). Only a non-conforming MCP client can reach the envelope gap — kiro-cli and KAS send 'arguments'.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_shared.py:1870-1874 — tool_args = params.get('arguments', {}); if not isinstance(tool_args, dict): tool_args = {}
  - src/kiro_crew/validation.py:660-663 — validate_tool_args: for key in args: if key not in known_fields: raise ValidationError(key, 'unknown field ...')
  - measured (merge/scripts/C/tool22_23_args.py): {'arguments': {...}} and {'input': {...}} as tool args -> REJECTED 'unknown field for tool get_chat_session'
  - src/kiro_crew/mcp_gateway/backend.py:1676-1678 — broker reads _params.get('arguments') only when it is a dict (same envelope convention)
- **Checked by:** read + ran-new-script
- **Required outcome:** A tools/call whose params carry no 'arguments' object (absent, non-object, or args under another key) is refused with JSON-RPC -32602 instead of running the tool with defaults.
- **Solution:**
  1. In mcp_shared.py:1870-1874, distinguish 'arguments' absent-and-no-other-keys (allowed: MCP permits omitting arguments for a no-arg tool) from 'arguments' non-dict or params carrying unknown keys besides name/arguments/_meta: answer the latter with JSONRPC_INVALID_PARAMS (validation.py) naming the key.
  2. Keep the inner validate_tool_args strictness as is.
- **Done when:** A stdio-loop test (the existing mcp_shared loop harness with in-memory pipes) sends tools/call {'name': 'list_sessions', 'input': {'limit': 1}} and gets error code -32602; {'name': 'list_sessions'} with no arguments still runs; {'name': 'list_sessions', 'arguments': 'x'} gets -32602.
- **Changed from the source claim:** Re-scoped: the claim describes an external harness and is out of scope as written (as FIX_PLAN §0.5 / REVIEW_FINDINGS §6.1 say). A real search found a weaker Kiro Crew analogue at the MCP envelope, reachable only from a non-conforming client; severity 35 -> 10, scope unknown -> default.
- **Sources:** verify_needed:X10
