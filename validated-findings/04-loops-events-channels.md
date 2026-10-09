# Kiro Crew validated findings: Loops, cron, injected events, channels and app timers

Part of the validated findings set; start at [00-index.md](00-index.md). Code verified at commit `397f4be`; sources snapshot 2026-10-07 09:53:08 UTC.

## 6. Loops, cron, injected events, channels and app timers

### LOOP-3 [65, armed, effort M] Issue Radar crews take a full turn every 5 minutes with no end — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/apps/builtins/issue_radar/backend/crew_runtime.py:113 DEFAULT_IDLE_SECS=1800 (commit a10c1bb7e); test/test_issue_radar_crew_runtime.py asserts the constant, not the 24 h fake-clock drive named in Done-when
- **Verified claim:** Every live Issue Radar crew arms an autonudge loop with idle_secs=300 and max_cycles=0 and passes no gate, judge, watch or max_runtime_secs (all four default off/0 in autonudge add()), so the loop delivers one full crew turn every 300 s of its persistent deadline (<=288 turns/day per crew) until the crew is stopped, retired or the app disabled, while the zero-LLM 60 s sweep (watch.py) already wakes the crew with one turn per sweep when an unblock signal moves. The 10-20M tokens/day figure in the notes is modelled (288 turns x an assumed 35-70k-token crew context), not measured.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/issue_radar/backend/crew_runtime.py:108 — DEFAULT_IDLE_SECS = 300 (comment :104-107: 'the fallback clock that lets an idle crew pick up NEW work')
  - src/kiro_crew/apps/builtins/issue_radar/backend/crew_runtime.py:769 — '``max_cycles=0`` — a crew is not a bounded errand'
  - src/kiro_crew/apps/builtins/issue_radar/backend/crew_runtime.py:781-788 — svc.add(slot_key=..., idle_secs=DEFAULT_IDLE_SECS, max_cycles=0, stop_sentinel_path=..., admission_check=...) — no gate/judge/watch/max_runtime_secs
  - src/kiro_crew/autonudge_service/mutations.py:91,100-102 — add() defaults max_runtime_secs=0, gate=False, judge=None, watch=""
  - src/kiro_crew/autonudge_service/firing.py:130-161 — the only bounds checked before a fire are max_cycles (0 = skip) and runtime_budget (0 = none); quiet-streak logic exists only in gate.py/judge_tick.py, which an ungated, judge-less loop never reaches
  - src/kiro_crew/autonudge_service/timers.py:315-320 — notify_turn_complete re-arms toward the loop's PERSISTENT deadline, so sweep-woken turns do not push the 300 s idle fire back
  - src/kiro_crew/apps/builtins/issue_radar/backend/watch.py:65 — POLL_INTERVAL_SEC = 60; watch.py:28-29 'zero-LLM/zero-token'
  - src/kiro_crew/apps/builtins/issue_radar/backend/crew_runtime.py:1846-1847,1961-1967 — sweep_repo 'One zero-LLM pass' then ONE wake_crew per crew carrying every moved signal
  - …and 1 more in the verification results.
- **Checked by:** read
- **Required outcome:** An idle crew makes no model calls: turns happen when the sweep sees a signal move (wake_crew), plus a liveness turn every 30 min or less often. 24 h with no change makes <= 48 turns; a CI change makes one turn within 60 s.
- **Solution:**
  1. Raise the fallback clock: DEFAULT_IDLE_SECS (crew_runtime.py:108) to >= 1800 — this is a cadence default change, so it is a take-away change: grep docs/decisions/ (no entry today), list readers (watchdog re-arm in watchdog_cycle crew_runtime.py:1293+, crew_routes, tests under apps/builtins/issue_radar/tests) and confirm the number with the user.
  2. Or make the idle fire conditional: arm the loop with gate=True plus a probe/pre-check over the crew's signals snapshot (read_signals/build_snapshot), or skip the fire when the snapshot hash (compose_nudge(build_snapshot(...)), crew_runtime.py:307) equals the last delivered one — wake_crew already covers moved signals.
  3. Add a quiet-streak floor and a max_runtime_secs (or rely on the watchdog to re-arm a stopped loop each cycle).
  4. Document the crew cadence in docs/system-specs/modules/issue-radar.md in the same commit.
- **Done when:** A fake-clock test in apps/builtins/issue_radar/tests drives the autonudge timer for 24 h with an unchanged signals snapshot and asserts <= 48 delivered crew turns; a second test writes a moved CI signal, runs one sweep_repo pass and asserts exactly one wake_crew turn before the next 60 s tick.
- **Upstream:** #10094 #13504 (state unverified)
- **Changed from the source claim:** none on mechanism; added that the loop fires on a persistent deadline (sweep-woken turns do not delay it), that the owning spec does not document the cadence, and that the tokens/day figure is modelled. Severity 70 -> 65 because the cost figure is modelled.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:LOOP-3, REVIEW_FINDINGS:N1

### EVT-1 [60, default, effort M] Queued automation events each drain as their own main-chat turn — CONFIRMED
- **Verified claim:** Every queued system injection (subagent completion, synthetic recovery, cron notification, MCP-app message, false-tool-blocker replay) drains as its own main-chat turn: the merge loop breaks at the first system-injection entry and pops it alone, and merge_queued_messages defaults False and only ever merges consecutive user entries. On an idle slot each completion launches its own _run_chat; channel (Slack/Discord/Telegram) and cron parents inject one model turn per completion. So N separately delivered completions on a dashboard parent cost N completion turns plus one synthesis turn (N>=2). Turn counts re-measured with the real _ChatSlot/drain helpers (dispatch order modelled from _subagent_done): 1->1, 2->3, 3->4, 5->6, 10->11; a mixed queue of 3 completions + 2 crons + 2 user msgs = 7 turns (6 with merge on). The token figures in the notes (5 completions ~1.8M vs ~0.3M at 300k context) are modelled on an assumed 300k context, not measured. Doc half also holds: subagent.md:2158 says a Slack parent gets the raw result with no model turn and :2160 says a cron parent gets a notification only; the code injects a model turn for both.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/slack/gateway.py:9686 — '# (a system injection never merges), so it is' / :9688 _synthesis_completion_turns += 1 then queue_append(kind=SUBAGENT_COMPLETION_KIND)
  - src/kiro_crew/slack/gateway.py:9790-9806 — idle slot: _synthesis_completion_turns += 1; asyncio.create_task(bounded_chat_turn(_run_chat(... announce ...))) — one turn per completion
  - src/kiro_crew/dashboard/chat_utils.py:3847-3849 — _SYSTEM_INJECTION_KINDS = SUBAGENT_DELIVERY_KINDS | {CRON_NOTIFICATION_KIND, MCP_APP_MESSAGE_KIND, FALSE_TOOL_BLOCKER_REPLAY_KIND}
  - src/kiro_crew/dashboard/chat_utils.py:3990-4018 — _dequeue_next_message: merge loop 'break's at is_system_injection_item(item) (:4003); otherwise item = slot.queue_pop(0) (:4017) — a system entry always drains alone
  - src/kiro_crew/dashboard/chat_utils.py:4021-4035 — _dequeue_next_system_message pops exactly one system injection per call (hold-users path)
  - src/kiro_crew/config/sections.py:2648-2649 — merge_queued_messages: bool = field(default=False, ...)
  - src/kiro_crew/dashboard/chat_runner.py:6665 / :6685 / :6694 — _start_next_queued_turn reads merge flag, then calls _dequeue_next_system_message or _dequeue_next_message
  - src/kiro_crew/slack/gateway.py:9861 + :9936 — non-cron/non-subagent parent (Slack thread, Discord, Telegram…): build_message + _inject_with_retry per completion = one model turn each
  - …and 7 more in the verification results.
- **Checked by:** ran-existing-script SCRATCHPAD/evt/queue_drain.py
- **Required outcome:** Consecutive queued system entries of the same kind drain as one turn, up to the digest's 60k-char budget; siblings that finish seconds apart share a turn on dashboard, channel and cron parents; delivery debt is still settled per entry; subagent.md describes what the code does.
- **Solution:**
  1. Merge same-kind system entries in _dequeue_next_message/_dequeue_next_system_message (dashboard/chat_utils.py:3990-4035) when called from _start_next_queued_turn (chat_runner.py:6685/6694); stop at a kind change, an attachment-bearing entry or 60_000 chars. Keep delivery debt per entry: on a retry before the model consumes the turn re-queue the ORIGINAL entries, not the merged string (_defer_queued_delivery, slack/gateway.py:8436); _settleable (chat_runner.py:7040) already takes several entries.
  2. Carry SUBAGENT_COMPLETION_META_KEY as a list for a merged drain, and count a merged drain as ONE turn for the synthesis gate (_synthesis_completion_turns, gateway.py:9688/:9790; chat_runner.py:7245).
  3. On an idle slot whose parent still has running children, wait a 5-10 s settle window (injected clock) before launching _run_chat (gateway.py:9790-9806) so near-simultaneous siblings land in one drain.
  4. Channel and cron parents (gateway.py:9861-10257): keep a pending list per parent while an injection is in flight and inject the pending announces as one message when it completes.
  5. Same commit: fix subagent.md:2158 and :2160 (Slack and cron parents DO get a model turn) and rewrite :2218 to the merged behaviour. Cadence/turn-shape change is a take-away for anything reading one-turn-per-completion (crew log rows, completion cards, SUBAGENT_COMPLETION_META_KEY readers): list them as Reader: lines.
- **Done when:** In test/test_merge_queued_messages.py, 5 SUBAGENT_COMPLETION_KIND entries queued on a _ChatSlot drain as ONE _run_chat whose text carries all 5 announces, and all 5 delivery debts settle on consumption (and re-queue as 5 entries on a pre-consumption failure). With an injected fake clock, 3 siblings finishing 2 s apart on an idle slot produce exactly one parent turn and no separate synthesis turn; a cron-parent variant with a stubbed _inject_with_retry records one call.
- **Changed from the source claim:** none on the mechanism; numbers re-measured identical. Added: the cron-parent doc row (subagent.md:2160) is also wrong, and FALSE_TOOL_BLOCKER_REPLAY_KIND is a fifth non-merging kind. Severity 65 -> 60 because the prompt's default fan-out is one spawn_run(tasks=[…]) wave whose members are digested in chunks (EVT-3), so the strict N+1 shape needs separate spawns or mixed bursts; token figures are modelled.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:EVT-1, REVIEW_FINDINGS:Part6/EVT-1

### EVT-2 [60, default, effort M] Subagent completion carries the opening narration, not the answer — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/slack/gateway.py:571 DIGEST_ANSWER_CHARS=1500 digest closing segment; subagent_manager/run.py closing-segment tracking (commit 22472c4d9, 22472c4d9 also touches subagent.py)
- **Verified claim:** A subagent's result is every streamed text chunk of the run (narration between tool calls included), cut by completion_keep (default 'head') to completion_keep_chars (default 3,000). When that cut drops content the parent's [Subagent completion event] carries summarize_result's first-100 + last-100-word preview OF THE ALREADY HEAD-CUT TEXT plus the result.txt path, so the end of a long transcript never reaches the envelope; wave-digest success lines carry only a pointer line. Re-measured at HEAD with the real helpers: a 10,153- or 26,913-char narrated transcript whose 30-item answer is at the end gives a 1,649-char envelope with 0/30 items ('tail' mode would give 11/30); a 3-success digest is 913 chars and a 10-success digest 2,266 chars with no result text. spawn_sub_agents, by contrast, builds its preview from the full on-disk result, so its tail does contain the answer.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/subagent_manager/run.py:3013 — 'result_text += event.text' (every text event of every attempt; only reset at :2468)
  - src/kiro_crew/subagent_manager/run.py:3220 — cleaned = extract_options(result_text) … then :3258 info.result = cleaned
  - src/kiro_crew/subagent_manager/run.py:3262-3270 — info.result_truncated = keep_chars > 0 and len > keep_chars; info.result = apply_completion_keep(info.result, _completion_keep, _completion_keep_chars)
  - src/kiro_crew/config/sections.py:1757-1758 — completion_keep default='head'; :1769-1770 completion_keep_chars default=3000
  - src/kiro_crew/context_management.py:32 — RESULT_SUMMARY_WORDS = 200; :161-195 summarize_result = first 100 + last 100 words of the text it is given + 'Full transcript: <path>'
  - src/kiro_crew/slack/gateway.py:9158-9159 — 'elif result_path and info.result_truncated: detail = summarize_result(info.result, result_path)' (info.result is the head-cut text)
  - src/kiro_crew/slack/gateway.py:9297-9300 — wave ok_lines: '— `id` ✅ {task_text[:80]} · {usage}' + '→ {result_path}' — no result text
  - src/kiro_crew/mcp_tools/spawn.py:1544-1550 — spawn_sub_agents: if len(result_text) > COMPLETION_KEEP_DEFAULT_CHARS: summarize_result(result_text, …) where result_text comes from /api/spawn/{id}, which reads the FULL result.txt (dashboard/messaging_api/run_views.py:201 'Read full result from disk (i…
  - …and 1 more in the verification results.
- **Checked by:** ran-existing-script SCRATCHPAD/evt/envelopes.py
- **Required outcome:** The deliverable arrives inline and bounded in the completion envelope and in each digest line; result.txt is read only for detail.
- **Solution:**
  1. (S) In _subagent_done (slack/gateway.py:9158-9159) build the truncated-result preview from the full text or its tail — read result.txt or keep an untruncated copy on SubagentInfo — as spawn_sub_agents already does (mcp_tools/spawn.py:1544-1550).
  2. (M) In subagent_manager/run.py track the final assistant segment (text after the last tool call; result_text accumulates at :3013) and inline it anchored at the end, up to completion_keep_chars, before the head preview.
  3. Give each wave-digest success line (gateway.py:9297-9300) its member's final segment, ~1,500 chars, within the 60_000 digest budget (:9427).
  4. Changing the completion_keep 'head' default (config/sections.py:1757) is a take-away change: grep docs/decisions/ (no entry at HEAD), list readers (spawn_status, result.txt consumers, completion cards) as Reader: lines, confirm with the user. Update subagent.md and injected-messages.md (Sub-agent completion) in the same commit.
- **Done when:** Extend test/test_subagent_completion_meta.py: a 30,000-char narrated transcript ending in a 2,000-char answer produces a completion envelope that contains the whole answer (assert every one of 30 marker lines present) and stays <= completion_keep_chars + a fixed header; a 3-member wave's final digest contains each member's answer marker.
- **Changed from the source claim:** none; all numbers re-measured identical at HEAD
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:EVT-2, REVIEW_FINDINGS:Part6/EVT-2

### LOOP-2 [60, armed, effort M] Pipeline-conductor patrol: ungated turn after each 90 s idle; work-ledger watch won't fix — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/builtin_skills/goal-conductor/references/patrol.md:11 watch="work-ledger" mandatory; patrol_budget.py present (commit d3de09a60)
- **Original claim:** Pipeline-conductor patrol is an ungated 90 s model turn (corrected below)
- **Verified claim:** pipeline-conductor/SKILL.md:269-271 arms the patrol with monitor_start at ~90 s, max_cycles=960, max_runtime_secs=259200 and no watch and no judge brief. With no monitor bound, _monitor_tick_is_quiet asks the judge first; a loop with no criteria of its own gets the default brief only when the nudge_evidence scope is granted (absent reads False), so the judge returns None, the gate returns not-quiet and every tick delivers a full conductor turn. The dashboard re-arms idle_secs after each nudge turn ENDS, so the run is up to 960 delivered turns over 960 x (90 s + mean turn time) (about 1-3 days), each turn mandated to do one session_ledger_read plus one fleet_probe.py call (>= 3 full-context requests; modelled, not measured). Mis-scoped part: prompt.md:120 requires watch='work-ledger' only for 'a conductor patrolling workers it dispatched on work-ledger items'; kirocrew-pipeline-conductor does not mount @kirocrew-work and keeps its items in session_ledger, so a work-ledger watch would observe an empty board and is NOT the fix. SKILL.md is 105,481 bytes (104,948 chars) > SKILL_READ_CAPACITY 99,000 (confirmed). The per-cycle session_ledger_read has no char cap but is structurally bounded (32 artifacts, 20-event tail), so 'uncapped' is overstated.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/builtin_skills/pipeline-conductor/SKILL.md:269-271 — 'Arm the patrol with `monitor_start` using an interval near 90 seconds, an explicit `max_cycles=960`, and an explicit `max_runtime_secs=259200`' (no watch, no judge)
  - src/kiro_crew/autonudge_service/gate.py:301-306 — probe_will_run = monitor is not None ...; judged = await self._judge_tick_is_quiet(loop); if monitor is None ...: return False (fire)
  - src/kiro_crew/autonudge_service/judge_tick.py:139-142 — default brief only if core_decisions.judge_evidence_scope_granted(...) else return None
  - src/kiro_crew/decisions/consent.py:296 — consented_nudge_evidence: 'Absent reads False'
  - src/kiro_crew/autonudge_service/timers.py:303-322 — notify_turn_complete: 'the first turn-complete after a delivered fire ... starts the next full cycle' (period = turn + idle_secs)
  - src/kiro_crew/autonudge_service/firing.py:293-300 — cycle_count advances only on a DELIVERED fire
  - src/kiro_crew/config/prompt.md:120 — 'A conductor patrolling workers it dispatched on work-ledger items passes watch="work-ledger"' (scoped to work-ledger conductors)
  - src/kiro_crew/agent.py:4985-4999 — _PIPELINE_CONDUCTOR_CORE_GRANTS: session_ledger_read/record, no @kirocrew-work verbs
  - …and 5 more in the verification results.
- **Checked by:** read
- **Required outcome:** The pipeline conductor takes a model turn only when its fleet probe reports something actionable (fired>0, NOPROGRESS or BANNED) or when a worker's turn ends; a liveness fallback runs every 15-30 min or less often; quiet cycles cost no turn.
- **Solution:**
  1. Do NOT arm this conductor with watch='work-ledger': it has no work ledger (agent.py _PIPELINE_CONDUCTOR_CORE_GRANTS ~4985; pipeline-conductor.md:368-375). Instead add a fleet probe kind per monitor-architecture.md 'Adding a new monitored kind': an irq.Probe subclass in probes/ that runs the classification scripts/fleet_probe.py already computes (OK line 'fired' count, BANNED, NOPROGRESS), a branch in probes/__init__.py build, and the watch value in validation.py _MONITOR_WATCH_FIELD (~1556) and mcp_tools/control.py _WATCH_* enum (~99, ~704, ~857) -- or, if mounting the work ledger on this conductor is preferred, that is a maintainer decision reversing the documented retraction.
  2. Update SKILL.md:269-271 and agent.py _PIPELINE_CONDUCTOR_SYSTEM_PROMPT (~4933) to pass the watch, an interval in 300..900 s and bounds that pass goal-conductor/scripts/patrol_budget.py check (see LOOP-20).
  3. Make the per-cycle session_ledger_read conditional on a fired signal.
  4. Split SKILL.md into a core under 30 KB plus references/ (SKL-4).
  5. Take-away/cadence change: grep docs/decisions/ (nothing at HEAD), confirm the numbers with the user, and update pipeline-conductor.md 'The patrol cycle' and dashboard/handlers/agent_panel BOARD_STALE_AFTER_SECONDS (sized as ~10 intervals) in the same commit.
- **Done when:** (a) test_pipeline_conductor_skill_contract.py parses the SKILL.md patrol example and asserts it names a watch (or judge) and its bounds pass patrol_budget.py check (exit 0); (b) a real-AutoNudgeService test with the new probe stubbed to report nothing fired, a spied _arm_timer and hand-driven _timer ticks over 1 h of fake time at interval 300 s delivers <= 2 turns (liveness floor only); (c) len(SKILL.md.encode()) < 99,000.
- **Upstream:** #17051 #17603 #14610 PR #17069 PR #17079 (state unverified)
- **Changed from the source claim:** Mechanism confirmed. Corrected: 'contradicts prompt.md:120' is mis-scoped (that rule is for work-ledger conductors) and FIX_PLAN step 1 (watch='work-ledger') does not fit this conductor -- a fleet probe kind or a judge is needed; ledger read is structurally bounded (no char cap), not uncapped; turn period is 90 s + turn time. Upstream #17051/#17603/#14610 are distinct claims (LOOP-19/LOOP-20).
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:LOOP-2, REVIEW_FINDINGS:U2, REVIEW_FINDINGS:U10

### LOOP-1 [55, armed, effort M] HEARTBEAT.md tasks poll every 60 s forever with the full persona and all core schemas — CONFIRMED
- **Verified claim:** A HEARTBEAT.md task whose reply contains HEARTBEAT_KEEP (or whose task raises) is re-run every 60 s forever: the interval is the constructor default (heartbeat.py:41/:149) and the gateway never passes one, HeartbeatConfig has no interval field (only default_deliver), and nothing counts keeps, backs off or retires a task. Every cycle ends with an unconditional recycle_heartbeat, so the next cycle's first task is a fresh session. That task's message is built with no agent= (main prompt.md persona, 38,965 B block) although the session's spec already carries the 3,014-char heartbeat charter natively, so the session gets both. The kirocrew-heartbeat spec mounts `@kirocrew-core` whole (83 tools, 107,418 B of schemas measured from tools/list) with no tool_search, while only 8 core MCP tools are approvable. Re-measured at HEAD on the 120-task seeded home: first task message 88,603 B, each further task +474 B, 144,686 B injected per cycle. Correction to the mechanism: `_strip_flags` exists but is a no-op on public builds (the main spec's kirocrew-core args are ['mcp-core'] and mcp-core accepts no --include-tools flag); the schema surface is whole because the spec mounts the whole server, not because a filter is removed. Cost per day is modelled, not measured: ~1,440 cold cycles/day x (~88.6 KB message + ~107 KB schemas) is roughly 70M raw input tokens/day per armed HEARTBEAT.md before any cross-session caching (unknown 0.4(b)). Opt-in: an empty HEARTBEAT.md makes no model call.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/heartbeat.py:41 — `_DEFAULT_INTERVAL = 60`; :149 `interval: int = _DEFAULT_INTERVAL`; :190-201 `_loop` waits `timeout=self._interval` every tick, no backoff
  - src/kiro_crew/heartbeat.py:365-380 — exception -> `keep.append(...)`; `_should_keep(result)` -> `keep.append(...)`; no keep/fail counter anywhere in the module
  - src/kiro_crew/slack/gateway.py:6408-6413 — `HeartbeatService(memory=..., on_task=..., consolidator=..., on_cycle_end=...)` (no interval passed)
  - src/kiro_crew/config/service_sections.py:189-201 — `class HeartbeatConfig` has only `default_deliver`
  - docs/system-specs/modules/heartbeat.md:5 — 'runs ... on a configurable interval (default 60s)' (no config knob exists: doc drift)
  - src/kiro_crew/slack/gateway.py:6389-6406 — `_on_cycle_end` -> `recycle_heartbeat()` 'is unconditional'; src/kiro_crew/session_background.py:1040-1066 pops the `_hb` session and shuts the provider down
  - src/kiro_crew/slack/gateway.py:6225-6228 — `get_or_create(session_key, agent="kirocrew-heartbeat")`; :6246-6253 `build_message(injected, is_new, memory_store=..., needs_reinjection=..., skill_bodies_session=session_key)` — no agent=
  - src/kiro_crew/context.py:3219-3235 — on is_new_session the `[AGENT SYSTEM PROMPT]` block is appended unless slim_resume, whatever minimal_context says; :2987-2990 agent=None reads `_prompt_path()` (main persona)
  - …and 8 more in the verification results.
- **Checked by:** ran-existing-script tm/heartbeat_msg.py
- **Required outcome:** A heartbeat task whose condition is still false costs close to nothing: it backs off from 60 s to a 1 h cap, retires after N keeps or D days with one notice to the user, the interval is configurable, and the first task message is <= 15 KB with no main-persona [AGENT SYSTEM PROMPT] block and only the heartbeat-safe core tools mounted. (Cadence/retire defaults are a take-away change: grep docs/decisions/ — nothing recorded today — and have the user confirm the numbers.)
- **Solution:**
  1. heartbeat.py:_process_heartbeat_file (:342-410) / _rewrite_heartbeat_locked (:92): persist per-task keep_streak, fail_streak and next_due (sidecar JSON keyed by task text hash, or an HTML comment on the task line like the existing <!-- deliver: --> tag); skip tasks not yet due; double next_due on each keep or failure up to 3600 s; reset on a non-keep reply.
  2. Retire a task after N consecutive keeps or D days (defaults to confirm with the user), deliver one notice through gateway._deliver_result, and drop it in the same locked rewrite.
  3. Add heartbeat.interval_secs to config/service_sections.py:HeartbeatConfig and pass it at slack/gateway.py:6408; fix docs/system-specs/modules/heartbeat.md:5 (it already says 'configurable').
  4. Spec: in agent.py:_install_heartbeat_agent (:5300-5386) replace `"tools": ["@kirocrew-core"]` with per-tool refs `@kirocrew-core/<name>` for the MCP members of the platform `heartbeat_safe_tools()` seam (platform/defaults.py:221 via tool_policy.py:243), plus the kiro-cli built-ins the safe list names; do NOT rely on an --include-tools arg (mcp-core has no such flag, and the main entry carries none). Alternatively grant tool_search.
  5. Message: land SPEC-2 (send a custom agent's prompt once) first, then pass agent="kirocrew-heartbeat" at slack/gateway.py:6246; check build_message's is_custom branches (context.py:3107, :3570) are acceptable for heartbeat. minimal_context=True alone does NOT drop the persona (context.py:3219-3235).
  6. Point the charter (agent.py:5247-5297) and monitor_start's refusal text (mcp_tools/control.py:1539) at script/irq crons for 'wait until X'.
  7. Update docs/system-specs/modules/heartbeat.md (interval, backoff, retire, tool surface) in the same commit.
- **Done when:** With HeartbeatService driven by a fake clock and a fake on_task that always returns 'HEARTBEAT_KEEP': <= 12 on_task calls in 24 simulated hours, and the task is removed from HEARTBEAT.md after N keeps with exactly one delivery; a reply without the keep token resets the streak. A spec test asserts kirocrew-heartbeat's tools are exactly the core members of heartbeat_safe_tools() (or include tool_search). A message test builds the heartbeat's first task message on an empty home and asserts len <= 15 KB and no '[AGENT SYSTEM PROMPT]' carrying prompt.md text (and, after SPEC-2, no duplicated heartbeat charter).
- **Upstream:** #15822 / PR #17172 (SPEC-2 prerequisite) (state unverified)
- **Changed from the source claim:** Numbers reproduce (88,603 B first message, 474 B/task, 38,965 B persona block). Corrections: (a) the 107 KB schema surface comes from mounting @kirocrew-core whole with no tool_search; `_strip_flags` is a no-op on public builds, so O1's 'set --include-tools on kirocrew-core' fix cannot work (mcp-core has no such flag) — use per-tool @kirocrew-core/<tool> refs; (b) X14's '~5.9 KB heartbeat prompt' is wrong: _HEARTBEAT_SYSTEM_PROMPT is 3,014 chars, matching SPEC-2's figure; (c) the heartbeat session currently receives both its 3 KB charter (spec) and the 39 KB main persona (message); (d) heartbeat.md:5 documents a 'configurable interval' that does not exist; (e) cost/day is modelled (~70M raw tokens/day per armed file), and scales per HEARTBEAT.md, not per task.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:LOOP-1, REVIEW_FINDINGS:H1, REVIEW_FINDINGS:M6, FIX_PLAN-old:H-1, verify_needed:O1, verify_needed:O2, verify_needed:X14

### LOOP-6 [55, armed, effort M] LLM cron runs start cold with ~39K-char persona even on minimal context; duplicates paid — PARTLY
- **Original claim:** LLM cron runs start cold with full context and pay even for duplicate results (corrected below)
- **Verified claim:** LLM cron jobs default minimal_context=False on every creation path (MCP cron_add, dashboard, CLI) and persistent_session=True; the gateway resets the provider session in the finally of EVERY run (persistent_session only keeps the stable cron:<id> key and prepends last_result), and cron: is a stateless prefix, so every run is a cold session. Duplicate results are detected only after the model ran and was paid (consecutive_dupes counts, nothing slows the cadence). On the default (persistent, non-minimal) job the whole last_result is prepended uncapped; the 2000-char cap applies only when minimal_context is on. Even a minimal-context first message is ~39.2K chars (measured 39,182 with a short message; 38,965 of it the [AGENT SYSTEM PROMPT] main persona), against 48.9K full-context on an empty home and 88.0K on the seeded home, so the '~200 tokens/wake' promised by cron_add's help and prompt.md:24 is wrong by ~50x. REFUTED sub-claim: acked_items does not grow forever — ack_job keeps only the newest 20 entries of <= 500 chars (<= 10K chars). Per-day token figures (~1.3-4M/day for a 15-min job) are modelled on assumed context size, not measured.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/cron_service/model.py:172-173 — `persistent_session: bool = True`; `minimal_context: bool = False # True -> skip memory/lessons/skills/history`
  - src/kiro_crew/mcp_cron.py:3482, :3522 — `minimal_context=minimal_context if isinstance(minimal_context, bool) else False`; dashboard/handlers/cron.py:872 `body.get("minimal_context", False)`; cli_commands.py:1936 default False
  - src/kiro_crew/mcp_cron.py:2063-2068 — help: 'only date/time and agent identity are included (~200 tokens vs ~30-55k)'; src/kiro_crew/config/prompt.md:24 — '`minimal_context=true` (~200 tokens/wake, not 30-55k)'
  - src/kiro_crew/slack/gateway.py:6074-6102 — finally: `await self.sessions.reset(session_key)` unless subagents pending (deferred reset), for every job
  - src/kiro_crew/session.py:545-548 — `_STATELESS_PREFIXES = ("cron:", ...)` (no resume across restarts)
  - src/kiro_crew/cron_service/identity.py:42-75 — persistent job: key `cron:{job.id}`, prepends `job.last_result`; truncates to 2000 only `if job.minimal_context and len(last) > 2000`
  - src/kiro_crew/slack/gateway.py:5142-5156 — `build_message(msg, is_new, session_key, interactive=False, agent=cron_agent or None, ..., minimal_context=job.minimal_context)`
  - src/kiro_crew/slack/gateway.py:5163-5202 then :5267-5287 — model turn and set_run_result first, then `rh = _result_hash(...)`; `if rh == job.last_posted_hash: job.consecutive_dupes += 1` (suppresses delivery only)
  - …and 4 more in the verification results.
- **Checked by:** ran-new-script merge/scripts/D/A/cron_msg.py
- **Required outcome:** New unattended LLM cron jobs run with slim context whose first message is a few KB, not ~39K chars; a job that keeps producing the same result slows down instead of paying full price every fire; prompts carry bounded history; cron_add warns about LLM jobs scheduled more often than every 15 min; the help text and prompt.md stop promising '~200 tokens/wake' until it is true. Default changes are take-away changes (stored jobs keep their values; grep docs/decisions/ — nothing recorded — and confirm numbers with the user).
- **Solution:**
  1. Default minimal_context=True for NEW model-calling jobs at all three creation paths (mcp_cron.py:3522, dashboard/handlers/cron.py:872, cli_commands.py:1936); stored jobs keep their value (cron_service/store.py loads it).
  2. minimal_context alone does not drop the 39 KB persona (context.py:3219-3235): give crons a slim cron agent spec, landing SPEC-2 first so a custom agent's prompt is not sent twice; that is the only route to a few-KB first message. CTX-8 (prompt sections by surface) helps but cannot reach it alone: gating Orchestration, Wait/Webhook, Browser and Computer Use removes ~24 KB of prompt.md's 38,989 B and leaves ~14-15 KB (Output Format, Capabilities, Apps, Injected context, Rules).
  3. Backoff on duplicates: at slack/gateway.py:5271 when consecutive_dupes >= 5, multiply the job's effective next-due (in the scheduler, cron.py) by 2 up to 4x and notify once; reset on a changed hash.
  4. Cap last_result at 2000 chars for every job in cron_service/identity.py:63-67 (not only minimal_context). acked_items is already capped at 20 x 500 chars (cron.py:3093-3095): leave it.
  5. cron_add: warn when an LLM job's every_secs < 900.
  6. Correct mcp_cron.py:2067 and config/prompt.md:24 to the measured size, and update docs/system-specs/modules/learn-cron-dashboard.md in the same commit.
  7. Script-cron reports waking the main chat are EVT-4.
- **Done when:** A new MCP/dashboard/CLI LLM job has minimal_context=True and a stored job keeps False; with a stub provider, a new job's first message contains no memory, lessons or skills block and (after the slim cron agent spec, with SPEC-2 landed first) is <= 8 KB, and with CTX-8's section gating alone is <= ~16 KB; with a fake scheduler clock, five identical results in a row double the next due time and a changed result restores it; a persistent job with a 10 KB last_result yields a prompt carrying <= 2000 chars of it; a prompt test pins that prompt.md:24 no longer says '~200 tokens/wake' unless a size test proves it.
- **Upstream:** #15906 / PR #15524 (auto-pause; distinct claim LOOP-24) PR #17648 (deliver only a marked <deliverable> block) (state unverified)
- **Changed from the source claim:** acked_items is bounded (newest 20 x 500 chars), so 'ever-growing acked_items' is refuted and FIX_PLAN step 'cap acked_items at 50' is moot. 'Reset after every run' holds even for the default persistent_session=True (it only keeps the key + last_result). Minimal message re-measured at 39,182 chars (claim ~39.6K) — persona 38,965 chars; full context 48.9K empty / 88.0K seeded. Severity 60 -> 55 because flipping minimal_context alone saves only ~9.7K chars on an empty home while the persona stays.
- **Second reader:** top-20 check: corrected. Mechanism, evidence and severity hold, and the SPEC-2/CTX-8 dependency is named. But the done_when ('after CTX-8 OR the slim agent, first message <= 8 KB') and the 'few KB' outcome cannot be reached through CTX-8: gating its four sections removes ~24 KB of prompt.md's 38,989 B and leaves ~14-15 KB of persona (measured section sizes at 397f4be). Only the slim cron agent (SPEC-2 first) gets to a few KB.
- **Sources:** FIX_PLAN:LOOP-6, REVIEW_FINDINGS:H3, REVIEW_FINDINGS:H4, verify_needed:O3, verify_needed:X14, verify_needed:G37

### MSG-1 [55, armed, effort M] Slack runs each queued message as its own full turn — CONFIRMED
- **Verified claim:** Slack drains its busy-session queue one message per turn: every turn-end drain (_drain_slack_queue, the transport path's _on_transport_done, the native _on_done) dequeues exactly ONE entry and runs it through _dispatch_queued -> a full handle_message turn, so N messages sent during a turn cost N more turns and N replies. The queue is an unbounded per-session deque (plus an unbounded _pending_queue list), enqueued with force=True, and Slack has no inbound rate limit. Slack also has no steer path. By contrast Discord, Telegram, Teams and Webex collapse one sender's queued burst into one turn (_pump_queue, MAX_COLLAPSE=50) and, with messaging.queue_mode at its default 'steer', fold a mid-turn DM into the running turn with no extra turn at all.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/slack/events.py:1228-1253 — `_drain_slack_queue`: 'One message per call; each dispatched turn calls this again when it ends.' `_next = orch.sessions.dequeue(session_key)` then one `_dispatch_queued` task
  - src/kiro_crew/slack/events.py:3277-3297 — `_on_transport_done`: `_next = orch.sessions.dequeue(session_key)` -> one `_dispatch_queued(...)`, re-arming itself
  - src/kiro_crew/slack/events.py:~3339-3362 — native `_on_done`: same single dequeue + `_dispatch_queued`
  - src/kiro_crew/slack/events.py:1976-1983 — `_dispatch_queued`: 'remove ⏳ reaction and call handle_message' (one full turn per entry)
  - src/kiro_crew/slack/events.py:3122-3143 — busy key: `orch.sessions.enqueue(session_key, msg_ts, clean_text, force=True, ...)`; fallback `orch._pending_queue.setdefault(session_key, []).append(...)`
  - src/kiro_crew/session_allocation.py:1690-1706 — enqueue: `session.queue.append((msg_ts, text, kwargs))`, no length check; src/kiro_crew/session.py:1110 — `queue: deque[...] = field(default_factory=deque)` (no maxlen)
  - grep: no `queue_mode`, `.steer(` or `supports_steer` anywhere under src/kiro_crew/slack/; no rate limiter in slack/ or messaging/
  - src/kiro_crew/discord/transport_dispatch.py:1208-1262 — `_pump_queue` collapses same-sender entries up to `_MAX_COLLAPSE`; same in telegram/transport_dispatch.py:1592, teams:1205, webex:1378; src/kiro_crew/messaging/queue_receipt.py:108 — `MAX_COLLAPSE = 50`
  - …and 2 more in the verification results.
- **Checked by:** read
- **Required outcome:** Queued Slack messages from one sender in one thread become one turn (bounded by MAX_COLLAPSE, FIFO preserved, other senders' entries drained separately); when queue_mode allows it a mid-turn message steers the live turn; the queue is bounded.
- **Solution:**
  1. Read docs/system-specs/modules/messaging.md (Queued messages collapse, ~:1978) and slack-gateway.md (Message Queue, ~:1001) first; update slack-gateway.md's 'FIFO drain' bullet in the same commit.
  2. Replace the three single-dequeue drains (slack/events.py `_drain_slack_queue` ~:1228, `_on_transport_done` ~:3277, native `_on_done` ~:3339) with one Slack `_pump` that dequeues the burst, collapses entries whose `queued_owner` (already set by `_queue_tags`, events.py:169) and channel/thread match, up to `messaging.queue_receipt.MAX_COLLAPSE`, joins texts with blank lines, merges `image_temp_paths`, and re-enqueues the first non-fitting entry and everything behind it (port discord/transport_dispatch.py `_pump_queue` ~:1208).
  3. `_dispatch_queued` (events.py:1976) takes the list of consumed entries: remove every ⏳ reaction, honour `from_trusted_bot` if ANY entry has it (echo-loop guard), and unlink every entry's temp paths after the turn.
  4. Optional second commit: steer — when `messaging.queue_mode == 'steer'` and `provider.has_active_turn()` and `supports_steer`, steer instead of enqueueing (messaging.md 'A mid-turn steer requires a genuinely live turn').
  5. Bound `session.queue` / `_pending_queue` per key (e.g. 4×MAX_COLLAPSE) with a visible refusal reaction instead of silent drop.
  6. Behaviour change: today each queued Slack message gets its own reply. List readers (`_dispatch_queued`, `handle_message_transport`, `_handle_message_deleted`/`cancel_queued`, `!stop` `_settle_stop_hold`) under Backwards compatibility.
- **Done when:** New test in test_slack_transport_dispatch.py (or test_slack_events_queue.py): with a fake orch whose session is busy, enqueue 3 messages from user A and 1 from user B in one thread, then fire the turn-end callback: exactly 2 dispatches occur — one handle_message carrying A's 3 texts in arrival order, then one for B; all 4 ⏳ reactions removed; all temp paths unlinked; a 60-message burst from A drains as 50 + 10. No sleeps (drive the done-callbacks directly).
- **Changed from the source claim:** none on the mechanism. Added: Slack also lacks the steer path that Discord/Telegram use by default (queue_mode='steer'), and the transport path's _on_transport_done (events.py:3277) and native _on_done are two more single-dequeue drains besides _drain_slack_queue; Teams and Webex also collapse.
- **Second reader:** top-20 check: agreed.
- **Sources:** FIX_PLAN:MSG-1, REVIEW_FINDINGS:N5, FIX_PLAN-old:A-6

### EVT-4 [50, armed, effort M] Script-cron reports wake the main chat with no dedupe or cap — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/slack/gateway.py:3095 message capped at 5000 chars; script report dedupe/bell (commit a1890d144)
- **Verified claim:** A non-silent script cron's Done/Report result is delivered into the dashboard slot of the session that CREATED the job (job.session_key, typically the user's main chat — not the job's cron tab): on an idle slot it starts a full `_run_chat` turn at once, on a busy slot it is queued as CRON_NOTIFICATION_KIND and drains later as its own turn. The message is the `message` field of the last stdout JSON line, passed through redact() only — no size cap (a 228,890-char Report stays 228,890 chars after redact) — and the report branch has no hash/consecutive-dupe check, so identical reports each wake the model. The script wrapper is `[Cron notification: "<label>"] ... [/Cron notification]`, which does not start with CRON_NOTIFY_PREFIX (`[Cron notification from `), so a QUEUED script report is classified is_cron=False and its drained row is written with role 'user' (the idle-slot path writes a proper inject/cron row). The '288 turns/day, ~86M tokens/day' for a 5-minute reporter is arithmetic on an assumed 300k-token main chat (modelled).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/slack/gateway.py:3050-3101 — `_deliver_script_result`: `if message and not job.silent and self.dashboard_state and job.session_key:` -> slot of `job.session_key`; `wrapped = f'[Cron notification: "{label}"]\n{message}\n[/Cron notification]'`; running -> `slot.queue_append(wrapped, kin…
  - src/kiro_crew/cron_service/model.py:163 — `session_key: str = "" # session that created this job`
  - src/kiro_crew/slack/gateway.py:4259-4274 (done) and :4287-4301 (report) — `msg = result.get("message", "")`; `script_msg = redact(msg)`; `await _deliver_script_result(job, script_msg)` with no hash compare
  - src/kiro_crew/cron_script.py:2655-2669 — `parsed = json.loads(stdout.strip().split("\n")[-1])` returned whole (only error paths are bounded: _MAX_SCRIPT_STDERR_TAIL, _MAX_BAD_OUTPUT_HEAD)
  - src/kiro_crew/dashboard/state.py:1840 — `CRON_NOTIFY_PREFIX = "[Cron notification from "`; src/kiro_crew/dashboard/chat_runner.py:6777 `is_cron = ... next_msg.startswith(CRON_NOTIFY_PREFIX)`; :6858-6863 `else: row_role = "user"`
  - src/kiro_crew/dashboard/chat_utils.py:3847-3848 — CRON_NOTIFICATION_KIND is a `_SYSTEM_INJECTION_KINDS` member (never merges; see EVT-1)
  - ran evt/envelopes.py (copied home): 'script cron Report/Done: 117 chars, startswith(CRON_NOTIFY_PREFIX)=False'; 'script Report(msg) of 228,890 chars -> after security.redact 228,890 chars'
  - docs/system-specs/common/injected-messages.md:17-47 — documents only the send_message(session='origin') envelope; the script-cron wrapper is undocumented
  - …and 2 more in the verification results.
- **Checked by:** ran-existing-script evt/envelopes.py
- **Required outcome:** Script-cron reports that need no action do not wake the model, identical repeats are free, the injected text is bounded, and a drained script report is attributed as a cron event, not as the user. (Changing the default wake behaviour is a take-away change: grep docs/decisions/ — nothing recorded — and confirm with the user.)
- **Solution:**
  1. Add a per-job `wake` setting (on_done default, always, never) to CronJob (cron_service/model.py) and honour it in slack/gateway.py:_deliver_script_result (:3050): when the report does not wake, write the inject row + bell and park the text as next-turn context instead of calling _run_chat / queue_append.
  2. Dedupe in the report branch (:4287-4301) with the existing `_result_hash` / `last_posted_hash` / `consecutive_dupes` fields, as the LLM path does at :5267-5287.
  3. Cap `script_msg` before wrapping (choose a number, e.g. 5,000 chars with a 'truncated — full text in the job's history' tail; the only existing precedent is the 100 KB MAX_PROMPT_BYTES heartbeat cap at :8576-8600).
  4. Build the wrapper with CRON_NOTIFY_PREFIX/CRON_NOTIFY_END (dashboard/state.py:1840) so chat_runner.py:6777 classifies a queued report as cron, and coalesce per EVT-1.
  5. Correct config/prompt.md:24's cost wording; document the script-cron envelope in docs/system-specs/common/injected-messages.md and learn-cron-dashboard.md in the same commit.
- **Done when:** Driving _deliver_script_result with a stub dashboard state: 10 identical Report messages on an idle slot start exactly 1 _run_chat; a 228,890-char Report reaches the slot as <= the chosen cap; a report queued on a running slot drains with row_role 'inject' and injectKind 'cron'; with wake='never' no _run_chat is started and one inject row + one bell are written.
- **Upstream:** PR #17648 (deliver only a marked <deliverable> block) (state unverified)
- **Changed from the source claim:** Holds. Corrections: delivery targets the job's ORIGIN session slot (job.session_key), not the job's cron tab; FIX_PLAN's '5,000 precedent at gateway.py:8576-8600' is actually a 100 KB byte cap, and the 5,000 send_message cap was not located in api_send_message at HEAD; per-day token figure is modelled.
- **Second reader:** random-sample check: agreed.
- **Sources:** FIX_PLAN:EVT-4, REVIEW_FINDINGS:Part6/EVT-4

### LOOP-4 [50, armed, effort M] Meetings: three agents each take a turn every 30 s on auto — CONFIRMED
- **Verified claim:** A live meeting runs three agent queues by default (note-taker and sketch-artist enabled_by_default, task-extractor always), each flushing queued transcript to its own long-lived session 30 s after the first queued line and then every 30 s (+ turn time) while lines keep arriving, so continuous speech buys up to 3 x 120 = 360 agent turns per meeting-hour (an upper bound; the cycle is 30 s plus the turn's own duration). All three agent specs pin "model": "auto"; the note-taker and task-extractor prompts tell the agent to re-read its output file before each update and the sketch-artist takes a turn on every batch even when it decides not to redraw; because each batch is appended to the same session, every turn replays the whole meeting so far and total input grows with the square of meeting length. The 5-15M input tokens per meeting-hour in the notes is modelled, not measured. Per-turn fixed cost is small (4 fs tools, no MCP, ~1.1-1.4 KB prompts), and these turns go through stream_and_collect, which records no usage.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/meetings/backend/constants.py:76 — BATCH_INTERVAL_SECS = 30.0
  - src/kiro_crew/apps/builtins/meetings/backend/domain/session.py:309-311 — enqueue() appends and _schedule_flush(); :349-366 _delayed_flush sleeps batch_interval (+backoff) then flush(), repeating while more is queued
  - src/kiro_crew/apps/builtins/meetings/backend/domain/session.py:650-664 — one AgentQueue per enabled agent plus the always-on task extractor ('The task extractor always runs')
  - src/kiro_crew/apps/builtins/meetings/backend/store.py:239-259 — DEFAULT_MEETING_AGENTS note-taker and sketch-artist both enabled_by_default: True
  - src/kiro_crew/apps/builtins/meetings/agents/meetings-note-taker.json:4, meetings-sketch-artist.json:4, meetings-task-extractor.json:4 — "model": "auto"; specs have tools [fs_read, fs_write, grep, glob], mcpServers {}, includeMcpJson false
  - agents/meetings-note-taker.json prompt step 6 — 'Read the current notes file before each update'; task-extractor step 7 — 'Read the current JSON before each update'
  - src/kiro_crew/apps/builtins/meetings/backend/domain/session.py:200-270 dispatch_to_agent — get_or_create(key, agent=...) then stream_and_collect; 'The session is long-lived for the meeting's duration (each batch adds to the same conversation)'
  - src/kiro_crew/apps/builtins/meetings/backend/constants.py:22 — MAX_SESSION_DURATION = 4 * 3600
  - …and 1 more in the verification results.
- **Checked by:** read
- **Required outcome:** <= 100 agent turns per meeting-hour across the default agents, per-turn input that does not grow with the square of meeting length, and agent turns on the background role model with usage recorded.
- **Solution:**
  1. Per-agent interval instead of one constant: note-taker and task-extractor 90-120 s, sketch-artist 180 s or on demand (AgentQueue.batch_interval, session.py:290; set from the agent definition in _make_queue, session.py:676). Cadence change = take-away change: grep docs/decisions/ (no entry today), list readers (meetings.md:218/:306, MeetingsSessionLogic tests, flush_soon/flush_now callers) and confirm the numbers with the user.
  2. Stop the quadratic: recycle each agent session every N batches (destroy + re-kickoff with build_meeting_context plus the current OUTPUT_FILE content), so replay is bounded; then drop the 're-read before each update' instruction where the content is already in the prompt.
  3. Route model choice through the background role (agent.role_models.background / acp.client.resolve_usable_model) instead of the spec's "auto" — never hardcode a model id.
  4. Record usage for these turns (they go through stream_and_collect, which persists nothing; see USE-1).
  5. Update docs/system-specs/modules/meetings.md 'Agent dispatch' in the same commit.
- **Done when:** A fake-clock test feeds one transcript line every 2 s for 3600 s into a MeetingSession with the default agents and a fake dispatch_to_agent, and asserts <= 100 total dispatches and that no agent session receives more than N batches before it is recycled.
- **Changed from the source claim:** 360 turns/h is an upper bound under continuous speech (cycle = 30 s + turn time), not a fixed rate; per-turn fixed cost is small (slim 4-tool agents), so the cost driver is session replay growth; tokens/hour figure is modelled; turns also record no usage. Severity 62 -> 50.
- **Sources:** FIX_PLAN:LOOP-4, REVIEW_FINDINGS:N2

### EVT-3 [45, default, effort M] A spread-out subagent wave produces several parent turns — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): 15-minute straggler deadline (commit 266cbc14b); test/test_subagent_scale.py exists
- **Verified claim:** For a spawn_run(tasks=[…]) wave (the prompt's default fan-out), a finished member is held at most DIGEST_HOLD_SECS (120 s; env KIROCREW_SUBAGENT_DIGEST_HOLD_SECS only, no config field) and the reaper (every 60 s) force-flushes the held results as a partial [Subagent batch completion event] chunk; every chunk is its own parent turn, and >=2 completion turns also fire a synthesis turn. Re-measured with the real hold-deadline decision and a fake clock: members at 1/5/10 min -> 4 parent turns (3 chunks + synthesis); 5 members over 20 min -> 6; 2 fast + 1 straggler at 15 min -> 3; 3 members over 2.5 min -> 1 or 3 depending on reaper phase; 3 within 20 s -> 1. Context the claim omits: the 120 s deadline is a deliberate latency fix for issue #2215 (a hung straggler withheld every sibling result for up to the 3 h reap), pinned by test_subagent_scale.py::TestDigestHoldDeadline::test_expired_hold_forces_flush — so cutting turns trades back delivery latency.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/subagent.py:1487 — _DEFAULT_DIGEST_HOLD_SECS = 120.0; :1492 os.environ.get('KIROCREW_SUBAGENT_DIGEST_HOLD_SECS'); :1509 DIGEST_HOLD_SECS = _digest_hold_secs() (no config.json key)
  - src/kiro_crew/subagent.py:1467-1486 — comment: deadline caps worst-case delivery LATENCY; '0/negative disables the deadline (count-trigger-only, i.e. pre-fix behavior)'
  - src/kiro_crew/subagent.py:1141 — _REAPER_INTERVAL = 60
  - src/kiro_crew/subagent_manager/waves.py:297 — _expired_digest_holds(now) (the reaper's deadline decision)
  - src/kiro_crew/slack/gateway.py:9368 — _flush = _last or _flush_only or _pending >= SUBAGENT_DIGEST_CHUNK_SIZE (chunk size default 10, :562)
  - src/kiro_crew/slack/gateway.py:9510-9540 — non-final chunk announce 'Batch results k/j — N of M delivered, R still running … Process these results now' (a turn)
  - src/kiro_crew/dashboard/chat_runner.py:10750-10760 — _pending_subagent_failures and drain_pending_context(slot) prepend parked context to the next turn (the mechanism the fix would reuse)
  - test/test_subagent_scale.py:1937 TestDigestHoldDeadline, :1976 test_expired_hold_forces_flush — pins the forced partial flush ('THE BUG … forces the partial digest out instead of waiting for the straggler')
  - …and 2 more in the verification results.
- **Checked by:** ran-existing-script SCRATCHPAD/evt/digest_timing.py
- **Required outcome:** One parent turn per wave unless a member truly hangs; finished results stay visible to the user immediately (cards) and reach the model at wave close or at a long, configurable straggler deadline.
- **Solution:**
  1. On a hold-deadline flush (_flush_only, slack/gateway.py:9368/:9510-9540) show the results in the UI (the cards already do) and park the chunk as next-turn context via _pending_subagent_failures / drain_pending_context (dashboard/chat_runner.py:10750-10760) instead of launching a turn.
  2. Fire a model turn only at wave close, or at a long straggler deadline (e.g. 15 min) exposed as a config field (agent.subagent_digest_hold_secs) alongside the existing env var (subagent.py:1487-1509).
  3. This is a cadence and latency change that partly reverses the #2215 fix: grep docs/decisions/ (no entry at HEAD), rewrite test_expired_hold_forces_flush deliberately with a Reader: line, update subagent.md:2856-2858 in the same commit, and confirm the numbers (15 min, park-not-turn) with the user. See also upstream #15220 (EVT-10).
- **Done when:** Extend test/test_subagent_scale.py::TestDigestHoldDeadline with an injected clock: members finishing at 60, 300 and 600 s produce exactly ONE parent turn carrying all 3 results (and no synthesis turn); a member that never finishes releases the parked siblings to the model no later than the configured straggler deadline.
- **Upstream:** #15220 #2215 (state unverified)
- **Changed from the source claim:** Numbers re-measured identical. Added: the hold deadline is a deliberate fix for #2215 pinned by test_expired_hold_forces_flush, so the FIX_PLAN 'Done when' (extend test_subagent_scale.py:1981-2009) must REWRITE that pinned test, i.e. this is a take-away/cadence decision; severity 50 -> 45 for that trade-off.
- **Sources:** FIX_PLAN:EVT-3, REVIEW_FINDINGS:Part6/EVT-3

### LOOP-7 [45, armed, effort M] A blind monitor probe fires a full turn on every tick — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): blind-probe back-off after first three fallbacks, one warning (commit 1ac9efb78)
- **Verified claim:** For a gated auto-nudge loop (monitor_start's default), a probe that cannot observe its subject makes every tick fire a full turn with no backoff: a probe returning fetch_ok=False becomes Skip(blind=True) -> Outcome.FALLBACK, a probe that raises becomes FALLBACK('probe raised'), and the gate spends a turn on FALLBACK exactly like WAKE; gate_fallbacks is only a counter that nothing reads to change cadence. Measured on the real gate + irq kernel with a fake clock at idle_secs=60: 60 of 60 ticks fire in 1 h and 1,440 of 1,440 in 24 h. Two facts the claim missed: (a) the kernel already sends a 'watch is blind' alert (a Report, i.e. a WAKE turn) at the 6th consecutive fetch failure and re-alerts every 6 h (4 alerts/24 h measured) — but a probe that RAISES bypasses the kernel's error counter and gets no alert at all (0 alerts/24 h measured); (b) fallback fires are delivered turns and advance cycle_count, so the default monitor_start loop (24 cycles, 4 h, 300 s interval) is bounded to 24 wasted turns over ~2 h; the per-day cost only applies to loops armed with large caps (max_cycles up to 1000, runtime up to the 7-day ceiling).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/irq.py:686-741 — `if not tick.fetch_ok:` increments state['errors']; below the threshold raises `Skip(...); failed.blind = True`
  - src/kiro_crew/irq.py:710-732 — at `errors >= max_consecutive_errors` (DEFAULT_MAX_CONSECUTIVE_ERRORS = 6, irq.py:102) raises a blind-alert Report once per realert_secs (DEFAULT_REALERT_SECS = 6*3600, irq.py:98), otherwise Skip(blind)
  - src/kiro_crew/irq.py:1137-1142 — poll(): `if getattr(exc, 'blind', False): return Verdict(Outcome.FALLBACK, ...)`; :1153-1162 — any other probe exception -> FALLBACK('probe raised') without touching the error streak
  - src/kiro_crew/autonudge_service/gate.py:961-982 — '# WAKE, and FALLBACK, both spend a turn' ... `monitor.gate_fallbacks += 1`; `return False` (not quiet -> fire)
  - src/kiro_crew/monitoring/models.py:960 — `gate_fallbacks: int = 0`; only other readers are serialization (:1081) — nothing adjusts cadence
  - src/kiro_crew/autonudge_service/firing.py:130 — `if loop.max_cycles and loop.cycle_count >= loop.max_cycles` stops the loop; :291-297 — 'a floor delivery, a fallback and a follow-up are all delivered turns that advance it'
  - src/kiro_crew/mcp_tools/_limits.py:14-15 — monitor_start defaults 24 cycles / 14,400 s; mcp_tools/control.py:1544 — default interval 300 s
  - measured (merge/scripts/D/B2/loop7_blind_probe.py, fetch_ok=False): 60 ticks @60 s -> 60 fires, verdicts {fallback: 58, wake: 1}; 1,440 ticks -> 1,440 fires, {fallback: 1,432, wake: 4}
  - …and 1 more in the verification results.
- **Checked by:** ran-new-script merge/scripts/D/B2/loop7_blind_probe.py
- **Required outcome:** A watch whose probe cannot observe its subject costs about nothing after it has said so once: the first FALLBACK still fires; after 3 consecutive FALLBACK ticks the loop's cadence backs off (doubling, up to DEFAULT_REALERT_SECS) and exactly one 'watch is blind' alert is delivered — for a raising probe as well as a fetch_ok=False one; the first good reading resets the cadence and re-arms the alert.
- **Solution:**
  1. Read docs/system-specs/modules/monitor-architecture.md ('Runtime bounds', 'Decision', 'Rules the engine enforces') and agent-interrupt-controller.md first; update the 'Decision' section and the irq backstop description in the same commit.
  2. irq.py:1153-1162 — in poll(), count a raising probe into the same per-watch error streak the fetch_ok=False branch uses (load_state/save_state on the state_path), so the kernel's existing blind alert at :710-732 covers it too.
  3. autonudge_service/gate.py:961-982 — track a consecutive-FALLBACK streak on MonitorState (next to gate_fallbacks, models.py:960; persisted like quiet_streak); after 3 consecutive fallbacks return quiet (skip) for a doubling number of ticks, capped so the effective interval reaches at most DEFAULT_REALERT_SECS; reset on any WAKE/QUIET/TERMINAL verdict.
  4. Let the kernel's blind Report (irq.py:724) be the one alert: do not add a second notifier; ensure it still fires during back-off (the back-off skips the TURN, not the poll).
  5. This changes cadence on armed loops: it is a take-away change (docs/system-specs/common/take-away-changes.md) — grep docs/decisions/ (no loop entry exists at HEAD) and confirm the numbers (3 fallbacks, 6 h cap) with the user.
- **Done when:** A deterministic test (fake clock patched on irq.time and autonudge.time, probe.observe patched) drives AutoNudgeService._monitor_tick_is_quiet for 60 ticks at idle_secs=60: an always-fetch_ok=False probe and an always-raising probe each produce <= 8 fires and exactly 1 blind alert; a good reading on tick 61 makes tick 62 observe normally (no back-off left).
- **Changed from the source claim:** Mechanism confirmed and measured (60/60 fires per hour, 1,440/1,440 per day). Corrections: a blind alert already exists for fetch_ok=False probes (6th error, every 6 h), but a raising probe never alerts; fallback fires count toward max_cycles, so default monitor_start loops are bounded to 24 turns / 4 h — 'tens of millions of tokens per day' needs a loop armed with a large cap. Severity 55 -> 45.
- **Sources:** FIX_PLAN:LOOP-7, REVIEW_FINDINGS:H2

### MSG-2 [45, armed, effort M] Slack thread-follow never expires and the model cannot stay silent — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/slack/thread_follow.py:13 FOLLOW_TTL_SECS=45*60; [[NO_REPLY]] posts nothing (commit fd07b46d2)
- **Verified claim:** With thread_follow on (default True), any reply from an AUTHORIZED user in a Slack thread for which a session, a session link or a conversation-log FILE exists is admitted as a full turn, in mention, review and observe channels, with no time limit: the predicate is file existence (ConversationLog.has_log), and a live transcript is never aged out (archive retention only touches trimmed archive files). Two narrowings the claim omits: unauthorized users get an ephemeral rejection and no turn, and a reply that opens with an @-mention of someone else is skipped. The model has no way to stay silent: there is no no-reply marker on any channel, and an empty answer is still finalized in Slack as a '_No response._' message.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/config/sections.py:3797-3803 — `thread_follow: bool = field(default=True, ... 'Respond to all messages in threads where bot was previously @mentioned.')`
  - src/kiro_crew/slack/events.py:2224-2264 — `_thread_follow_admits`: admitted when `thread_follow and thread_ts and (sessions.has_session(thread_ts) or sessions.get_session_for_thread(thread_ts) or conv_log.has_log(thread_ts))` and not `_addressed_to_someone_else`; no timestamp/TTL input
  - src/kiro_crew/history.py:2096-2098 — `has_log`: `return self._path(key).exists()`
  - src/kiro_crew/history.py:1067-1100 — retention (`session.archive_retention_days`) deletes archive files of trimmed lines only, not the live transcript
  - src/kiro_crew/session_map.py:1758-1760 — `get_session_for_thread` is a plain reverse-index lookup, no expiry
  - src/kiro_crew/slack/events.py:2652-2680 — observe and mention/review branches both admit via `_thread_follow_admits`; :2682-2694 unauthorized users get an ephemeral rejection and return
  - grep NO_REPLY|NO_RESPONSE|no reply needed|SILENT across src/kiro_crew: only `_NO_RESPONSE = "_No response._"` placeholders (slack/handler.py:426, slack/gateway_runtime/cron_verdict.py:44)
  - src/kiro_crew/slack/renderer.py:1110-1117 — empty answer: `update_message(..., first or _NO_RESPONSE)` 'so an empty answer still finalizes'
  - …and 1 more in the verification results.
- **Checked by:** read
- **Required outcome:** A followed Slack thread admits unmentioned replies only within a window (30-60 min, configurable) of the bot's last post in that thread; an @-mention re-arms it. The model can end a followed-thread turn with a 'no reply needed' marker that posts nothing (no '_No response._' bubble) and books the turn as a success.
- **Solution:**
  1. Read docs/system-specs/modules/slack-gateway.md (thread_follow, ~:870) and messaging.md first; update slack-gateway.md in the same commit.
  2. Add `ChannelConfig.thread_follow_ttl_secs` (config/sections.py ~:3797; 0 = no expiry for operators who want today's behaviour). Default 3600 is a take-away change (follow-forever is removed): grep every reader of `thread_follow` (slack/events.py:2656, :2673) and list them under Backwards compatibility; confirm the number with the user.
  3. In `_thread_follow_admits` (slack/events.py:2224) also require `now - last_bot_post_ts(thread_ts) <= ttl`, reading the last assistant row's timestamp from the conversation log tail (or a `last_bot_post_ts` metadata field written when the reply posts); log SEL `thread-follow: expired`. An explicit @-mention bypasses it as today.
  4. Silent marker: add a shared constant (e.g. messaging/no_reply.py `NO_REPLY_MARKER`) taught only in the followed-thread/observe turn context; when the final clean text equals the marker, the Slack renderer/handler deletes the streamed placeholder (or never posts) instead of `_NO_RESPONSE` (slack/renderer.py:1117, slack/handler.py ~:2536/:2682) and books success, not failure (the consecutive-failure breaker must not trip). Keep it opt-in per surface so DMs/mentions always answer.
- **Done when:** Tests with an injected clock: (a) `_thread_follow_admits` with a conversation log whose last assistant row is 30 min old -> True, 61 min old with ttl 3600 -> False and one SEL denial `thread-follow: expired`, ttl 0 -> True; an @-mention at 61 min still dispatches. (b) A transport-path turn whose provider streams exactly the marker posts no message (fake Slack client records zero post/update calls with text) and books no failure.
- **Changed from the source claim:** Corrected scope: only authorized users trigger a turn, and replies opening with someone else's @-mention are skipped; the follow predicate is 'a conversation-log file exists', which lasts until the user deletes the conversation. Added: an empty answer still posts '_No response._'. Severity 50 -> 45.
- **Sources:** FIX_PLAN:MSG-2, REVIEW_FINDINGS:N6

### EVT-5 [40, default, effort M] wait ends only on user end or steer, not on pushed events; poll recipe is for external CI — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): wait ends on a queued/parked session event; spawn_status marks collected; prompt no longer teaches wait-then-poll (commit 1d4177fdc); test/test_wait_tool_early_end.py exists; WAIT_PING_SECS at src/kiro_crew/mcp_core.py:473
- **Original claim:** wait does not end on pushed events; prompt teaches wait-then-poll (corrected below)
- **Verified claim:** Mechanism holds: a `wait` (60-1800 s) ends early for exactly two reasons — the End-wait button / session_end_wait ('user') or a steer landing after the sleep began ('steer'); a queued subagent completion, cron notification or parked completion does not end it (re-measured: 2 queued system injections -> None, 3 parked completions -> None, steer -> 'steer'), so pushed events wait behind the sleep for up to 1800 s. Reading a finished run with spawn_status does not mark it collected (only spawn_sub_agents POSTs /api/spawn/mark-collected), so the completion turn still fires after a poll. Overstated part: prompt.md's wait-then-poll recipe (:105-111, up to 5 rounds of wait(300) + a fetch) is scoped to EXTERNAL systems (code review / static analysis on a PR), and the prompt explicitly tells the model not to poll child work (:44 'Do not poll or duplicate child work'); the only shipped texts that invite polling pushed subagent events are the spawn_sub_agents timeout note (spawn.py:1572) and the orphaned-parent fallback (spawn.py:897-903, :1003), which belong to EVT-8. The request counts (spawn->wait->spawn_status x3 = 5-10 parent requests vs 2 for the blocking call) are modelled from real turn counts, not measured. Side question #2347: at HEAD the strict resolver used by the wait tool accepts a signed per-session token ABOVE KIROCREW_SESSION_KEY (switch-free, no MCP gateway needed), so the sessions.py:4453-4456 comment ('MCP gateway disabled … a subagent's wait and its parent's resolve to the SAME session key') and subagent.md:2235 (env var then session_pid file) are both stale by code read.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/handlers/sessions.py:4348-4392 — _wait_end_reason: 'Exactly two reasons, and the narrowness is the design' (:4351): 'user' (End-wait / session_end_wait) or 'steer'; nothing reads slot._queue or _subagent_deliveries_inflight
  - src/kiro_crew/constants.py:1616 — WAIT_TOOL_MAX_SECS = 1800; mcp_core.py:473 WAIT_PING_SECS = 5.0, :478 WAIT_STALENESS_PING_SECS = 60.0
  - src/kiro_crew/mcp_tools/control.py:1172 — _identified = bool(mcp_core.require_strict_session_key(...)[0]); only an identified wait sends wait_id and honours end_wait
  - src/kiro_crew/mcp_tools/spawn.py:1612-1627 — only spawn_sub_agents POSTs /api/spawn/mark-collected for its settled ids; spawn_status (:1179) never does
  - src/kiro_crew/slack/gateway.py:9626-9636 — _subagent_done skips injection only when info.id in slot._subagents_inline_collected
  - src/kiro_crew/config/prompt.md:98 — wait is for 'an external system to finish (code review analysis, CI build, deployment)'; :105-111 'Short task (user is waiting, < 30 min): use wait+poll' … 'completed 5 total iterations'
  - src/kiro_crew/config/prompt.md:44 — 'Report the dispatch and END YOUR TURN … Do not poll or duplicate child work.'
  - src/kiro_crew/mcp_core.py:724-726 — _resolve_session_key_strict: from_token = _session_key_from_token() checked before os.environ KIROCREW_SESSION_KEY (signed per-session token, session_token_sig.py:1-30 'switch-free identity')
  - …and 4 more in the verification results.
- **Checked by:** ran-existing-script SCRATCHPAD/evt/wait_check.py
- **Required outcome:** A `wait` returns when an event for its session is queued or parked; nothing is delivered twice (a run already read with spawn_status does not cause a second completion turn); no shipped text invites polling for anything Kiro Crew pushes.
- **Solution:**
  1. Add an 'event' reason to _wait_end_reason (dashboard/handlers/sessions.py:4348-4392): end the sleep when a system-injection entry (is_system_injection_item) is queued for the slot or slot._subagent_deliveries_inflight > 0 after the sleep began (baseline taken at mint, like the steer stamp). Update the docstring's 'exactly two reasons' and the wait tool description/prompt.md:98 to name the third reason.
  2. Make spawn_status on a FINISHED run add its id to the parent slot's _subagents_inline_collected via /api/spawn/mark-collected (mcp_tools/spawn.py:1179, reuse the POST at :1620-1625), so _subagent_done (gateway.py:9626-9636) skips the redundant turn; keep it to terminal runs only.
  3. prompt.md:105: keep wait+poll only for external systems with no push path, and point PR watching at monitor_watch (already :113-115); do not add subagent polling anywhere. Polling texts in spawn.py:1572 and :897-903 are EVT-8.
  4. Fix the stale identity comment at sessions.py:4453-4456 and subagent.md:2235 to name the signed per-session token (session_token_sig.py), or confirm live first (see #2347).
- **Done when:** In test/test_wait_tool_early_end.py, with an injected clock, a SUBAGENT_COMPLETION_KIND entry queued 30 s into a wait(600) makes the next keepalive ping (<= WAIT_PING_SECS later) return end_wait with reason 'event'; an entry queued BEFORE the wait began does not end it. A run already read with spawn_status (stubbed /api/spawn/{id} done=true) is skipped by _subagent_done and produces no second parent turn.
- **Upstream:** #2347 (state unverified)
- **Changed from the source claim:** Severity 45 -> 40 and verdict PARTLY: prompt.md's wait+poll recipe is scoped to external CR/CI systems and :44 forbids polling child work, so 'the prompt teaches wait-then-poll' for pushed events is overstated; the wait/spawn_status mechanisms are confirmed and the script numbers are unchanged. #2347 side question resolved by code read: a signed per-session token identifies sessions on a default install, so the sessions.py comment and subagent.md:2235 are stale.
- **Sources:** FIX_PLAN:EVT-5, REVIEW_FINDINGS:Part6/EVT-5

### LOOP-15 [40, armed, effort M] Work-ledger: staggered reports each buy a turn (no settle window, by design); bursts merge — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/autonudge_service/ settle window and per-loop budget (commit 502154bbf, merged de6da10c3); test/test_conductor_wake.py exists
- **Original claim:** Work-ledger wakes are not batched across items (corrected below)
- **Verified claim:** There is no cross-item settle window and no per-conductor wake/turn budget: the work-ledger probe deliberately sets coalesce_secs=0 ('rather be woken early than woken once'), conductor_wake._admit caps pull-forwards per (loop_id, item_id) at 12/hour and ledger_wake caps wakes at 12/item/hour, so reports from different workers that arrive while the conductor is idle each buy their own full conductor turn (measured with the real service + real probe: 5 staggered `question` reports -> 5 delivered turns). Overstated part: reports that land together are already merged -- 5 reports before the pushed tick starts -> 1 turn (_admit's pending branch + one tick folds every fresh WAKE), and reports landing while a conductor turn runs collapse into one owed wake (delivered late, see LOOP-16). Only 'done', 'blocked' and 'question' wake; 'progress' never does.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/probes/work_ledger.py:133-146 — tuning(): 'No coalescing window: every fresh wake in a tick is delivered at once' -> return {"coalesce_secs": 0}
  - src/kiro_crew/conductor_wake.py:506 — ITEM_PULLS_PER_HOUR = 12; :512-567 _admit keyed per_loop[item_id] (:537, :540 pair=(loop_id, item_id))
  - src/kiro_crew/conductor_wake.py:527-531 — a push is 'admitted and not counted' when a pushed tick is armed and not started, or a firing cycle already holds a deferred pull-forward (the only coalescing)
  - src/kiro_crew/ledger_wake.py:57 — WAKE_STATUSES = {done, blocked, question}; :72 MAX_WAKES_PER_ITEM_PER_HOUR = 12 (per item, not per conductor)
  - src/kiro_crew/irq.py:50-56 — coalescing exists in the kernel ('coalesce_secs=0 turns it off for callers that would rather be woken early than woken once')
  - merge/scripts/D/B1/wake_sim.py — staggered_5: {reports 5, delivered_turns 5}; burst_5: {reports 5, delivered_turns 1}
  - src/kiro_crew/work_ledger.py:174 — MAX_ITEMS_PER_CONDUCTOR = 32 (theoretical ceiling 32 x 12 = 384 wakes/h; in practice bounded by turn length because wakes during a turn collapse)
- **Checked by:** ran-new-script merge/scripts/D/B1/wake_sim.py
- **Required outcome:** Reports from different workers that land within a short settle window of each other share one conductor wake turn; a per-conductor wake budget bounds turns per hour across items; a lone report still wakes the conductor within the settle window.
- **Solution:**
  1. Review upstream PR #17583 (+ #17582) first; it reportedly batches a burst of reports into one turn.
  2. Otherwise, in conductor_wake._fire (~649-688) arm the pushed tick with a settle delay (e.g. 30-60 s) instead of fire_now's delay 0, keeping the _pushed_ticks mark so later pushes coalesce through _admit's pending branch (~527-531); keep delay 0 for a 'question' that blocks a worker if the user wants that latency.
  3. Add a per-loop pull-forward budget beside ITEM_PULLS_PER_HOUR (~506) so N items cannot buy N x 12 ticks an hour.
  4. This reverses the recorded design choice in probes/work_ledger.py:133-146 and adds latency: a cadence/take-away change -- grep docs/decisions/ (nothing at HEAD), confirm the window with the user, update agent-interrupt-controller.md and monitor-architecture.md in the same commit.
- **Done when:** In test/test_conductor_wake.py, with the real AutoNudgeService, real work-ledger probe, spied _arm_timer and an injected clock: 5 workers reporting 'question' 10 s apart while the conductor is idle produce exactly 1 delivered turn; a single report produces 1 turn armed at <= the settle delay; the (budget+1)-th cross-item pull-forward in one hour returns '' from conductor_wake._fire.
- **Upstream:** #17571 PR #17583 PR #17582 (state unverified)
- **Changed from the source claim:** Narrowed: simultaneous reports already merge (same tick, or during a running turn); staggered reports each buy a turn. The zero coalescing window is a documented design choice (probes/work_ledger.py:133-146), so changing it is a take-away/latency trade.
- **Sources:** verify_needed:G1(#17571), verification_needed:Part3(#17571), verify_needed:G36

### LOOP-16 [40, armed, effort S] Work-ledger wake can be lost while the conductor is mid-turn — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): busy-refused wake re-armed on first tick after turn end, _OVERDUE_REARM_SECS=10 at src/kiro_crew/autonudge_service/timers.py:58 (commit 56eeeef7e); test/test_conductor_wake.py
- **Verified claim:** Drop point pinned (a full-interval delay, not a permanent loss): defer_if_firing only covers the _firing window, which on a dashboard slot closes as soon as _on_fire returns -- the nudge turn itself runs afterwards in a spawned task. A worker report landing while the conductor's turn runs therefore arms a pushed tick; the real probe answers WAKE (the kernel dedupes the observation), _fire_dashboard_nudge returns BUSY because slot.running, and the refused path keeps the wake owed (followup_ticks=1) and re-arms a 15 s backoff. When the running turn ends, notify_turn_complete calls _arm_from_deadline; next_due_ts was zeroed at the delivered fire, so it arms a FULL idle_secs countdown that replaces the 15 s retry. The owed wake is delivered only on that tick. Measured: idle_secs=600 -> retry armed at 15 s, turn-end re-arm 600 s, B's wake delivered on the next tick (latency = one patrol interval after the turn ends; 300-900 s for a goal conductor). The existing test test_a_push_during_the_fire_window_re_arms_at_delay_zero models the turn INSIDE on_fire, so it does not cover the dashboard shape.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/conductor_wake.py:679 — svc.fire_now(loop_id, defer_if_firing=True)
  - src/kiro_crew/autonudge_service/firing.py:815-823 — defer recorded only 'if loop_id in self._firing' (_pulled_forward.add)
  - src/kiro_crew/slack/gateway.py:7458-7469 — task = spawn_guarded_turn(...); returns DISPATCHED immediately (turn runs after _on_fire returns)
  - src/kiro_crew/slack/gateway.py:7262-7275 — 'if slot.running: # Turn still active — drop this nudge ... return BUSY'
  - src/kiro_crew/autonudge_service/firing.py:435-460 — refused fire: followup_ticks = _WAKE_FOLLOWUP_TICKS, wake re-owed; :671 self._arm_timer(loop, delay=backoff) with _REARM_BACKOFF_SECS = 15 (timers.py:47)
  - src/kiro_crew/autonudge_service/firing.py:675 — delivered fire sets loop.next_due_ts = 0.0
  - src/kiro_crew/autonudge_service/timers.py:396-399 — notify_turn_complete -> self._arm_from_deadline(loop); :599-600 next_due_ts <= 0 -> now + loop.idle_secs (full interval, cancels the backoff timer via _arm_timer :545)
  - merge/scripts/D/B1/wake_sim.py scenario mid_turn — B_tick {refused 1, retry_arm_delay [15], wake_owed true}; turn_end_rearm_delay [600.0]; B delivered on the next tick
  - …and 1 more in the verification results.
- **Checked by:** ran-new-script merge/scripts/D/B1/wake_sim.py
- **Required outcome:** A wake refused because the conductor's slot was busy is delivered on the first tick after that turn ends (within _OVERDUE_REARM_SECS), never a full patrol interval later; nothing is delivered twice.
- **Solution:**
  1. In autonudge_service/timers.py notify_turn_complete (~396-399): when the loop owes a wake (loop.id in self._pending_monitor_wake, or monitor.followup_ticks > 0 after a BUSY refusal), arm at delay 0 / _OVERDUE_REARM_SECS instead of _arm_from_deadline.
  2. Or record the BUSY refusal in _run_fire_cycle's refused branch (firing.py ~430-460) in a set that notify_turn_complete drains, mirroring _pulled_forward.
  3. Keep the 15 s backoff ladder for non-dashboard refusals (channel keys, callback errors).
  4. Add a dashboard-shaped test (on_fire returns BUSY while a separate turn flag is set) beside test_a_push_during_the_fire_window_... in test/test_conductor_wake.py.
  5. Update agent-interrupt-controller.md / monitor-architecture.md layer 7 in the same commit; land with LOOP-17 (upstream plans #17567 with #17574).
- **Done when:** With the real AutoNudgeService and real work-ledger probe, spied _arm_timer and hand-driven ticks (as merge/scripts/D/B1/wake_sim.py scenario_mid_turn): a 'question' report that lands while the conductor's turn runs is refused BUSY, and the notify_turn_complete that ends the turn arms the next tick at <= 10 s (not idle_secs), which delivers exactly one turn; a turn that ends with no wake owed still re-arms the full interval.
- **Upstream:** #17567 (state unverified)
- **Changed from the source claim:** Drop point now pinned: notify_turn_complete's full-interval re-arm overrides the refused wake's 15 s retry; 'dropped' is precisely 'delayed by one patrol interval after the running turn ends'. The mitigation G5 cites (defer_if_firing) only covers the fire window.
- **Sources:** verify_needed:G5(#17567)

### MSG-4 [40, armed, effort L] Remote-crew relay never forwards tool approvals; the turn stalls until the 600 s timeout — PARTLY
- **Original claim:** Remote-crew tool approvals never register (infinite re-prompt) (corrected below)
- **Verified claim:** The remote-relay hop has no approval path, and the code says so: on a dashboard session bound to a remote crew (executor == 'remote'), each turn runs on the peer via relay_remote_turn, and a tool approval the peer raises is never mirrored back — the hub renders approval cards from local slot state the relay never populates, and the hub's api_chat_slot_approve has no local future to resolve, so the turn stalls on the peer until the peer's tool_approval_timeout_secs (default 600 s) declines it and tells the user to resend. Adopted remote transcripts drop 'permission' rows for the same reason. What the code does NOT show is an automatic infinite re-prompt: per turn it is a stall-then-decline; a loop arises only if the user (or an automation) resends. The separate Crew Window view (hub proxy onto the peer's own chat API) CAN answer the peer's native approvals through api/chat/slots/{key}/approve, which is inside the proxy allowlist; non-native (coordinator) approvals there are shown as 'approval elsewhere'. Whether #17455's reporter hit the relay gap, the crew-window path or a channel (Slack/Discord) hop needs the live two-end repro.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/remote_relay.py:743-751 — 'KNOWN GAP — a tool the peer wants approved stalls the turn there. The approval card is rendered from the SLOT PROJECTION ... a relayed turn never populates: the card does not appear here, and ``api_chat_slot_approve`` would find no local future to …
  - src/kiro_crew/dashboard/remote_adopt.py:110-116 — `permission` rows dropped on adopt: 'an adopted one has no local future to resolve — the buttons would answer ``404 no pending approval``... the same pending-approval gap ``relay_remote_turn`` already has (plan E1)'
  - src/kiro_crew/dashboard/chat_runner.py:8110-8133 — a crew-bound slot never executes locally; turns go through relay_remote_turn
  - src/kiro_crew/dashboard/chat_handlers.py:9954-10010 — api_chat_slot_approve resolves only `slot._approval_futures` (local) or a state-level future; a relayed turn creates neither
  - src/kiro_crew/config/sections.py:1611-1621 — `tool_approval_timeout_secs` default 600: 'declining it and telling the user to resend'
  - src/kiro_crew/dashboard/handlers_instances.py:1672-1675 — proxy allowlist `(("api","chat"), ("api","stream"))`; website/src/pages/chat/crew-window/CrewChatWindow.tsx:176-185 — crew window approves native requests via `api/chat/slots/<key>/approve` through the proxy; :258 non-native approvals -> 'ap…
  - website/src/api/client/approvals.ts:11 — the generic bell/command-center path posts to hub-local `/api/approvals/{id}/{action}`, which is outside the proxy allowlist and has no peer forwarding
- **Checked by:** read
- **Required outcome:** An approval raised by a remote crew for a hub-relayed turn appears on the hub's slot and the user's decision is forwarded to the peer's pending request exactly once; until that exists, a crew-bound session that hits an approval says so immediately (not after a silent 600 s stall), and no path re-prompts the same tool call without new user input.
- **Solution:**
  1. Read docs/system-specs/modules/messaging.md (approvals) and the remote-crew design notes referenced by remote_relay.py ('plan E1'); the fix is the 'second mechanism' the KNOWN GAP describes.
  2. Peer side: emit the pending approval (request_id, request_mid, tool, origin) as a streamed frame on the relayed api/chat SSE (or poll the peer slot's `pending_approval_info` over the already-allowed `api/chat/slots/<key>` route, as CrewChatWindow does).
  3. Hub side (dashboard/remote_relay.py `_apply_row` ~:350): mirror that frame onto the local slot's approval projection with a forwarding marker instead of a local future.
  4. Route the hub's `api_chat_slot_approve` (chat_handlers.py:9954) for an executor=='remote' slot to the peer's `api/chat/slots/<peer key>/approve` through `instances_manager.proxy_request` with the strict native body (origin/request_id/request_mid), so a stale card cannot decide a newer request.
  5. Interim (S): when a relayed turn's stream shows the peer waiting on approval, append a visible row on the hub ('approve on the crew or in its Crew Window') rather than stalling silently.
  6. Update the remote-crew spec section and remove the KNOWN GAP note in the same commit.
- **Done when:** Test driving relay_remote_turn with a scripted chunk iterator (the `chunks=` test seam) that emits a peer approval frame: the hub slot exposes one pending approval; POST /api/chat/slots/<key>/approve on the hub issues exactly one proxied POST to the peer's approve route with origin='native', request_id and request_mid; a second POST for the same request returns 404; no local tool executes. A no-forwarding build fails the first assertion.
- **Upstream:** #17455 (state unverified)
- **Changed from the source claim:** Upgraded from 'PARTIAL, needs live repro' to a code-level finding: the relay hop's missing approval forwarding is a documented KNOWN GAP (remote_relay.py:743). Corrected: per turn it is a stall until the 600 s approval timeout declines, not an automatic infinite re-prompt; the Crew Window proxy path does forward native approvals. Severity assigned 40.
- **Sources:** verification_needed:Part3(#17455)

### EVT-6 [35, default, effort M] A separate synthesis turn follows every completion batch — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): synthesis rides last completion turn (commit 3a218b7ca)
- **Verified claim:** On a dashboard parent, the last outstanding child arms slot._pending_synthesis in _subagent_done; once the queue drains, a SEPARATE synthesis turn runs SUBAGENT_SYNTHESIS_PROMPT unless exactly one completion turn carried the whole batch (_drop_single_turn_synthesis drops it only when _synthesis_completion_turns == 1). So every batch delivered in 2+ completion turns pays one extra full-context turn after the last completion turn. Re-measured: N single-spawn completions -> synthesis fires for N>=2; a wave flushed in 3 chunks -> synthesis fires. The 'restating' wording is a characterisation: the prompt asks for a cross-result consolidation (goal, combined findings, next actions) that the per-result turns do not produce; this is a documented design (subagent.md 'Post-fan-out Synthesis Turn', injected-messages.md:129-139), dashboard-only.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/slack/gateway.py:9579-9605 — '── Fix 2 (B1): arm a one-shot post-fan-out synthesis turn ──' … _arm_synthesis when running_agents_for(parent_key) == [] … _injection_slot._pending_synthesis = True
  - src/kiro_crew/dashboard/chat_runner.py:7237-7249 — _drop_single_turn_synthesis: 'if slot._synthesis_completion_turns != 1: return False' (only a single-turn batch skips it)
  - src/kiro_crew/dashboard/chat_runner.py:7141-7215 — _run_pending_synthesis: appends an inject row and spawn_guarded_turn(_run_chat(state, slot, SUBAGENT_SYNTHESIS_PROMPT, …)) — a separate turn
  - src/kiro_crew/dashboard/state.py:1858-1866 — SUBAGENT_SYNTHESIS_PROMPT (465 chars): '(1) restate the original goal … (2) synthesize the combined findings … (3) give concrete recommended next actions'
  - docs/system-specs/modules/subagent.md:2162-2167 — 'a single dedicated synthesis turn produces the user-facing summary … Dashboard chat only'
  - docs/system-specs/common/injected-messages.md:129-136 — 'more completion turns, one further synthesis turn is fired … one wave digest normally gets no synthesis turn'
  - SCRATCHPAD/evt/queue_drain.py (re-run at HEAD) — synthesis fired False for N=1, True for N=2,3,5,10; SCRATCHPAD/evt/digest_timing.py — synthesis=True for every multi-chunk wave
- **Checked by:** ran-existing-script SCRATCHPAD/evt/queue_drain.py
- **Required outcome:** The synthesis rides on the last completion turn: a batch delivered in N completion turns costs N turns, not N+1, and the user still gets one consolidated synthesis.
- **Solution:**
  1. When the arm is set at delivery of the last outstanding child (slack/gateway.py:9595-9605), append the synthesis instruction (SUBAGENT_SYNTHESIS_PROMPT body, dashboard/state.py:1858) to that completion's envelope and mark the batch done (clear _pending_synthesis, zero _synthesis_completion_turns).
  2. Fire a separate turn (chat_runner.py:7141-7215) only when the fire gate (chat_utils.synthesis_fire_verdict) shows a child the in-memory arm missed.
  3. Optional: agent.subagent_synthesis = inline|turn|off (default inline). Removing the separate turn changes a documented behaviour: update subagent.md 'Post-fan-out Synthesis Turn' and injected-messages.md:129-139 in the same commit, grep docs/decisions/ (no entry at HEAD) and list readers of injectKind='synthesis' rows as Reader: lines.
- **Done when:** 3 completions on a dashboard slot (fake clock, real _ChatSlot and drain helpers) produce exactly 3 _run_chat calls, the last one's message contains the synthesis instruction, and _run_pending_synthesis never dispatches; a store-held sibling the arm missed still gets one separate synthesis turn.
- **Changed from the source claim:** Mechanism confirmed. Severity 40 -> 35: the separate turn is a documented design whose prompt asks for a cross-result consolidation, so 'restating' overstates the waste; the saving is one full-context request per multi-turn batch.
- **Sources:** FIX_PLAN:EVT-6, REVIEW_FINDINGS:Part6/EVT-6

### LOOP-19 [35, armed, effort M] Conductor bind does not arm a patrol — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): conductor bind arms work-ledger loop (commit 9e2c31936, merged)
- **Verified claim:** No code arms a patrol when a conductor binds (or creates) a work item: the bind path writes the item, the binding and a crew-dispatch record and never touches the AutoNudge service; arming is prompt-only ('Always pass watch="work-ledger"'). A conductor that never calls monitor_start leaves conductor_wake nothing to push -- work_ledger_loop_id returns '' (its own docstring: 'No loop at all is the common case (a conductor that armed nothing)') -- so worker reports reach nobody until a human prompts the conductor. Applies to kirocrew-conductor only; the pipeline and security conductors do not use the work ledger.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/work_ledger.py:2012-2077 — apply_conductor_action 'bind' commits the item/binding; no autonudge/monitor call
  - src/kiro_crew/dashboard/handlers/work_ledger.py:1640-1655 — after bind only crew_log_emit.on_crew_dispatch(...)
  - grep 'autonudge|monitor_start|get_by_slot' in work_ledger.py, mcp_work.py, dashboard/handlers/work_ledger.py — no hits on the bind path
  - src/kiro_crew/conductor_wake.py:597-626 — work_ledger_loop_id: 'No loop at all is the common case (a conductor that armed nothing)' -> ''
  - src/kiro_crew/agent.py:4332-4336 — 'Arm a loop on your own session with `monitor_start` ... **Always pass `watch="work-ledger"`**' (prose)
  - src/kiro_crew/builtin_skills/goal-conductor/SKILL.md:237-248 — 'After dispatching, arm a loop ... `watch="work-ledger"` is mandatory' (prose)
- **Checked by:** read
- **Required outcome:** A conductor that has bound at least one work item always has an active, gated work-ledger loop on its own session, so every worker report and turn end can pull it forward; arming is idempotent and never creates a second loop.
- **Solution:**
  1. Review upstream PRs #17069 + #17079 ('arm a work-ledger patrol on conductor bind') before writing code; check they arm with watch='work-ledger' and gate on (otherwise LOOP-2's cost shape applies).
  2. Otherwise, after a successful 'bind' in dashboard/handlers/work_ledger.py (~1640), when conductor_wake.work_ledger_loop_id(svc, key) is '', arm one through the same authorized add path monitor_start uses (autonudge_authz), with watch='work-ledger', gate=True, self-armed provenance, and default bounds that pass patrol_budget.py check (e.g. interval 600 s); emit one transcript notice row.
  3. Keep the prose in agent.py:4332-4336 as the primary path; the bind-arm is the backstop.
  4. New defaults (interval, cycles, runtime) are cadence choices: confirm the numbers with the user and record them in pipeline-conductor.md / work-ledger docs.
- **Done when:** Test with a tmp KIROCREW_HOME, the real work ledger and a real AutoNudgeService: a conductor slot with no loop binds an item through the handler -> exactly one active loop for that slot with monitor.kind == 'work-ledger' and gate True; a second bind arms nothing new; a worker 'question' report then makes conductor_wake._fire return that loop's id.
- **Upstream:** #17051 #17603 PR #17069 PR #17079 (state unverified)
- **Changed from the source claim:** none (issue claim confirmed in code at HEAD; previously not code-checked)
- **Sources:** verify_needed:G15(#17051/#17603), verify_needed:G37

### LOOP-21 [35, armed, effort M] Wake judge fires every interval on a sustained run of partial readings — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): partial-reading floor (commits f9d6cfd18, b1bf12554); test/test_autonudge.py exists
- **Verified claim:** On a loop screened by the wake judge, a reading that stays PARTIAL — any target the collector counts as dropped: a pull-request observation whose observation_status is not 'ok' (a partial fetch), an observation with no facts, a transcript target still behind after MAX_PAGES_PER_TARGET pages, or a reader that raised — makes the judge return FALLBACK ('wake judge could not read every target') before any model is asked, and every non-QUIET verdict fires the turn and RESETS the quiet streak. The only backstop, the quiet-streak floor (10), counts QUIET verdicts, i.e. complete readings; nothing counts consecutive partial readings, so a sustained partial reading fires a full turn every interval. Measured on the real gate -> judge -> nudge_wake.judge_tick path with a fake clock at idle_secs=60: one dropped target every tick -> 60 fires in 60 ticks, 0 judge calls; the same loop with complete readings judged quiet -> 6 fires in 60 ticks (the floor). Bounded, like any loop, by its cycle/runtime caps (monitor_start default 24 turns / 4 h).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/decisions/points/nudge_wake.py:802-810 — `if dropped:` ... `return irq.Verdict(irq.Outcome.FALLBACK, body="wake judge could not read every target")` (before the oracle is asked)
  - src/kiro_crew/autonudge_judge.py:624-643 — pr_target_is_unread: True when `observation_status` is set and != 'ok' (a partial fetch) or the observation has no facts
  - src/kiro_crew/autonudge_judge.py:72-78 — 'A target still behind after the last page is counted as unread, so the tick fires instead of judging a partial reading quiet' (MAX_PAGES_PER_TARGET)
  - src/kiro_crew/autonudge_service/judge_tick.py:292-300 — only `verdict.outcome is irq.Outcome.QUIET` advances judge_quiet_streak toward the floor; :343-344 — every other verdict sets `loop.judge_quiet_streak = 0` and `judge_wake_pending = True` (fires)
  - src/kiro_crew/autonudge_service/gate.py:814, :841-845 — on a kernel-QUIET tick the judge is asked; `if judged is False:` resets monitor.quiet_streak and fires
  - src/kiro_crew/autonudge_service/gate.py:46-64 — _MAX_QUIET_STREAK = 10 = _JUDGE_QUIET_STREAK_FLOOR_DEFAULT (the backstop counts quiet ticks only)
  - measured (merge/scripts/D/B2/loop21_partial_judge.py, dropped=1 every tick): {ticks: 60, fires: 60, oracle_calls: 0, outcomes: fallback x60, judge_quiet_streak: 0}
  - measured (same script, QUIET=1, complete readings, fake judge answers quiet): {ticks: 60, fires: 6, oracle_calls: 60, outcomes: quiet x60}
- **Checked by:** ran-new-script merge/scripts/D/B2/loop21_partial_judge.py
- **Required outcome:** A partial reading that persists costs no more than a calm one: the first partial tick fires (the owner learns the watch cannot see everything), and further ticks whose dropped-target set and readable evidence are unchanged count toward the same floor as QUIET ticks (at most one delivery per floor interval), with one notice naming the unreadable target. A change in what is dropped, fresh readable evidence that the judge rules non-quiet, or a recovery to a complete reading resets the count.
- **Solution:**
  1. Read docs/system-specs/modules/monitor-architecture.md ('Decision', 'Rules the engine enforces'), agent-interrupt-controller.md and decisions.md (nudge.wake); update them in the same commit.
  2. decisions/points/nudge_wake.py:802-810 — return a distinguishable verdict body/key for 'partial reading' (or put the dropped count on the trace) so the caller can tell it from a provider failure.
  3. autonudge_service/judge_tick.py:343 — for a partial-reading FALLBACK, keep a per-loop `judge_partial_streak` (persisted with the other judge fields) keyed by the dropped-target set; fire on the first, then treat repeats as quiet ticks against _judge_quiet_streak_floor (judge_tick.py:34-56) instead of resetting it; reset on a complete reading or a changed dropped set.
  4. Consider letting the judge answer about the readable targets when the unread one has been unread for N ticks (and mark it in the state), instead of never asking.
  5. Cadence change on armed loops — a take-away change: grep docs/decisions/ (nothing on the judge at HEAD) and confirm the numbers with the user. Review upstream #14071 first.
- **Done when:** Extend test/test_autonudge.py (or the judge tests) with the fake-clock pattern of merge/scripts/D/B2/loop21_partial_judge.py: 60 ticks at idle_secs=60 with one target dropped every tick and unchanged evidence -> <= 7 fires (first + one per floor); a tick whose dropped set changes fires; a complete reading resets the streak. No sleeps, frozen autonudge.time.
- **Upstream:** #14071 (state unverified)
- **Changed from the source claim:** Issue title confirmed in code and measured (60/60 fires vs 6/60 for complete quiet readings). 'Partial reading' pinned to the collector's dropped-target rule (pr_target_is_unread, MAX_PAGES_PER_TARGET, a raising reader). The judge is not even asked on such ticks, so the waste is watched-session turns, not judge calls.
- **Sources:** verify_needed:G39(#14071)

### LOOP-26 [35, armed, effort S] Disabling Auto-Improvement stops its run but not its PR watchers (up to 4, 6 passes each) — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): PR watchers stop on app disable (commit f87360f00, 160945ba2); test/..../auto_improvement/tests/test_pr_watchers.py extended
- **Original claim:** Disabling Auto-Improvement does not stop its workers (corrected below)
- **Verified claim:** Disabling Auto-Improvement now stops an in-flight RUN: the app's on_shutdown hook, which the disable teardown invokes, calls get_supervisor().stop() (bounded; the spine stops between candidates). It does NOT stop the PR watchers: their only stop on a non-explicit path is the aiohttp on_cleanup hook at gateway shutdown, nothing in pr_watchers checks whether the app is still enabled, and _require_enabled only gates the HTTP routes (403 app_disabled), which also removes the operator's per-watcher stop button. So up to MAX_ACTIVE_WATCHERS = 4 live watchers keep running agent passes (DEFAULT_MAX_NUDGES = 6 each, 1800 s apart, each pass up to 1800 s) after disable. The 'run supervisor keeps spending' half is fixed at HEAD except for the start-during-build race (LOOP-27).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/auto_improvement/backend/routes.py:123-135 — _require_enabled wraps handlers only: 403 {code: app_disabled}
  - src/kiro_crew/apps/builtins/auto_improvement/app.json — backend.hooks on_shutdown = backend.crew:on_shutdown
  - src/kiro_crew/apps/builtins/auto_improvement/backend/crew.py:139-142 — on_shutdown: await ... asyncio.to_thread(get_supervisor().stop) — no pr_watchers stop
  - src/kiro_crew/apps/teardown.py:265-271 — disable teardown calls on_app_disable(..., run_app_hooks=app_may_be_running); apps/hooks_integration.py:705-715 invokes on_shutdown
  - src/kiro_crew/apps/builtins/auto_improvement/backend/routes.py:1614-1619, :1645-1646 — _stop_watchers (stop_all) is registered on app.on_cleanup, i.e. gateway shutdown only
  - src/kiro_crew/apps/builtins/auto_improvement/backend/pr_watchers.py:998-1052 _nudge_loop — up to max_nudges passes of fetch status -> _run_agent_pass -> _wait(interval); stops only on stop_ev, READY, BLOCKED, isolation refusal or exhaustion; no is_app_enabled check anywhere in the module (grep)
  - src/kiro_crew/apps/builtins/auto_improvement/backend/pr_watchers.py:77-90 — DEFAULT_MAX_NUDGES = 6, DEFAULT_NUDGE_INTERVAL_S = 1800.0, MAX_ACTIVE_WATCHERS = 4, DEFAULT_NUDGE_TIMEOUT_S = 1800.0
  - src/kiro_crew/apps/builtins/auto_improvement/tests/test_shutdown_stops_run.py:1-13 — covers gateway shutdown stopping the run; no test covers app disable stopping watchers
- **Checked by:** read
- **Required outcome:** After the operator disables Auto-Improvement, no Auto-Improvement agent pass or run starts or continues past its current step: the run supervisor stops (already true) and every PR watcher stops after at most its current pass, with deferred watchers dropped from promotion.
- **Solution:**
  1. In backend/crew.py:on_shutdown also call pr_watchers.get_registry().stop_all() (off-loop, like the supervisor stop), so the disable hook stops watchers the same way gateway shutdown does (routes.py:1614-1619).
  2. Defence in depth: in pr_watchers._nudge_loop check is_app_enabled(store.APP_NAME) (off the loop is already the case — it runs on a worker thread) before each _run_agent_pass and end the watcher as STATUS_STOPPED with note 'app disabled'.
  3. Land together with LOOP-27 (the start-during-build race) — upstream PR #16860 is reported to address this and #16936 says it is incomplete without the race fix.
  4. Update docs/system-specs/modules/auto-improvement.md (it documents _require_enabled only, :31) with the disable semantics.
- **Done when:** A test enables the app, starts a PR watcher whose runner parks in its agent pass (Event-gated, no sleeps), runs the disable teardown (on_app_disable with run_app_hooks=True), then releases the pass and asserts the watcher ends STATUS_STOPPED without a second pass and registry.active_summary() reports 0 live watchers.
- **Upstream:** #16855 PR #16860 PR #6797 (state unverified)
- **Changed from the source claim:** Narrowed: at HEAD the disable hook (on_shutdown) already stops the run supervisor; only the PR watchers (<= 4 x 6 bounded passes) keep spending, plus the LOOP-27 race. Severity unset -> 35.
- **Sources:** verify_needed:G23(#16855), verify_needed:G36

### LOOP-5 [35, armed, effort M] Opt-in meeting translation makes one small lite call per line, unbatched, no usage row — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/apps/builtins/meetings/backend/domain/translate.py:117 usage row per call (usage_surface=meetings_translate); batching 5 s / 25 per call, one session per meeting (commit a2766cbfe)
- **Original claim:** Meetings translation makes one model call per transcript line (corrected below)
- **Verified claim:** When a meeting has a translation language configured (OFF by default: DEFAULT_TRANSLATION_LANG = ""), every non-filler transcript line becomes its own model call: run_oneshot_translation opens a fresh ephemeral kirocrew-lite session per line (tool-less, REJECT_ALL), sends a ~546-char one-line prompt through stream_and_collect, then destroys the session; calls are sequential per meeting with a 40-line backlog that drops the oldest; there is no batching (deliberate, documented for latency) and no viewer check (the docstring says it runs 'whether or not anyone has the panel open'); stream_and_collect persists no usage row. Overstated in the source: each call is a small lite call (no persona, no tool schemas), not a full-context turn, and the feature is opt-in; the 500-1500 calls/meeting-hour figure is modelled from an assumed speech rate (the real ceiling is wall-clock: one call in flight at a time).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/meetings/backend/constants.py:256 — DEFAULT_TRANSLATION_LANG = "" (translation off unless configured); domain/session.py:666-674 builds TranslationQueue only when translation_language is a known code
  - src/kiro_crew/apps/builtins/meetings/backend/domain/translate.py:114 — get_or_create(key, agent="kirocrew-lite") with key = f"{SLOT_PREFIX}-translate-{uuid4().hex}" (a new session per line); :125 — await sessions.destroy(key)
  - src/kiro_crew/apps/builtins/meetings/backend/domain/translate.py:108 — 'one start per line, whether or not anyone has the panel open'
  - src/kiro_crew/apps/builtins/meetings/backend/domain/translate.py:21 — 'Sequential per meeting. One in-flight call'; :196 + constants.py:265 — backlog cap MAX_TRANSLATION_BACKLOG = 40, oldest dropped
  - src/kiro_crew/llm_helpers.py:2354 stream_and_collect — no persist_token_record_async inside it; usage is persisted only in run_bg_oneliner (:1809/:1832) and background_turn (:2316/:2332), which translate.py does not use
  - docs/system-specs/modules/meetings.md:380-390 — 'Off by default — it costs one model call per spoken line'; 'this exists to avoid batching ... one tool-less call on kirocrew-lite per line with the ephemeral session destroyed after'
  - ran merge/scripts/D/D1/loop5_translate.py (real TranslationQueue + run_oneshot_translation, fake session manager, stubbed stream_and_collect): 50 lines in one burst -> 40 model calls on 40 distinct kirocrew-lite sessions, 40 destroyed, 10 dropped by the backlog cap, 546 prompt chars per call; Trans…
- **Checked by:** ran-new-script merge/scripts/D/D1/loop5_translate.py
- **Required outcome:** With translation on, lines are translated in small batches on one session per meeting (or on the shared background runtime), usage is recorded for every call, and the per-line session churn is gone; whether translation should pause while no viewer is attached is a product decision, because translations.json is also read later through GET /meetings/{id}/translations.
- **Solution:**
  1. Batch: in TranslationQueue._drain (translate.py:211) take every pending line (up to a char cap) and send one prompt asking for one translated line per numbered input line; keep the 'DATA, not instructions' guard and parse the numbered answer back per line. A 5-10 s window keeps latency acceptable.
  2. Session: keep one kirocrew-lite session per meeting (key f"{SLOT_PREFIX}-translate-{meeting_id}") and destroy it in TranslationQueue.clear/drain at teardown, or route through llm_helpers.run_bg_oneliner which already records usage (check the shared-runtime serialization in session_background.py first, since translation is latency-bound).
  3. Usage: if staying on stream_and_collect, persist usage the way run_bg_oneliner does (llm_helpers.py:1790-1840), attributed to the meetings app.
  4. Viewer gating (optional): only with a maintainer decision — it removes persisted translations for lines nobody watched, so it is a take-away change (Reader: GET /meetings/{id}/translations and the sidebar's source column).
  5. Update docs/system-specs/modules/meetings.md 'Live translation' (it states per-line, no batching) in the same commit.
- **Done when:** test_meetings_translation.py: with a fake runner and a fake clock, 50 lines enqueued within 10 s produce <= 3 runner calls on 1 session key, every output line maps back to its source in order, and a usage row is persisted per call; a teardown test asserts the per-meeting session is destroyed once.
- **Changed from the source claim:** Corrected: translation is opt-in (off by default), per-line is the documented design for latency, each call is a ~546-char tool-less kirocrew-lite call (not a full turn), calls are sequential with a 40-line backlog; the calls/hour figure is modelled. Confirmed: fresh session per line, no batching, no viewer check, no usage row. Severity 70 -> 35.
- **Sources:** FIX_PLAN:LOOP-5, REVIEW_FINDINGS:B3

### LOOP-8 [35, armed, effort M] Unbounded nudge loops from the goal popover and ctx.nudge — CONFIRMED
- **Verified claim:** The loop model defaults `max_cycles: int = 0 # 0 = unlimited` (and max_runtime_secs 0 = unlimited), and the 1..1000 cycle / positive-runtime bounds exist only on the MCP tool schemas (monitor_start, monitor_update) and on structured monitors (MonitorBudgets, 4 h default runtime). Enumerated arming paths at HEAD: BOUNDED — monitor_start (24 cycles, 4 h, 300 s, gated), monitor_watch / POST /api/monitors (structured: runtime 4 h, agent turns unlimited by default), /goal (50 cycles, 15 s, no runtime cap), spec-builder handoff (60 cycles, 120 s). UNBOUNDED BY DEFAULT — the dashboard goal popover (POST /api/autonudge: max_cycles 0, max_runtime_secs 0 accepted with allow_unbounded=True, idle 60 s, UNGATED), workflow ctx.nudge (max_cycles defaults 0 and the port passes no runtime at all), Issue Radar crews (max_cycles=0, 300 s; see LOOP-3), Auto-Research campaigns (row max_cycles or 0; see LOOP-13). Pass-through paths that keep whatever they are given: authorize_and_add_nudge defaults max_cycles=0; the directive applier turns a missing max_cycles into 0; the slot-close re-arm replays 0 as 0; the store loader passes a stored row's caps through unvalidated and a row without them loads unlimited. So the 24-cycle/4-h guarantee is enforced by monitor_start's handler and schema, not by the model, and the popover and ctx.nudge produce loops that fire a full turn every interval until someone stops them.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/autonudge_service/model.py:357 — `max_cycles: int = 0 # 0 = unlimited`; max_runtime_secs (0 = unlimited) in the same dataclass
  - src/kiro_crew/autonudge_authz.py:821 — authorize_and_add_nudge(..., max_cycles: int = 0, ..., max_runtime_secs: int = 0)
  - src/kiro_crew/dashboard/handlers/autonudge.py:924, :974, :983, :1004 — api_autonudge_start: `max_cycles = int(body.get("max_cycles", 0))`, `validate_runtime_secs(body.get("max_runtime_secs", 0), allow_unbounded=True)`, `gate = False if raw_gate is None`
  - website/src/components/AutoNudgePopover.tsx:104, :224, :447 — popover seeds max_cycles 0, idle 60 and POSTs `{slot_key, ...fields}` with no max_runtime_secs
  - src/kiro_crew/dashboard/server_runtime/workflow_startup.py:111-124 — _wf_nudge_authorizer passes idle_secs/max_cycles only (no max_runtime_secs); workflows/runner.py:566 — `def nudge(self, *, idle_secs, message, max_cycles: int = 0)`
  - src/kiro_crew/validation.py:1606, :1735 — FieldSpec("max_cycles", int, min_val=1, max_val=1000) on MONITOR_START_SCHEMA / MONITOR_UPDATE_SCHEMA only
  - src/kiro_crew/mcp_tools/control.py:1544-1557 + mcp_tools/_limits.py:14-15 — monitor_start defaults 300 s / 24 cycles / 14,400 s
  - src/kiro_crew/dashboard/session_directive_apply.py:644 — `max_cycles = int(args.get("max_cycles") or 0)` (a directive without the field arms unlimited)
  - …and 5 more in the verification results.
- **Checked by:** read
- **Required outcome:** Every arming path that does not name its bounds gets the same defaults as monitor_start (24 cycles, 4 h runtime capped to the operator ceiling); 'unlimited' exists only when the user explicitly chooses it on a surface that says so, and the loop model / authorizer default to the bounded values so a new or bypassing path cannot silently reopen unbounded loops.
- **Solution:**
  1. Read docs/system-specs/modules/monitor-architecture.md ('Runtime bounds and activation evidence') and agent-interrupt-controller.md; update them in the same commit.
  2. Move the defaults into one place: have authorize_and_add_nudge (autonudge_authz.py:821) default max_cycles/max_runtime_secs to mcp_tools/_limits.py's _MONITOR_DEFAULT_MAX_CYCLES / min(_MONITOR_DEFAULT_MAX_RUNTIME_SECS, runtime_ceiling_secs()), with an explicit `unbounded=True` argument as the only way to pass 0.
  3. Popover: AutoNudgePopover.tsx:104/:224 default the cycles field to 24 and send max_runtime_secs; offer 'unlimited' as an explicit choice; api_autonudge_start (handlers/autonudge.py:974-983) treats an ABSENT field as the default, and 0 only when the body says so.
  4. Workflow ctx.nudge: workflows/runner.py:566 and workflows/__init__.py:175 default max_cycles to the monitor default and pass a runtime budget through workflow_startup.py:116.
  5. session_directive_apply.py:644 — a missing max_cycles means the default, not 0.
  6. Leave the loader (autonudge.py:1091) reading stored rows as they are (existing loops keep their caps); do NOT change NudgeLoop's dataclass default unless every constructor passes caps explicitly.
  7. Do not use decisions.nudge_wake.quiet_streak_floor (config/sections.py:4597) for this: it is the judge's quiet floor, and lowering it makes judged loops fire MORE often.
  8. Issue Radar (LOOP-3) and Auto-Research (LOOP-13) are separate items.
  9. Take-away change: grep docs/decisions/ (nothing on loop caps at HEAD), list readers (popover, workflow scripts, directive replays) under Backwards compatibility, and confirm 24 cycles / 4 h with the user.
- **Done when:** Tests with a frozen clock: (a) POST /api/autonudge with no max_cycles/max_runtime_secs stores a loop with 24 cycles and the default runtime; with an explicit unlimited flag stores 0/0; (b) a workflow calling ctx.nudge(idle_secs=60, message='x') arms a loop with the default caps; (c) a monitor_start directive replay missing max_cycles arms 24, not 0.
- **Changed from the source claim:** Narrowed claim confirmed and extended: the unbounded set also includes the directive-applier fallback (missing field -> 0), Issue Radar crews and Auto-Research (separate items), and the store loader passes caps through unvalidated (X12's angle holds). The popover loop is also UNGATED by default. FIX_PLAN's 'quiet_streak_floor defaults to 3' is a misreading: that config key is the wake judge's quiet-verdict floor, not a loop bound.
- **Sources:** FIX_PLAN:LOOP-8, REVIEW_FINDINGS:B4, FIX_PLAN-old:T5, verify_needed:X12

### EVT-8 [30, armed, effort M] Workflow/spawn texts invite polling pushed results; peer-session results are not pushed — PARTLY
- **Original claim:** Peer-session and workflow tools invite polling (corrected below)
- **Verified claim:** Two halves with different facts. (1) Workflow and spawn texts invite polling for results that ARE pushed: workflow_run's result says 'its result will be injected here on completion. You can keep working; check back with workflow_status(…)' while dashboard/workflow_inject.py injects a [Workflow completion event]; spawn_sub_agents' timeout note says completion events 'still arrive; poll spawn_list or spawn_status'. (2) Peer-session tools (session_send, session_broadcast, session_read_message; prompt.md:62; docs/session-control.md:285-293) tell the model to `wait`, then poll session_read_message — but for peer sessions NOTHING is pushed back to the caller at HEAD (no completion envelope, no [Session update], no blocking read option), so polling is currently the only mechanism and the claim's 'although results are pushed' is wrong for this half; the fix is a new push or blocking read, not a text change. Peer-session tools are opt-in ('only when present'). The '~12 requests per 30 min of watching' figure is modelled (wait+read every ~5 min), not measured.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/mcp_tools/workflows.py:308-311 — 'its result will be injected here on completion. You can keep working; check back with workflow_status(…)'; :298-300 'Its result will be injected here on completion — or check progress with workflow_status(…)'
  - src/kiro_crew/dashboard/workflow_inject.py:125 — inject_workflow_result(...); :62 '[Workflow completion event]' envelope (the push exists)
  - src/kiro_crew/mcp_tools/spawn.py:1569-1573 — spawn_sub_agents timeout note: 'Their [Subagent completion event] messages still arrive; poll spawn_list or spawn_status for progress.'
  - src/kiro_crew/mcp_tools/spawn.py:897-903 — orphaned-parent fallback 'Poll spawn_list and read …/result.txt instead' (legitimate: no events arrive there)
  - src/kiro_crew/mcp_dashboard.py:961 — session_send: 'Use session_read_message afterwards to watch what the target did with it.'; :1017 broadcast 'Poll the targets afterwards with session_read_message'; :1157 session_read_message 'Use it to watch a peer session's progress: `wait`, then read'; :2781 '…
  - src/kiro_crew/mcp_dashboard.py:1154-1180 — session_read_message schema has only target/limit/since (no wait/blocking parameter)
  - src/kiro_crew/docs/session-control.md:285-293 — 'Poll by passing the previous read's next_since back as since … `wait`, then read.'
  - src/kiro_crew/config/prompt.md:62 — 'Peer sessions: … only when present (opt-in) … New sessions are EMPTY: seed with session_send, poll session_read_message.'
  - …and 1 more in the verification results.
- **Checked by:** read
- **Required outcome:** Watching a peer session or a workflow costs about one caller request when the peer's turn (or the run) ends; no shipped text tells the model to poll for something Kiro Crew already pushes.
- **Solution:**
  1. (S) Drop 'check back with workflow_status' from workflow_run's results (mcp_tools/workflows.py:298-300, :308-311): say the result will be injected and the model should end its turn. Reword spawn.py:1569-1573 to 'their completion events will arrive; end your turn' (keep the orphan fallback at :897-903).
  2. (M) Peer sessions need a delivery mechanism first: either session_read_message(wait_secs <= 1800) that returns on new rows or when the target goes idle (server-side hold, keepalive-pinged like wait), or an opt-in '[Session update]' push to the sender when the target's turn ends, coalesced per EVT-1 and registered in injected-messages.md as a new envelope.
  3. Then update the texts at mcp_dashboard.py:961, :1017, :1157, :2781, docs/session-control.md:285-293 and prompt.md:62 to use it.
- **Done when:** A test with an injected clock: a caller watching a stubbed peer that works for 10 min makes <= 2 caller requests (one session_read_message with wait_secs returning when running flips false, or one pushed update); a string test asserts workflow_run's result text no longer contains 'check back'.
- **Changed from the source claim:** Verdict PARTLY (prior: code-read defect): peer-session results are NOT pushed at HEAD, so the polling advice there is accurate for today's mechanism; only the workflow_run and spawn timeout texts invite polling of pushed events. Severity 35 -> 30; the 12-requests figure is modelled.
- **Sources:** FIX_PLAN:EVT-8, REVIEW_FINDINGS:Part6/EVT-8

### LOOP-17 [30, armed, effort S] Work-ledger wake turn has neither banner nor item id, only the standing loop message — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/ledger_wake.py:470 "[work-ledger wake] item=<id> status=..." brief; autonudge.py:817 carries it
- **Original claim:** Work-ledger wake carries only a banner, forcing a ledger read (corrected below)
- **Verified claim:** Worse than claimed: the wake turn does not even carry the banner. The irq kernel builds '[work-ledger wake] item=<id> status=<status>' plus the footer 'this text names what changed', but the gated prompt-loop path keeps only a claim (_pending_monitor_wake) and discards verdict.body; _fire_dashboard_nudge composes nudge_cycle_header + compose_nudge_body(loop.message), whose snapshot is the session_ledger's, not the work ledger's. Measured: delivered text == the standing loop message only, with no brief and no item id. So the conductor's first act on every wake is a work_ledger_read of the whole board (goal-conductor SKILL.md mandates work_ledger_read compact=true first every cycle anyway).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/ledger_wake.py:455-470 — wake_brief returns '[work-ledger wake] item={item_id} status=...'
  - src/kiro_crew/probes/work_ledger.py:246-251 — wake_suffix: 'Read the items with work_ledger_read before acting: this text names what changed'
  - src/kiro_crew/irq.py:658-663 — body(): 'The delivered wake: every brief, then the footer ONCE'
  - src/kiro_crew/autonudge_service/gate.py:961-976 — WAKE branch only does self._pending_monitor_wake.add(loop.id); verdict.body is used only in the quiet-tick debug log (:958)
  - src/kiro_crew/slack/gateway.py:7212-7213 — msg = await compose_nudge_body(_fired_message, ...); tagged = f"{nudge_cycle_header(loop)}\n{msg}"
  - src/kiro_crew/dashboard/handlers/autonudge.py:61, :80-104 — compose_nudge_body prefixes session_ledger.render_snapshot only
  - merge/scripts/D/B1/wake_text.py — irq_verdict_body '[work-ledger wake] item=it_4b749fd6 status=question ...'; delivered_turn_text == standing message; contains_wake_brief false, contains_item_id false
  - src/kiro_crew/builtin_skills/goal-conductor/SKILL.md:297 — '`work_ledger_read` with `compact=true` first, every cycle'
  - …and 1 more in the verification results.
- **Checked by:** ran-new-script merge/scripts/D/B1/wake_text.py
- **Required outcome:** A work-ledger wake turn names which items moved and how (the kernel's verdict body: wake and stall briefs plus the footer), so a single-item wake can be handled with a targeted read instead of a full board read; liveness/floor turns carry no brief.
- **Solution:**
  1. In autonudge_service/gate.py WAKE branch (~961-976) keep verdict.body beside the claim in memory only (e.g. self._pending_monitor_wake_body[loop.id]); do not persist it beyond what irq already persists (wake_brief is structural only by design, ledger_wake.py:455-468).
  2. In slack/gateway.py _fire_dashboard_nudge (~7212) append that body after compose_nudge_body for gated loops; consume/clear it where _run_fire_cycle consumes the claim (firing.py ~424-425), re-owing it on a refused fire like the claim.
  3. Optionally add the moved items' compact rows (the #17574 ask), read at fire time through the identity-gated store path, never written into irq state.
  4. Let goal-conductor SKILL.md:297 allow a targeted read when the wake names one item. Ship with LOOP-16 (upstream pairs #17574 with #17567).
- **Done when:** A test driving the real service + real probe (as merge/scripts/D/B1/wake_text.py): a 'question' report on item X produces one delivered turn whose text contains '[work-ledger wake] item=X status=question' exactly once; a quiet-streak floor turn contains no '[work-ledger wake]' line; a refused (BUSY) fire keeps the body for the retry.
- **Upstream:** #17574 (state unverified)
- **Changed from the source claim:** Corrected: the turn carries neither the banner nor the item id (claim said it carries only the banner); the brief is built by irq and dropped by the autonudge gated path.
- **Sources:** verify_needed:G6(#17574), verification_needed:Part3(#17574)

### LOOP-18 [30, armed, effort S] Conductor 20-items-per-goal cap is prompt-only; code caps 32 open / 256 stored items — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): item budget enforced in ledger store (commit 146cb5784); builtin_skills/goal-conductor/SKILL.md:253-263 documents item_budget_exceeded refusal
- **Original claim:** Conductor spend cap is prompt-only (corrected below)
- **Verified claim:** The 20-item-per-goal cap is prompt-only (agent.py conductor prompt and goal-conductor SKILL.md), but the claim's 'no code-side ledger cap (no patrol_budget.py)' is wrong: the store refuses a 33rd OPEN item per conductor (MAX_ITEMS_PER_CONDUCTOR = 32) and a 257th create over a board's life (WORK_STORED_ITEM_LIMIT = 256); and goal-conductor/scripts/patrol_budget.py exists -- it bounds the loop's interval, cycles and runtime, not items. The compaction-miscount risk is mitigated in prose only ('Count both from work_ledger_read, not from memory'). Net: a conductor that ignores the prose can dispatch up to 32 concurrent / 256 lifetime items per board (and nested conductors at depth <= 2 each hold their own board).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/agent.py:4350-4353 — 'With no budget from the user, a goal holds at most 20 ledger items in total' (prose)
  - src/kiro_crew/builtin_skills/goal-conductor/SKILL.md:423-431 — 'Item cap ... at most **20 ledger items** ... Count both from `work_ledger_read`, not from memory'
  - src/kiro_crew/work_ledger.py:174 — MAX_ITEMS_PER_CONDUCTOR = 32; :1950-1956 refuses with CODE_ITEM_CAP_EXCEEDED
  - src/kiro_crew/work_vocab.py:50 — WORK_STORED_ITEM_LIMIT = 256; work_ledger.py:192, :1933-1943 refuses with CODE_ITEM_STORE_FULL
  - src/kiro_crew/work_ledger.py:194 — MAX_DEPTH = 2
  - src/kiro_crew/builtin_skills/goal-conductor/scripts/patrol_budget.py:1-48 — 'the one code owner of the conductor's loop bounds' (interval/cycles/runtime; no item count)
  - grep 'item_budget|20 ledger|ITEM_CAP' over src/kiro_crew/*.py — no code reads a per-goal 20-item budget
- **Checked by:** read
- **Required outcome:** The per-goal item budget (20 by default, or the user's own budget / an approved larger Round-0 plan) is durable state the store enforces, so a compaction cannot make the conductor lose count: a create past it is refused with a code the conductor turns into an ask_question.
- **Solution:**
  1. Add an item_budget field to the conductor header (work_ledger ConductorRecord), written by the 'goal' action, default 20.
  2. In _create_item (work_ledger.py ~1925-1956) refuse when created_total >= item_budget with a new code (e.g. item_budget_exceeded), before the 32/256 caps.
  3. Show 'items used N of B' in work_ledger_read compact rows (dashboard/handlers/work_ledger.py compact fields ~1235).
  4. Align agent.py:4350-4356 and goal-conductor SKILL.md:423-437 to the code.
  5. A new refusal is a tightened validator (take-away): list Reader lines for work_ledger_record create in chat and crew paths; update work-ledger docs in the same commit. Review upstream #17076 first.
- **Done when:** test_work_ledger.py: a conductor with no budget set creates 20 items, the 21st apply_conductor_action('create') raises WorkLedgerError(code='item_budget_exceeded') with nothing written; after a 'goal' action setting item_budget=25 the 21st succeeds; the 32 open / 256 stored caps still refuse as before.
- **Upstream:** #17076 (state unverified)
- **Changed from the source claim:** Corrected: code caps exist (32 open / 256 stored per conductor) and patrol_budget.py exists (loop bounds, not items); only the 20-item per-goal budget is prose.
- **Sources:** verification_needed:Part3(#17076)

### LOOP-27 [30, armed, effort S] Auto-Improvement disable race restarts the driver — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): start() re-checks stop after _build_driver (commit 160945ba2); auto_improvement/tests/test_runner.py exists
- **Verified claim:** RunSupervisor.start() checks _in_flight() under the lock, RELEASES the lock for the seconds-long _build_driver (git checkout + profile build under the clone lock), then re-takes it, sets _stop_requested = False and launches the worker thread. A stop() — which is what the disable hook (on_shutdown -> get_supervisor().stop()) calls — that lands during _build_driver sees no live thread, returns 'no active run' without recording anything, and the start then launches anyway, so a run started just before disable keeps spending after the app is disabled. stop() also ignores the _reserved flag, so the short assigned-but-unstarted window has the same hole.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/auto_improvement/backend/runner.py:695-697 — first `with self._lock: if self._in_flight(): raise`; lock released
  - src/kiro_crew/apps/builtins/auto_improvement/backend/runner.py:699 — driver = self._build_driver(config) outside the lock (:519-541: blocking git/profile work under clone_lock)
  - src/kiro_crew/apps/builtins/auto_improvement/backend/runner.py:708-714 — re-check _in_flight() only, then self._stop_requested = False; :749-752 thread assigned, _reserved = True, thread.start()
  - src/kiro_crew/apps/builtins/auto_improvement/backend/runner.py:1192-1209 — stop() (check at :1203): `if thread is None or not thread.is_alive(): return {... 'note': 'no active run'}` — no flag set, _reserved not consulted
  - src/kiro_crew/apps/builtins/auto_improvement/backend/crew.py:139-142 — on_shutdown (the disable hook) only calls get_supervisor().stop()
  - ran merge/scripts/D/D1/ai_disable_race.py (real RunSupervisor, Event-gated fake _build_driver, no sleeps): stop() during _build_driver -> {'status': 'idle', 'stopped': False, 'note': 'no active run'}; _stop_requested stays False; start() then returns {'status': 'running'}; the worker calls driver.r…
- **Checked by:** ran-new-script merge/scripts/D/D1/ai_disable_race.py
- **Required outcome:** A stop (including the disable hook) that lands at any point of start() — before, during or after _build_driver — prevents the run from launching, and start() never clears a stop it did not observe.
- **Solution:**
  1. Make the stop request survive a start in progress: in start() set self._reserved = True (and remember a start generation) inside the FIRST locked block, before _build_driver; clear it on every refusal/exception path.
  2. In stop(), treat `self._reserved` like a live thread: set self._stop_requested = True and status STOPPING, and return 'stopping' instead of 'no active run'.
  3. In start()'s second locked block, if _stop_requested is set for this generation, drop the built driver and return/raise a 'stopped before launch' result instead of clearing the flag and launching (do not reset _stop_requested there; reset it only at the start of the first block).
  4. Optionally re-check is_app_enabled(store.APP_NAME) in the second block.
  5. Land with LOOP-26 (watchers) and record the disable semantics in docs/system-specs/modules/auto-improvement.md.
- **Done when:** A test in auto_improvement/tests/test_runner.py patches _build_driver with an Event-gated builder, calls stop() once the builder is entered, releases it, and asserts start() does not launch a worker (supervisor status not 'running', driver.run never called) and stop() reported a stopping/stopped result rather than 'no active run'.
- **Upstream:** #16936 PR #16860 (state unverified)
- **Changed from the source claim:** none — the race reproduces at HEAD exactly as described; added that stop() also ignores the _reserved window.
- **Sources:** verify_needed:G40(#16936)

### UI-2 [30, armed, effort S] Mochi keeps planning while the pet is hidden — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): hidden pet pauses plan and freestyle spawns (commit b66cf9157); test/test_mochi_hidden_pet_gate.py exists
- **Verified claim:** A hidden Mochi pet keeps POSTing presence beats every 30 s with visible=false; presence_beat() refreshes the shell timestamp on every beat but the pet timestamp only when visible, and the owner loop gates poller.poll() (watch checks, missed-notify recovery, plan/replan, freestyle agent tasks) and reminders on shell_present() alone, while pet_present() gates only the companion-time clock — so planning and agent-task spawns continue for as long as the desktop shell runs, whether or not anyone can see the pet. This is a deliberate parity choice written into both sides ('polling continues (the original polled while hidden too)'), so changing it is a take-away/product decision. The 24-96 runs per 8 h estimate is modelled, and its 96 ceiling (12/h balanced tier) does not hold: the hourly budget is enforced only on watch-check spawns, not on plan, replan or freestyle spawns.
- **Evidence (at `397f4be`):**
  - website/src/apps/mochi/pet/main.tsx:70-86 — PRESENCE_BEAT_MS = 30_000; body {visible: document.visibilityState === 'visible'}; 'a hidden pet keeps beating with visible=false — polling continues (the original polled while hidden too), only the companionship clock stops'
  - src/kiro_crew/apps/builtins/mochi/hooks.py:920-937 — presence_beat sets _last_shell_beat_ms every beat, _last_presence_ms only if visible; :939-940 pet_present; :942-952 shell_present 'Gates ALL autonomous work'
  - src/kiro_crew/apps/builtins/mochi/hooks.py:409, :437-446 — shell_on = self.shell_present(now); if not self.idle.is_paused and shell_on: poll_task = asyncio.create_task(self.poller.poll())
  - src/kiro_crew/apps/builtins/mochi/hooks.py:81-82 — _PRESENCE_FRESH_MS = 90_000, _SHELL_FRESH_MS = 120_000
  - src/kiro_crew/apps/builtins/mochi/queue_poller.py:455-459 — budget.max_spawns_per_hour applied only inside the watch-check branch; plan/replan (:531-555) and freestyle (:559-565) spawn with no budget check
  - src/kiro_crew/apps/builtins/mochi/activity_budget.py:60-88 — tiers economy 4, balanced 12, active 30 spawns/h; DEFAULT_TIER = 'balanced'
  - ran merge/scripts/D/D1/mochi_poller.py Part A (real MochiRuntime, fake ctx, fake ms clock): 2 h of visible=false beats every 30 s -> shell_present true on 7200/7200 owner ticks, pet_present true on 0/7200
  - docs/decisions/ — no Mochi entry; docs/system-specs/modules/mochi.md does not state the hidden-pet polling rule (it lives only in the code comments above)
- **Checked by:** ran-new-script merge/scripts/D/D1/mochi_poller.py
- **Required outcome:** While the pet has been hidden longer than a grace period, Mochi makes no plan, replan or freestyle-task spawns; watch checks and reminders the user asked for keep running on shell presence; and every background spawn kind counts against the hourly activity budget.
- **Solution:**
  1. In the owner loop (hooks.py:437-446) split the gate: pass a 'pet visible recently' flag (pet_present(now), or hidden for less than N min) into the poller, and in QueuePoller._do_poll skip step 6 (plan/replan, queue_poller.py:531-555) and step 7 (freestyle, :557-564) when it is false; keep steps 3-5b (deterministic tasks, watch checks, missed-notify) on shell_present.
  2. Count plan/replan/freestyle spawns against budget.max_spawns_per_hour the way the watch branch does (:455-480), or through SpawnLedger before each spawn — review together with upstream PR #17418 ('bound freestyle bg spawns by the hourly budget').
  3. Take-away change: the hidden-pet polling rule is a deliberate parity choice (main.tsx:70-75, hooks.py:926-931) — confirm with the user, list readers (poller tests, the panel's usage display backend/routes.py:291-306) and record the rule in docs/system-specs/modules/mochi.md in the same commit.
- **Done when:** A fake-clock test drives MochiRuntime's owner-loop body for 2 h with visible=false beats every 30 s, a queue whose planned_until expires mid-run, one due freestyle task and one due watch item, and asserts 0 plan/replan/freestyle spawns and >= 1 watch-check spawn; a second test with visible=true beats asserts plan spawns resume.
- **Upstream:** #13129 PR #17418 (state unverified)
- **Changed from the source claim:** Mechanism confirmed and measured; added that it is a documented-in-code parity choice (take-away), that the run-count estimate is modelled, and that the hourly budget bounds only watch-check spawns, so the 96/8 h ceiling in the estimate is not enforced. Severity 35 -> 30.
- **Sources:** FIX_PLAN:UI-2, REVIEW_FINDINGS:Part6/UI-2

### UI-5 [30, armed, effort S] Mochi QueuePoller retries a refused freestyle-task spawn every 1 s, no backoff (~400/min) — PARTLY

> **COMPLETED** — verified fixed on `main` by code read (no tests run): exponential backoff on refused freestyle spawn, src/kiro_crew/apps/builtins/mochi/queue_poller.py (commit 0bac927b0); test/test_mochi_freestyle_backoff.py exists
- **Original claim:** Mochi background poller retries spawn_run ~400/min with no backoff (corrected below)
- **Verified claim:** Mochi's QueuePoller retries a REFUSED freestyle-task spawn on the very next 1 s poll with no backoff, no failure count and no hourly-budget check: one due freestyle task whose spawn raises costs 60 spawn attempts per minute, and 7 due tasks cost 420/min (the issue's ~400/min). Watch checks and plans do back off (a failed watch spawn keeps the full 5-min lock; plans use a 10-min lock plus exponential retry). At HEAD, however, a start that fails the memory floor is QUEUED by admission (should_queue=True, SubagentInfo queued=True, no error), so SpawnSDK returns an id and the poller then waits up to SPAWN_TIMEOUT_MS (2 attempts per 10 min) — the deferred_low_memory deferral itself no longer drives the tight loop; what still does is any refusal that reaches SpawnSDK as an error (memory wait past agent.subagent_queue_max_wait_secs, task store unable to record the wait, profile/agent-scope denial, a missing app agent). The skill-view alias side effect was not re-measured here.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/mochi/queue_poller.py:62 — POLL_INTERVAL_MS = 1_000; hooks.py:352 owner loop sleeps POLL_INTERVAL_MS and fires poller.poll() each tick
  - src/kiro_crew/apps/builtins/mochi/queue_poller.py:559-565 (step 7) and _spawn_agent_task_serial (:646-677) — on spawn_agent exception: logger.exception, on_agent_spawn_end, return — task stays not-done, no fail count, no retry-after
  - src/kiro_crew/apps/builtins/mochi/queue_poller.py:499-504 and _spawn_watch_check (:679-697) — a failed watch spawn re-raises so 'the full lock stands' (5-min SPAWN_TIMEOUT_MS natural backoff); :380-395 plan failures back off 60 s -> 16 min
  - src/kiro_crew/apps/builtins/mochi/queue_poller.py:455-459 — max_spawns_per_hour applied only to watch-check spawns
  - src/kiro_crew/subagent_manager/admission/gate.py:1013-1072 — floor miss logs SEL outcome='deferred_low_memory' and builds memory_wait; :1187-1188 'if memory_wait is not None: should_queue = True'; :1247-1340 queued SubagentInfo(queued=True) returned; :1113-1135 only past taskq_memory_wait_bound_sec…
  - src/kiro_crew/apps/spawn_sdk.py:216-217 — build_spawn_impl raises SpawnError only when info.error is set; a queued info returns its id; :238-249 done probe treats is_queued ids as pending
  - ran merge/scripts/D/D1/mochi_poller.py Part B (real QueuePoller, fake callbacks, fake ms clock, one tick+poll per fake second): B1 1 due freestyle task + refusing spawn -> 60 attempts/60 s; B2 spawn returns an id that never completes (queued deferral) -> 2 attempts/600 s; B3 7 due freestyle tasks +…
- **Checked by:** ran-new-script merge/scripts/D/D1/mochi_poller.py
- **Required outcome:** A refused or deferred Mochi background spawn is retried with exponential backoff (and counted toward the hourly activity budget), never on the next 1 s poll; a deferral is treated as 'try later'.
- **Solution:**
  1. In QueuePoller._spawn_agent_task_serial (queue_poller.py:646-677), on a spawn exception record a per-task retry_at (exponential, e.g. 30 s doubling to 15 min, reusing PLAN_BACKOFF_BASE_MS/_PLAN_BACKOFF_CAP_MS) and increment fail_count under the queue lock like _locked_mark_timeout does (:704-738); have get_executable_tasks skip tasks whose retry_at is in the future.
  2. Charge plan/replan/freestyle spawns against budget.max_spawns_per_hour (today only the watch branch, :455-480) — review with upstream PR #17418 first.
  3. Keep the HEAD admission behaviour (a memory-floor miss queues and returns an id); add a test that pins it for app spawns so a regression back to a refusal is caught.
- **Done when:** With a fake clock, a QueuePoller whose spawn_agent always raises and one due freestyle task makes <= 6 spawn attempts in 10 minutes (and 7 due tasks <= 42); a SpawnSDK test with a SubagentManager double reporting a memory-floor miss asserts run() returns the queued id instead of raising.
- **Upstream:** #13129 PR #17418 #13119 #16341 (state unverified)
- **Changed from the source claim:** The no-backoff retry is real and reproduces ~400/min (420/min at 7 due tasks), but at HEAD the low-memory deferral is queued rather than refused, so deferred_low_memory alone no longer triggers it; the remaining triggers are refusals. Watch/plan paths already back off; only freestyle tasks lack backoff. Skill-view alias side effect not re-verified.
- **Sources:** verify_needed:G33(#13129), verify_needed:G37

### EVT-11 [25, default, effort S] Messages queued before a subagent is flagged stalled wait for the next send — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): parked messages drain when subagent hold lifts (commit 6c6819e75)
- **Verified claim:** While a dashboard parent's background children run, user messages are parked in the slot queue (subagents_hold_user_messages). Once the reaper flags the last live child 'stalled' the hold condition becomes false, but flagging only emits a subagent_stalled UI event — nothing starts a queue drain — so the parked messages sit until the user sends another message (which joins the queue behind them and drains it), clicks Run-now on a card, or the child completes/is reaped at the wall-clock timeout. The code states this as accepted behaviour. This is latency, not token spend.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/dashboard/chat_runner.py:6515-6528 — subagents_hold_user_messages: a child flagged 'stalled' does not hold the queue ('The flag clears when the child streams again, and the hold with it')
  - src/kiro_crew/dashboard/chat_handlers.py:1334-1341 — 'Messages parked while a child was live stay parked once it is flagged stalled (nothing starts a drain then). A send behind them joins the queue and drains it'
  - src/kiro_crew/subagent_manager/monitoring.py:1388-1405 — _maybe_flag_stall_impl: info.stalled = True, then only _fire_event('subagent_stalled', …) — 'deliberately surface-only'
  - repo grep: subagent_stalled is consumed only by ws_event_scope.py / ws.py / subagent_scale.py (UI forwarding); no handler calls _start_next_queued_turn
- **Checked by:** read
- **Required outcome:** When the hold on parked user messages lifts because the last live child was flagged stalled, the queue drains at once (subject to the usual idle-slot and memory-preparation guards), without waiting for another send.
- **Solution:**
  1. In the gateway/dashboard consumer of the stall flag (where subagent_stalled is fired, subagent_manager/monitoring.py:1388-1405, via the manager's event hook), after setting info.stalled, find the parent slot; if it is idle, has queued entries and subagents_hold_user_messages(state, key) is now False, call the existing parked-drain helper (_arm_parked_queue_drain / _start_next_queued_turn, dashboard/chat_runner.py) once.
  2. Keep it idempotent (one drain per hold release) and do not drain while slot.running or memory preparation is pending.
  3. Remove the 'nothing starts a drain then' caveat in chat_handlers.py:1334-1341 and note the behaviour in subagent.md (queue-during-subagents).
- **Done when:** Test with a fake clock and a stubbed manager: a user message queued while one child is live is NOT drained; when the reaper marks that child stalled (two sweeps), exactly one _start_next_queued_turn runs and the message's turn starts without a second send; a still-live non-stalled sibling keeps the message parked.
- **Upstream:** #17059 (state unverified)
- **Changed from the source claim:** Prior status was an issue title only; now confirmed by code (the behaviour is documented in a code comment). Severity set at 25: a latency stall, not a token cost.
- **Sources:** verify_needed:G15(#17059)

### LOOP-20 [25, armed, effort S] Pipeline conductor's 960-cycle cap ends patrol before 72 h when turns average under 180 s — PARTLY
- **Original claim:** Patrol cycle cap ends the conductor loop before its runtime budget (corrected below)
- **Verified claim:** The pipeline conductor's prescribed bounds (~90 s, max_cycles=960, max_runtime_secs=259200) fail goal-conductor's own patrol_budget.py check (exit 20: interval below 300 and interval x cycles < runtime). Because the dashboard re-arms idle_secs after each nudge turn ends and cycle_count counts delivered turns, an ungated pipeline patrol reaches cycle 960 after 960 x (90 s + mean turn time): before the 72 h runtime whenever the mean turn is under 180 s (24 h is the lower bound, not the figure). The server validates max_cycles and max_runtime_secs independently, with no consistency check. Mitigations: every capped cycle carries a '[patrol budget: ...; 10% or less left]' header and SKILL.md tells the conductor to raise bounds with monitor_update, so the early end happens only when the model does not renew; the goal conductor runs patrol_budget.py check (script, not server) which enforces interval x cycles >= runtime.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/builtin_skills/pipeline-conductor/SKILL.md:269-273 — ~90 s, max_cycles=960, max_runtime_secs=259200; 'raise it with `monitor_update` before it expires'
  - ran builtin_skills/goal-conductor/scripts/patrol_budget.py check --interval-secs 90 --max-cycles 960 --max-runtime-secs 259200 -> exit 20, problems: 'interval_secs must be 300..900', 'interval_secs x max_cycles must cover max_runtime_secs'
  - src/kiro_crew/validation.py:1605-1607 — FieldSpec interval_secs 15..86400, max_cycles 1..1000, max_runtime_secs 1..ceiling (independent)
  - src/kiro_crew/autonudge_service/firing.py:130-133 — cycle cap deactivates with stopped_reason='cycle_cap'; :293-300 cycle_count counts delivered turns
  - src/kiro_crew/autonudge_service/timers.py:303-322 — next full cycle starts at the nudge turn's end
  - src/kiro_crew/autonudge_service/model.py:682-724 — nudge_cycle_header adds '[patrol budget: cycle N/M, ...; 10% or less left]'
  - src/kiro_crew/builtin_skills/goal-conductor/SKILL.md:255 — goal conductor runs patrol_budget.py check before monitor_start
  - docs/system-specs/modules/pipeline-conductor.md:333-337 — 'Coasting into the cap is a failure, not a finish'
- **Checked by:** ran-existing-script src/kiro_crew/builtin_skills/goal-conductor/scripts/patrol_budget.py
- **Required outcome:** Every conductor patrol is armed with bounds where the runtime budget, not the cycle count, ends the loop (interval x max_cycles >= max_runtime_secs), so a live fleet is not orphaned by the cycle cap before its runtime.
- **Solution:**
  1. Land after LOOP-2: once the pipeline patrol is gated, max_cycles counts delivered turns only and the mismatch mostly disappears.
  2. Change pipeline-conductor SKILL.md:269-271 to bounds that pass patrol_budget.py check (or ship that script with the pipeline skill and reference it), and have the skill-contract test enforce it. Cadence change: take-away, confirm numbers with the user.
  3. Optional server side: _validate_monitor_runtime (validation.py) returns a warning string (not a refusal) when interval_secs x max_cycles < max_runtime_secs.
- **Done when:** test_pipeline_conductor_skill_contract.py extracts the SKILL.md patrol bounds and asserts patrol_budget.py check exits 0 on them; with a gated patrol, a fake-clock service test shows the loop ends on stopped_reason 'runtime_budget', not 'cycle_cap'.
- **Upstream:** #14610 (state unverified)
- **Changed from the source claim:** Arithmetic corrected: the cap is reached after 960 x (90 s + mean turn time), so 24 h is a lower bound; the mismatch is specific to the pipeline conductor's prescribed bounds (goal conductor bounds are script-checked) and is mitigated by the renewal header.
- **Sources:** verify_needed:G39(#14610)

### LOOP-24 [25, armed, effort S] Cron auto-pause threshold not configurable; expired-credential failures count toward it — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): auth failures do not count toward auto-pause (commit 074cf8892)
- **Verified claim:** The cron auto-pause threshold is a module constant (`_AUTO_PAUSE_THRESHOLD = 5`) with no config key and no per-job field, and nothing classifies an authentication failure: an AcpAuthRequired (expired kiro-cli sign-in) is non-transient, so it skips the transient retry ladder and reaches record_failure(), and five such fires auto-pause the job. record_success() is the only automatic un-pause, and a paused job never fires, so every LLM cron that fired during an expired-credential window stays paused until the user re-enables each one. Exemptions that do exist: transient backend errors (3 retries), governance denials, starvation/never-started runs and shared-runtime deaths (substitute bound) are not charged.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/cron_service/model.py:26 — `_AUTO_PAUSE_THRESHOLD = 5 # consecutive failures before a script/command cron auto-pauses`
  - src/kiro_crew/cron_service/model.py:345-358 — `record_failure`: `if self.consecutive_failures >= _AUTO_PAUSE_THRESHOLD and not self.auto_paused: self.enabled = False; self.auto_paused = True`
  - src/kiro_crew/cron_service/model.py:360-391 — `record_success` is the only automatic clear of auto_paused
  - src/kiro_crew/acp/transport_errors.py:310-325 — `class AcpAuthRequired(AcpError)`: 'Non-retryable: respawning the process hits the same wall'
  - src/kiro_crew/slack/gateway.py:5671-5675 — transient ladder only when `acp_error_is_transient(exc)`; src/kiro_crew/llm_helpers.py:247-262 reads the structured `transient` flag
  - src/kiro_crew/slack/gateway.py:5751-5775 — every other exception the job owns -> `_charge_failure()` -> `job.record_failure()`; no auth-class branch in the LLM failure handler (grep 'Auth' in :5597-6073 finds none)
  - rg 'auto_pause|pause_threshold' src/kiro_crew/config: no config key
- **Checked by:** read
- **Required outcome:** An expired sign-in does not silently disable a user's schedules: auth failures are reported once and either do not count toward auto-pause or auto-resume after the next successful sign-in; the threshold is configurable per job (including 'never'), defaulting to today's 5.
- **Solution:**
  1. In slack/gateway.py's cron failure handler (:5733+) add an arm for `isinstance(exc, AcpAuthRequired)`: alert once (dedup via last_failure_hash), do NOT call record_failure(), and keep the job enabled (or set a distinct `auth_paused` that the sign-in path clears).
  2. Add `auto_pause_after: int | None` to CronJob (cron_service/model.py) read by record_failure (None = global default 5, 0 = never), exposed through cron_add/cron_update, the dashboard and the CLI; check upstream PR #15524 first.
  3. Script/command crons: leave their counting as is unless the script exits with a distinguishable auth code.
  4. Update docs/system-specs/modules/learn-cron-dashboard.md. Changing what pauses a job is a take-away change: list readers of auto_paused (doctor cron check cron.py:283-349, dashboard) in the PR.
- **Done when:** A cron callback whose stream raises AcpAuthRequired six times in a row leaves consecutive_failures == 0 and enabled == True with exactly one alert; a job with auto_pause_after=0 never pauses after 10 failures; a job with the default still pauses at the 5th non-auth failure.
- **Upstream:** #15906 PR #15524 (state unverified)
- **Changed from the source claim:** none (code-checked for the first time; claim holds). Exemptions that already exist (transient, governance denial, starvation, shared-runtime deaths) narrow which failures count.
- **Sources:** verify_needed:G25(#15906), verify_needed:G37

### LOOP-32 [25, armed, effort M] Cron misses a boundary the host slept through — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): slept-through cron boundary fires once on wake (commits 86df94054, 40d79a29b)
- **Verified claim:** Only cron-EXPRESSION jobs miss a boundary the host slept through (or the gateway was down for): `is_due` for kind 'cron' is true only while the expression matches the current minute, so a host that wakes at 09:01 or later never fires a '0 9 * * *' job that day and re-arms for the next boundary. `every` and `at` jobs do catch up (they are due whenever now >= last_run + interval / at_ts, firing once). The timer is capped at 30 s, but the matching minute is gone by then. No persisted catch-up marker exists; the code comment calls one 'a possible follow-up'.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/cron_service/schedule.py:535-560 — `is_due`: kind 'cron' -> `if not seams.cron_expr_matches(job.schedule.cron_expr, dt): return False`; every/at compare against last_run/at_ts
  - src/kiro_crew/cron_service/schedule.py:564-600 — `next_wake_secs` re-arms a cron job ON its next boundary (`_next_cron_boundary_ts(job, now)`)
  - src/kiro_crew/cron_service/schedule.py:40 — `_TIMER_POLL_SECS = 30`
  - src/kiro_crew/cron.py:3625-3632 — 'A cron-expression job is only due while its expression matches the current minute ... an in-memory catch-up marker loses the occurrence on gateway restart ... (persisted deferral markers are a possible follow-up)'
  - ran merge/scripts/D/A/cron_catchup.py (real is_due/next_wake_secs, fake now): wake 09:00:30 -> cron due True; wake 09:01:10 -> cron due False, next wake in 23.98 h; wake 09:30 -> cron due False; every/at jobs due True at all three wakes
- **Checked by:** ran-new-script merge/scripts/D/A/cron_catchup.py
- **Required outcome:** A cron-expression job whose boundary passed while the host slept or the gateway was down runs once on wake (within a configurable catch-up window), never more than once per missed boundary, and the behaviour is documented.
- **Solution:**
  1. Persist the last boundary the job was due at (or reuse last_run_ts): in cron_service/schedule.py:is_due, for kind 'cron', also return True when the most recent boundary at or before now (`_prev_cron_boundary_ts`) is later than last_run_ts and within a catch-up window (e.g. 6 h; skip_dates still apply).
  2. Keep the same-minute guard so a catch-up cannot double-fire.
  3. Make the window a per-job or global setting with 0 = today's behaviour.
  4. Review upstream PR #17656 first; update docs/system-specs/modules/learn-cron-dashboard.md. Changing when jobs fire is a take-away-adjacent change: confirm the default window with the user.
- **Done when:** With a fake clock: a '0 9 * * *' job with last_run 24 h ago is due once at 09:30 the same day (catch-up), not due again at 09:31, not due at 15:30 when the window is 6 h; an `every` job's behaviour is unchanged; a skip-dated day is not caught up.
- **Upstream:** #750 PR #17656 (state unverified)
- **Changed from the source claim:** Scoped: the gap is specific to cron-expression schedules; interval and one-shot jobs already catch up.
- **Sources:** verify_needed:G37(#750)

### LOOP-9 [25, armed, effort S] Nudge judge: fresh tool-less lite session per judged tick, background model, no usage row — PARTLY
- **Original claim:** Nudge judge starts a fresh session every tick (corrected below)
- **Verified claim:** On every gated tick that the wake judge screens, the LLM lane opens a brand-new ephemeral session (key `judge-<uuid4>`) on the tool-less `kirocrew-lite` agent through `sessions.get_or_create`, streams ONE prompt with `stream_and_collect` (default `retry_transient=True`, so up to 3 same-model transient retries, all inside the gate's 8-60 s wait_for budget — 8 s on the default timeout_ms), then releases and destroys it; no usage row is written, because `stream_and_collect` itself persists nothing and this runner does not go through `run_bg_oneliner`/`background_turn`, the two helpers that record background spend. Two parts of the claim do NOT hold: (a) the 'inherit' model IS the background role — an empty `llm_model` passes no model, and the session resolves the kirocrew-lite spec's own model, which the spec writer sets from `agent.role_models['background']` (default "auto"); (b) the judge does not run a 'fresh full session': the lite agent has no tools, no MCP servers and an empty prompt, so the per-tick cost is the judge prompt (fenced, budgeted evidence + questions) plus a session start, not the main persona/schemas. Z2 is half right: the judge does not ride the watched session's own turn (it is a separate session), but it is the cheap lite agent. Scope: it runs only on gated loops whose owner armed a `judge` brief (LLM lane authorized by provider=auto/llm alone, on by default in that case) or, for loops without a brief, only once the owner granted the nudge_evidence scope (off by default).
- **Evidence (at `397f4be`):**
  - src/kiro_crew/decisions/impl_llm.py:65 — `JUDGE_AGENT_NAME = "kirocrew-lite"`; :74 — `JUDGE_MODEL_DEFAULT = "auto"` (inherit sentinel, stripped to no model at :183-186)
  - src/kiro_crew/decisions/impl_llm.py:206-221 — `key = f"judge-{uuid.uuid4().hex}"`; `sessions.get_or_create(key, agent=JUDGE_AGENT_NAME, model=resolved or None)`; `stream_and_collect(provider, prompt, approval_policy=REJECT_ALL, on_chunk=_bound)`; finally `sessions.release(key)` + `await sessions.de…
  - src/kiro_crew/llm_helpers.py:2366 — stream_and_collect `retry_transient: bool = True`; :108 — `_TRANSIENT_RETRIES = 3`
  - src/kiro_crew/llm_helpers.py:1790-1830 (run_bg_oneliner finally) and :2290-2335 (background_turn) — the only usage persists (`persist_token_record_async`) for background calls; the judge runner uses neither; decisions/*.py contain no usage write
  - src/kiro_crew/decisions/gate.py:109-110 — LLM lane budget clamped to 8.0..60.0 s; :160 `_DEFAULT_TIMEOUT_MS = 1000.0` -> 8 s; :1059 `asyncio.wait_for(_oracle(...).ask(...), timeout=budget)`; a timeout returns None -> the tick fires
  - src/kiro_crew/agent_materialization/service_agents.py:49-57 — kirocrew-lite spec: `"model": agent_mod._background_agent_model()`, `"tools": []`, `"mcpServers": {}`, `"prompt": ""`; agent.py:565-582 — _background_agent_model resolves `agent.role_models['background']` (default "auto")
  - src/kiro_crew/session.py:803-814 + :817-847 — with no caller model, a per-agent pin returns None so kiro-cli resolves the lite spec's model (the background role), not agent.model
  - docs/system-specs/modules/decisions.md:455 — 'An empty llm_model INHERITS -- ... the kirocrew-lite background template's model rather than the model of whichever session the tick belongs to'
  - …and 3 more in the verification results.
- **Checked by:** read
- **Required outcome:** A judge tick costs one small, accounted model call on the background role with no hidden retries: its usage is recorded (surface `bg:judge`, owner = the loop's slot), transient failures are not retried inside the 8 s budget (the gate already falls back to firing), and it does not start and destroy a separate ACP session per tick when a shared background runtime can serve it.
- **Solution:**
  1. Read docs/system-specs/modules/decisions.md (LLM lane, ~:453-470) and monitor-architecture.md 'Decision'; update decisions.md in the same commit.
  2. decisions/impl_llm.py:211 — pass `retry_transient=False` to stream_and_collect (the gate's wait_for already turns a failure into FALLBACK).
  3. Record usage: either route the runner through llm_helpers.run_bg_oneliner (it uses kirocrew-lite on the shared `_bg` runtime with ephemeral handles and persists usage at :1790-1830, with `crew_log_kind='judge'`, `crew_log_session_key=<loop slot>`), or snapshot `provider_last_turn_usage` before destroy and call persist_token_record_async the way background_turn does. If run_bg_oneliner is used, measure that queueing behind other background callers (Z3: one shared runtime) does not push the call past the 8 s floor; raise the floor or keep the per-call session if it does.
  4. Do NOT add a model change: the inherit default already resolves to role_models.background via the kirocrew-lite spec (decisions.md:455). Do not keep one long-lived session per loop either: replayed judge prompts would grow its context every tick.
  5. #14071 (partial-reading backstop) is a different defect (LOOP-21).
- **Done when:** A unit test registers a fake sessions manager with a fake provider that raises one transient error: the judge's ask raises without a retry (1 prompt sent); with a successful fake provider reporting usage, exactly one usage row with surface `bg:judge` is persisted per ask; no wall-clock sleeps (fake provider, injected clock).
- **Upstream:** #14071 (state unverified)
- **Changed from the source claim:** Mechanism partly confirmed: fresh session per judged tick, default transient retries (<=3, bounded by an 8 s wait), and no usage record all hold. Refuted: the model is NOT the chat model — 'inherit' resolves to the kirocrew-lite spec model, i.e. role_models.background. Overstated: the per-tick session is the tool-less, prompt-less lite agent, not a full session, and it runs only on judged (armed) loops. Z2 settled: the judge is a separate lite session, not the watched session's turn. Severity 55 -> 25. #14071 belongs to LOOP-21, not here.
- **Sources:** FIX_PLAN:LOOP-9, REVIEW_FINDINGS:B5, verify_needed:Z2

### LOOP-13 [20, armed, effort S] Auto-research has no judge but is bounded (cycles, stagnation, 24 h); fan-out is opt-in — PARTLY
- **Original claim:** Auto-research runs parallel subagents per cycle with no judge or runtime cap (corrected below)
- **Verified claim:** Auto-research agent mode (the default execution_mode) arms its worker loop with idle_secs=120 and the campaign's max_cycles (default 30, hard cap 100) but no autonudge judge and no max_runtime_secs. Parallel sub-agents are opt-in: parallel_workers defaults to 1 (no fan-out instruction at all) and is capped at 5; only when it is > 1 does the brief tell the worker to spawn_run up to that many tasks per cycle, and the brief does not pass include_memory=false, so those children inherit memory/lessons/project by the spawn default. Overstated in the source: the loop is bounded — max_cycles <= 100, a model-free stagnation check parks the campaign as STAGNANT after 5 cycles with new_findings_count == 0, a verified finding completes it, and the watchdog parks it for re-authorization after 24 h (_TRUST_TTL_SECS), which acts as a runtime cap.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/apps/builtins/auto_research/campaign/agent_mode.py:319-326 — svc.add(..., idle_secs=int(row['idle_secs'] or lifecycle.DEFAULT_IDLE_SECS), max_cycles=int(row['max_cycles'] or 0), stop_sentinel_path=..., admission_check=...) — no judge, no max_runtime_secs
  - src/kiro_crew/apps/builtins/auto_research/campaign/lifecycle.py:34-39 — MAX_CYCLES_HARD_CAP = 100, _MAX_PARALLEL_WORKERS = 5, DEFAULT_IDLE_SECS = 120; :155/:238 max_cycles default 30; :243 min(int(config.get('parallel_workers', 1)), _MAX_PARALLEL_WORKERS)
  - src/kiro_crew/apps/builtins/auto_research/campaign/storage.py:59 — DEFAULT_EXECUTION_MODE = "agent"
  - src/kiro_crew/apps/builtins/auto_research/campaign/publication.py:158-167 — 'if pw > 1:' brief says 'use spawn_run with a tasks array to investigate up to {pw} open sub-questions' — no include_memory guidance
  - src/kiro_crew/mcp_tools/spawn.py:728 — inc_memory = args.get('include_memory', True) is not False (default true); :262-270 tool description recommends false for parallel fan-out
  - src/kiro_crew/apps/builtins/auto_research/campaign/watchdog.py:61-81 — check_stagnation: 5 latest cycle files all new_findings_count == 0 -> True; :493-507 count >= max_cycles -> COMPLETE, stagnation -> STAGNANT
  - src/kiro_crew/apps/builtins/auto_research/campaign/watchdog.py:47,439-442 — _TRUST_TTL_SECS = 24 * 3600; after 24 h the run is parked (_expire_trust -> NEEDS_INPUT 'Auto-approval expired after 24h')
  - src/kiro_crew/apps/builtins/auto_research/handlers.py:600 — fork config defaults max_cycles 30, idle_secs DEFAULT_IDLE_SECS
- **Checked by:** read
- **Required outcome:** A parallel auto-research cycle spawns children that carry only the task text (no memory/lessons/project unless needed), and a campaign whose cycles stop producing findings ends early; an explicit max_runtime_secs is optional given the existing 24 h park.
- **Solution:**
  1. In publication.py:159-167 (the pw > 1 brief block) instruct spawn_run tasks with include_memory=false, include_lessons=false, include_project=false — the sub-question text is fully specified; keep it true only if the campaign is about the user's own work.
  2. Optionally pass max_runtime_secs to svc.add (agent_mode.py:319-326) equal to the watchdog's park horizon, so the autonudge loop itself stops rather than relying on the watchdog alone.
  3. Optionally tighten check_stagnation (watchdog.py:61) from 5 to 3 empty cycles — a cadence/cap change, so take-away: confirm with the user. No LLM progress judge is needed; the model-free stagnation check already is one.
- **Done when:** A test renders the brief for parallel_workers=3 and asserts the spawn instruction carries include_memory=false; a watchdog test with five synthetic cycle files of new_findings_count == 0 asserts the campaign transitions to STAGNANT (already covered) and, if step 2 lands, a fake-clock autonudge test asserts the loop deactivates with stopped_reason runtime_budget at the configured horizon.
- **Changed from the source claim:** Corrected: parallel fan-out is opt-in (default parallel_workers=1, cap 5); a model-free stagnation check, max_cycles (default 30, cap 100) and a 24 h watchdog park bound the run, so 'no progress judge / no runtime cap' is overstated. Confirmed: no autonudge judge or max_runtime_secs, and fan-out children inherit memory by default. Severity 30 -> 20.
- **Sources:** FIX_PLAN:LOOP-13, REVIEW_FINDINGS:N-lower(auto-research)

### LOOP-25 [20, armed, effort S] One unanswered approval stops a goal loop on its next wake (deliberate); it can be resumed — PARTLY
- **Original claim:** Goal loop dies permanently on one unanswered approval (corrected below)
- **Verified claim:** One tool-approval prompt that times out unanswered in a loop's session (dashboard window = min(slot approval timeout, tool_approval_timeout_secs); 300 s on channels) sets `loop.approval_stalled`, and the loop's NEXT wake deactivates it with stopped_reason 'approval_stalled' before firing, whatever cycles remain (so a 200-cycle patrol can end at 47). The evidence is slot-level: an unanswered prompt in an attended tab counts too. 'Permanently' is overstated: the loop is deactivated, not removed — it stays inspectable, a person can resume it from the goal popover, and a revival clears the flag. It never resumes on its own. The stop is deliberate and documented in code (a reactive stop on evidence that a cycle cannot act, so the loop does not spend its cap on cycles that will be declined); changing it is a design choice about tolerance, not a bug fix. Upstream #16976's numbers are the reporter's.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/autonudge_service/timers.py:92-123 — notify_approval_stalled: 'Called from the approval path when a prompt times out with no decision' ... 'The evidence is slot-level, not cycle-level: an unanswered prompt in an attended tab counts too' -> `loop.approval_stalled = True`
  - src/kiro_crew/autonudge_service/firing.py:164-188 — terminal check on the next wake: `if loop.approval_stalled: ... await self.update(loop.id, active=False, stopped_reason=APPROVAL_STALL_REASON); self._emit("expired", loop)`
  - src/kiro_crew/autonudge_service/model.py:479-491 — approval_stalled: 'consumed by _timer as a terminal condition on the NEXT wake' ... 'cleared on every revival'
  - src/kiro_crew/autonudge_service/mutations.py:1003-1004 — a revival (`not was_active` -> active) sets `loop.approval_stalled = False`
  - src/kiro_crew/dashboard/chat_runner.py:14128-14130 — `_approval_window = min(state.approval_timeout_for(slot), tool_approval_timeout_secs())`; :14186-14201 — on timeout calls `_autonudge.notify_approval_stalled(slot.key)`
  - src/kiro_crew/slack/gateway.py:1835-1846, messaging/approval.py:676-708, discord/renderer.py:606 — the same one-timeout trigger on channel surfaces (APPROVAL_TIMEOUT_S = 300.0, messaging/approval.py:481)
- **Checked by:** read
- **Required outcome:** A maintainer decides how much evidence stops an unattended loop. Options: (a) keep today's one-strike stop but make it visible as 'paused waiting for approval' and auto-resume when the user next answers or grants in that slot; (b) stop only after K consecutive cycles (e.g. 2) whose approvals went unanswered; (c) count only approvals raised by the loop's own cycle, not an attended tab's. Whatever is chosen, a long patrol is not ended by one prompt nobody saw, and a loop that truly cannot act still stops before spending its cap.
- **Solution:**
  1. Read docs/system-specs/modules/monitor-architecture.md 'Runtime bounds' and agent-interrupt-controller.md; grep docs/decisions/ (no entry at HEAD) and get the maintainer's choice among (a)/(b)/(c).
  2. For (b): replace the boolean `approval_stalled` (autonudge_service/model.py:491) with a consecutive counter raised in timers.py:92-123 and zeroed by notify_cycle_landed when a cycle completes with its approvals answered; firing.py:179 stops at the threshold.
  3. For (a): keep the stop, but set a resumable stopped_reason the revive logic auto-resumes when a later approval in that slot is answered (hook the answer path next to chat_runner.py:14201 / messaging/approval.py:676).
  4. Loosening a documented safety stop is a behaviour change: list the readers (firing.py terminal checks, the popover's stopped_reason wording, mutations.py revival) under Backwards compatibility and update learn-cron-dashboard.md's AutoNudge section in the same commit.
- **Done when:** A deterministic test drives AutoNudgeService with a fake clock: one notify_approval_stalled followed by a cycle whose approvals are answered keeps the loop active (option b) or auto-resumes it (option a); two consecutive stalled cycles still deactivate it with stopped_reason 'approval_stalled'.
- **Upstream:** #16976 (state unverified)
- **Changed from the source claim:** Mechanism confirmed: one unanswered approval stops the loop on its next wake. Corrected: the loop is deactivated and resumable (flag cleared on revival), not destroyed; the behaviour is a deliberate, documented reactive stop, so the fix is a maintainer decision on tolerance. Not a token-waste item (it ends spending early).
- **Sources:** verify_needed:G25(#16976)

### EVT-12 [15, default, effort M] Memory-queued spawns are not woken on events — CONFIRMED
- **Verified claim:** Code half confirmed: a spawn the memory-posture gate defers is parked until a fixed not-before stamp (agent.admit_wait_secs, default 30 s, restart-only) and woken only by a timer armed for that stamp (store.defer(now + wait) + call_later(wait, _drain_queue) for durable rows; MEMORY_WAIT_UNTIL_KEY + arm_memory_wait for window rows). A run ending or memory pressure easing does not make a deferred row eligible earlier: the pick's eligible() filter refuses any entry whose memory-wait stamp has not passed, and the store row is not claimable before its deferral time. A weighted per-lane round-robin (LaneScheduler) already orders the pick, so 'per-lane shares' exists for ordering but not for an event-driven memory wake. That PR #17030 adds the event wake is an unverified PR-title claim. Cost is start latency (up to admit_wait_secs per deferral round), not model tokens.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/subagent_manager/admission/taskq_bridge.py:576-592 — taskq_defer: 'Keep a queued row parked until the admit-wait passes; wake the pump then' — store.defer(agent_id, store.now() + wait); call_later(wait, self._manager._drain_queue)
  - src/kiro_crew/subagent_manager/admission/gate.py:799-812 — a memory-gate deferral of a durable row calls taskq_defer_posted / taskq_defer(agent_id, reason=reason)
  - src/kiro_crew/subagent_manager/admission/gate.py:1279-1281 — window row that missed the memory floor: queue_params[MEMORY_WAIT_UNTIL_KEY] = now + admit_wait; :1324 arm_memory_wait(...)
  - src/kiro_crew/subagent_manager/admission/fairness.py:378-386 — eligible(): float(params.get(MEMORY_WAIT_UNTIL_KEY) or 0.0) <= now (a memory wait is never picked before its stamp)
  - src/kiro_crew/subagent_manager/admission/fairness.py:406-430 — arm_memory_wait(until): timer wake one tick past the stamp, then _drain_queue()
  - src/kiro_crew/config/sections.py:1333-1340 — admit_wait_secs default=30: 'how long a spawn deferred by the memory posture gate waits before it is re-checked', restart=True
  - src/kiro_crew/subagent_manager/admission/fairness.py:84-92 — lane_scheduler(): weighted round-robin LaneScheduler shared by refill and pick (per-lane ordering exists)
  - src/kiro_crew/subagent.py:1226 — _RELEASE_REPUMP_SECS = 1.0 (a release re-pumps, but a deferred row is still ineligible until its stamp)
- **Checked by:** read
- **Required outcome:** A spawn deferred for memory is re-checked when memory is actually released (a run ends, the pressure reading drops) rather than only after a fixed admit wait, with freed capacity shared fairly across lanes; the timer stays as a backstop.
- **Solution:**
  1. Review upstream PR #17030 first (state unverified); if it is open, review/re-land it rather than writing a second fix.
  2. Otherwise: on a run's release (pump.py release path, _RELEASE_REPUMP_SECS) and on a memory-posture improvement, clear MEMORY_WAIT_UNTIL_KEY for window entries and pull the store row's deferral forward (a store 'undefer' for memory-reason rows), then _drain_queue() once; keep store.defer + call_later as the backstop (taskq_bridge.py:576-592).
  3. Let LaneScheduler (fairness.py:84-92) pick which lane's deferred row is admitted first, so one parent's flood cannot take all freed memory.
  4. Update subagent.md (admission / memory posture) in the same commit.
- **Done when:** Test with a fake monotonic clock and a stubbed store: a spawn deferred for memory at t=0 with admit_wait_secs=30 is dispatched within one pump pass after a sibling run releases at t=5 (not at t=30); with no release it is re-checked at t=30; two lanes with deferred rows are admitted in LaneScheduler order.
- **Upstream:** #14592 #16480 PR #17030 (state unverified)
- **Changed from the source claim:** Prior status was a PR-title scan; the code half is now confirmed by reading: memory deferrals are timer-woken (30 s admit wait), not event-woken. Per-lane ordering already exists (LaneScheduler); the PR half is unverified. Latency only, so severity 15.
- **Sources:** verify_needed:G37(#14592/#16480)

### LOOP-11 [15, armed, effort S] Channel auto-title has no attempt limit — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): src/kiro_crew/messaging/auto_title.py:114 TITLE_MAX_ATTEMPTS=3 (commit 004bf5a59)
- **Verified claim:** Channel auto-title (Slack native + transport paths, Telegram) has no per-key attempt cap: a SKIP verdict, a 30 s timeout or any exception calls release_claim, so the NEXT exchange in the same untitled conversation claims again and spends another background turn, indefinitely. It is per exchange (one extra call per user message in an untitled thread), not a timer loop, and each attempt is a small tool-free kirocrew-lite background turn (prompt <= ~682 chars), so the waste is low. The dashboard's own titler already stops after _TITLE_MAX_ATTEMPTS = 5 and falls back to the first message; the channel module has no equivalent.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/messaging/auto_title.py:494-498 — `title = clean_title(raw)` / `if not title: release_claim(session_key) # allow retry on the next exchange`
  - src/kiro_crew/messaging/auto_title.py:568-576 — TimeoutError and `except Exception` both `release_claim(session_key)`
  - src/kiro_crew/messaging/auto_title.py:133-150 — release_claim pops the key; try_claim succeeds whenever the key is absent; no attempt counter exists in the module
  - src/kiro_crew/slack/transport_dispatch.py:1061-1086, src/kiro_crew/slack/handler.py:3103-3118, src/kiro_crew/telegram/transport_dispatch.py:1338-1352 — every completed exchange of an untitled key re-claims and fires maybe_auto_title
  - src/kiro_crew/dashboard/chat_title.py:41 — `_TITLE_MAX_ATTEMPTS = 5` (dashboard precedent: gives up and writes the fallback title)
  - docs/system-specs/modules/messaging.md:2967-2980 — documents release on SKIP/timeout/exception; no cap described
  - merge/scripts/D/D2/auto_title_skip.py — real try_claim/maybe_auto_title with only background_turn faked: always-SKIP 1/10/100 exchanges -> 1/10/100 background turns, 0 titles; always-raise -> 1/10/100 turns
  - build_title_prompt('x'*200,'y'*200) = 682 chars; BACKGROUND_AGENT = kirocrew-lite (no tools, empty prompt)
- **Checked by:** ran-new-script merge/scripts/D/D2/auto_title_skip.py
- **Required outcome:** A channel conversation whose naming turn keeps answering SKIP or failing stops trying after 3 attempts per session key (FIX_PLAN number; the dashboard uses 5 — pick one with the user), and spends no further background turns on it in that process.
- **Solution:**
  1. messaging/auto_title.py: add `TITLE_MAX_ATTEMPTS = 3` and a bounded `_attempts: OrderedDict[str, int]` (evict at TITLE_LRU_MAX like _titled); clear it in reset().
  2. In maybe_auto_title's three release sites that follow a model turn (empty/SKIP title ~:495, TimeoutError ~:568, except Exception ~:575), increment the key's counter; when it reaches the cap call mark_titled(session_key, TITLE_KIND_AUTO) (or a new 'gave_up' kind) instead of release_claim, so try_claim refuses later exchanges. Leave the ABSENT-pin release (~:470, no model turn spent) and the replaced-record release (~:535) uncounted.
  3. Optionally write a fallback title as dashboard/chat_title.py does (_fallback_title_from_messages) — only via update_metadata_if with the same _untitled_and_still_ours guard.
  4. Update docs/system-specs/modules/messaging.md 'Auto-titling' (the release bullets ~:2967) in the same commit. Stopping titling after N skips removes behaviour a long untitled thread has today (late naming) — treat as a take-away change: list readers (slack/handler.py, slack/transport_dispatch.py, telegram/transport_dispatch.py) under Backwards compatibility.
- **Done when:** test_messaging_auto_title.py: with background_turn monkeypatched to a fake client that always streams 'SKIP', 10 sequential exchanges (is_titled/try_claim/maybe_auto_title as the callers do) produce exactly 3 stream() calls; the same with a client that raises; a client answering SKIP twice then 'Release notes' applies the title on attempt 3; reset() clears the counter. No clock needed.
- **Changed from the source claim:** Mechanism exactly as claimed. Added: retries are one per user exchange (no timer), each a ~682-char tool-free kirocrew-lite turn, so severity lowered 25 -> 15; Telegram and both Slack paths share the module; dashboard already has a 5-attempt cap to mirror.
- **Sources:** FIX_PLAN:LOOP-11, REVIEW_FINDINGS:H6

### LOOP-22 [15, armed, effort S] Monitoring supplemental retry burns budget on permanent failures — CONFIRMED

> **COMPLETED** — verified fixed on `main` by code read (no tests run): non-retryable supplemental provider error decided by the primary rule (commit f0914dec7)
- **Verified claim:** The structured-monitor decision retries a SUPPLEMENTAL provider error of any kind (authentication, authorization, not_found, setup included) until the consecutive-error streak reaches max_provider_errors, while a PRIMARY provider error of a non-retryable kind stops on the first tick via _RETRYABLE_PROVIDER_ERRORS. Measured with the real decide_monitor: every non-retryable kind stops at tick 1 as primary and at tick 3 as supplemental (2 RETRY_PROVIDER rounds) on the default budget of 3, and at tick 20 (19 retries) on the maximum budget of 20. What it burns is provider API calls and the provider-error budget, NOT model turns: RETRY_PROVIDER only re-arms the probe deadline (15 s doubling, capped at 300 s and at the cadence). It ends the same way either way: the watch is retired as STOP_BLOCKED once the streak trips.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/monitoring/decision.py:35-37 — _RETRYABLE_PROVIDER_ERRORS = frozenset({TRANSIENT, RATE_LIMITED})
  - src/kiro_crew/monitoring/decision.py:585-608 — _provider_error_decision: `if error not in _RETRYABLE_PROVIDER_ERRORS: return MonitorDecision.STOP_BLOCKED`
  - src/kiro_crew/monitoring/decision.py:611-618 — _supplemental_provider_error_decision checks only `state.consecutive_provider_errors + 1 >= budgets.max_provider_errors`, never the error kind
  - src/kiro_crew/monitoring/decision.py:291, :385 — both readable-target branches route supplemental errors to that kind-blind helper
  - src/kiro_crew/autonudge_service/monitor_records.py:442 — `provider_error = observation.provider_error or observation.supplemental_provider_error` (supplemental errors advance the same streak)
  - src/kiro_crew/autonudge_service/monitor_records.py:469-476 — RETRY_PROVIDER only sets the next probe deadline (backoff); no wake, no model turn
  - src/kiro_crew/monitoring/models.py:34 — DEFAULT_MONITOR_PROVIDER_ERRORS = 3; :49 — MAX_MONITOR_PROVIDER_ERRORS = 20
  - docs/system-specs/modules/learn-cron-dashboard.md:3217-3222 — spec says the supplemental failure is 'counted against the bounded provider-error streak, and retried' with no kind distinction
  - …and 1 more in the verification results.
- **Checked by:** ran-new-script merge/scripts/D/B2/loop22_supplemental.py
- **Required outcome:** A supplemental provider error of a non-retryable kind is decided by the same retryable-kind rule as a primary one, so a permanent secondary failure is not retried as if transient; what the watch then does with its readable primary facts is decided explicitly (retire as BLOCKED, or keep the primary facts and stop asking the supplemental source) rather than by streak exhaustion.
- **Solution:**
  1. Review upstream PR #12232 ("gate supplemental retry on retryable error kind") before writing code; per the parallel file it implements exactly this (state unverified).
  2. In src/kiro_crew/monitoring/decision.py:611 give _supplemental_provider_error_decision the observation's supplemental kind and apply `kind not in _RETRYABLE_PROVIDER_ERRORS` the way _provider_error_decision does at :591; keep the is_unattempted_probe exemption.
  3. Decide (owner question, write it in the PR) whether a permanent supplemental failure retires the watch (STOP_BLOCKED) or keeps observing on primary facts with the supplemental evidence marked incomplete; the spec text at learn-cron-dashboard.md:3217-3222 ('retried without discarding the readable primary facts') must be updated in the same commit either way.
  4. Update docs/system-specs/modules/learn-cron-dashboard.md (GitHub PR adapter section) and monitor-architecture.md 'Decision' if the decision table changes.
  5. Changing when a watch retires is a take-away change for owners of long watches: list the readers (autonudge_service/monitor_records.py, monitoring/shadow.py) under Backwards compatibility.
- **Done when:** In test/test_monitor_controller.py (or a decision unit test), a readable PENDING observation carrying supplemental_provider_error=AUTHORIZATION yields the chosen terminal/non-retry decision on tick 1, while supplemental TRANSIENT still yields RETRY_PROVIDER until max_provider_errors; both pinned with a fixed `now` and no sleeps.
- **Upstream:** #16707 PR #12232 PR #16709 (state unverified)
- **Changed from the source claim:** Mechanism confirmed exactly. Cost corrected: the retries are provider API probes bounded by max_provider_errors (default 3 -> 2 extra probes, max 20 -> 19), with no model turns; the 'burns budget' wording means the provider-error budget, not tokens. Nuance added: the documented design (learn-cron-dashboard.md:3217-3222) retries supplemental failures on purpose, so the fix needs an explicit decision on what a permanent supplemental failure should do.
- **Sources:** verify_needed:G32(#16707), verify_needed:G38, verify_needed:G41, verify_needed:G42, verify_needed:G43

### LOOP-28 [12, armed, effort S] Opt-in Jev client: option criteria sent as null, one 1 s call, no retry (both deliberate) — PARTLY
- **Original claim:** Jev decisions client sends no Choice criteria and allows 1 s with no retry (corrected below)
- **Verified claim:** The Jev client (decisions/impl_jev.py) sends every Choice's per-option `criteria` map with each value pinned to None — the option names travel, their meaning does not — and it makes exactly one POST bounded by `decisions.provider.timeout_ms` (default 1000 ms) with no retry; a timeout or error returns None to the caller's fallback. Both are deliberate and documented: 'the gate supplies fallback, not retries', and nudge.wake puts the owner's wake/quiet criteria in the question's instruction text instead of the per-option map. So 'sends no Choice criteria' is half true: the owner's criteria do reach Jev in the prompt for needs_owner, but options such as nudge.wake's outcome values (nothing_new / progress_only / needs_action / ...) and the other points' options go with no rubric. Scope: only when the Decisions seam's Jev lane is consented and keyed (the seam is off by default); a timeout costs the decision's saving (the tick fires / the default path runs), not extra spend.
- **Evidence (at `397f4be`):**
  - src/kiro_crew/decisions/impl_jev.py:154-160 — _question_to_wire: Choice -> `"criteria": {opt: None for opt in q.options}`
  - src/kiro_crew/decisions/types.py:32-37 — `class Choice: id, prompt, options` — no field for per-option rubric text
  - src/kiro_crew/decisions/points/nudge_wake.py:261-265 — 'The criteria ride in the PROMPT rather than in the provider's own per-option criteria map. That map exists on the wire but impl_jev._to_wire pins every entry to None'
  - src/kiro_crew/decisions/impl_jev.py:1-10 — 'Transport and protocol failures raise; the gate supplies fallback, not retries'
  - src/kiro_crew/decisions/impl_jev.py:274, :300 — `self._timeout_ms = getattr(provider, "timeout_ms", 1000)`; `aiohttp.ClientTimeout(total=max(0.001, timeout_ms / 1000.0))`, one session.post
  - src/kiro_crew/config/sections.py:4686-4687 — DecisionProviderConfig.timeout_ms default=1000; :4699-4700 — 'Decision seam ... Off by default'
  - src/kiro_crew/decisions/gate.py:505-533 — timeout_secs: Jev lane uses timeout_ms/1000 as-is (1 s); only the LLM lane is clamped up to 8-60 s
- **Checked by:** read
- **Required outcome:** Jev answers the questions it is asked with the meaning of each option available to it, and a transient provider hiccup does not silently discard the decision's saving: either one bounded retry inside the configured budget, or a measured timeout default that fits Jev's observed latency, decided from the decision log's timeout rate.
- **Solution:**
  1. Read docs/system-specs/modules/decisions.md (Jev provider section, ~:27) and update it in the same commit.
  2. Add an optional per-option rubric to decisions/types.py Choice (e.g. `option_rubric: dict[str, str] | None`), included in types.question_texts so the scrub sees it, and send it from impl_jev.py:160 instead of None; fill it for nudge.wake's OUTCOME_OPTIONS first (one sentence each).
  3. Read the decision log's error='timeout' rate for the Jev lane before touching retries; if material, allow ONE retry for connection errors only while the remaining budget exceeds the median latency (keep the total within timeout_ms), or raise the shipped timeout_ms.
  4. Keep allow_redirects=False and the no-credential rule for loopback endpoints unchanged.
- **Done when:** A loopback-server test (test_decisions_*) asserts a Choice with a rubric is sent as `criteria: {option: text}`; a server that fails the first connection and answers the second within timeout_ms produces answers (if the retry option is chosen) and one that stays silent still yields None within timeout_ms (fake clock / bounded wait, no sleeps beyond the server stub).
- **Upstream:** #14476 (state unverified)
- **Changed from the source claim:** Confirmed: 1 s default budget and no retry (deliberate, documented). Corrected: per-option criteria are sent as null by design and the owner's wake/quiet criteria travel in the instruction text, so it is 'no rubric for the options', not 'no criteria'. Opt-in seam (off by default); severity set at 12.
- **Sources:** verify_needed:G35(#14476)
